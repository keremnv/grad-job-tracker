#!/usr/bin/env python3
"""Non-writing checks for the calendar selection rollout. Output is counts, hashes and fixed codes.

artifacts runs on the operator machine; snapshot, compare and probe run as root on the host.
Nothing here changes a unit, a permission, configuration or a database row.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

TABLES = ('employers','sources','collection_runs','postings','posting_versions','posting_events',
          'leads','verifications','triage','applications','owner_audit')
UNITS = ('jobwatch-postgresql.service','jobwatch-agent-read.service','jobwatch-agent-owner.service',
         'jobwatch-tunnel@read.service','jobwatch-tunnel@owner.service','jobwatch-collect.timer',
         'jobwatch-backup.timer','jobwatch-backup-health.timer','jobwatch-calendar.timer','jobwatch-calendar.path')
SELECTION_COLUMNS = ('posting_key','lead_key','evidence_key','mode','approved_deadline','decided_at','recorded_by')
EXPECTED_GRANTS = sorted({(r,c,'SELECT') for r in ('jobwatch_agent_owner','jobwatch_agent_readonly','jobwatch_calendar_reader') for c in SELECTION_COLUMNS}
    |{('jobwatch_agent_owner',c,'INSERT') for c in ('posting_key','lead_key','mode','approved_deadline','recorded_by')}
    |{('jobwatch_agent_owner',c,'UPDATE') for c in ('mode','approved_deadline','decided_at','recorded_by')})
MISSING = 'vacancy:__calendar_rollout_missing__'


def query(sql):
    out = subprocess.check_output(['runuser','-u','jobwatch-migrator','--','psql','-X','-h','/run/jobwatch-postgresql',
        '-U','jobwatch_migrator','-d','jobwatch','-qAt','-v','ON_ERROR_STOP=1','-c','BEGIN READ ONLY; '+sql+'; COMMIT'],text=True)
    return json.loads(out)


def table_hash(table):
    return query("SELECT json_build_object('count',count(*),'sha256',encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex')) "
                 "FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM jobwatch."+table+" t) q")


def snapshot():
    state = {'release': {p: str(Path(p).resolve()) for p in ('/opt/jobwatch/current','/opt/jobwatch-agent-runtime','/opt/jobwatch-tunnel')}}
    state['units'] = {}
    for unit in UNITS:
        out = subprocess.check_output(['systemctl','show',unit,'--property=ActiveState,UnitFileState,MainPID'],text=True)
        state['units'][unit] = dict(x.split('=',1) for x in out.splitlines() if '=' in x)
    state['tables'] = {t: table_hash(t) for t in TABLES}
    state['migrations'] = query('SELECT json_agg(json_build_object(\'version\',version,\'sha256\',sha256) ORDER BY version) FROM jobwatch.schema_migrations')
    present = query("SELECT to_json(to_regclass('jobwatch.calendar_selections') IS NOT NULL)")
    state['selection_rows'] = table_hash('calendar_selections')['count'] if present else None
    state['selection_grants'] = query("""SELECT coalesce(json_agg(json_build_array(r.role,c.column_name,p.p) ORDER BY r.role,c.column_name,p.p),'[]')
        FROM (VALUES ('jobwatch_agent_owner'),('jobwatch_agent_readonly'),('jobwatch_calendar_reader'),('jobwatch_collector'),('jobwatch_chat_readonly')) r(role)
        JOIN pg_roles a ON a.rolname=r.role
        CROSS JOIN information_schema.columns c CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE')) p(p)
        WHERE c.table_schema='jobwatch' AND c.table_name='calendar_selections'
        AND has_column_privilege(r.role,'jobwatch.calendar_selections',c.column_name,p.p)""") if present else []
    state['private_files'] = {}
    for directory in (Path('/etc/jobwatch'),Path('/etc/jobwatch-tunnel'),*Path('/etc/systemd/system').glob('jobwatch*.d')):
        for p in directory.rglob('*'):
            if p.is_file() and not p.is_symlink():
                state['private_files'][str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    root = Path(json.loads(Path('/etc/jobwatch/calendar.json').read_text())['output'])
    manifest = json.loads((root/'current/manifest.json').read_text())
    uploaded = json.loads((root/'uploaded.json').read_text())
    state['calendar'] = {'uids': {u: {k: e[k] for k in ('active','sequence','created_at','fingerprint')} for u,e in manifest['events'].items()},
        'files': {k: {'events': v['events'],'sha256': v['sha256']} for k,v in manifest['files'].items()},
        'warnings': len(manifest['warnings']),'generated_at': manifest['generated_at'],
        'uploaded_matches': uploaded['generated_at']==manifest['generated_at']
            and all(uploaded['objects'][k]['sha256']==v['sha256'] for k,v in manifest['files'].items())}
    return state


def differences(before, after):
    """Fixed codes for anything the rollout must not change. Empty means preserved."""
    found = []
    for table,row in before['tables'].items():
        if after['tables'].get(table)!=row:
            found.append('table_changed:'+table)
    if after['migrations'][:len(before['migrations'])]!=before['migrations']:
        found.append('migration_ledger_changed')
    if [m['version'] for m in after['migrations']]!=[1,2,3,4,5,6,7]:
        found.append('schema_not_at_0007')
    if after['selection_rows']!=0:
        found.append('selection_rows_present')
    if sorted(tuple(g) for g in after['selection_grants'])!=[tuple(g) for g in EXPECTED_GRANTS]:
        found.append('selection_grants_differ')
    if after['private_files']!=before['private_files']:
        found.append('private_files_changed')
    for unit,row in before['units'].items():
        now = after['units'].get(unit,{})
        if any(now.get(k)!=row[k] for k in ('ActiveState','UnitFileState')):
            found.append('unit_state_changed:'+unit)
    if after['units']['jobwatch-postgresql.service']['MainPID']!=before['units']['jobwatch-postgresql.service']['MainPID']:
        found.append('postgresql_restarted')
    for uid,row in before['calendar']['uids'].items():
        if after['calendar']['uids'].get(uid)!=row:
            found.append('calendar_uid_changed:'+uid[:12])
    if set(after['calendar']['uids'])-set(before['calendar']['uids']):
        found.append('calendar_uid_added')
    if after['calendar']['files']!=before['calendar']['files']:
        found.append('calendar_files_changed')
    if after['calendar']['warnings'] or not after['calendar']['uploaded_matches']:
        found.append('calendar_publication_unhealthy')
    return found


async def check(session, kind):
    """The non-writing probe for one MCP connection, as the running tunnel would call it."""
    tools = {t.name: t for t in (await session.list_tools()).tools}
    if kind=='read':
        if sorted(tools)!=['inspect_evidence','run_report','search','sql_query','sql_schema']:raise SystemExit('unexpected_read_tools')
        if 'selection' not in tools['run_report'].inputSchema['properties']:raise SystemExit('selection_not_advertised')
        data = (await session.call_tool('run_report',{'kind':'calendar_selection','selection':[{'key':MISSING}]})).structuredContent
        if not data['ok'] or data['data']['items'][0]['resolution']!='not_found':raise SystemExit('calendar_report_failed')
        return {'connection':'read','calendar_report':True,'publisher':data['data']['publisher']['state'],
                'projected_deadline_events':data['data']['projected_deadline_events'],'recorded_approvals':data['data']['recorded_approvals']}
    if sorted(tools)!=['commit_owner_change','prepare_owner_change']:raise SystemExit('unexpected_owner_tools')
    if 'select_calendar_deadlines' not in tools['prepare_owner_change'].description:raise SystemExit('recipe_not_advertised')
    data = (await session.call_tool('prepare_owner_change',{'recipe':'select_calendar_deadlines',
        'values':{'items':[{'key':MISSING,'action':'include'}]}})).structuredContent
    if not data['ok'] or data['data']['change_id'] is not None or data['data']['review']['blocked'][0]['reasons']!=['not_found']:
        raise SystemExit('calendar_prepare_failed')
    return {'connection':'owner','recipe_advertised':True,'missing_item_blocked':True,
            'deadline_events':data['data']['review']['feed_effect']['deadline_events_after'],'commit_called':False}


def probe_child(kind):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run():
        path = '/run/jobwatch-agent-'+kind+'/mcp.sock'
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=path),timeout=60) as http:
            async with streamable_http_client('http://127.0.0.1:'+('8765' if kind=='read' else '8766')+'/mcp',http_client=http) as (reader,writer,_):
                async with ClientSession(reader,writer) as session:
                    await session.initialize()
                    print(json.dumps(await check(session,kind)))
    asyncio.run(run())


def probe():
    # Exactly the running tunnel's identity. runuser omits systemd's supplementary groups.
    for kind in ('read','owner'):
        pid = int(subprocess.check_output(['systemctl','show','jobwatch-tunnel@'+kind+'.service','--property=MainPID','--value'],text=True))
        if pid<=0:raise SystemExit('tunnel_not_running:'+kind)
        fields = {l.split(':',1)[0]: l.split(':',1)[1].split() for l in Path('/proc/'+str(pid)+'/status').read_text().splitlines() if ':' in l}
        subprocess.run(['setpriv','--reuid='+fields['Uid'][1],'--regid='+fields['Gid'][1],'--groups='+','.join(fields['Groups']),'--no-new-privs',
            '/opt/jobwatch-agent-runtime/bin/python',str(Path(__file__).resolve()),'probe-child',kind],check=True,cwd='/',
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})


def artifacts(application, agent, app_sha, agent_sha, commit):
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from deploy_release import verify
    verify(application,app_sha,commit);verify(agent,agent_sha,commit)
    with tarfile.open(application) as archive:
        names = set(archive.getnames())
        for required in ('src/jobwatch/postgres/migrations/0007_calendar_selections.sql','sql/postgresql/calendar_selection_access.sql',
                         'infra/aws/scripts/calendar_selection_check.py'):
            if required not in names:raise SystemExit('application_missing:'+required)
    with tarfile.open(agent) as archive:
        read_unit = archive.extractfile('systemd/jobwatch-agent-read.service').read().decode()
        owner_unit = archive.extractfile('systemd/jobwatch-agent-owner.service').read().decode()
        publisher = archive.extractfile('systemd/jobwatch-calendar.service').read().decode()
        wheel = next(m for m in archive.getmembers() if m.name.startswith('wheels/') and 'job' in m.name.split('/')[1].lower())
        with zipfile.ZipFile(archive.extractfile(wheel)) as z:
            members = z.namelist()
            cookbook = next(n for n in members if n.endswith('owner_workflows.sql'))
            if 'jobwatch/postgres/calendar_selection.py' not in members or '-- BEGIN set_calendar_selection' not in z.read(cookbook).decode():
                raise SystemExit('agent_wheel_incomplete')
    if ('--calendar-status' not in read_unit or '--calendar-policy' not in owner_unit
            or 'RuntimeDirectory=jobwatch-calendar-status' not in publisher):
        raise SystemExit('agent_units_incomplete')
    print(json.dumps({'commit':commit,'application':True,'agent':True}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    a = sub.add_parser('artifacts');a.add_argument('application',type=Path);a.add_argument('agent',type=Path)
    a.add_argument('--application-sha256',required=True);a.add_argument('--agent-sha256',required=True);a.add_argument('--commit',required=True)
    s = sub.add_parser('snapshot');s.add_argument('output',type=Path)
    c = sub.add_parser('compare');c.add_argument('before',type=Path);c.add_argument('after',type=Path)
    sub.add_parser('probe')
    q = sub.add_parser('probe-child');q.add_argument('kind',choices=('read','owner'))
    args = p.parse_args()
    if args.command=='artifacts':
        artifacts(args.application,args.agent,args.application_sha256,args.agent_sha256,args.commit)
    elif args.command=='snapshot':
        fd = os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        state = snapshot()
        with os.fdopen(fd,'w') as stream:json.dump(state,stream)
        print(json.dumps({'snapshot':str(args.output),'migrations':[m['version'] for m in state['migrations']],
            'rows':{k:v['count'] for k,v in state['tables'].items()},'selection_rows':state['selection_rows'],
            'calendar_uids':len(state['calendar']['uids']),'calendar_files':{k:v['events'] for k,v in state['calendar']['files'].items()},
            'uploaded_matches':state['calendar']['uploaded_matches']}))
    elif args.command=='compare':
        found = differences(json.loads(args.before.read_text()),json.loads(args.after.read_text()))
        print(json.dumps({'preserved':not found,'differences':found}))
        raise SystemExit(1 if found else 0)
    elif args.command=='probe':
        probe()
    else:
        probe_child(args.kind)


if __name__=='__main__':
    main()
