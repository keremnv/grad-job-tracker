"""Exact SNS resource-policy amendment, no AWS credentials or calls."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('render_calendar_alert_policy',ROOT/'infra/aws/scripts/render_calendar_alert_policy.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
PROJECT='000000000000'
TOPIC=f'arn:aws:sns:eu-north-1:{PROJECT}:grad-jobwatch-backup-alerts'
PREFIX=f'arn:aws:cloudwatch:eu-north-1:{PROJECT}:alarm:'


def policy():
    return {'Version':'2012-10-17','Statement':[{'Sid':'AllowReviewedCloudWatchAlarms','Effect':'Allow',
        'Principal':{'Service':'cloudwatch.amazonaws.com'},'Action':'sns:Publish','Resource':TOPIC,
        'Condition':{'StringEquals':{'aws:SourceAccount':PROJECT},'ArnEquals':{'aws:SourceArn':
            [PREFIX+n for n in ('grad-jobwatch-host-status','grad-jobwatch-cpu-credit-balance',
                               'grad-jobwatch-operation-failure','grad-jobwatch-backup-health')]}}}]}


class CalendarAlarmPolicyTests(unittest.TestCase):
    def test_preserves_original_policy_and_adds_only_the_exact_new_alarm(self):
        before=policy();saved=deepcopy(before)
        after=helper.extend(before,TOPIC,PROJECT)
        self.assertEqual(before,saved);self.assertEqual(after['Statement'][:-1],saved['Statement'])
        new=after['Statement'][-1]
        self.assertEqual(new['Action'],'sns:Publish');self.assertEqual(new['Resource'],TOPIC)
        self.assertEqual(new['Principal'],{'Service':'cloudwatch.amazonaws.com'})
        self.assertEqual(new['Condition'],{'StringEquals':{'aws:SourceAccount':PROJECT},
            'ArnEquals':{'aws:SourceArn':PREFIX+'grad-jobwatch-calendar-health'}})
        self.assertNotIn('*',str(new))

    def test_refuses_already_changed_wrong_topic_project_or_region(self):
        after=helper.extend(policy(),TOPIC,PROJECT)
        for current,topic,project in [(after,TOPIC,PROJECT),(policy(),TOPIC+'-other',PROJECT),
            (policy(),TOPIC,'111111111111'),(policy(),TOPIC.replace('eu-north-1','us-east-1'),PROJECT)]:
            with self.subTest(topic=topic,project=project),self.assertRaises(ValueError):helper.extend(current,topic,project)

    def test_rejects_weakened_principal_missing_account_and_unexpected_alarm(self):
        changes=[lambda p:p['Statement'][0].update(Principal='*'),
            lambda p:p['Statement'][0]['Condition'].pop('StringEquals'),
            lambda p:p['Statement'][0]['Condition']['ArnEquals']['aws:SourceArn'].append(PREFIX+'unexpected'),
            lambda p:p['Statement'].append(deepcopy(p['Statement'][0]))]
        for change in changes:
            before=policy();change(before)
            with self.assertRaises(ValueError):helper.extend(before,TOPIC,PROJECT)

    def test_unrelated_existing_statements_are_preserved_byte_for_byte_as_values(self):
        before=policy();before['Statement'].append({'Sid':'UnrelatedDeny','Effect':'Deny',
            'Principal':'*','Action':'sns:DeleteTopic','Resource':TOPIC})
        after=helper.extend(before,TOPIC,PROJECT)
        self.assertEqual(after['Statement'][:-1],before['Statement'])
