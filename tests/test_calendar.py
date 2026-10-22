"""Deterministic offline RFC 5545, evidence, privacy and output-safety checks."""
from contextlib import chdir, redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jobwatch import calendar as cal
from jobwatch.applications import add, make_application
from jobwatch.cli import main

NOW = '2026-10-22T12:34:56Z'
KEY = 'lever:example:one'
URL = 'https://jobs.lever.co/example/one'
ROOT = Path(__file__).resolve().parents[1]


def deadline(value='2026-10-29', **extra):
    return {'value': value, 'url': URL, 'wording': 'Please apply by 29 October 2026.',
            'verified_at': NOW, **extra}


def posting(url=URL, **extra):
    return {'company': 'Example', 'title': 'Graduate Engineer', 'url': url, 'provider': 'lever',
            'board': 'example', 'external_id': 'one', 'first_seen': NOW, 'feed_status': 'not_seen',
            'priority': 'review', **extra}


def state(**extra):
    return {'schema': 1, 'postings': {KEY: posting(**extra)}}


def verification(d=None, **extra):
    return {KEY: {'status': 'unknown', 'url': URL, 'deadlines': [deadline() if d is None else d], **extra}}


def lead(value='2026-10-29', url=URL, **extra):
    return {'company': 'Example', 'title': 'Graduate Engineer', 'url': url,
            'official': True, 'status': 'listed', 'checked_at': NOW,
            'deadline': deadline(value, url=url), **extra}


def application(key='manual:one', **extra):
    return make_application({'job_key': key, 'company': 'Example', 'role': 'Graduate Engineer',
        'url': 'https://example.org/job/' + key.replace(':', '-'), 'stage': 'applied',
        'next_action': 'Review status', 'next_action_date': '2026-10-29',
        'notes': 'PRIVATE_NOTE_DO_NOT_EXPORT', 'cv_version': 'PRIVATE_CV_DO_NOT_EXPORT', **extra})


def properties(content):
    """Read unfolded content lines for assertions, preserving RFC TEXT escapes."""
    raw = content.decode('utf-8').replace('\r\n ', '').replace('\r\n\t', '')
    blocks = raw.split('BEGIN:VEVENT\r\n')[1:]
    result = []
    for block in blocks:
        fields = {}
        for line in block.split('\r\nEND:VEVENT\r\n')[0].split('\r\n'):
            if line:
                name, value = line.split(':', 1)
                if name in fields:
                    raise AssertionError('duplicate property ' + name)
                fields[name] = value
        result.append(fields)
    return result


class CalendarEvidenceTests(unittest.TestCase):
    def test_all_day_deadline_exact_date_and_exclusive_end(self):
        events, warnings = cal.deadline_events(state(), verification(), {})
        self.assertFalse(warnings)
        event = properties(cal.render(events, NOW))[0]
        self.assertEqual(event['DTSTART;VALUE=DATE'], '20261029')
        self.assertEqual(event['DTEND;VALUE=DATE'], '20261030')
        self.assertEqual(event['DTSTAMP'], '20261022T123456Z')
        self.assertIn('closing time and timezone are unknown', event['DESCRIPTION'])
        self.assertIn('not independent proof', event['DESCRIPTION'])
        self.assertIn(NOW, event['DESCRIPTION'])
        self.assertIn('Please apply', event['DESCRIPTION'])
        self.assertNotIn('TZID', event)
        for value, end in (('2024-02-29', '20240301'), ('2026-12-31', '20270101')):
            events, _ = cal.deadline_events(state(), verification(deadline(value)), {})
            self.assertEqual(properties(cal.render(events, NOW))[0]['DTEND;VALUE=DATE'], end)

    def test_explicit_times_normalised_utc_without_assumed_duration(self):
        for value in ('2026-10-29T17:00:00+01:00', '2026-10-29T16:00:00Z', '2026-10-29T16:00+00:00'):
            events, warnings = cal.deadline_events(state(), verification(deadline(value)), {})
            self.assertFalse(warnings)
            item = properties(cal.render(events, NOW))[0]
            self.assertEqual(item['DTSTART'], '20261029T160000Z')
            self.assertNotIn('DTEND', item)
            self.assertIn(value, item['DESCRIPTION'])
            self.assertIn('instantaneous', item['DESCRIPTION'])
        equivalent = verification()
        equivalent[KEY]['deadlines'] = [deadline('2026-10-29T17:00:00+01:00'), deadline('2026-10-29T16:00:00Z')]
        events, warnings = cal.deadline_events(state(), equivalent, {})
        self.assertEqual(len(events), 1); self.assertFalse(warnings)

    def test_ambiguous_timezone_invalid_dates_and_unsupported_precision_warn_and_omit(self):
        for value in ('2026-10-29T17:00:00', '2026-10-29T17:00:00 BST', '2026-10-29T17:00:00-00:00',
                      '2026-10-29T17:00:00.123Z', '2026-02-29', '20261029', '2026-W44-4',
                      '2026-10-29 Europe/London', '9999-12-31'):
            with self.subTest(value=value):
                events, warnings = cal.deadline_events(state(), verification(deadline(value)), {})
                self.assertFalse(events); self.assertTrue(warnings)
                self.assertNotIn(value, '\n'.join(warnings))

    def test_missing_dates_prose_and_rolling_never_inferred(self):
        items = [lead(value=None), lead(deadline={'wording': 'Possible deadline 29 October 2026'}),
                 lead(deadline={'wording': 'No deadline; applications considered on a rolling basis.'})]
        events, warnings = cal.deadline_events({'postings': {}}, {}, {'vacancies': items})
        self.assertFalse(events); self.assertFalse(warnings)
        events, warnings = cal.deadline_events({'postings': {}}, {}, {'vacancies': [lead(deadline=deadline(wording='Rolling basis, no deadline.'))]})
        self.assertFalse(events); self.assertTrue(warnings)

    def test_structured_feed_dates_need_explicit_field_observation_and_matching_id(self):
        evidence = deadline(source_field='application_deadline', observed_at=NOW)
        evidence.pop('verified_at')
        events, warnings = cal.deadline_events(state(deadlines=[evidence]), {}, {})
        self.assertEqual(len(events), 1); self.assertFalse(warnings)
        self.assertIn('Feed observed', events[0]['description'])
        for bad in ({'source_field': 'description'}, {'observed_at': ''}, {'url': 'https://example.org/shared'}):
            events, warnings = cal.deadline_events(state(deadlines=[{**evidence, **bad}]), {}, {})
            self.assertFalse(events); self.assertTrue(warnings)
        invalid = state(deadlines=[evidence]); invalid['postings'][KEY]['external_id'] = 'different'
        events, warnings = cal.deadline_events(invalid, {}, {})
        self.assertFalse(events); self.assertTrue(warnings)
        # not_seen above is deliberately retained; it does not establish closure.

    def test_manual_and_verification_dates_need_checked_official_provenance(self):
        for item in (lead(official=False), lead(status='unverified'),
                     lead(checked_at='', deadline={'value': '2026-10-29', 'wording': 'Apply by 29 October.'}),
                     lead(deadline=deadline(verified_at=''))):
            events, warnings = cal.deadline_events({'postings': {}}, {}, {'vacancies': [item]})
            self.assertFalse(events); self.assertTrue(warnings)
        d = deadline(); d.pop('verified_at')
        events, warnings = cal.deadline_events(state(), verification(d), {})
        self.assertFalse(events); self.assertTrue(warnings)

    def test_current_incident_riverlane_once_each_and_cambridge_rolling_omitted(self):
        source_state = json.loads((ROOT/'data/jobs.json').read_text())
        verified = json.loads((ROOT/'data/verifications.json').read_text())['postings']
        leads = json.loads((ROOT/'data/leads.json').read_text())
        events, warnings = cal.deadline_events(source_state, verified, leads)
        self.assertFalse(warnings)
        selected = [e for e in events if 'incident.io' in e['summary'] or 'Riverlane' in e['summary']]
        self.assertEqual(len(selected), 2)
        self.assertEqual({e['date'][2] for e in selected}, {'date:2026-10-29', 'date:2026-11-06'})
        self.assertFalse(any('Cambridge Consultants' in e['summary'] for e in events))
        self.assertEqual(len({e['uid'] for e in events}), len(events))

    def test_exact_url_reconciles_lead_with_orphan_verification_id(self):
        events, warnings = cal.deadline_events({'postings': {}}, verification(),
                    {'vacancies': [lead(url='https://JOBS.LEVER.CO:443/example/one')]})
        self.assertEqual(len(events), 1); self.assertFalse(warnings)
        self.assertEqual(events[0]['uid'], cal.uid('deadline', KEY))
        self.assertIn('Graduate Engineer', events[0]['summary'])

    def test_same_company_title_and_shared_deadline_source_dont_merge_vacancies(self):
        first = lead(url='https://example.org/jobs/one?opening=1')
        second = lead(url='https://example.org/jobs/one?opening=2')
        for item in (first, second): item['deadline']['url'] = 'https://example.org/deadlines'
        events, warnings = cal.deadline_events({'postings': {}}, {}, {'vacancies': [first, second]})
        self.assertEqual(len(events), 2); self.assertFalse(warnings)
        self.assertEqual(len({e['uid'] for e in events}), 2)

    def test_conflicting_dates_omit_entire_vacancy_without_newest_source_guess(self):
        events, warnings = cal.deadline_events(state(), verification(), {'vacancies': [lead(value='2026-10-30')]})
        self.assertFalse(events)
        self.assertTrue(any('conflict' in w.lower() for w in warnings))
        self.assertFalse(any('Example' in w or URL in w for w in warnings))
        # Date-only and timestamp evidence do not establish a resolution either.
        events, warnings = cal.deadline_events(state(), verification(), {'vacancies': [lead(value='2026-10-29T17:00:00Z')]})
        self.assertFalse(events); self.assertTrue(warnings)

    def test_manual_fragment_openings_are_distinct_and_source_urls_retained(self):
        rows = [lead(url='https://example.org/careers#opening-one'),
                lead(url='https://example.org/careers#opening-two')]
        events, warnings = cal.deadline_events({'postings': {}}, {}, {'vacancies': rows})
        self.assertFalse(warnings); self.assertEqual(len(events), 2)
        self.assertEqual({e['url'] for e in events}, {r['url'] for r in rows})

    def test_conflicting_id_to_url_mapping_is_not_silently_merged(self):
        verified = verification()
        verified['lever:example:two'] = deepcopy(verified[KEY])
        events, warnings = cal.deadline_events(state(), verified, {'vacancies': [lead()]})
        self.assertFalse(events); self.assertTrue(warnings)

    def test_deadline_uid_stable_when_date_text_and_posting_url_change(self):
        events, _ = cal.deadline_events(state(), verification(), {})
        changed = state(url='https://example.org/changed', title='Changed title')
        verified = verification(deadline('2026-11-06', url='https://example.org/changed', wording='Changed wording'), url='https://example.org/changed')
        updated, _ = cal.deadline_events(changed, verified, {})
        self.assertEqual(events[0]['uid'], updated[0]['uid'])
        self.assertNotEqual(events[0]['date'], updated[0]['date'])
        self.assertNotEqual(events[0]['summary'], updated[0]['summary'])

    def test_secret_urls_and_untrusted_text_cannot_inject_components(self):
        for url in ('https://user:SECRET@example.org/job', 'https://example.org/job?access_token=SECRET',
                    'https://example.org/job#token=SECRET', 'file:///home/private/token',
                    'https://example.org/job?X-Amz-Credential=SECRET'):
            events, warnings = cal.deadline_events(state(), verification(deadline(url=url)), {})
            self.assertFalse(events); self.assertTrue(warnings)
            self.assertNotIn('SECRET', ''.join(warnings))
        events, _ = cal.deadline_events(state(), verification(deadline(wording='Source\r\nEND:VEVENT\r\nBEGIN:VALARM')), {})
        content = cal.render(events, NOW)
        self.assertEqual(len(properties(content)), 1)
        self.assertNotIn(b'\r\nBEGIN:VALARM', content)
        self.assertIn('\\nEND:VEVENT\\nBEGIN:VALARM', properties(content)[0]['DESCRIPTION'])


class CalendarFormatTests(unittest.TestCase):
    def test_followups_use_owner_dates_and_exclude_missing_actions_and_terminal_stages(self):
        rows = [application(), application('manual:rejected', stage='rejected'),
                application('manual:withdrawn', stage='withdrawn'),
                application('manual:no-action', next_action=''), application('manual:no-date', next_action_date='')]
        events, warnings = cal.followup_events(rows)
        self.assertEqual(len(events), 1); self.assertFalse(warnings)
        props = properties(cal.render(events, NOW))[0]
        self.assertEqual(props['DTSTART;VALUE=DATE'], '20261029')
        self.assertEqual(props['DTEND;VALUE=DATE'], '20261030')
        self.assertNotIn('PRIVATE_NOTE', str(props)); self.assertNotIn('PRIVATE_CV', str(props))
        changed = application(next_action='Prepare for interview', next_action_date='2026-11-06')
        self.assertEqual(events[0]['uid'], cal.followup_events([changed])[0][0]['uid'])
        self.assertNotIn('manual:one', events[0]['uid']); self.assertNotIn('Example', events[0]['uid'])
        self.assertNotEqual(events[0]['uid'], cal.uid('deadline', 'manual:one'))

    def test_unicode_text_escaping_line_folding_and_crlf_octets(self):
        text = 'Résumé 👩🏽‍💻, λ; backslash\\\nnext\r\nlast ' + '漢字🙂' * 50
        events, _ = cal.followup_events([application(next_action=text)])
        content = cal.render(events, NOW)
        self.assertTrue(content.endswith(b'END:VCALENDAR\r\n'))
        self.assertNotIn(b'\n', content.replace(b'\r\n', b''))
        self.assertNotIn(b'\r', content.replace(b'\r\n', b''))
        for line in content.split(b'\r\n'):
            self.assertLessEqual(len(line), 75)
            line.decode('utf-8')  # folding must not split a UTF-8 character
        summary = properties(content)[0]['SUMMARY']
        self.assertIn('Résumé 👩🏽‍💻\\, λ\\; backslash\\\\\\nnext\\nlast', summary)
        self.assertIn('漢字🙂' * 50, summary)
        self.assertNotIn(b'VALARM', content); self.assertNotIn(b'ATTENDEE', content)
        self.assertNotIn(b'RRULE', content); self.assertNotIn(b'METHOD:', content)

    def test_repeat_export_deterministic_and_order_independent_no_duplicate_uids(self):
        events, _ = cal.followup_events([application('manual:b', next_action_date='2026-11-01'), application('manual:a')])
        first = cal.render(events, NOW)
        self.assertEqual(first, cal.render(list(reversed(events)), NOW))
        props = properties(first)
        self.assertEqual(len({p['UID'] for p in props}), 2)
        self.assertEqual(props[0]['DTSTART;VALUE=DATE'], '20261029')
        for p in props: datetime.strptime(p['DTSTAMP'], '%Y%m%dT%H%M%SZ')
        with self.assertRaises(ValueError): cal.render(events + events, NOW)

    def test_empty_calendar_has_no_fabricated_events(self):
        content = cal.render([], NOW)
        self.assertEqual(properties(content), [])
        self.assertIn(b'VERSION:2.0\r\n', content)
        self.assertIn(b'PRODID:', content)
        self.assertTrue(content.startswith(b'BEGIN:VCALENDAR\r\n'))

    def test_followup_secret_url_is_omitted_and_unrepresentable_end_warns(self):
        events, warnings = cal.followup_events([application(url='https://example.org/job?token=SECRET')])
        self.assertEqual(len(events), 1); self.assertTrue(warnings)
        self.assertNotIn('SECRET', cal.render(events, NOW).decode())
        events, warnings = cal.followup_events([application(next_action_date='9999-12-31')])
        self.assertFalse(events); self.assertTrue(warnings)


class CalendarOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '--quiet', str(self.root)], check=True, capture_output=True)
        (self.root/'.gitignore').write_text('.local-reports/\n')
        (self.root/'data').mkdir()
        (self.root/'data/jobs.json').write_text(json.dumps(state()))
        (self.root/'data/verifications.json').write_text(json.dumps({'schema': 1, 'postings': verification()}))
        (self.root/'data/leads.json').write_text(json.dumps({'schema': 1, 'vacancies': [lead()]}))
        self.ledger = self.root/'data/applications.csv'; add(self.ledger, application())

    def run_export(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with chdir(self.root), redirect_stdout(out), redirect_stderr(err), patch('jobwatch.calendar.utcnow', return_value=NOW):
            code = main(['calendar', 'export', *map(str, args)])
        return code, out.getvalue(), err.getvalue()

    def test_default_cli_export_private_files_and_concise_console(self):
        original = {p: p.read_bytes() for p in (self.root/'data').iterdir()}
        code, out, err = self.run_export()
        self.assertEqual(code, 0, err); self.assertEqual(out.count('1 events:'), 2)
        self.assertNotIn('Review status', out); self.assertNotIn('PRIVATE', out + err)
        for name in ('deadlines.ics', 'followups.ics'):
            path = self.root/'.local-reports'/name
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(properties(path.read_bytes())), 1)
            self.assertNotIn('PRIVATE_NOTE', path.read_text()); self.assertNotIn('PRIVATE_CV', path.read_text())
            self.assertNotIn(str(self.root), path.read_text())
            result = subprocess.run(['git', 'check-ignore', '--quiet', str(path)], cwd=self.root)
            self.assertEqual(result.returncode, 0)
        self.assertTrue(all(p.read_bytes() == before for p, before in original.items()))
        first = (self.root/'.local-reports/followups.ics').read_bytes()
        (self.root/'.local-reports/followups.ics').chmod(0o644)
        self.assertEqual(self.run_export()[0], 0)
        self.assertEqual(first, (self.root/'.local-reports/followups.ics').read_bytes())
        self.assertEqual((self.root/'.local-reports/followups.ics').stat().st_mode & 0o777, 0o600)

    def test_bad_input_preserves_both_existing_outputs(self):
        self.assertEqual(self.run_export()[0], 0)
        before = {p: p.read_bytes() for p in (self.root/'.local-reports').glob('*.ics')}
        for malformed in ('job_key,company\nx,y\n', 'not CSV at all'):
            self.ledger.write_text(malformed)
            code, _, _ = self.run_export()
            self.assertEqual(code, 2)
            self.assertTrue(all(p.read_bytes() == old for p, old in before.items()))
        self.ledger.unlink(); add(self.ledger, application())
        (self.root/'data/leads.json').write_text('{"schema":1,"vacancies":[{"deadline":"bad"}]}')
        code, _, _ = self.run_export()
        self.assertEqual(code, 2)
        self.assertTrue(all(p.read_bytes() == old for p, old in before.items()))

    def test_ledger_timing_or_malformed_json_cannot_replace_exports(self):
        self.assertEqual(self.run_export()[0], 0)
        before = {p: p.read_bytes() for p in (self.root/'.local-reports').glob('*.ics')}
        self.ledger.write_bytes(self.ledger.read_bytes().replace(b'2026-10-29', b'2026-10-29T17:00:00Z'))
        code, _, _ = self.run_export()
        self.assertEqual(code, 2)
        self.assertTrue(all(p.read_bytes() == old for p, old in before.items()))
        self.ledger.unlink(); add(self.ledger, application())
        for payload in ('{', '{"schema":1,"postings":{"bad":42}}',
                        '{"schema":1,"postings":{"lever:example:one":{"deadlines":["bad"]}}}'):
            (self.root/'data/verifications.json').write_text(payload)
            self.assertEqual(self.run_export()[0], 2)
            self.assertTrue(all(p.read_bytes() == old for p, old in before.items()))

    def test_output_directory_paths_cannot_touch_source_config_or_actions(self):
        for directory in ('data', 'config', '.github/workflows', '.', '.local-reports/../data', '/tmp'):
            code, _, _ = self.run_export('--output-dir', directory)
            self.assertEqual(code, 2)
        self.assertFalse((self.root/'data/deadlines.ics').exists())
        self.assertFalse((self.root/'.github').exists())
        # Even source inputs deliberately placed in the output directory are protected.
        dest = self.root/'.local-reports'; dest.mkdir()
        collision = dest/'deadlines.ics'; collision.write_text('PROTECTED INPUT')
        code, _, _ = self.run_export('--leads', collision)
        self.assertEqual(code, 2); self.assertEqual(collision.read_text(), 'PROTECTED INPUT')

    def test_output_symlinks_hardlinks_and_unignored_or_tracked_files_refused(self):
        dest = self.root/'.local-reports'; dest.mkdir()
        target = dest/'deadlines.ics'
        target.symlink_to(self.root/'data/jobs.json')
        self.assertEqual(self.run_export()[0], 2); target.unlink()
        os.link(self.root/'data/jobs.json', target)
        self.assertEqual(self.run_export()[0], 2); target.unlink()
        (dest/'escape').symlink_to(self.root/'data', target_is_directory=True)
        self.assertEqual(self.run_export('--output-dir', dest/'escape')[0], 2)
        (dest/'escape').unlink()
        (self.root/'.gitignore').write_text('')
        self.assertEqual(self.run_export()[0], 2)
        (self.root/'.gitignore').write_text('.local-reports/\n')
        target.write_text('tracked')
        subprocess.run(['git', 'add', '-f', str(target)], cwd=self.root, check=True)
        self.assertEqual(self.run_export()[0], 2)
        self.assertEqual(target.read_text(), 'tracked')

    def test_failed_atomic_replace_or_fsync_leaves_old_files_and_cleans_temps(self):
        self.assertEqual(self.run_export()[0], 0)
        before = {p: p.read_bytes() for p in (self.root/'.local-reports').glob('*.ics')}
        for operation in ('replace', 'fsync'):
            with patch('jobwatch.calendar.os.' + operation, side_effect=OSError('simulated failure')):
                self.assertEqual(self.run_export()[0], 2)
            self.assertTrue(all(p.read_bytes() == old for p, old in before.items()))
            self.assertEqual(list((self.root/'.local-reports').glob('.jobwatch-*')), [])
            self.assertFalse((self.root/'.local-reports/.calendar-export.lock').exists())

    def test_active_export_lock_refused_and_empty_source_exports_supported(self):
        dest = self.root/'.local-reports'; dest.mkdir()
        lock = dest/'.calendar-export.lock'; lock.write_text('')
        self.assertEqual(self.run_export()[0], 2)
        self.assertTrue(lock.exists()); self.assertFalse((dest/'deadlines.ics').exists())
        lock.unlink()
        (self.root/'data/jobs.json').write_text('{"schema":1,"postings":{}}')
        (self.root/'data/verifications.json').write_text('{"schema":1,"postings":{}}')
        (self.root/'data/leads.json').write_text('{"schema":1,"vacancies":[]}')
        self.ledger.unlink()  # existing Stage 1 reader treats a missing ledger as empty
        code, out, err = self.run_export()
        self.assertEqual(code, 0, err); self.assertEqual(out.count('0 events:'), 2)
        for path in dest.glob('*.ics'): self.assertEqual(properties(path.read_bytes()), [])

    def test_conflict_or_ambiguous_time_writes_other_events_and_returns_warning_status(self):
        (self.root/'data/leads.json').write_text(json.dumps({'schema': 1, 'vacancies': [lead('2026-11-06')]}))
        code, out, err = self.run_export()
        self.assertEqual(code, 1, err); self.assertIn('conflict', out)
        self.assertIn('0 events:', out); self.assertIn('1 events:', out)
        self.assertNotIn('PRIVATE', out + err)
        (self.root/'data/leads.json').write_text('{"schema":1,"vacancies":[]}')
        (self.root/'data/verifications.json').write_text(json.dumps({'schema': 1, 'postings': verification(deadline('2026-10-29T17:00:00'))}))
        code, out, _ = self.run_export()
        self.assertEqual(code, 1); self.assertIn('timezone', out)
