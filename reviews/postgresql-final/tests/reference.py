"""Review-only executable transaction probe, not an application persistence layer.

Exercises the proposed contract with the EXISTING v1 adapter/screener. Deployment
rollouts, retries/backoff and production import tooling remain implementation work.
"""
from __future__ import annotations
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from jobwatch.feeds import collect_board
from jobwatch.filtering import screen, inputs_from_posting


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def safe(value):
    if isinstance(value, str):
        return value.replace('\0', '\ufffd')
    if isinstance(value, list):
        return [safe(v) for v in value]
    if isinstance(value, dict):
        return {safe(k): safe(v) for k, v in value.items()}
    return value


class Words(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def semantic_text(text):
    parser = Words()
    parser.feed(unescape(unescape(text or '')))
    return ' '.join(' '.join(parser.parts).split())


def material(provider, raw):
    # Executable regression subset. The complete v1 field selection is in FINAL_REVIEW.md.
    if provider == 'ashby':
        return {k: raw[k] for k in ('title', 'descriptionPlain', 'location', 'department', 'team',
                'employmentType', 'jobUrl', 'applyUrl', 'workplaceType', 'compensation') if k in raw}
    if provider == 'greenhouse':
        result = {k: raw[k] for k in ('title', 'location', 'absolute_url', 'application_deadline') if k in raw}
        if 'content' in raw:
            result['description'] = semantic_text(raw['content'])
        if 'metadata' in raw:
            result['metadata'] = sorted(raw['metadata'], key=lambda x: encoded(x))
        return result
    raise NotImplementedError('Lever comparison policy is specified, not implemented by this probe')


def compare(provider, before, after):
    a, b = material(provider, before), material(provider, after)
    return ({k: {'before': a.get(k), 'after': b.get(k)} for k in a if a[k] != b.get(k)},
            {k: b[k] for k in b if k not in a})


def connect(user='postgres'):
    # Only the container's explicitly supplied loopback address is allowed.
    port = int(os.environ['JOBWATCH_REVIEW_PORT'])
    assert 1024 <= port <= 65535
    return psycopg.connect(host='127.0.0.1', port=port, dbname='final_review', user=user,
                           autocommit=True, row_factory=dict_row)


def actor(conn, name='test:review'):
    conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (name,))


def fixture(native_id='1', title='Graduate Software Engineer', content='Build Python services'):
    return {'id': native_id, 'internal_job_id': 100, 'title': title,
            'absolute_url': f'https://job-boards.greenhouse.io/test/jobs/{native_id}',
            'location': {'name': 'London'}, 'content': content}


def normalize(raw, board='test'):
    p = collect_board({'provider': 'greenhouse', 'board': board, 'company': 'Test'},
        get_json=lambda _: {'jobs': [raw], 'meta': {'total': 1}})[0]
    n = safe({'company': p.company, 'title': p.title, 'posting_url': p.url,
        'location_text': p.location, 'country': p.country, 'countries': list(p.countries),
        'department': p.department, 'team': p.team, 'employment_type': p.employment_type,
        'salary': p.salary, 'published_at': p.published_at, 'source_dates': p.source_dates,
        'deadlines': [], 'description_text': p.description, 'extra_fields': {}})
    # Re-evaluate on sanitized derived inputs, retaining unsanitized source bytes separately.
    result = screen(safe(inputs_from_posting(p)))
    return p, n, result


def version(conn, key, source, raw, run_id, observed, normalizer='v1', override=None):
    p, n, result = normalize(raw)
    if override:
        n.update(override)
    sha = hashlib.sha256(encoded(n)).hexdigest()
    blob = encoded(raw)
    evidence_sha = hashlib.sha256(blob).hexdigest()
    row = conn.execute('SELECT id FROM jobwatch.posting_versions WHERE posting_key=%s '
        'AND evidence_sha256=%s AND normalizer_version=%s AND normalized_sha256=%s',
        (key, evidence_sha, normalizer, sha)).fetchone()
    if row:
        return row['id'], n, result
    vid = uuid4()
    fields = ['id','posting_key','source_id','origin','evidence','first_run_id','observed_at',
              'normalizer_version','normalized_sha256','nul_replaced'] + list(n)
    values = [vid,key,source,'collector',blob,run_id,observed,normalizer,sha,
              raw != safe(raw)]
    values += [Jsonb(v) if isinstance(v, dict) or k=='deadlines' else v for k, v in n.items()]
    conn.execute('INSERT INTO jobwatch.posting_versions ('+','.join(fields)+') VALUES ('+
                 ','.join(['%s']*len(fields))+')', values)
    return vid, n, result


def register(source, revision='pipeline-v1', key=None):
    key = key or uuid4()
    with connect('jobwatch_collector') as conn, conn.transaction():
        actor(conn)
        # Original UUID reused before fetch; do not allocate a newer order on uncertain ack.
        conn.execute("INSERT INTO jobwatch.collection_runs(id,source_id,mode,request_version,processing_revision) "
                     "VALUES (%s,%s,'publish','gh:v1',%s) ON CONFLICT (id) DO NOTHING", (key,source,revision))
        run = conn.execute('SELECT * FROM jobwatch.collection_runs WHERE id=%s', (key,)).fetchone()
        assert run['source_id'] == source and run['processing_revision'] == revision
        return run


def event(conn, key, source, run, category, kind, before, after, changes=None):
    at=(conn.execute('SELECT fetched_at FROM jobwatch.collection_runs WHERE id=%s',(run,)).fetchone()['fetched_at']
        if run else datetime.now(timezone.utc))
    conn.execute('INSERT INTO jobwatch.posting_events(posting_key,source_id,run_id,observed_at,category,kind,'
                 'before_version_id,after_version_id,comparison_version,changes) '
                 'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                 (key,source,run,at,category,kind,before,after,'material-v1',Jsonb(safe(changes or {}))))


def publish(run_id, rows, absence_safe=True, entered=None, release=None, fault=False, fetched_at=None):
    # Rows are already the fully validated result of ALL network requests.
    fetched = fetched_at or datetime.now(timezone.utc)  # fixtures supply synthetic receipt; retain it across retries
    with connect('jobwatch_collector') as conn, conn.transaction():
        actor(conn)
        source_id = conn.execute('SELECT source_id FROM jobwatch.collection_runs WHERE id=%s',
                                 (run_id,)).fetchone()['source_id']
        src = conn.execute('SELECT * FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE',
                           (source_id,)).fetchone()
        if entered:
            entered.set()
        if release:
            assert release.wait(5), 'test release timed out'
        run = conn.execute('SELECT * FROM jobwatch.collection_runs WHERE id=%s FOR UPDATE',
                           (run_id,)).fetchone()
        if run['status'] != 'running':
            return run['status']
        last = conn.execute('SELECT run_order FROM jobwatch.collection_runs WHERE id=%s',
                            (src['last_published_run_id'],)).fetchone()
        status = ('skipped' if src['status'] != 'monitored' else
                  'superseded' if (last and last['run_order'] >= run['run_order']) or
                  src['processing_revision'] != run['processing_revision'] else None)
        if status:
            conn.execute('UPDATE jobwatch.collection_runs SET status=%s,finished_at=clock_timestamp() WHERE id=%s',
                         (status,run_id))
            return status
        conn.execute('UPDATE jobwatch.collection_runs SET fetched_at=%s WHERE id=%s',(fetched,run_id))
        seen = []
        for raw in rows:
            p,n,sc = normalize(raw)
            assert p.board == src['board'] and p.provider == src['provider']
            assert p.key not in seen, 'duplicate identity fails entire feed'
            seen.append(p.key)
            old = conn.execute('SELECT p.*,v.evidence FROM jobwatch.postings p JOIN jobwatch.posting_versions v '
                               'ON v.id=p.current_version_id WHERE p.posting_key=%s', (p.key,)).fetchone()
            if not old:
                # Deferred FK lets us insert the posting before its actual version UUID is known.
                conn.execute("INSERT INTO jobwatch.postings(posting_key,source_id,provider,board,native_id,status,"
                    "first_seen_at,first_seen_basis,last_seen_at,current_version_id,verdict,family,codes,rules_version,"
                    "screening_input_sha256,screening_basis,screened_at) "
                    "VALUES (%s,%s,%s,%s,%s,'listed',%s,'observed',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (p.key,source_id,p.provider,p.board,p.external_id,fetched,fetched,uuid4(),sc['verdict'],sc['family'],
                     sc['codes'],sc['rules_version'],sc['input_sha256'],sc['basis'],fetched))
            vid,n,sc = version(conn,p.key,source_id,raw,run_id,fetched)
            conn.execute("UPDATE jobwatch.postings SET status='listed',missing_since=NULL,last_seen_at=%s,"
                "current_version_id=%s,verdict=%s,family=%s,codes=%s,rules_version=%s,screening_input_sha256=%s,"
                "screening_basis=%s,screened_at=%s WHERE posting_key=%s",
                (fetched,vid,sc['verdict'],sc['family'],sc['codes'],sc['rules_version'],sc['input_sha256'],
                 sc['basis'],fetched,p.key))
            if not old:
                event(conn,p.key,source_id,run_id,'observation','first_seen',None,vid)
            else:
                changed,baseline = compare(p.provider,json.loads(bytes(old['evidence'])),raw)
                if changed or baseline:
                    event(conn,p.key,source_id,run_id,'provider','changed' if changed else 'baseline',
                          old['current_version_id'],vid,{'changed':changed,'baseline':baseline})
                if old['status']=='not_seen':
                    event(conn,p.key,source_id,run_id,'observation','reappeared',old['current_version_id'],vid)
                if old['verdict']!=sc['verdict'] or old['codes']!=sc['codes'] or old['family']!=sc['family']:
                    event(conn,p.key,source_id,run_id,'screening','rescreened',old['current_version_id'],vid,
                          {'before':old['verdict'],'after':sc['verdict']})
        if absence_safe:
            gone = conn.execute("UPDATE jobwatch.postings SET status='not_seen',missing_since=%s "
                "WHERE source_id=%s AND status='listed' AND NOT(posting_key=ANY(%s::text[])) RETURNING *",
                (fetched,source_id,seen)).fetchall()
            for row in gone:
                event(conn,row['posting_key'],source_id,run_id,'observation','not_seen',
                      row['current_version_id'],row['current_version_id'])
            assert conn.execute("SELECT count(*) AS n FROM jobwatch.postings WHERE source_id=%s AND status='listed'",
                                (source_id,)).fetchone()['n']==len(rows)
        if fault:
            conn.execute('SELECT 1/0')
        conn.execute("UPDATE jobwatch.collection_runs SET status='published',fetched_at=%s,finished_at=clock_timestamp(),"
            "source_records=%s,vacancies=%s,skipped_non_vacancies=0,absence_safe=%s WHERE id=%s",
            (fetched,len(rows),len(rows),absence_safe,run_id))
        conn.execute('UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s', (run_id,source_id))
        return 'published'
