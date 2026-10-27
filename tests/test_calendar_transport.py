"""Synthetic capability routing/publication; no AWS credentials or network calls."""
from datetime import datetime,timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from jobwatch.postgres import calendar_feed
from test_calendar_feed import data
ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'infra/aws/scripts'

def load(name):
    import sys
    sys.path.insert(0,str(SCRIPTS))
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class CalendarTransportTests(unittest.TestCase):
    def setUp(self):
        self.publisher=load('calendar_publish');self.caps=load('calendar_capabilities')

    def test_upload_whitelist_checksum_unchanged_and_failure_preservation(self):
        with TemporaryDirectory() as tmp:
            os.chmod(tmp,0o700);root=Path(tmp)/'snapshots';now=datetime.now(timezone.utc).isoformat()
            calendar_feed.publish(data(generated_at=now),root,initialize=True)
            calls=[]
            def aws(args,**kwargs):
                key=args[args.index('--key')+1];calls.append(key)
                raw=Path(args[args.index('--body')+1]).read_bytes()
                self.assertNotIn(b'PRIVATE_NOTE',raw);self.assertNotIn(b'PRIVATE_CV',raw)
                return SimpleNamespace(returncode=0,stdout=json.dumps({'VersionId':'synthetic-v1','ServerSideEncryption':'AES256','ChecksumSHA256':args[args.index('--checksum-sha256')+1]}))
            config={'bucket':'synthetic-calendar-only','region':'eu-north-1'}
            self.publisher.publish_snapshot(root,config,run_command=aws)
            self.assertEqual(calls,['deadlines.ics','actions.ics','combined.ics','_private/snapshot.json'])
            prior=(root/'uploaded.json').read_bytes();calls.clear()
            self.publisher.publish_snapshot(root,config,run_command=aws)
            self.assertEqual(calls,['_private/snapshot.json'])
            def fail(*a,**kw):return SimpleNamespace(returncode=1,stdout='')
            with self.assertRaises(calendar_feed.FeedError):self.publisher.publish_snapshot(root,config,run_command=fail)
            self.assertEqual((root/'uploaded.json').read_bytes(),prior)

    def test_capability_custody_and_actual_edge_code_revocation(self):
        with TemporaryDirectory() as tmp:
            os.chmod(tmp,0o700);p=Path(tmp)/'urls.json'
            hashes=self.caps.create(p,['deadlines','actions'],'synthetic.cloudfront.net')
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):self.caps.create(p,['actions'])
            values=json.loads(p.read_text());token=values['tokens']['actions'];old=token
            self.caps.bind_domain(p,'second.cloudfront.net')
            bound=json.loads(p.read_text())
            self.assertEqual(bound['tokens'],values['tokens'])
            self.assertEqual(bound['hashes'],hashes)
            self.assertEqual(bound['urls']['actions'],'https://second.cloudfront.net/'+token+'/actions.ics')
            with self.assertRaises(ValueError):self.caps.bind_domain(p,'untrusted.example')
            code=(ROOT/'infra/aws/integrations/calendar-capability.js.tftpl').read_text().replace('${jsonencode(capability_hashes)}',json.dumps(hashes))
            requests=[{'method':'GET','uri':'/'+token+'/actions.ics','querystring':{}},
                {'method':'GET','uri':'/'+token+'/../_private/snapshot.json','querystring':{}},
                {'method':'POST','uri':'/'+token+'/actions.ics','querystring':{}},
                {'method':'GET','uri':'/'+token+'/actions.ics','querystring':{'versionId':{'value':'old'}}},
                {'method':'GET','uri':'/'+values['tokens']['deadlines']+'/actions.ics','querystring':{}}]
            js=code+'\nconsole.log(JSON.stringify('+json.dumps(requests)+'.map(request=>handler({request}))));'
            results=json.loads(subprocess.check_output(['node','-e',js],text=True))
            self.assertEqual(results[0]['uri'],'/actions.ics')
            self.assertTrue(all(x['statusCode']==404 for x in results[1:]))
            revoked=code.replace(hashes['actions'],sha256(b'new-synthetic-token').hexdigest())
            js=revoked+'\nconsole.log(JSON.stringify(handler({request:'+json.dumps(requests[0])+'})));'
            self.assertEqual(json.loads(subprocess.check_output(['node','-e',js],text=True))['statusCode'],404)

    def test_state_protection_is_additive_and_native_lock_is_not_deleted(self):
        helper=load('render_integration_state_policy')
        base=json.loads((ROOT/'infra/aws/state/bucket-policy.json.template').read_text().replace('BUCKET_NAME','grad-jobwatch-state-000000000000-eu-north-1-an').replace('PROJECT_ID','000000000000'))
        after=helper.extend(base,'grad-jobwatch-state-000000000000-eu-north-1-an')
        self.assertEqual(after['Statement'][:-1],base['Statement'])
        self.assertEqual(len(after['Statement']),len(base['Statement'])+1)
        self.assertTrue(after['Statement'][-1]['Resource'].endswith('/integrations/terraform.tfstate'))
        self.assertNotIn('tflock',after['Statement'][-1]['Resource'])
        with self.assertRaises(ValueError):helper.extend(after,'grad-jobwatch-state-000000000000-eu-north-1-an')

    def test_private_transport_completion_restores_uid_and_rejects_corruption(self):
        with TemporaryDirectory() as tmp:
            os.chmod(tmp,0o700);parent=Path(tmp);root=parent/'current'
            calendar_feed.publish(data(generated_at=datetime.now(timezone.utc).isoformat()),root,initialize=True)
            uploaded={}
            def aws(args,**kw):
                name=args[args.index('--key')+1];uploaded[name]=Path(args[args.index('--body')+1]).read_bytes()
                return SimpleNamespace(returncode=0,stdout=json.dumps({'ServerSideEncryption':'AES256','VersionId':'synthetic-v1','ChecksumSHA256':args[args.index('--checksum-sha256')+1]}))
            self.publisher.publish_snapshot(root,{'bucket':'synthetic-calendar-only','region':'eu-north-1'},run_command=aws)
            completion=json.loads(uploaded['_private/snapshot.json']);bodies={k:uploaded[k] for k in calendar_feed.FILES}
            target=parent/'recovery';self.publisher.restore_transport_state(target,completion,bodies)
            _,original=calendar_feed.current_snapshot(root);_,restored=calendar_feed.current_snapshot(target)
            self.assertEqual(original,restored)
            with self.assertRaises(calendar_feed.FeedError):self.publisher.restore_transport_state(target,completion,bodies)
            with self.assertRaises(calendar_feed.FeedError):self.publisher.restore_transport_state(parent/'corrupt',completion,{**bodies,'actions.ics':b'corrupt'})

    def test_unix_listener_permissions_stale_socket_and_unsafe_occupants(self):
        from jobwatch.postgres.unix_mcp import listener
        import socket
        with TemporaryDirectory() as tmp:
            os.chmod(tmp,0o700);path=Path(tmp)/'mcp.sock'
            first=listener(path)
            self.assertEqual(path.stat().st_mode&0o777,0o660)
            with self.assertRaises(ValueError):listener(path)
            first.close()
            second=listener(path);second.close();path.unlink()
            private=Path(tmp)/'private';private.write_text('synthetic')
            path.symlink_to(private)
            with self.assertRaises(ValueError):listener(path)
            self.assertEqual(private.read_text(),'synthetic')
            path.unlink();path.write_text('unexpected')
            with self.assertRaises(ValueError):listener(path)
