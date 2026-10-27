#!/usr/bin/env python3
"""Local-only additive state deletion protection. Never calls AWS or overwrites input."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re


def extend(existing,bucket):
    if not re.fullmatch(r'grad-jobwatch-state-[0-9]{12}-eu-north-1-an',bucket):raise ValueError('Unexpected state bucket')
    required={'DenyInsecureTransport','DenyStateDeletionIncludingVersions','DenyBucketDeletion','DenyOtherProjects'}
    if not required<={s.get('Sid') for s in existing['Statement']}:raise ValueError('Unexpected original policy; inspect before changing it')
    if any(s.get('Sid')=='DenyIntegrationStateDeletionIncludingVersions' for s in existing['Statement']):
        raise ValueError('Integration protection already exists; inspect rather than adopt')
    proposed=deepcopy(existing)
    proposed['Statement'].append({'Sid':'DenyIntegrationStateDeletionIncludingVersions','Effect':'Deny','Principal':'*',
        'Action':['s3:DeleteObject','s3:DeleteObjectVersion'],'Resource':'arn:aws:s3:::'+bucket+'/integrations/terraform.tfstate'})
    return proposed

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--existing-policy',type=Path,required=True)
    p.add_argument('--bucket',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();os.umask(0o077)
    original=a.existing_policy.read_bytes();value=extend(json.loads(original),a.bucket)
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n')
    print(json.dumps({'original_sha256':sha256(original).hexdigest(),'added_statements':1,'aws_calls':False}))
