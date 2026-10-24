#!/usr/bin/env python3
"""Prepare a protected state bucket; creation is an explicit separately approved operation.

render is local-only. bootstrap is NEVER invoked by Terraform or deployment.
Only named S3 configuration APIs are used; no state objects or private data uploaded.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

STATE_KEY = 'tracker/terraform.tfstate'
REGION = 'eu-north-1'


class StateError(RuntimeError):
    pass


def configuration(path):
    data = json.loads(path.read_text())
    if (set(data) != {'expected_project_id','profile','region'}
            or not re.fullmatch('[0-9]{12}', data['expected_project_id'])
            or data['profile'] != 'grad-jobwatch' or data['region'] != REGION):
        raise StateError('Use the confirmed project, profile and Stockholm Region')
    return data


def bucket_name(config):
    return f"grad-jobwatch-state-{config['expected_project_id']}-{REGION}-an"


def policy(config, name):
    path = Path(__file__).resolve().parents[1]/'state'/(name+'-policy.json.template')
    return json.loads(path.read_text().replace('BUCKET_NAME', bucket_name(config))
                      .replace('PROJECT_ID', config['expected_project_id']))


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(data)


def render(config, output):
    repo = Path(__file__).resolve().parents[3]
    if output.resolve().is_relative_to(repo) and not output.resolve().is_relative_to(repo/'.local-reports'):
        raise StateError('Keep generated policies/configuration outside tracked source files')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ('access','bootstrap','bucket'):
        write_private(output/(name+'-policy.json'), json.dumps(policy(config,name),indent=2)+'\n')
    backend = (f'bucket = "{bucket_name(config)}"\n'
               f'allowed_account_ids = ["{config["expected_project_id"]}"]\n')
    write_private(output/'production.s3.tfbackend', backend)


def aws(config, service, operation, *, allow_missing=False, **parameters):
    args = ['aws','--profile',config['profile'],'--region',REGION,service,operation]
    for name, value in parameters.items():
        args += ['--'+name.replace('_','-'), json.dumps(value) if isinstance(value,(dict,list)) else str(value)]
    result = subprocess.run(args+['--output','json'], capture_output=True, text=True, timeout=90)
    if result.returncode:
        # Missing is accepted ONLY for documented bucket/policy absence, never AccessDenied.
        match = re.search(r'An error occurred \((\w+)\)', result.stderr)
        code = match[1] if match else 'Unavailable'
        if allow_missing and code in ('NoSuchBucket','NoSuchBucketPolicy','NoSuchLifecycleConfiguration'):
            return None
        raise StateError(f'AWS {operation} failed ({code}); stop and inspect privately')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def verify_bucket(config, call=aws):
    bucket = bucket_name(config)
    args = dict(bucket=bucket, expected_bucket_owner=config['expected_project_id'])
    if call(config,'s3api','get-bucket-location',**args).get('LocationConstraint') != REGION:
        raise StateError('State bucket Region differs')
    version = call(config,'s3api','get-bucket-versioning',**args)
    encryption = call(config,'s3api','get-bucket-encryption',**args)['ServerSideEncryptionConfiguration']['Rules']
    block = call(config,'s3api','get-public-access-block',**args)['PublicAccessBlockConfiguration']
    ownership = call(config,'s3api','get-bucket-ownership-controls',**args)['OwnershipControls']['Rules']
    actual = json.loads(call(config,'s3api','get-bucket-policy',**args)['Policy'])
    tags = call(config,'s3api','get-bucket-tagging',**args)['TagSet']
    lifecycle = call(config,'s3api','get-bucket-lifecycle-configuration',**args,allow_missing=True)
    if (lifecycle is not None or version.get('Status') != 'Enabled' or len(encryption) != 1
            or encryption[0]['ApplyServerSideEncryptionByDefault']['SSEAlgorithm'] != 'AES256'
            or encryption[0].get('BlockedEncryptionTypes',{}).get('EncryptionType') != ['SSE-C']
            or not all(block.get(k) is True for k in ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets'))
            or ownership != [{'ObjectOwnership':'BucketOwnerEnforced'}]
            or actual != policy(config,'bucket')
            or {'Key':'Project','Value':'graduate-job-watch'} not in tags):
        raise StateError('State bucket safeguards differ; do not initialize the backend')
    return {'versioning':True,'sse_s3':True,'sse_c_blocked':True,'public_access_blocked':True,
            'acl_disabled':True,'no_lifecycle_expiry':True,'tls_required':True,'state_deletion_denied':True,'bucket_deletion_denied':True}


def bootstrap(config, confirmation, *, call=aws):
    bucket = bucket_name(config)
    if confirmation != bucket:
        raise StateError('Separate owner approval and exact bucket-name confirmation required')
    if call(config,'sts','get-caller-identity')['Account'] != config['expected_project_id']:
        raise StateError('AWS project identity differs')
    if call(config,'s3api','get-bucket-location',bucket=bucket,allow_missing=True) is not None:
        raise StateError('Bucket already exists; use verify, never silently modify it')
    call(config,'s3api','create-bucket',bucket=bucket,bucket_namespace='account-regional',
         create_bucket_configuration={'LocationConstraint':REGION})
    args = dict(bucket=bucket, expected_bucket_owner=config['expected_project_id'])
    call(config,'s3api','put-bucket-versioning',**args,versioning_configuration={'Status':'Enabled'})
    call(config,'s3api','put-bucket-encryption',**args,server_side_encryption_configuration={'Rules':[{
        'ApplyServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'},
        'BlockedEncryptionTypes':{'EncryptionType':['SSE-C']}}]})
    call(config,'s3api','put-bucket-tagging',**args,tagging={'TagSet':[
        {'Key':'Project','Value':'graduate-job-watch'}, {'Key':'ManagedBy','Value':'reviewed-bootstrap'},
        {'Key':'Purpose','Value':'terraform-state'}]})
    # Never overwrite an existing policy, even if another operator wrote it during bootstrap.
    if call(config,'s3api','get-bucket-policy',**args,allow_missing=True) is not None:
        raise StateError('A bucket policy appeared; preserve it and review before any replacement')
    call(config,'s3api','put-bucket-policy',**args,policy=policy(config,'bucket'))
    return verify_bucket(config,call)


def check_backend(cache, config):
    backend = json.loads(cache.read_text()).get('backend',{})
    data = backend.get('config',{})
    if (backend.get('type') != 's3' or data.get('bucket') != bucket_name(config)
            or data.get('key') != STATE_KEY or data.get('region') != REGION
            or data.get('encrypt') is not True or data.get('use_lockfile') is not True
            or data.get('profile') != config['profile']
            or data.get('allowed_account_ids') != [config['expected_project_id']]
            or data.get('workspace_key_prefix') != 'no-workspaces'
            or any(data.get(k) for k in ('access_key','secret_key','token','insecure','skip_credentials_validation','skip_requesting_account_id','skip_region_validation','dynamodb_table'))
            or any((data.get('endpoints') or {}).values())):
        raise StateError('Backend must use the approved S3 bucket, identity, encryption and native locking')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('render','bootstrap','verify','check-backend'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--confirm-create-bucket')
    parser.add_argument('--backend-cache',type=Path)
    args = parser.parse_args()
    try:
        config = configuration(args.config)
        if args.command == 'render':
            if not args.output:raise StateError('New private output directory required')
            render(config,args.output)
        elif args.command == 'check-backend':
            if not args.backend_cache:raise StateError('Explicit backend cache path required')
            check_backend(args.backend_cache,config)
        else:
            result = (bootstrap(config,args.confirm_create_bucket) if args.command == 'bootstrap'
                      else verify_bucket(config))
            if args.output:
                write_private(args.output,json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),
                    'safeguards':result},indent=2)+'\n')
        print('State preparation/check completed. No tracker deployment is authorized.')
        return 0
    except StateError as exc:
        print('ERROR: '+str(exc),file=__import__('sys').stderr)
        return 2
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        print('State operation refused or incomplete; inspect configuration privately. Never rerun bootstrap over an existing bucket.',file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
