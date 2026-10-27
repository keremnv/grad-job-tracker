#!/usr/bin/env python3
"""Private SQL -> existing atomic ICS -> four allowlisted S3 objects. No URL tokens.

Only generated ICS is remotely readable through CloudFront. The hashed revision
manifest is private and retained solely to recover stable UID/sequence transport
metadata, not as an application ledger. Never upload reports, backups or notes.
"""
import argparse
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from jobwatch.postgres import reports,calendar_feed
from jobwatch.postgres.connection import connect
from run_operation import publish as operational_log


def put(path,key,config,*,run_command=subprocess.run):
    checksum=base64.b64encode(sha256(path.read_bytes()).digest()).decode()
    command=['aws','s3api','put-object','--region','eu-north-1','--bucket',config['bucket'],
        '--key',key,'--body',str(path),'--server-side-encryption','AES256',
        '--checksum-algorithm','SHA256','--checksum-sha256',checksum,
        '--content-type','text/calendar; charset=utf-8' if key.endswith('.ics') else 'application/json',
        '--cache-control','max-age=60,must-revalidate' if key.endswith('.ics') else 'no-store',
        '--output','json','--no-cli-pager']
    p=run_command(command,capture_output=True,text=True,timeout=90)
    if p.returncode:raise calendar_feed.FeedError('Calendar upload failed; old completed objects remain')
    result=json.loads(p.stdout)
    if result.get('ChecksumSHA256')!=checksum or not result.get('VersionId') or result.get('ServerSideEncryption')!='AES256':
        raise calendar_feed.FeedError('Calendar upload receipt not confirmed')
    return {'sha256':sha256(path.read_bytes()).hexdigest(),'version_id':result['VersionId']}


def publish_snapshot(root,config,*,run_command=subprocess.run):
    if config.get('region')!='eu-north-1' or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',config.get('bucket','')):
        raise calendar_feed.FeedError('Invalid calendar origin configuration')
    generation,manifest=calendar_feed.current_snapshot(root)
    if not calendar_feed.summary(manifest,max_age_hours=2)['fresh']:
        raise calendar_feed.FeedError('Refuse publishing stale calendar snapshot')
    receipt_path=root/'uploaded.json'
    previous=json.loads(calendar_feed.read_file(receipt_path,65536)) if receipt_path.exists() else {}
    receipts={}
    # These exact keys match the origin's GetObject policy; no caller-provided key.
    for name in calendar_feed.FILES:
        info=manifest['files'][name]
        if previous.get('bucket')==config['bucket'] and previous.get('objects',{}).get(name,{}).get('sha256')==info['sha256']:
            receipts[name]=previous['objects'][name]
        else:
            receipts[name]=put(generation/name,name,config,run_command=run_command)
    # Completion/recovery state last; never readable by the distribution.
    fd,temporary=tempfile.mkstemp(prefix='.transport-',dir=root)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump({'schema':'jobwatch.calendar-transport/1','snapshot':manifest,'feed_objects':receipts},f,sort_keys=True)
            f.flush();os.fsync(f.fileno())
        receipts['_private/snapshot.json']=put(Path(temporary),'_private/snapshot.json',config,run_command=run_command)
    finally:
        os.unlink(temporary)
    value={'bucket':config['bucket'],'generated_at':manifest['generated_at'],'objects':receipts}
    fd,tmp=tempfile.mkstemp(prefix='.uploaded-',dir=root)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,receipt_path);calendar_feed.sync_directory(root)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return {'operation':'calendar','exit_code':0,'healthy':1,'feeds':len(calendar_feed.FILES),
        'events':sum(manifest['files'][f+'.ics']['events'] for f in calendar_feed.FEEDS),
        'warnings':len(manifest['warnings']),'fresh':True}


def restore_transport_state(root, completion, contents):
    """Restore ONLY private transport cache from exact downloaded S3 versions.

    No database/network calls. Caller supplies the private completion JSON and
    three downloaded bodies; all references/hashes are checked before writing.
    """
    from uuid import uuid4
    if completion.get('schema')!='jobwatch.calendar-transport/1' or set(contents)!=set(calendar_feed.FILES):
        raise calendar_feed.FeedError('Invalid calendar recovery set')
    manifest=calendar_feed.check_manifest(completion['snapshot'])
    for name,body in contents.items():
        expected=manifest['files'][name]
        if (len(body)!=expected['bytes'] or sha256(body).hexdigest()!=expected['sha256']
                or completion['feed_objects'][name]['sha256']!=expected['sha256']
                or not completion['feed_objects'][name].get('version_id')):
            raise calendar_feed.FeedError('Calendar recovery hash/version mismatch')
    if root.exists():raise calendar_feed.FeedError('Calendar recovery requires a new private target')
    for parent in root.parents:
        if parent.is_symlink():raise calendar_feed.FeedError('Unsafe recovery parent')
    root.mkdir(mode=0o700,parents=False)
    generation=root/('snapshot-'+uuid4().hex);generation.mkdir(mode=0o700)
    raw=calendar_feed.encoded(manifest)
    for name,body in {**contents,'manifest.json':raw,'manifest.sha256':sha256(raw).hexdigest().encode()+b'\n'}.items():
        fd=os.open(generation/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
    calendar_feed.read_generation(generation)
    calendar_feed.sync_directory(generation)
    (root/'current').symlink_to(generation.name)
    calendar_feed.sync_directory(root)
    return {'restored':True,'database_writes':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path('/etc/jobwatch/calendar.json'))
    a=p.parse_args();os.umask(0o077)
    status={'operation':'calendar','exit_code':2,'healthy':0}
    try:
        config=json.loads(a.config.read_text());root=Path(config['output'])
        calendar_feed.private_directory(root.parent)
        with calendar_feed.publication_lock(root.parent):
            with connect() as conn:
                r=conn.execute('SELECT current_user AS name').fetchone()
                if r['name']!='jobwatch_calendar_reader':raise ValueError('Wrong calendar read identity')
                data=reports.calendar_data(conn,shared=True,deadline_selection_sql=Path(config['deadline_policy']).read_text())
            calendar_feed.publish(data,root,initialize=not (root/'current').exists())
            status=publish_snapshot(root,config)
    except Exception:
        # No raw SQL, values, object keys, URLs or credential-bearing diagnostics.
        pass
    try:
        log_config=json.loads(Path('/etc/jobwatch/aws.json').read_text())
        operational_log(status,log_config)
    except Exception:print(json.dumps(status))
    return status['exit_code']

if __name__=='__main__':raise SystemExit(main())
