import unittest
from jobwatch.feeds import Posting
from jobwatch.state import merge
from jobwatch.timing import analyze_posting_deadlines, deadline_evidence, deadline_window, parse_prose_deadline, stale
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

    def test_parse_prose_deadline_handles_start_dates_missing_years_and_noise(self):
        gc = parse_prose_deadline(
            "Start date: September 2027 Application deadline : Midnight, 29th November 2026 "
            "We encourage you to apply early, as we may close applications before the advertised deadline."
        )
        self.assertEqual(gc['kind'], 'explicit_date')
        self.assertEqual(gc['candidate_date'], '2026-11-29')
        self.assertEqual(gc['time_wording'].lower(), 'midnight')
        self.assertTrue(gc['early_closure_warning'])
        self.assertFalse(gc['missing_year'])

        oc = parse_prose_deadline("This role has a closing date of November 22, 2026.")
        self.assertEqual(oc['kind'], 'explicit_date')
        self.assertEqual(oc['candidate_date'], '2026-11-22')

        dct = parse_prose_deadline("Closing date for applications: Thursday 26th November at 12pm")
        self.assertEqual(dct['kind'], 'missing_year_date')
        self.assertIsNone(dct['candidate_date'])
        self.assertTrue(dct['missing_year'])
        self.assertEqual(dct['time_wording'].lower(), '12pm')

        noise = parse_prose_deadline("Ability to deliver high-quality work to deadline in a fast-paced environment.")
        self.assertEqual(noise['kind'], 'non_application_noise')
        self.assertIsNone(noise['candidate_date'])

        rolling = parse_prose_deadline("We recruit on a rolling basis and there is no fixed closing date.")
        self.assertEqual(rolling['kind'], 'rolling_or_no_deadline')
        self.assertTrue(rolling['rolling'])

        sup_html = parse_prose_deadline(
            "&lt;p&gt;Start date: September 2027&lt;br&gt;Application deadline: Midnight, 29&lt;sup&gt;th&lt;/sup&gt; November 2026&lt;/p&gt;"
        )
        self.assertEqual(sup_html['kind'], 'explicit_date')
        self.assertEqual(sup_html['candidate_date'], '2026-11-29')

    def test_multiline_headings_work_noise_and_conflict_analysis(self):
        palantir = deadline_evidence({}, "<h3>Application deadline</h3><p>We accept applications on an ongoing basis.</p>", "https://example.org", NOW)
        self.assertEqual(len(palantir), 1)
        self.assertIn("ongoing basis", palantir[0]["wording"])
        self.assertNotIn("value", palantir[0])
        self.assertTrue(parse_prose_deadline(palantir[0]["wording"])["rolling"])

        riverlane = deadline_evidence({}, "<p>Deadline</p><p>Please submit your application by November 6<sup>th</sup>, 2026</p>", "https://example.org", NOW)
        self.assertEqual(len(riverlane), 1)
        self.assertEqual(parse_prose_deadline(riverlane[0]["wording"])["candidate_date"], "2026-11-06")

        noise = deadline_evidence({}, "<li>Comfortable working in a deadline-driven environment and delivering work to deadline.</li>", "https://example.org", NOW)
        self.assertEqual(noise, [])

        conflict = analyze_posting_deadlines([
            {"source_field": "application_deadline", "value": "2026-11-29"},
            {"source_field": "description", "wording": "Application deadline: 30 November 2026"},
        ])
        self.assertTrue(conflict["has_date_conflict"])
        self.assertEqual(conflict["all_distinct_dates"], ["2026-11-29", "2026-11-30"])
