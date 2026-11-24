"""MCP health decision and SNS amendment (offline, no AWS calls)."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'infra/aws/scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class McpHealthEvaluateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.health = load('mcp_health')

    def test_healthy_requires_probe_active_and_no_restart(self):
        self.assertEqual(self.health.evaluate(True, 'active', 5, 5),
                         {'mcp_healthy': 1, 'restarts_delta': 0})

    def test_first_observation_with_probe_ok_is_healthy(self):
        self.assertEqual(self.health.evaluate(True, 'active', 5, None)['mcp_healthy'], 1)

    def test_probe_failure_is_unhealthy(self):
        self.assertEqual(self.health.evaluate(False, 'active', 5, 5)['mcp_healthy'], 0)

    def test_restart_delta_is_unhealthy(self):
        result = self.health.evaluate(True, 'active', 6, 5)
        self.assertEqual(result, {'mcp_healthy': 0, 'restarts_delta': 1})

    def test_inactive_is_unhealthy(self):
        self.assertEqual(self.health.evaluate(True, 'failed', 5, 5)['mcp_healthy'], 0)


class McpAlarmPolicyTests(unittest.TestCase):
    PROJECT = '000000000000'
    TOPIC = 'arn:aws:sns:eu-north-1:000000000000:grad-jobwatch-backup-alerts'
    PREFIX = 'arn:aws:cloudwatch:eu-north-1:000000000000:alarm:'

    @classmethod
    def setUpClass(cls):
        cls.helper = load('render_mcp_alert_policy')

    def policy(self):
        return {'Version': '2012-10-17', 'Statement': [
            {'Sid': 'AllowReviewedCloudWatchAlarms', 'Effect': 'Allow',
             'Principal': {'Service': 'cloudwatch.amazonaws.com'}, 'Action': 'sns:Publish',
             'Resource': self.TOPIC,
             'Condition': {'StringEquals': {'aws:SourceAccount': self.PROJECT},
                           'ArnEquals': {'aws:SourceArn': [self.PREFIX + n for n in (
                               'grad-jobwatch-host-status', 'grad-jobwatch-cpu-credit-balance',
                               'grad-jobwatch-operation-failure', 'grad-jobwatch-backup-health')]}}},
            {'Sid': 'AllowReviewedCalendarAlarm', 'Effect': 'Allow',
             'Principal': {'Service': 'cloudwatch.amazonaws.com'}, 'Action': 'sns:Publish',
             'Resource': self.TOPIC,
             'Condition': {'StringEquals': {'aws:SourceAccount': self.PROJECT},
                           'ArnEquals': {'aws:SourceArn': self.PREFIX + 'grad-jobwatch-calendar-health'}}},
        ]}

    def test_adds_only_exact_mcp_alarm_and_preserves_existing(self):
        before = self.policy()
        after = self.helper.extend(before, self.TOPIC, self.PROJECT)
        self.assertEqual(after['Statement'][:-1], before['Statement'])
        new = after['Statement'][-1]
        self.assertEqual(new['Sid'], 'AllowReviewedMcpAlarm')
        self.assertEqual(new['Condition']['ArnEquals']['aws:SourceArn'],
                         self.PREFIX + 'grad-jobwatch-mcp-health')
        self.assertNotIn('*', str(new))

    def test_refuses_repeat_or_drift(self):
        after = self.helper.extend(self.policy(), self.TOPIC, self.PROJECT)
        with self.assertRaises(ValueError):
            self.helper.extend(after, self.TOPIC, self.PROJECT)
        with self.assertRaises(ValueError):
            self.helper.extend(self.policy(), self.TOPIC + '-x', self.PROJECT)


if __name__ == '__main__':
    unittest.main()
