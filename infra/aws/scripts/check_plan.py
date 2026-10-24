#!/usr/bin/env python3
"""Read a PRIVATE terraform show -json plan; print only resource types/counts and safety checks."""
from collections import Counter
import json
from pathlib import Path
import sys

EXPECTED = Counter({'aws_security_group': 1, 'aws_ebs_volume': 1, 'aws_instance': 1,
                    'aws_volume_attachment': 1, 'aws_s3_bucket': 1, 'aws_s3_bucket_public_access_block': 1,
                    'aws_s3_bucket_ownership_controls': 1, 'aws_s3_bucket_versioning': 1,
                    'aws_s3_bucket_server_side_encryption_configuration': 1, 'aws_s3_bucket_lifecycle_configuration': 1,
                    'aws_s3_bucket_policy': 1, 'aws_iam_role': 1, 'aws_iam_instance_profile': 1,
                    'aws_iam_role_policy': 1, 'aws_cloudwatch_log_group': 1, 'aws_cloudwatch_log_stream': 1,
                    'aws_cloudwatch_metric_alarm': 3, 'aws_cloudwatch_log_metric_filter': 1})


def require(condition):
    if not condition:
        raise ValueError('Plan safety invariant failed')


def check(plan):
    resources = [r for r in plan.get('resource_changes', []) if r.get('mode') == 'managed']
    if Counter(r['type'] for r in resources) != EXPECTED:
        raise ValueError('Unexpected resource inventory')
    if any(r['change']['actions'] != ['create'] for r in resources):
        raise ValueError('Initial preflight allows only proposed creates; no existing resource changes')
    by_type = {r['type']: r['change']['after'] for r in resources}
    host = by_type['aws_instance']
    require(host['instance_type'] in ('t4g.small', 't4g.micro'))
    require(not host.get('key_name') and host['associate_public_ip_address'])
    require(host['disable_api_termination'] and not host['monitoring'])
    require(host['metadata_options'][0]['http_tokens'] == 'required')
    require(host['credit_specification'][0]['cpu_credits'] == 'standard')
    require(host['root_block_device'][0]['encrypted'])
    require(not by_type['aws_security_group']['ingress'])
    require({r['from_port'] for r in by_type['aws_security_group']['egress']} == {80, 443})
    disk = by_type['aws_ebs_volume']
    require(disk['encrypted'] and disk['type'] == 'gp3' and disk['final_snapshot'])
    require(not by_type['aws_s3_bucket']['force_destroy'])
    require(all(by_type['aws_s3_bucket_public_access_block'][f] for f in (
        'block_public_acls', 'block_public_policy', 'ignore_public_acls', 'restrict_public_buckets')))
    require(by_type['aws_s3_bucket_versioning']['versioning_configuration'][0]['status'] == 'Enabled')
    require(by_type['aws_cloudwatch_log_group']['retention_in_days'] == 14)
    require(not by_type['aws_volume_attachment']['force_detach'])
    require(by_type['aws_volume_attachment']['stop_instance_before_detaching'])
    return {'proposed_resources': len(resources), 'types': dict(sorted(EXPECTED.items())),
            'plan_safety_checks_passed': True, 'deployment_authorized': False}


if __name__ == '__main__':
    try:
        print(json.dumps(check(json.loads(Path(sys.argv[1]).read_text())), indent=2, sort_keys=True))
    except (AssertionError, ValueError, KeyError, IndexError):
        raise SystemExit('Plan safety check failed; inspect the private plan, do not apply')
