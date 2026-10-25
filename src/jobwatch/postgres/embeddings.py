"""Explicit local text inference and derived rows. No model/DB access on import."""
from dataclasses import dataclass
from hashlib import sha256
import argparse
import importlib.metadata
import json
import math
import os
import subprocess
import sys
import threading

from jobwatch.filtering import plain
from .connection import DatabaseConfig, connect

MODEL = 'google/embeddinggemma-2'
REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
PACKAGES = {'transformers': '5.20.2', 'torch': '2.14.1', 'safetensors': '0.8.0',
            'tokenizers': '0.23.3', 'huggingface-hub': '2.3.0'}
RUNTIME = ';'.join(f'{k}={v}' for k, v in PACKAGES.items()) + ';cpu-float32-mean-l2'
INFERENCE_SLOT = threading.BoundedSemaphore(1)


class EmbeddingError(Exception):
    """Only fixed, non-sensitive codes cross command/tool boundaries."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Profile:
    model_id: str = MODEL
    model_revision: str = REVISION
    dimensions: int = 768
    input_recipe: str = 'google-search-title-posting-v1'
    chunker_revision: str = 'offset-tokens-1024-overlap128-v1'
    runtime_revision: str = RUNTIME

    @property
    def values(self):
        return (self.model_id, self.model_revision, self.dimensions, self.input_recipe,
                self.chunker_revision, self.runtime_revision)

    @property
    def key(self):
        return sha256('\n'.join(map(str, self.values)).encode()).hexdigest()

    def metadata(self):
        return dict(zip(('model_id', 'model_revision', 'dimensions', 'input_recipe',
                         'chunker_revision', 'runtime_revision'), self.values), profile_id=self.key)


DEFAULT = Profile()


def vector_text(values, dimensions):
    if not isinstance(values, (list, tuple)) or len(values) != dimensions:
        raise EmbeddingError('invalid_vector')
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
        raise EmbeddingError('invalid_vector')
    if not any(x != 0 for x in values):
        raise EmbeddingError('invalid_vector')
    return json.dumps(values, allow_nan=False, separators=(',', ':'))


def query_text(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000 or '\0' in query:
        raise EmbeddingError('invalid_query')
    return 'task: search result | query: ' + query


def body_text(version):
    fields = ('company', 'location_text', 'department', 'team', 'employment_type', 'salary')
    metadata = '\n'.join(f'{k}: {version[k]}' for k in fields if version.get(k))
    # A retrieval recipe only; source bytes/normalisation/screening are never updated.
    description = version.get('description_text')
    body = '[description not retained]' if description is None else plain(description)
    return (metadata + '\n\n' + body).replace('\0', '\ufffd')


def chunks(version, tokenizer, *, max_tokens=1024, overlap=128):
    if not 1 <= overlap < max_tokens <= 8192:
        raise EmbeddingError('invalid_chunk_budget')
    title = version.get('title') or 'none'
    if '\0' in title:
        title = title.replace('\0', '\ufffd')
    prefix = f'title: {title} | text: '
    body = body_text(version)
    if len(body) > 1_000_000:
        raise EmbeddingError('input_too_large')
    header_tokens = len(tokenizer(prefix, add_special_tokens=True)['input_ids'])
    budget = max_tokens - header_tokens - 8  # Boundary tokenisation is also checked below.
    if budget <= overlap:
        raise EmbeddingError('title_too_long')
    offsets = tokenizer(body, add_special_tokens=False, return_offsets_mapping=True)['offset_mapping']
    result, cursor = [], 0
    while cursor < len(offsets) or not result:
        end = min(cursor + budget, len(offsets))
        start_char = offsets[cursor][0] if cursor else 0
        while True:
            end_char = offsets[end][0] if end < len(offsets) else len(body)
            text = prefix + body[start_char:end_char]
            if len(tokenizer(text, add_special_tokens=True)['input_ids']) <= max_tokens:
                break
            end -= 1
            if end <= cursor:
                raise EmbeddingError('chunk_budget_exceeded')
        result.append({'ordinal': len(result), 'start_char': start_char, 'end_char': end_char,
                       'input_text': text, 'input_sha256': sha256(text.encode()).hexdigest()})
        if end == len(offsets):
            break
        cursor = max(cursor + 1, end - overlap)
    for row in result:
        row['chunk_count'] = len(result)
    return result


class LocalEncoder:
    """Pinned, public, cache-only model. Acquisition is a separate explicit command."""
    def __init__(self):
        for package, version in PACKAGES.items():
            if importlib.metadata.version(package).split('+')[0] != version:
                raise EmbeddingError('runtime_version_mismatch')
        import torch
        from transformers import AutoConfig, AutoModel, AutoTokenizer
        torch.set_num_threads(2)
        kwargs = dict(revision=REVISION, local_files_only=True, trust_remote_code=False, token=False)
        config = AutoConfig.from_pretrained(MODEL, vision_config=None, audio_config=None, **kwargs)
        self.model = AutoModel.from_pretrained(MODEL, config=config, dtype=torch.float32, **kwargs).eval()
        core = self.model
        if (core.config.vision_config is not None or core.config.audio_config is not None
                or any(('vision' in k or 'audio' in k) for k, _ in core.named_parameters())
                or any(p.dtype != torch.float32 or p.device.type != 'cpu' for p in core.parameters())
                or core.config.text_config.embedding_dim != DEFAULT.dimensions):
            raise EmbeddingError('model_configuration_mismatch')
        # A text tokenizer, not AutoProcessor's image/video/audio stack.
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL, **kwargs)

    def encode(self, texts):
        if any(len(self.tokenizer(t, add_special_tokens=True)['input_ids']) > 8192 for t in texts):
            raise EmbeddingError('input_too_large')
        import torch
        # Google's projected token states -> masked mean pooling -> L2 normalisation.
        # Batch one bounds CPU memory; no float16, autocast, quantisation or dimension truncation.
        values = []
        with torch.inference_mode():
            for text in texts:
                inputs = self.tokenizer(text, return_tensors='pt', padding=False, truncation=False)
                states = self.model(**inputs).last_hidden_state
                mask = inputs['attention_mask'].unsqueeze(-1)
                pooled = (states * mask).sum(1) / mask.sum(1).clamp(min=1)
                values.append(torch.nn.functional.normalize(pooled, dim=1)[0].tolist())
        for value in values:
            vector_text(value, DEFAULT.dimensions)
        return values


def query_embedding(query):
    query_text(query)  # Validate before loading modules/starting a process.
    if not INFERENCE_SLOT.acquire(blocking=False):
        raise EmbeddingError('embedding_busy')
    try:
        # A separate process gives a real wall-clock deadline, including model loading.
        # No DB transaction or credentials are needed by inference. Nothing uses a web API.
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'AWS_', 'JOBWATCH_'))
               and k not in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN')}
        env['HF_HUB_OFFLINE'] = '1'
        p = subprocess.run([sys.executable, '-m', __name__, 'encode-query'],
                           input=json.dumps({'query': query}), capture_output=True, text=True,
                           timeout=60, env=env)
        if p.returncode:
            raise EmbeddingError('embedding_model_unavailable')
        result = json.loads(p.stdout)
        vector_text(result, DEFAULT.dimensions)
        return result
    except subprocess.TimeoutExpired:
        raise EmbeddingError('embedding_timeout') from None
    finally:
        INFERENCE_SLOT.release()


def write_chunks(conn, version, items, vectors, *, profile=DEFAULT):
    """Atomic per-version insert/reuse. Existing tuple disagreements are never overwritten."""
    if len(items) != len(vectors) or not items:
        raise EmbeddingError('invalid_chunk_set')
    if ([x['ordinal'] for x in items] != list(range(len(items)))
            or any(x['chunk_count'] != len(items) for x in items)):
        raise EmbeddingError('invalid_chunk_set')
    inserted = 0
    with conn.transaction():
        actual = conn.execute('SELECT evidence_sha256,normalized_sha256 FROM jobwatch.posting_versions WHERE id=%s',
                              (version['id'],)).fetchone()
        if actual is None or any(actual[k] != version[k] for k in actual):
            raise EmbeddingError('source_hash_mismatch')
        for item, values in zip(items, vectors):
            data = vector_text(values, profile.dimensions)
            if sha256(item['input_text'].encode()).hexdigest() != item['input_sha256']:
                raise EmbeddingError('input_hash_mismatch')
            result = conn.execute('''INSERT INTO jobwatch.embedding_chunks(version_id,model_id,model_revision,
                dimensions,input_recipe,chunker_revision,runtime_revision,ordinal,chunk_count,start_char,end_char,
                evidence_sha256,normalized_sha256,input_text,profile_id,input_sha256,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::public.vector)
                ON CONFLICT(version_id,profile_id,ordinal) DO NOTHING''',
                (version['id'], *profile.values, item['ordinal'], item['chunk_count'], item['start_char'], item['end_char'],
                 version['evidence_sha256'], version['normalized_sha256'], item['input_text'], profile.key,
                 item['input_sha256'], data))
            inserted += result.rowcount
        stored = conn.execute('''SELECT ordinal,chunk_count,start_char,end_char,input_text,input_sha256,
            evidence_sha256,normalized_sha256 FROM jobwatch.embedding_chunks
            WHERE version_id=%s AND profile_id=%s ORDER BY ordinal''', (version['id'], profile.key)).fetchall()
        expected = [{**x, 'evidence_sha256': version['evidence_sha256'],
                     'normalized_sha256': version['normalized_sha256']} for x in items]
        if stored != expected:
            raise EmbeddingError('embedding_conflict')
    return inserted


def index_missing(conn, encoder, *, limit=100, history=False):
    from psycopg.pq import TransactionStatus
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise EmbeddingError('idle_connection_required')
    if type(limit) is not int or not 1 <= limit <= 1000 or type(history) is not bool:
        raise EmbeddingError('invalid_arguments')
    # Fetch inputs then finish all reading transactions before expensive inference.
    versions = conn.execute('''SELECT v.* FROM jobwatch.posting_versions v JOIN jobwatch.postings p USING(posting_key)
        LEFT JOIN (SELECT version_id,count(*) AS n,max(chunk_count) AS expected,min(chunk_count) AS least
                   FROM jobwatch.embedding_chunks WHERE profile_id=%s GROUP BY version_id) e ON e.version_id=v.id
        WHERE (%s OR v.id=p.current_version_id) AND (e.n IS NULL OR e.n<>e.expected OR e.least<>e.expected)
        ORDER BY v.id LIMIT %s''', (DEFAULT.key, history, limit)).fetchall()
    inserted = 0
    for version in versions:
        items = chunks(version, encoder.tokenizer)
        vectors = encoder.encode([x['input_text'] for x in items])
        inserted += write_chunks(conn, version, items, vectors)
    return {'profile': DEFAULT.metadata(), 'versions': len(versions), 'inserted_chunks': inserted}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('download-model', help='Explicit public model download; no job text is uploaded')
    sub.add_parser('encode-query', help='Encode one JSON query from stdin using the local cache')
    worker = sub.add_parser('index')
    worker.add_argument('--limit', type=int, default=100)
    worker.add_argument('--history', action='store_true')
    args = p.parse_args(argv)
    try:
        if args.command == 'download-model':
            from huggingface_hub import snapshot_download
            snapshot_download(MODEL, revision=REVISION, token=False,
                              allow_patterns=['*.json', '*.safetensors', 'tokenizer.model'])
            result = {'downloaded': True, 'profile': DEFAULT.metadata()}
        elif args.command == 'encode-query':
            request = json.loads(sys.stdin.read(32_769))
            result = LocalEncoder().encode([query_text(request['query'])])[0]
        else:
            dsn = os.environ.get('JOBWATCH_EMBEDDING_DATABASE_URL', '')
            if not dsn:
                raise EmbeddingError('embedding_database_url_required')
            config = DatabaseConfig(dsn)
            with connect(config) as conn:
                identity = conn.execute('''SELECT current_user AS name,session_user AS login,
                    rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls,
                    EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS membership
                    FROM pg_roles r WHERE rolname=current_user''').fetchone()
                if (identity['name'] != 'jobwatch_embedding_writer' or identity['login'] != identity['name']
                        or any(identity[k] for k in ('rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls','membership'))):
                    raise EmbeddingError('wrong_embedding_identity')
                encoder = LocalEncoder()
                result = index_missing(conn, encoder, limit=args.limit, history=args.history)
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'error': exc.code if isinstance(exc, EmbeddingError) else 'embedding_operation_failed'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
