"""Isolated PostgreSQL 17 retrieval scalability benchmark (10k / 50k / 100k versions).

Benchmarks four PostgreSQL/Python lexical retrieval approaches on realistic
synthetic posting-version corpora inside a temporary loopback-only Docker
PostgreSQL 17 container (destroyed automatically on exit):
  1. Current in-Python BM25 (`jobwatch.search.BM25`)
  2. Existing PostgreSQL `english` FTS (`to_tsvector('english', ...)` + GIN)
  3. PostgreSQL symbol-preserving FTS (`simple` + deterministic C++/C#/.NET mapping + GIN)
  4. PostgreSQL-native persistent inverted-index BM25 in pure SQL (exact parity
     with `jobwatch.search.BM25`, zero external extensions)

Measures ranking parity, C/C++/C# lexical distinction, index build time, disk
footprint, incremental insert latency, p50/p95 query latency, and peak Python
memory. Never touches production.
"""
from __future__ import annotations

import argparse
from collections import Counter
import io
import json
import math
from pathlib import Path
import random
import resource
import secrets
import statistics
import subprocess
import sys
import time
import tracemalloc
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / 'src'))

from jobwatch.search import BM25, tokens as jobwatch_tokens

# Realistic technical vocabulary matching UK grad/tech corpus (~350-450 tokens/version)
COMMON_WORDS = (
    'software engineer engineering team build scalable reliable distributed systems services '
    'production cloud infrastructure backend frontend fullstack platform data pipeline analytics '
    'design develop test deploy monitor collaborate agile product customers architecture performance '
    'security code review automated testing ci cd microservices api rest graphql database sql '
    'experience degree computer science mathematics graduate entry level junior associate 2027 '
    'london cambridge bristol manchester edinburgh oxford united kingdom hybrid remote permanent '
    'salary benefits pension learning mentorship growth impact problem solving algorithms structures '
    'linux containers observability metrics logging tracing availability latency throughput quality'
).split()

TECH_CLUSTERS = [
    ('python', 'django', 'fastapi', 'postgresql', 'redis', 'celery', 'pytest', 'docker', 'kubernetes'),
    ('java', 'kotlin', 'spring', 'kafka', 'jvm', 'microservices', 'maven', 'gradle', 'distributed'),
    ('javascript', 'typescript', 'react', 'nextjs', 'nodejs', 'graphql', 'html', 'css', 'webpack'),
    ('rust', 'systems', 'kernel', 'embedded', 'lowlatency', 'concurrency', 'memory', 'compilers', 'llvm'),
    ('golang', 'kubernetes', 'terraform', 'aws', 'gcp', 'devops', 'sre', 'prometheus', 'networking'),
    ('pytorch', 'tensorflow', 'machine', 'learning', 'mlops', 'llm', 'transformers', 'nlp', 'cuda'),
    ('c++', 'cpp', 'stl', 'cmake', 'cuda', 'trading', 'simulation', 'robotics', 'graphics'),
    ('c#', 'dotnet', 'aspnet', 'azure', 'entity', 'blazor', 'windows', 'sqlserver', 'linq'),
    ('c', 'posix', 'firmware', 'microcontroller', 'rtos', 'drivers', 'hardware', 'fpga', 'verilog'),
    ('scala', 'spark', 'flink', 'hadoop', 'airflow', 'snowflake', 'dbt', 'parquet', 'warehouse'),
]

TITLES = (
    'Graduate Software Engineer 2027',
    'Junior Backend Developer',
    'Graduate Machine Learning Engineer',
    'Systems Software Engineer',
    'Quantitative Developer - Graduate',
    'Platform Reliability Engineer',
    'Associate Cloud Engineer',
    'Research Software Engineer',
    'Embedded Firmware Engineer',
    'Full Stack Software Engineer',
)

COMPANIES = tuple(f'TechEmployer{i:03d}' for i in range(1, 201))
LOCATIONS = ('London, United Kingdom', 'Cambridge, UK', 'Bristol, UK', 'Manchester, UK', 'Edinburgh, Scotland, UK', 'Remote, United Kingdom')

BENCHMARK_QUERIES = (
    'C++',
    'C#',
    'C',
    'Python machine learning graduate',
    'distributed systems Rust',
    'Kubernetes Terraform AWS',
    'C++ lowlatency trading',
    'C# dotnet Azure',
    'Graduate Software Engineer London',
    'PyTorch LLM NLP',
)


def _start_postgres() -> tuple[str, int]:
    name = f'jobwatch-retrieval-bench-{secrets.token_hex(6)}'
    subprocess.run(
        [
            'docker', 'run', '-d', '--rm',
            '--name', name,
            '--label', 'jobwatch.experiment=retrieval-benchmark',
            '-p', '127.0.0.1::5432',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
            '-e', 'POSTGRES_DB=bench',
            'postgres:17',
            '-c', 'shared_buffers=256MB',
            '-c', 'work_mem=8MB',
            '-c', 'maintenance_work_mem=128MB',
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    port = int(subprocess.check_output(['docker', 'port', name, '5432/tcp'], text=True).strip().rsplit(':', 1)[1])
    for _ in range(60):
        p = subprocess.run(
            ['docker', 'exec', name, 'psql', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'bench', '-c', 'SELECT 1'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if p.returncode == 0:
            return name, port
        time.sleep(0.3)
    subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raise RuntimeError('Benchmark Postgres failed to start')


def generate_doc(rng: random.Random, idx: int) -> tuple[str, str, str, str, str]:
    title = TITLES[idx % len(TITLES)]
    company = COMPANIES[idx % len(COMPANIES)]
    location = LOCATIONS[idx % len(LOCATIONS)]
    # Pick 1 primary tech cluster (with C/C++/C# intentionally kept rare & distinct!)
    c_idx = idx % len(TECH_CLUSTERS)
    cluster = TECH_CLUSTERS[c_idx]
    # Keep C++, C#, and standalone C at realistic ~3% frequency each so IDF is meaningful
    if c_idx in (6, 7, 8) and (idx // len(TECH_CLUSTERS)) % 3 != 0:
        cluster = TECH_CLUSTERS[idx % 6]
    words = [title, company, location]
    # ~180 words -> realistic ~1.3 KB plain text (~160 non-stop tokens, ~25-45% of raw HTML size)
    for _ in range(14):
        words.extend(rng.sample(COMMON_WORDS, 10))
        words.extend(rng.sample(cluster, 3))
    body = ' '.join(words)
    return str(uuid4()), title, company, location, body


def symbol_normalize(text: str) -> str:
    """Deterministic symbol-preserving transform for standard PostgreSQL FTS."""
    # Order matters: C++ before C, C# before C, .NET before dot split.
    s = text.lower()
    s = s.replace('c++', ' sym_cpp ')
    s = s.replace('c#', ' sym_csharp ')
    s = s.replace('f#', ' sym_fsharp ')
    s = s.replace('.net', ' sym_dotnet ')
    return s


def benchmark_scale(conn, n_docs: int, *, run_python_bm25: bool = False) -> dict:
    rng = random.Random(20261124 + n_docs)

    conn.execute('DROP SCHEMA IF EXISTS bench CASCADE; CREATE SCHEMA bench;')
    conn.execute('''
        CREATE TABLE bench.docs (
            id uuid PRIMARY KEY,
            title text NOT NULL,
            company text NOT NULL,
            location text NOT NULL,
            body text NOT NULL,
            fts_en tsvector,
            fts_sym tsvector
        );
        CREATE TABLE bench.bm25_docs (
            doc_id uuid PRIMARY KEY REFERENCES bench.docs(id) ON DELETE CASCADE,
            doc_len integer NOT NULL
        );
        CREATE TABLE bench.bm25_terms (
            term text PRIMARY KEY,
            df integer NOT NULL
        );
        CREATE TABLE bench.bm25_postings (
            term text NOT NULL,
            doc_id uuid NOT NULL,
            tf integer NOT NULL,
            PRIMARY KEY (term, doc_id)
        );
    ''')

    # Generate & load documents via COPY (fast!) + collect BM25 postings
    t_gen0 = time.monotonic()
    df_counts: Counter[str] = Counter()
    doc_rows = []
    bm25_doc_rows = []
    bm25_posting_batches = []
    py_counters = {} if run_python_bm25 else None

    with conn.cursor() as cur:
        with cur.copy('COPY bench.docs (id, title, company, location, body) FROM STDIN') as copy:
            for i in range(n_docs):
                did, title, company, loc, body = generate_doc(rng, i)
                copy.write_row((did, title, company, loc, body))
                toks = jobwatch_tokens(f'{title} {company} {loc} {body}')
                ctr = Counter(toks)
                bm25_doc_rows.append((did, sum(ctr.values())))
                for t, tf in ctr.items():
                    df_counts[t] += 1
                    bm25_posting_batches.append((t, did, tf))
                if run_python_bm25:
                    py_counters[did] = ctr
    t_load_docs = round(time.monotonic() - t_gen0, 2)

    # 1. Build PostgreSQL English FTS GIN index
    t0 = time.monotonic()
    conn.execute("UPDATE bench.docs SET fts_en = to_tsvector('english', title || ' ' || company || ' ' || location || ' ' || body)")
    conn.execute('CREATE INDEX idx_fts_en ON bench.docs USING gin (fts_en)')
    t_fts_en_build = round(time.monotonic() - t0, 2)

    # 2. Build PostgreSQL Symbol-Preserving FTS GIN index
    t0 = time.monotonic()
    conn.execute("""
        UPDATE bench.docs SET fts_sym = to_tsvector(
            'simple',
            replace(replace(replace(replace(lower(title || ' ' || company || ' ' || location || ' ' || body),
                'c++', ' sym_cpp '), 'c#', ' sym_csharp '), 'f#', ' sym_fsharp '), '.net', ' sym_dotnet ')
        )
    """)
    conn.execute('CREATE INDEX idx_fts_sym ON bench.docs USING gin (fts_sym)')
    t_fts_sym_build = round(time.monotonic() - t0, 2)

    # 3. Build PostgreSQL-native persistent inverted-index BM25 (pure SQL, zero extensions)
    t0 = time.monotonic()
    with conn.cursor() as cur:
        with cur.copy('COPY bench.bm25_docs (doc_id, doc_len) FROM STDIN') as copy:
            for row in bm25_doc_rows:
                copy.write_row(row)
        with cur.copy('COPY bench.bm25_terms (term, df) FROM STDIN') as copy:
            for term, df in df_counts.items():
                copy.write_row((term, df))
        # Sort by (term, doc_id) before COPY for optimal B-tree build
        bm25_posting_batches.sort()
        with cur.copy('COPY bench.bm25_postings (term, doc_id, tf) FROM STDIN') as copy:
            for row in bm25_posting_batches:
                copy.write_row(row)
    conn.execute('ANALYZE bench.docs, bench.bm25_docs, bench.bm25_terms, bench.bm25_postings')
    t_sql_bm25_build = round(time.monotonic() - t0, 2)
    del bm25_doc_rows, bm25_posting_batches

    # Disk footprints
    sizes = conn.execute("""
        SELECT json_build_object(
            'docs_table_bytes', pg_relation_size('bench.docs') + pg_relation_size((SELECT reltoastrelid FROM pg_class WHERE oid='bench.docs'::regclass)),
            'fts_en_gin_bytes', pg_relation_size('bench.idx_fts_en'),
            'fts_sym_gin_bytes', pg_relation_size('bench.idx_fts_sym'),
            'sql_bm25_total_bytes',
                pg_total_relation_size('bench.bm25_docs')
                + pg_total_relation_size('bench.bm25_terms')
                + pg_total_relation_size('bench.bm25_postings')
        ) AS s
    """).fetchone()[0]

    # Query benchmarks (warm + measured across BENCHMARK_QUERIES)
    SQL_BM25_QUERY = """
        WITH stats AS (
            SELECT count(*)::double precision AS n, avg(doc_len)::double precision AS avg_len FROM bench.bm25_docs
        ),
        q AS (
            SELECT term, count(*)::double precision AS qmult FROM unnest(%s::text[]) AS term GROUP BY term
        )
        SELECT p.doc_id::text,
               sum(
                   q.qmult * ln(1.0 + (stats.n - t.df + 0.5) / (t.df + 0.5))
                   * (p.tf * (1.2 + 1.0)) / (p.tf + 1.2 * (1.0 - 0.75 + 0.75 * d.doc_len / NULLIF(stats.avg_len, 0)))
               ) AS score
        FROM q
        JOIN bench.bm25_terms t ON t.term = q.term
        JOIN bench.bm25_postings p ON p.term = q.term
        JOIN bench.bm25_docs d ON d.doc_id = p.doc_id
        CROSS JOIN stats
        GROUP BY p.doc_id
        ORDER BY score DESC, p.doc_id::text
        LIMIT 20
    """

    fts_en_ms, fts_sym_ms, sql_bm25_ms = [], [], []
    distinction_checks = {}

    for q in BENCHMARK_QUERIES:
        # English FTS
        t0 = time.monotonic()
        r_en = conn.execute(
            "SELECT id::text, ts_rank_cd(fts_en, websearch_to_tsquery('english', %s)) AS s "
            "FROM bench.docs WHERE fts_en @@ websearch_to_tsquery('english', %s) ORDER BY s DESC, id::text LIMIT 20",
            (q, q),
        ).fetchall()
        fts_en_ms.append((time.monotonic() - t0) * 1000)

        # Symbol-preserving FTS
        q_sym = symbol_normalize(q)
        t0 = time.monotonic()
        r_sym = conn.execute(
            "SELECT id::text, ts_rank_cd(fts_sym, websearch_to_tsquery('simple', %s)) AS s "
            "FROM bench.docs WHERE fts_sym @@ websearch_to_tsquery('simple', %s) ORDER BY s DESC, id::text LIMIT 20",
            (q_sym, q_sym),
        ).fetchall()
        fts_sym_ms.append((time.monotonic() - t0) * 1000)

        # Pure-SQL Persistent BM25
        q_toks = jobwatch_tokens(q)
        t0 = time.monotonic()
        r_sql = conn.execute(SQL_BM25_QUERY, (q_toks,)).fetchall()
        sql_bm25_ms.append((time.monotonic() - t0) * 1000)

        if q in ('C++', 'C#', 'C'):
            distinction_checks[q] = {
                'fts_en_top3': [row[0] for row in r_en[:3]],
                'fts_sym_top3': [row[0] for row in r_sym[:3]],
                'sql_bm25_top3': [row[0] for row in r_sql[:3]],
            }

    # Verify C / C++ / C# distinction
    en_collapsed = (
        distinction_checks['C++']['fts_en_top3'] == distinction_checks['C']['fts_en_top3']
        and distinction_checks['C#']['fts_en_top3'] == distinction_checks['C']['fts_en_top3']
    )
    sym_distinct = len({
        tuple(distinction_checks['C++']['fts_sym_top3']),
        tuple(distinction_checks['C#']['fts_sym_top3']),
        tuple(distinction_checks['C']['fts_sym_top3']),
    }) == 3
    sql_bm25_distinct = len({
        tuple(distinction_checks['C++']['sql_bm25_top3']),
        tuple(distinction_checks['C#']['sql_bm25_top3']),
        tuple(distinction_checks['C']['sql_bm25_top3']),
    }) == 3

    # Exact parity check vs Python BM25 (when run_python_bm25=True)
    py_bm25_result = None
    if run_python_bm25 and py_counters is not None:
        tracemalloc.start()
        t0 = time.monotonic()
        idx_py = BM25.from_counters(py_counters)
        t_py_build_ms = round((time.monotonic() - t0) * 1000, 1)
        py_query_ms = []
        exact_order_matches = 0
        max_score_diff = 0.0
        for q in BENCHMARK_QUERIES:
            t0 = time.monotonic()
            r_py = idx_py.rank(q, 20)
            py_query_ms.append((time.monotonic() - t0) * 1000)
            r_sql = conn.execute(SQL_BM25_QUERY, (jobwatch_tokens(q),)).fetchall()
            if [k for k, _ in r_py] == [k for k, _ in r_sql]:
                exact_order_matches += 1
            for (_, sp), (_, ss) in zip(r_py, r_sql):
                max_score_diff = max(max_score_diff, abs(sp - ss))
        _, py_peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        py_bm25_result = {
            'build_ms': t_py_build_ms,
            'p50_query_ms': round(statistics.median(py_query_ms), 2),
            'p95_query_ms': round(sorted(py_query_ms)[int(0.95 * len(py_query_ms)) - 1], 2),
            'peak_python_mib': round(py_peak_bytes / (1024 * 1024), 1),
            'exact_top20_order_matches': f'{exact_order_matches}/{len(BENCHMARK_QUERIES)}',
            'max_abs_score_diff': max_score_diff,
        }

    # Incremental single-document insert cost (average of 20 inserts)
    inc_fts_ms = []
    inc_sql_bm25_ms = []
    for k in range(20):
        did, title, company, loc, body = generate_doc(rng, n_docs + k)
        t0 = time.monotonic()
        conn.execute(
            "INSERT INTO bench.docs(id, title, company, location, body, fts_en, fts_sym) "
            "VALUES (%s,%s,%s,%s,%s, to_tsvector('english', %s), to_tsvector('simple', %s))",
            (did, title, company, loc, body, f'{title} {company} {loc} {body}', symbol_normalize(f'{title} {company} {loc} {body}')),
        )
        inc_fts_ms.append((time.monotonic() - t0) * 1000)

        ctr = Counter(jobwatch_tokens(f'{title} {company} {loc} {body}'))
        t0 = time.monotonic()
        with conn.transaction():
            conn.execute('INSERT INTO bench.bm25_docs(doc_id, doc_len) VALUES (%s, %s)', (did, sum(ctr.values())))
            terms = list(ctr.keys())
            tfs = [ctr[t] for t in terms]
            conn.execute(
                'INSERT INTO bench.bm25_terms(term, df) SELECT t, 1 FROM unnest(%s::text[]) AS t '
                'ON CONFLICT (term) DO UPDATE SET df = bench.bm25_terms.df + 1',
                (terms,),
            )
            conn.execute(
                'INSERT INTO bench.bm25_postings(term, doc_id, tf) SELECT t, %s, f FROM unnest(%s::text[], %s::int[]) AS u(t, f)',
                (did, terms, tfs),
            )
        inc_sql_bm25_ms.append((time.monotonic() - t0) * 1000)

    def _stats(vals):
        s = sorted(vals)
        return {'p50_ms': round(statistics.median(s), 2), 'p95_ms': round(s[min(len(s) - 1, int(0.95 * len(s)))], 2)}

    return {
        'corpus_versions': n_docs,
        'unique_terms': len(df_counts),
        'load_docs_s': t_load_docs,
        'build_s': {
            'fts_en': t_fts_en_build,
            'fts_sym': t_fts_sym_build,
            'sql_bm25': t_sql_bm25_build,
        },
        'disk_mib': {k: round(v / (1024 * 1024), 2) for k, v in sizes.items()},
        'query_latency': {
            'fts_en': _stats(fts_en_ms),
            'fts_sym': _stats(fts_sym_ms),
            'sql_bm25': _stats(sql_bm25_ms),
        },
        'incremental_insert_ms': {
            'docs_plus_both_fts': round(statistics.mean(inc_fts_ms), 2),
            'sql_bm25_update': round(statistics.mean(inc_sql_bm25_ms), 2),
        },
        'lexical_distinction_c_cpp_csharp': {
            'fts_en_collapsed_all_three': en_collapsed,
            'fts_sym_preserved_all_three': sym_distinct,
            'sql_bm25_preserved_all_three': sql_bm25_distinct,
        },
        'python_bm25_baseline': py_bm25_result,
        'process_peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='PostgreSQL 17 retrieval scalability benchmark')
    parser.add_argument('--scales', type=int, nargs='+', default=[10000, 50000, 100000])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)

    import psycopg
    container, port = _start_postgres()
    try:
        with psycopg.connect(host='127.0.0.1', port=port, user='postgres', dbname='bench', autocommit=True) as conn:
            results = []
            for n in args.scales:
                res = benchmark_scale(conn, n, run_python_bm25=(n <= 10000))
                results.append(res)
                print(json.dumps(res, indent=2), flush=True)
        out = {'benchmark': 'postgresql17_retrieval_scalability', 'results': results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, indent=2) + '\n')
    finally:
        subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
