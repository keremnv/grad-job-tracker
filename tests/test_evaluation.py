import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobwatch import evaluation as ev
from jobwatch.cli import main
from jobwatch.feeds import Posting
from jobwatch.search import BM25, tokens
from jobwatch.state import merge, save_state

NOW = '2026-10-22T12:00:00Z'
ROOT = Path(__file__).resolve().parents[1]


def case(i, title, label, category='c', description='Build software.'):
    return {'id': i, 'category': category, 'label': label, 'label_source': 'test',
            'inputs': {'title': title, 'location': 'London, United Kingdom', 'country': 'GB', 'countries': ['GB'],
                       'department': '', 'team': '', 'employment_type': '', 'description': description}}


class MetricTests(unittest.TestCase):
    def test_controlled_sample_metrics_and_uncertain_exclusion(self):
        cases = [case('a', 'Graduate Software Engineer', 'relevant'),            # priority
                 case('b', 'Software Engineer', 'relevant'),                     # review
                 case('c', 'Graduate Mechanical Engineer', 'relevant', 'x'),     # excluded -> false negative
                 case('d', 'Senior Accountant', 'not_relevant'),                 # excluded
                 case('e', 'Graduate Developer Advocate', 'not_relevant'),       # kept -> false positive
                 case('f', 'Graduate Engineer', 'uncertain')]                    # review, not in denominators
        m = ev.evaluate(cases)
        self.assertEqual(m['labels'], {'relevant': 3, 'not_relevant': 2, 'uncertain': 1})
        self.assertEqual(m['relevant_recall_kept'], round(2 / 3, 3))
        self.assertEqual(m['relevant_recall_priority'], round(1 / 3, 3))
        self.assertEqual(m['not_relevant_excluded_rate'], 0.5)
        self.assertEqual([f['id'] for f in m['false_negatives']], ['c'])
        self.assertEqual(m['false_negatives'][0]['codes'], ['exclude.non_software_discipline'])
        self.assertEqual([f['id'] for f in m['false_positives_kept']], ['e'])
        at5 = m['queue_precision_at_k'][0]
        self.assertEqual((at5['returned'], at5['judged'], at5['precision']), (4, 3, round(2 / 3, 3)))
        self.assertEqual(m['abstention'], {'review_verdicts': 2, 'uncertain_labels': 1})
        self.assertEqual(m['per_category']['x']['false_negatives'], 1)

    def test_empty_denominators_are_null_not_zero(self):
        m = ev.evaluate([case('u', 'Graduate Engineer', 'uncertain')])
        self.assertIsNone(m['relevant_recall_kept']); self.assertIsNone(m['queue_precision_at_k'][0]['precision'])

    def test_bm25_is_deterministic_and_ignores_zero_scores(self):
        index = BM25({'a': 'graduate software engineer python', 'b': 'senior accountant', 'c': 'software engineer'})
        self.assertEqual([k for k, _ in index.rank('graduate software')], ['a', 'c'])
        self.assertEqual(index.rank('graduate software'), index.rank('graduate software'))
        self.assertIn('c++', tokens('C++ and C#'))
        m = ev.evaluate([case('a', 'Graduate Software Engineer', 'relevant'), case('d', 'Senior Accountant', 'not_relevant')], baseline=True)
        self.assertEqual(m['bm25_baseline']['relevant_with_zero_score'], 0)

    def test_synthetic_dataset_is_labelled_and_marked_not_independent(self):
        cases = ev.synthetic_cases(ROOT / 'evaluation' / 'synthetic_cases.json')
        self.assertGreaterEqual(len(cases), 40)
        categories = {c['category'] for c in cases}
        for required in ('graduate_software', 'graduate_programme', 'infrastructure_platform', 'applied_ml', 'cloud_support_ambiguity',
                         'non_engineering_tech', 'senior', 'internship_placement', 'international_only', 'poorly_described'):
            self.assertIn(required, categories)
        self.assertTrue(all(c['label_source'] == 'author_synthetic' for c in cases))


class OwnerLabelWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory(); root = Path(self.dir.name)
        self.state = root / 'data' / 'jobs.json'
        posts = [Posting('E', 'lever', 'e', str(i), t, 'London, United Kingdom', f'https://jobs.lever.co/e/{i}',
                         description='Build software.', country='GB', countries=('GB',))
                 for i, t in enumerate(['Graduate Software Engineer', 'Software Engineer', 'Senior Accountant', 'Graduate Engineer', 'Office Manager'])]
        s, _ = merge({'schema': 2, 'postings': {}}, {'lever:e': posts}, NOW)
        save_state(self.state, s, NOW)
        self.root = root

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()), contextlib.chdir(self.root):
            code = main([*args, '--json', '--state', str(self.state), '--triage-file', str(self.root / 'data' / 'triage.json'),
                         '--applications', str(self.root / 'data' / 'applications.csv'), '--as-of', NOW])
        return code, json.loads(out.getvalue())

    def test_blind_template_unlabelled_never_negative_then_owner_labels_score(self):
        code, r = self.run_cli('eval', 'template', '--per-stratum', '2')
        self.assertEqual(code, 0)
        path = self.root / '.local-reports' / 'eval-label-template.json'
        template = json.loads(path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('verdict', json.dumps(template['items']))
        self.assertEqual(template['strata_sizes'], {'priority': 1, 'review': 2, 'excluded': 2})
        code, r = self.run_cli('eval', 'run', '--dataset', 'owner', '--labels', str(path))
        self.assertEqual(r['data']['status'], 'pending: no labelled cases; no performance claim is possible')
        template['items'][0]['label'] = 'relevant'
        path.write_text(json.dumps(template))
        code, r = self.run_cli('eval', 'run', '--dataset', 'owner', '--labels', str(path))
        self.assertEqual(r['data']['metrics']['cases'], 1)
        self.assertEqual(r['data']['meta']['labelled'], 1)

    def test_template_must_stay_private(self):
        code, r = self.run_cli('eval', 'template', '--out', str(self.root / 'reports' / 'labels.json'))
        self.assertEqual((code, r['errors'][0]['code']), (2, 'invalid_argument'))

    def test_triage_reasons_map_to_labels_and_preferences_are_ignored(self):
        self.run_cli('triage', 'set', 'lever:e:0', '--decision', 'interested')
        self.run_cli('triage', 'set', 'lever:e:1', '--decision', 'dismissed', '--reason', 'not_entry_level')
        self.run_cli('triage', 'set', 'lever:e:3', '--decision', 'dismissed', '--reason', 'location')
        code, r = self.run_cli('eval', 'run', '--dataset', 'triage')
        self.assertEqual(r['data']['meta']['used_as_labels'], 2)
        self.assertEqual(r['data']['meta']['ignored_preference_or_later'], 1)
        self.assertEqual(r['data']['metrics']['labels'], {'relevant': 1, 'not_relevant': 1, 'uncertain': 0})


if __name__ == '__main__':
    unittest.main()
