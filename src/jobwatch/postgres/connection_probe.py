"""Optional MCP client qualification. Read-only calls; receipts omit query results."""
import argparse
import asyncio
import base64
from hashlib import sha256
import json
import os
import sys
import time
from urllib.parse import urlsplit

TOOLS = {'sql_query', 'sql_schema', 'search', 'inspect_evidence', 'run_report'}
ENVIRONMENT = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'PYTHONPATH',
               'JOBWATCH_CHAT_DATABASE_URL', 'PGPASSFILE', 'PGSERVICEFILE',
               'PGSSLROOTCERT', 'SSL_CERT_FILE', 'HF_HOME', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')


class ProbeError(Exception):
    """No server payloads, URLs, credentials or database values in diagnostics."""


def server_environment(environment):
    # This limits inherited credentials, not the filesystem privileges of the OS user.
    return {key: environment[key] for key in ENVIRONMENT if key in environment}


def loopback_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.username or parsed.password or parsed.path != '/mcp'
            or parsed.query or parsed.fragment):
        raise ValueError('Probe HTTP must be an explicit loopback /mcp endpoint')
    return value


async def research_session(session, *, query='wording', query_inference='disabled'):
    """A test itinerary, not a required research policy or additional tool layer."""
    if query_inference not in ('disabled', 'local'):
        raise ProbeError('Invalid inference expectation')
    initialized = await session.initialize()
    listing = await session.list_tools()
    if ({t.name for t in listing.tools} != TOOLS
            or any(not t.annotations or not t.annotations.readOnlyHint or t.annotations.destructiveHint
                   for t in listing.tools)):
        raise ProbeError('Unexpected tool inventory or write annotations')
    checks = []

    async def call(check, name, arguments, *, denied=False):
        started = time.monotonic()
        result = await session.call_tool(name, arguments)
        payload = result.structuredContent
        if (not isinstance(payload, dict) or payload.get('schema') != 'jobwatch.read/1'
                or payload.get('ok') is not (not denied) or bool(result.isError) is not denied):
            raise ProbeError('Unexpected tool result: '+check)
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
        if len(raw) > 131072:
            raise ProbeError('Oversized tool result')
        checks.append({'check': check, 'passed': True, 'milliseconds': round((time.monotonic()-started)*1000),
                       'response_bytes':len(raw), 'response_sha256':sha256(raw).hexdigest()})
        return payload.get('data') if not denied else payload['error']

    async def sql(check, statement, parameters=None, limit=20):
        return await call(check, 'sql_query', {'sql':statement, 'parameters':parameters, 'limit':limit})

    await call('schema', 'sql_schema', {'table':'postings'})
    identity = await sql('read_identity', "SELECT current_user,current_setting('transaction_read_only')", limit=1)
    if identity['rows'] != [['jobwatch_chat_readonly','on']]:
        raise ProbeError('Unexpected database identity or transaction mode')
    corpus = await sql('composed_research', '''WITH corpus AS (
        SELECT p.posting_key,p.verdict,p.status,e.name,v.normalizer_version
        FROM jobwatch.postings p JOIN jobwatch.sources s ON s.id=p.source_id
        LEFT JOIN jobwatch.employers e ON e.id=s.employer_id
        JOIN jobwatch.posting_versions v ON v.id=p.current_version_id)
        SELECT verdict,status,count(*) FROM corpus WHERE %s::boolean
        GROUP BY verdict,status ORDER BY verdict,status''', [True])
    await sql('material_history', 'SELECT category,kind,count(*) FROM jobwatch.posting_events GROUP BY category,kind ORDER BY category,kind')
    for method in ('bm25','fts'):
        await call(method, 'search', {'query':query, 'method':method, 'history':True, 'limit':5})
    native = await sql('retained_native_version', '''SELECT posting_key,id FROM jobwatch.posting_versions
        WHERE evidence IS NOT NULL AND origin IN ('collector','git_history') ORDER BY posting_key,observed_at DESC,id LIMIT 1''', limit=1)
    evidence_verified = False
    if native['rows']:
        key,version = native['rows'][0]
        evidence = await call('native_evidence', 'inspect_evidence',
                              {'posting_key':key,'version_id':version,'events_limit':5})
        encoded = evidence['persisted_evidence_bytes']
        raw = base64.b64decode(encoded['data'], validate=True)
        if len(raw) != encoded['bytes'] or sha256(raw).hexdigest() != encoded['sha256']:
            raise ProbeError('Evidence integrity mismatch')
        evidence_verified = True
    owner = await sql('private_owner_read', '''SELECT count(*),count(notes),count(cv_version)
        FROM jobwatch.applications''', limit=1)
    await sql('audit_read', 'SELECT table_name,op,count(*) FROM jobwatch.owner_audit GROUP BY table_name,op ORDER BY table_name,op')
    for kind in ('weekly','calendar'):
        await call(kind+'_report', 'run_report', {'kind':kind, 'limit':10})
    permissions = await sql('mutation_privileges_absent', '''SELECT
        has_table_privilege(current_user,'jobwatch.applications','UPDATE'),
        has_sequence_privilege(current_user,'jobwatch.owner_audit_id_seq','USAGE'),
        has_function_privilege(current_user,'pg_catalog.pg_read_file(text)','EXECUTE')''', limit=1)
    if permissions['rows'] != [[False,False,False]]:
        raise ProbeError('Unexpected write or file privileges')
    await call('bounded_response', 'sql_query', {'sql':"SELECT repeat('x',200000)",'limit':1}, denied=True)
    methods = ('vector','hybrid') if query_inference == 'local' else ('vector',)
    for method in methods:
        result = await call(method+'_inference', 'search', {'query':query, 'method':method, 'limit':5},
                            denied=query_inference=='disabled')
        if query_inference == 'disabled' and result.get('code') != 'query_inference_disabled':
            raise ProbeError('Inference was not explicitly disabled')
    return {'schema':'jobwatch.connection-qualification/1', 'protocol':initialized.protocolVersion,
            'passed':True, 'tools':sorted(TOOLS), 'query_inference':query_inference,
            'retained_native_evidence_verified':evidence_verified,
            'corpus_groups':len(corpus['rows']), 'owner_record_count':owner['rows'][0][0],
            'checks':checks, 'online_chatgpt_verified':False, 'secure_tunnel_verified':False}


async def run(args):
    from mcp import ClientSession, StdioServerParameters
    if args.http:
        import httpx
        from mcp.client.streamable_http import streamable_http_client
        async def reject_redirect(response):
            # MCP's SDK follows same-origin redirects regardless of the client's
            # follow_redirects setting. Reject at the response hook, before that.
            if response.is_redirect:
                await response.aclose()
                raise ProbeError('Probe refuses HTTP redirects')
        # Loopback must remain loopback even with ambient proxy/redirect settings.
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                      timeout=httpx.Timeout(30,read=120),
                                      event_hooks={'response':[reject_redirect]}) as client:
            async with streamable_http_client(args.http,http_client=client) as (rx,tx,_), ClientSession(rx,tx) as session:
                return await research_session(session, query=args.query, query_inference=args.query_inference)
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable,
        args=['-m','jobwatch.postgres.read_mcp','--query-inference',args.query_inference],
        env=server_environment(os.environ))
    with open(os.devnull,'w') as errors:
        async with stdio_client(params,errlog=errors) as (rx,tx), ClientSession(rx,tx) as session:
            return await research_session(session, query=args.query, query_inference=args.query_inference)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http',help='Existing loopback server; default launches stdio using this Python')
    parser.add_argument('--query',default='wording', help='Research text; not copied into the receipt')
    parser.add_argument('--query-inference',choices=('disabled','local'),default='disabled')
    args=parser.parse_args(argv)
    try:
        if args.http:
            loopback_url(args.http)  # Validate inside sanitized errors; argparse echoes rejected values.
        print(json.dumps(asyncio.run(run(args)),ensure_ascii=True));return 0
    except Exception:
        print(json.dumps({'schema':'jobwatch.connection-qualification/1','passed':False,
                          'error':'Connection qualification failed; inspect private configuration or server availability.'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
