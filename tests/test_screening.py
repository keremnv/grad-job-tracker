import json
from pathlib import Path
import unittest

from jobwatch.filtering import CODES, RULES_VERSION, input_fingerprint, screen

CASES = json.loads((Path(__file__).parent / 'fixtures' / 'screening_regressions.json').read_text())['cases']


def inputs(case):
    return {'title': case['title'], 'location': case.get('location', 'London, United Kingdom'),
            'country': case.get('country', 'GB'), 'countries': [case['country']] if case.get('country') else ([] if 'country' in case else ['GB']),
            'department': case.get('department', ''), 'team': case.get('team', ''), 'employment_type': '',
            'description': case.get('description', 'Build and operate software.')}


class ScreeningModelTests(unittest.TestCase):
    def test_regression_cases(self):
        for case in CASES:
            with self.subTest(case=case['id']):
                result = screen(inputs(case))
                self.assertEqual(result['verdict'], case['expect'], result['codes'])
                for code in case['codes']:
                    self.assertIn(code, result['codes'])

    def test_structured_result_is_machine_readable_and_reproducible(self):
        a = screen(inputs(CASES[0])); b = screen(inputs(CASES[0]))
        self.assertEqual(a, b)
        self.assertEqual(set(a), {'verdict', 'family', 'codes', 'explanation', 'rules_version', 'basis', 'input_sha256'})
        self.assertEqual(a['rules_version'], RULES_VERSION)
        for code in a['codes']:
            self.assertTrue(code.startswith('family.') or code in CODES, code)
        # Excluded results lead with their exclusion codes.
        excluded = screen(inputs({'title': 'Senior Accountant'}))
        self.assertTrue(excluded['codes'][0].startswith('exclude.'))

    def test_unretained_text_is_distinguished_from_empty_description(self):
        missing = screen({**inputs({'title': 'Graduate Software Engineer'}), 'description': None})
        empty = screen({**inputs({'title': 'Graduate Software Engineer'}), 'description': ''})
        self.assertEqual(missing['basis'], 'title_metadata_only'); self.assertEqual(empty['basis'], 'full')
        self.assertIn('evidence.source_text_unavailable', missing['codes'])
        self.assertIn('evidence.description_empty', empty['codes'])
        self.assertEqual(missing['verdict'], 'review')  # uncertainty, never a forced verdict
        self.assertNotEqual(missing['input_sha256'], empty['input_sha256'])

    def test_fingerprint_tracks_every_screened_input(self):
        base = inputs({'title': 'Graduate Software Engineer'})
        for field, value in (('title', 'Graduate Backend Engineer'), ('location', 'Leeds'), ('department', 'Graduate'),
                             ('team', 'Platform'), ('countries', ['GB', 'US']), ('description', 'Different.')):
            with self.subTest(field=field):
                self.assertNotEqual(input_fingerprint(base), input_fingerprint({**base, field: value}))

    def test_eligibility_and_availability_are_never_asserted(self):
        result = screen(inputs({'title': 'Graduate Software Engineer'}))
        self.assertEqual(result['verdict'], 'priority')
        self.assertIn('eligibility.unverified', result['codes'])
        self.assertIn('unverified', result['explanation'])


if __name__ == '__main__':
    unittest.main()
