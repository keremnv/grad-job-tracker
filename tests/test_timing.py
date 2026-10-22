import unittest
from jobwatch.feeds import Posting
from jobwatch.state import merge
from jobwatch.timing import deadline_evidence, deadline_window, stale
from jobwatch.cli import report

NOW='2026-10-17T12:00:00Z'


class TimingTests(unittest.TestCase):
    def test_explicit_offsets_date_only_and_14_day_boundary(self):
        for value,expected in [('2026-10-31T13:00:00+01:00','within 14 days'),('2026-10-31T13:00:01+01:00','later than 14 days'),('2026-10-17T11:00:00Z','source timestamp passed'),('2026-10-31','within 14 calendar days'),('2026-11-01','later than 14 calendar days'),('2026-10-31T23:59:59','timezone-free'),('31 October 2026','unparseable')]:
            with self.subTest(value=value): self.assertIn(expected,deadline_window({'value':value},NOW))

    def test_absent_prose_and_explicit_no_deadline_never_invented(self):
        self.assertEqual(deadline_evidence({},'Start in September 2027','https://example.org',NOW),[])
        for wording in ('There is no closing date for this job.','Applications close 31 October.','Apply by Friday at noon.'):
            evidence=deadline_evidence({},wording,'https://example.org',NOW)[0]
            self.assertEqual(evidence['wording'],wording); self.assertNotIn('value',evidence)
            self.assertIn('exact deadline unknown',deadline_window(evidence,NOW))
        evidence=deadline_evidence({'application_deadline':'2026-10-31T16:00:00+01:00'},'','https://example.org',NOW)[0]
        self.assertEqual(evidence['observed_at'],NOW);self.assertEqual(evidence['wording'],'2026-10-31T16:00:00+01:00')

    def test_offer_deadlines_are_not_application_deadlines(self):
        self.assertEqual(deadline_evidence({}, "Offer Deadline\nOur offer deadline expectations.", "https://example.org", NOW), [])

    def test_deadline_changes_observation_only_no_noise_and_source_description(self):
        def p(value,desc='Original requirement'):
            return Posting('X','greenhouse','x','1','Graduate Software Engineer','UK','https://job-boards.greenhouse.io/x/jobs/1',description=desc,raw={'application_deadline':value})
        s={'schema':1,'postings':{}}
        s,_=merge(s,{'greenhouse:x':[p('2026-10-31')]},NOW)
        s,events=merge(s,{'greenhouse:x':[p('2026-10-31')]},'2026-10-18T12:00:00Z');self.assertEqual(events,[])
        s,events=merge(s,{'greenhouse:x':[p('2026-11-01','Changed conditions')]},'2026-10-19T12:00:00Z')
        self.assertIn('deadlines',events[0]['changes']);self.assertIn('description_sha256',events[0]['changes'])
        self.assertEqual(s['postings']['greenhouse:x:1']['first_seen'],NOW)

    def test_stale_report_as_of_does_not_refresh_scan_time(self):
        self.assertTrue(stale(NOW,'2026-10-21T12:00:00Z'));self.assertFalse(stale(NOW,'2026-10-20T12:00:00Z'));self.assertTrue(stale(None,NOW))
        s={'schema':1,'postings':{},'boards':{'lever:x':{'status':'success','last_attempt':NOW,'last_success':NOW,'count':0}},'last_scan':{'at':NOW,'events':[],'failures':[]}}
        out=report(s,as_of='2026-10-21T12:00:00Z')
        self.assertIn('STALE',out);self.assertIn('Last scan attempt (UTC): '+NOW,out)
