#!/usr/bin/env python3
"""Render one exact calendar-only SNS permission, privately; never calls AWS."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re

SID='AllowReviewedCalendarAlarm'


def extend(existing,topic_arn,project_id):
    if not re.fullmatch(r'[0-9]{12}',project_id):raise ValueError('Invalid expected project')
    if topic_arn!=f'arn:aws:sns:eu-north-1:{project_id}:grad-jobwatch-backup-alerts':
        raise ValueError('Unexpected existing notification topic')
    if existing.get('Version')!='2012-10-17' or not isinstance(existing.get('Statement'),list):
        raise ValueError('Unexpected existing policy format')
    if any(s.get('Sid')==SID for s in existing['Statement']):
        raise ValueError('Calendar permission already exists; inspect rather than overwrite')
    prefix=f'arn:aws:cloudwatch:eu-north-1:{project_id}:alarm:'
    required={prefix+n for n in ('grad-jobwatch-host-status','grad-jobwatch-cpu-credit-balance',
                                 'grad-jobwatch-operation-failure','grad-jobwatch-backup-health')}
    matches=[s for s in existing['Statement'] if s.get('Sid')=='AllowReviewedCloudWatchAlarms']
    if len(matches)!=1:raise ValueError('Original alarm permission missing or ambiguous')
    old=matches[0]
    expected={'Sid':'AllowReviewedCloudWatchAlarms','Effect':'Allow',
        'Principal':{'Service':'cloudwatch.amazonaws.com'},'Action':'sns:Publish','Resource':topic_arn,
        'Condition':{'StringEquals':{'aws:SourceAccount':project_id},
                     'ArnEquals':{'aws:SourceArn':old.get('Condition',{}).get('ArnEquals',{}).get('aws:SourceArn')}}}
    arns=expected['Condition']['ArnEquals']['aws:SourceArn']
    if old!=expected or not isinstance(arns,list) or len(arns)!=4 or set(arns)!=required:
        raise ValueError('Original notification protection differs; stop for inspection')
    result=deepcopy(existing)
    result['Statement'].append({'Sid':SID,'Effect':'Allow','Principal':{'Service':'cloudwatch.amazonaws.com'},
        'Action':'sns:Publish','Resource':topic_arn,
        'Condition':{'StringEquals':{'aws:SourceAccount':project_id},
                     'ArnEquals':{'aws:SourceArn':prefix+'grad-jobwatch-calendar-health'}}})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--attributes',type=Path,required=True,help='Private GetTopicAttributes JSON')
    p.add_argument('--inputs',type=Path,required=True,help='Private approved project/topic inputs')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    inputs=json.loads(a.inputs.read_text());attributes=json.loads(a.attributes.read_text())
    original=json.loads(attributes['Attributes']['Policy'])
    proposed=extend(original,inputs['alert_topic_arn'],inputs['expected_project_id'])
    raw=(json.dumps(proposed,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    print(json.dumps({'aws_calls':False,'added_statements':1,'existing_statements_unchanged':True,
                      'proposed_sha256':sha256(raw).hexdigest()}))


if __name__=='__main__':main()
