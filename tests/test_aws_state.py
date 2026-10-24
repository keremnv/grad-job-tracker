"""State policy/bootstrap contracts. All AWS operations here are mocked."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'infra/aws/scripts'
sys.path.insert(0,str(SCRIPTS))
import state_bucket as state
import review_plan

CONFIG={'expected_project_id':'000000000000','profile':'grad-jobwatch','region':'eu-north-1'}


class StateSafetyTests(unittest.TestCase):
    def test_backend_access_cannot_delete_state_or_control_buckets(self):
        statements=state.policy(CONFIG,'access')['Statement']
        state_arn='arn:aws:s3:::'+state.bucket_name(CONFIG)+'/'+state.STATE_KEY
        self.assertEqual(statements[1]['Resource'],state_arn)
        self.assertEqual(set(statements[1]['Action']),{'s3:GetObject','s3:PutObject'})
        self.assertEqual(statements[2]['Resource'],state_arn+'.tflock')
        self.assertEqual(set(statements[2]['Action']),{'s3:GetObject','s3:PutObject','s3:DeleteObject'})
        self.assertEqual(statements[0]['Resource'],'arn:aws:s3:::'+state.bucket_name(CONFIG))
        self.assertEqual(statements[0]['Action'],'s3:ListBucket')
        self.assertNotIn('Condition',statements[0])
        self.assertNotIn('PutBucket',json.dumps(statements))
        self.assertNotIn('DeleteObjectVersion',json.dumps(statements))

    def test_bucket_deletion_denies_do_not_prevent_unlocking(self):
        statements={s['Sid']:s for s in state.policy(CONFIG,'bucket')['Statement']}
        deleted=statements['DenyStateDeletionIncludingVersions']
        self.assertEqual(deleted['Resource'],'arn:aws:s3:::'+state.bucket_name(CONFIG)+'/'+state.STATE_KEY)
        self.assertNotIn('.tflock',deleted['Resource'])
        self.assertEqual(set(deleted['Action']),{'s3:DeleteObject','s3:DeleteObjectVersion'})
        self.assertEqual(statements['DenyBucketDeletion']['Action'],'s3:DeleteBucket')
        self.assertEqual(statements['DenyInsecureTransport']['Condition'],{'Bool':{'aws:SecureTransport':'false'}})

    def test_local_render_is_private_and_never_uploads_or_overwrites(self):
        with TemporaryDirectory() as d:
            out=Path(d)/'generated';state.render(CONFIG,out)
            self.assertEqual(out.stat().st_mode & 0o777,0o700)
            for f in out.iterdir():self.assertEqual(f.stat().st_mode & 0o777,0o600)
            self.assertIn('allowed_account_ids',(out/'production.s3.tfbackend').read_text())
            self.assertNotIn('secret',(out/'production.s3.tfbackend').read_text())
            with self.assertRaises(FileExistsError):state.render(CONFIG,out)

    def test_bootstrap_refuses_missing_confirmation_wrong_identity_or_existing_bucket(self):
        calls=[]
        def wrong_identity(c,s,o,**kw):calls.append(o);return {'Account':'111111111111'}
        with self.assertRaises(state.StateError):state.bootstrap(CONFIG,None,call=wrong_identity)
        self.assertEqual(calls,[])
        with self.assertRaisesRegex(state.StateError,'identity'):state.bootstrap(CONFIG,state.bucket_name(CONFIG),call=wrong_identity)
        self.assertEqual(calls,['get-caller-identity'])
        calls.clear()
        def existing(c,s,o,**kw):
            calls.append(o);return {'Account':CONFIG['expected_project_id']} if s=='sts' else {'LocationConstraint':'eu-north-1'}
        with self.assertRaisesRegex(state.StateError,'already exists'):state.bootstrap(CONFIG,state.bucket_name(CONFIG),call=existing)
        self.assertEqual(calls,['get-caller-identity','get-bucket-location'])

    def simulator(self, *, existing_policy=False, bad_public=False):
        calls=[];created=False;settings={}
        def invoke(c,s,op,**kw):
            nonlocal created
            calls.append((op,kw))
            if op=='get-caller-identity':return {'Account':c['expected_project_id']}
            if op=='get-bucket-lifecycle-configuration':return None
            if op=='get-bucket-location':return {'LocationConstraint':'eu-north-1'} if created else None
            if op=='create-bucket':created=True;return {}
            if op=='get-bucket-policy':return {'Policy':json.dumps(settings['policy'])} if 'policy' in settings else ({'Policy':'{}'} if existing_policy else None)
            if op=='put-bucket-policy':settings['policy']=kw['policy'];return {}
            if op=='put-bucket-versioning':settings['version']=kw['versioning_configuration'];return {}
            if op=='get-bucket-versioning':return settings['version']
            if op=='put-bucket-encryption':settings['encryption']=kw['server_side_encryption_configuration'];return {}
            if op=='get-bucket-encryption':return {'ServerSideEncryptionConfiguration':settings['encryption']}
            if op=='get-public-access-block':return {'PublicAccessBlockConfiguration':{k:not bad_public for k in ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')}}
            if op=='get-bucket-ownership-controls':return {'OwnershipControls':{'Rules':[{'ObjectOwnership':'BucketOwnerEnforced'}]}}
            if op=='put-bucket-tagging':settings['tags']=kw['tagging'];return {}
            if op=='get-bucket-tagging':return settings['tags']
            self.fail('Unexpected operation '+op)
        return invoke,calls

    def test_approved_bootstrap_uses_exact_namespace_and_validates_all_controls(self):
        invoke,calls=self.simulator()
        checked=state.bootstrap(CONFIG,state.bucket_name(CONFIG),call=invoke)
        self.assertTrue(all(checked.values()))
        create=next(parameters for op,parameters in calls if op=='create-bucket')
        self.assertEqual(create['bucket_namespace'],'account-regional')
        self.assertEqual(create['create_bucket_configuration'],{'LocationConstraint':'eu-north-1'})
        self.assertFalse(any(op.startswith(('delete','put-public-access','put-bucket-ownership')) for op,_ in calls))
        self.assertFalse(any(op in ('put-object','create-access-key') for op,_ in calls))
        self.assertNotIn('PutObject',json.dumps(state.policy(CONFIG,'bootstrap')))

    def test_existing_policy_is_preserved_and_public_controls_fail_closed(self):
        invoke,calls=self.simulator(existing_policy=True)
        with self.assertRaisesRegex(state.StateError,'policy appeared'):state.bootstrap(CONFIG,state.bucket_name(CONFIG),call=invoke)
        self.assertFalse(any(op=='put-bucket-policy' for op,_ in calls))
        invoke,_=self.simulator(bad_public=True)
        with self.assertRaisesRegex(state.StateError,'safeguards'):state.bootstrap(CONFIG,state.bucket_name(CONFIG),call=invoke)
        invoke,_=self.simulator()
        def expiring(c,s,op,**kw):
            if op=='get-bucket-lifecycle-configuration':return {'Rules':[{'Status':'Enabled','NoncurrentVersionExpiration':{'NoncurrentDays':1}}]}
            return invoke(c,s,op,**kw)
        with self.assertRaisesRegex(state.StateError,'safeguards'):state.bootstrap(CONFIG,state.bucket_name(CONFIG),call=expiring)

    def test_backend_verifier_rejects_local_unlocked_insecure_or_wrong_project_cache(self):
        base={'type':'s3','config':{'bucket':state.bucket_name(CONFIG),'key':state.STATE_KEY,
            'region':'eu-north-1','profile':'grad-jobwatch','encrypt':True,'use_lockfile':True,
            'allowed_account_ids':[CONFIG['expected_project_id']],'workspace_key_prefix':'no-workspaces',
            'endpoints':{'s3':None,'sts':None}}}
        with TemporaryDirectory() as d:
            path=Path(d)/'cache.json';path.write_text(json.dumps({'backend':base}));state.check_backend(path,CONFIG)
            for field,value in (('use_lockfile',False),('encrypt',False),('region','us-east-1'),('bucket','other'),('secret_key','not-a-real-secret')):
                changed=json.loads(json.dumps(base));changed['config'][field]=value
                path.write_text(json.dumps({'backend':changed}))
                with self.assertRaises(state.StateError):state.check_backend(path,CONFIG)
            path.write_text(json.dumps({'backend':{'type':'local','config':{}}}))
            with self.assertRaises(state.StateError):state.check_backend(path,CONFIG)

    def test_review_copy_is_isolated_retains_source_s3_backend_and_excludes_local_state(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'repo';source=root/'infra/aws';source.mkdir(parents=True)
            (root/'sql/postgresql').mkdir(parents=True)
            (root/'sql/postgresql/bootstrap.sql').write_text('-- public bootstrap')
            (source/'versions.tf').write_text('terraform { backend "s3" {} }')
            (source/'.terraform.lock.hcl').write_text('# lock')
            (source/'private.tfvars').write_text('private=true')
            (source/'terraform.tfstate').write_text('private state')
            module=review_plan.prepare(source,Path(d)/'private-review')
            self.assertIn('backend "local"',(module/'review_override.tf').read_text())
            self.assertIn('backend "s3"',(source/'versions.tf').read_text())
            self.assertFalse((module/'private.tfvars').exists());self.assertFalse((module/'terraform.tfstate').exists())
            with self.assertRaises(ValueError):review_plan.prepare(source,root/'bad-output')


if __name__=='__main__':unittest.main()
