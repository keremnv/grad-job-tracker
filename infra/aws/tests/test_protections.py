"""Real Terraform destroy PLANS on fake local state; no apply or AWS endpoints."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE/'scripts'))
from review_plan import prepare


class DestructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='jobwatch-destroy-refusal-');cls.addClassCleanup(cls.temp.cleanup)
        cls.output=Path(cls.temp.name)/'review'
        cls.module=prepare(SOURCE,cls.output)
        # Static dummy credentials only in the disposable copy. Any attempted API is loopback.
        (cls.module/'zzz_offline_provider_override.tf').write_text('''provider "aws" {
          profile = null
          access_key = "local-test-only"
          secret_key = "local-test-only"
          allowed_account_ids = null
          skip_credentials_validation = true
          skip_requesting_account_id = true
          skip_region_validation = true
          skip_metadata_api_check = true
          max_retries = 0
          endpoints {
            ec2 = "http://127.0.0.1:9"
            s3 = "http://127.0.0.1:9"
            sts = "http://127.0.0.1:9"
            iam = "http://127.0.0.1:9"
          }
        }
        ''')
        cls.env={k:v for k,v in os.environ.items() if not k.startswith(('AWS_','TF_CLI_ARGS','TF_LOG'))}
        cls.env.update(TF_DATA_DIR=str(cls.output/'cache'),AWS_EC2_METADATA_DISABLED='true')
        cls.run_tf('init','-input=false','-no-color','-lockfile=readonly',
                   '-plugin-dir='+str(SOURCE/'.terraform/providers'),check=True)
        schema=json.loads(cls.run_tf('providers','schema','-json',check=True).stdout)
        cls.schemas=schema['provider_schemas']['registry.terraform.io/hashicorp/aws']['resource_schemas']

    @classmethod
    def run_tf(cls,*args,check=False):
        result=subprocess.run(['terraform','-chdir='+str(cls.module),*args],env=cls.env,capture_output=True,text=True,timeout=120)
        if check and result.returncode:raise AssertionError(result.stderr)
        return result

    def refused(self,kind,name,attrs):
        state={'version':4,'terraform_version':'1.17.0','serial':1,'lineage':'c8d53c54-2fad-4f5b-88b2-a62810daf907',
               'outputs':{},'resources':[{'mode':'managed','type':kind,'name':name,
               'provider':'provider["registry.terraform.io/hashicorp/aws"]',
               'instances':[{'schema_version':self.schemas[kind]['version'],'attributes':attrs,'sensitive_attributes':[]}]}]}
        (self.output/'REVIEW_ONLY.tfstate').write_text(json.dumps(state))
        result=self.run_tf('plan','-destroy','-refresh=false','-input=false','-no-color',
                           '-var=expected_project_id=000000000000','-var=ami_id=ami-00000000000000000')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('prevent_destroy',result.stderr)
        self.assertIn(kind+'.'+name,result.stderr)
        self.assertNotIn('connection refused',result.stderr.lower())

    def test_data_volume_destroy_refused(self):
        self.refused('aws_ebs_volume','database',{'id':'vol-00000000000000000','availability_zone':'eu-north-1a',
            'size':16,'type':'gp3','encrypted':True,'final_snapshot':True})

    def test_backup_bucket_destroy_refused(self):
        self.refused('aws_s3_bucket','backups',{'id':'local-safety-fixture','bucket':'local-safety-fixture',
            'arn':'arn:aws:s3:::local-safety-fixture','force_destroy':False})


if __name__=='__main__':unittest.main()
