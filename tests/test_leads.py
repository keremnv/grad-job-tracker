import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from jobwatch.cli import main
from jobwatch.leads import load_leads, render_leads

NOW = '2026-10-17T23:00:00Z'


def vacancy(**kw):
    row = {'company': 'Example', 'title': 'Graduate Software Engineer', 'url': 'https://example.org/job',
           'source': 'official page', 'official': True, 'checked_at': '2026-10-17T22:00:00Z',
           'status': 'listed', 'intake': '2027', 'intake_wording': 'Start date: September 2027'}
    row.update(kw)
    return row


def programme(**kw):
    row = {'company': 'Example', 'programme': 'Graduate scheme', 'url': 'https://example.org/grads',
           'source': 'official page', 'official': True, 'checked_at': '2026-10-17T22:00:00Z',
           'cycle': '2026', 'state': 'previous_cycle', 'wording': 'Applications for the 2026 programme have now closed.'}
    row.update(kw)
    return row


def load(data):
    with TemporaryDirectory() as d:
        path = Path(d) / 'leads.json'
        path.write_text(json.dumps(data))
        return load_leads(path)


class LeadRegistryTests(unittest.TestCase):
    def test_registry_separates_previous_cycles_and_flags_near_deadlines(self):
        # Synthetic registry; the live data/leads.json is checked by `jobwatch validate-data`.
        data = load({'schema': 1,
                     'vacancies': [vacancy(deadline={'wording': 'Apply by 29 October 2026', 'value': '2026-10-29'})],
                     'programmes': [programme(), programme(company='Next', cycle='2027', state='open_2027',
                                                           url='https://example.org/2027', wording='Our 2027 programme is open.')]})
        out = render_leads(data, NOW)
        current, previous = out.split('## Previous-cycle programmes')
        for row in data['programmes']:
            if row['state'] == 'previous_cycle':
                self.assertNotIn(row['url'], current.split('## Programmes stating a 2027 intake')[1])
        self.assertIn('2026', previous)
        self.assertIn('within 14 calendar days', out)
        self.assertIn('never** evidence of a 2027 intake', out)

    def test_2027_intake_must_be_quoted_not_inferred(self):
        base = {'schema': 1, 'vacancies': [vacancy()], 'programmes': [programme()]}
        load(base)
        bad = [
            {'vacancies': [vacancy(intake_wording='Start date: September 2026')]},
            {'vacancies': [vacancy(intake='2027', intake_wording='')]},
            {'vacancies': [vacancy(intake='next year')]},
            {'programmes': [programme(state='open_2027', cycle='2026')]},
            {'programmes': [programme(state='open_2027', cycle='2027')]},  # 2027 not in wording
            {'programmes': [programme(state='previous_cycle', cycle='2027', wording='2027 programme')]},
            {'programmes': [programme(state='previous_cycle', cycle='unspecified')]},
            {'programmes': [programme(state='announced_window', cycle='2027', wording='Applications: January 2027')]},
            {'programmes': [programme(state='guessed_open')]},
        ]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ValueError):
                load({**copy.deepcopy(base), **change})

    def test_sources_dates_and_deadlines_validated(self):
        base = {'schema': 1, 'vacancies': [vacancy()], 'programmes': []}
        for row in (vacancy(url='http://example.org/job'), vacancy(url='javascript:alert(1)'),
                    vacancy(checked_at='2026-10-17'), vacancy(official='yes'), vacancy(status='open'),
                    vacancy(deadline={'value': '2026-11-06'}), vacancy(deadline={'wording': 'x', 'value': '6 Nov'}),
                    vacancy(deadline={'wording': 'x', 'url': 'ftp://example.org'})):
            with self.subTest(row=row), self.assertRaises((ValueError, TypeError)):
                load({**base, 'vacancies': [row]})

    def test_rendering_escapes_and_keeps_missing_deadline_unknown(self):
        data = {'schema': 1, 'programmes': [], 'vacancies': [
            vacancy(company='<b>X</b>', title='[click](javascript:bad)', deadline={'wording': 'No deadline; rolling.'}),
            vacancy(company='Y', intake='unknown', intake_wording='', official=False, notes='start not stated')]}
        out = render_leads(load(data), NOW)
        self.assertNotIn('<b>', out); self.assertNotIn('](javascript', out)
        self.assertIn('exact deadline unknown', out)
        self.assertIn("not verified as the employer's own source", out)
        self.assertIn('without a quoted 2027 intake', out)

    def test_role_sample_checker_flags_inconsistent_records(self):
        from jobwatch.datacheck import role_sample_problems
        good = {'families': ['software'], 'postings': [{'id': 'S1', 'family': 'software', 'url': 'https://example.org/a',
                'checked_at': '2026-10-17T00:00:00Z', 'official': True, 'posting_key': 'lever:x:1'}]}
        checks = {'lever:x:1': {'description_url': 'https://example.org/a'}}
        self.assertEqual(role_sample_problems(good, checks), [])
        bad = copy.deepcopy(good); bad['postings'].append({**bad['postings'][0], 'family': 'ml', 'url': 'http://x', 'official': 'yes'})
        problems = role_sample_problems(bad, {})
        self.assertTrue(any('duplicate' in p for p in problems)); self.assertTrue(any('family' in p for p in problems))
        self.assertTrue(any('HTTPS' in p for p in problems)); self.assertTrue(any('disagrees' in p for p in problems))

    def test_cli_writes_report_and_protects_registry(self):
        with TemporaryDirectory() as d:
            d = Path(d); src = d / 'leads.json'; out = d / 'leads.md'
            src.write_text(json.dumps({'schema': 1, 'vacancies': [vacancy()], 'programmes': [programme()]}))
            before = src.read_bytes()
            self.assertEqual(main(['leads', '--leads', str(src), '--output', str(out)]), 0)
            self.assertIn('Previous-cycle programmes', out.read_text())
            self.assertEqual(main(['leads', '--leads', str(src), '--output', str(src)]), 2)
            self.assertEqual(src.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
