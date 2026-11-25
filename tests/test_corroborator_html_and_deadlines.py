"""Regression tests for HTML normalisation, deadline hygiene, Ocado URL fix, and corroborator HTML extraction."""
from __future__ import annotations

import unittest

from jobwatch import feeds, filtering, timing
from jobwatch.postgres.agent_evidence import (
    _extract_html_evidence,
    _links_corroborate_board,
    _links_match_target_url,
)


class TestOcadoUrlCanonicalization(unittest.TestCase):
    def test_collapses_duplicate_identical_gh_jid_preserving_posting_key(self):
        raw_board = {
            "meta": {"total": 1},
            "jobs": [{
                "id": 5036245101,
                "internal_job_id": 99001,
                "title": "Hardware Engineering Graduate - Robotics & Automation 2027",
                "absolute_url": "https://careers.ocadogroup.com/jobs/details?gh_jid=5036245101&gh_jid=5036245101",
                "location": {"name": "Hatfield, United Kingdom"},
                "content": "<p>Graduate role</p>",
            }],
        }
        rows = feeds.collect_board(
            {"company": "Ocado Group", "provider": "greenhouse", "board": "ocadogroup", "region": "eu"},
            get_json=lambda _: raw_board,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, "greenhouse:ocadogroup:5036245101")
        self.assertEqual(rows[0].url, "https://careers.ocadogroup.com/jobs/details?gh_jid=5036245101")

    def test_rejects_mismatched_duplicate_gh_jid(self):
        raw_board = {
            "meta": {"total": 1},
            "jobs": [{
                "id": 5036245101,
                "internal_job_id": 99001,
                "title": "Graduate Role",
                "absolute_url": "https://careers.ocadogroup.com/jobs/details?gh_jid=5036245101&gh_jid=9999999999",
                "location": {"name": "Hatfield, United Kingdom"},
                "content": "<p>Graduate role</p>",
            }],
        }
        with self.assertRaisesRegex(ValueError, "custom hosts"):
            feeds.collect_board(
                {"company": "Ocado Group", "provider": "greenhouse", "board": "ocadogroup", "region": "eu"},
                get_json=lambda _: raw_board,
            )


class TestHtmlAndDeadlineHygiene(unittest.TestCase):
    def test_graphcore_inline_sup_strong_and_double_nbsp(self):
        html_no_year = (
            "&lt;p&gt;Start date: September 2027&lt;br&gt;"
            "&lt;strong&gt;Application deadline:&lt;/strong&gt; Midnight 29&lt;sup&gt;th&lt;/sup&gt; November&lt;br&gt;"
            "&lt;em&gt;We encourage you to apply early, as we may close applications before the advertised deadline.&lt;/em&gt;&lt;/p&gt;"
        )
        ev = timing.deadline_evidence({}, html_no_year, "https://job-boards.greenhouse.io/graphcore/jobs/1", "2026-11-25T00:00:00Z")
        wordings = [e["wording"] for e in ev]
        self.assertIn("Application deadline: Midnight 29th November", wordings)
        parsed = timing.parse_prose_deadline(wordings[0])
        self.assertEqual(parsed["kind"], "missing_year_date")
        self.assertIsNone(parsed["candidate_date"])
        self.assertTrue(parsed["missing_year"])
        self.assertEqual(parsed["time_wording"].lower(), "midnight")

        html_with_year = (
            "&lt;strong&gt;Application deadline&lt;/strong&gt;: Midnight, 29th November 2026&amp;nbsp;&lt;br&gt;"
            "&lt;em&gt;We encourage you to apply early.&lt;/em&gt;"
        )
        ev2 = timing.deadline_evidence({}, html_with_year, "https://job-boards.greenhouse.io/graphcore/jobs/2", "2026-11-25T00:00:00Z")
        self.assertEqual(ev2[0]["wording"], "Application deadline: Midnight, 29th November 2026")
        p2 = timing.parse_prose_deadline(ev2[0]["wording"])
        self.assertEqual(p2["kind"], "explicit_date")
        self.assertEqual(p2["candidate_date"], "2026-11-29")

    def test_stripe_dmy_with_of_and_cambridge_double_escaped_rolling(self):
        stripe_html = "&lt;ul&gt;&lt;li&gt;&lt;strong&gt;Applications close:&lt;/strong&gt; 30th of November 2026&lt;/li&gt;&lt;/ul&gt;"
        ev = timing.deadline_evidence({}, stripe_html, "https://stripe.com/jobs/search?gh_jid=1", "2026-11-25T00:00:00Z")
        self.assertEqual(ev[0]["wording"], "Applications close: 30th of November 2026")
        self.assertEqual(timing.parse_prose_deadline(ev[0]["wording"])["candidate_date"], "2026-11-30")

        cc_html = "&lt;p&gt;&lt;strong&gt;&lt;span data-contrast=&quot;auto&quot;&gt;There is no deadline for this role, as we recruit on a rolling basis.&lt;/span&gt;&lt;/strong&gt;&lt;/p&gt;"
        ev_cc = timing.deadline_evidence({}, cc_html, "https://job-boards.greenhouse.io/cc/jobs/1", "2026-11-25T00:00:00Z")
        self.assertEqual(ev_cc[0]["wording"], "There is no deadline for this role, as we recruit on a rolling basis.")
        self.assertNotIn("&lt;", ev_cc[0]["wording"])
        self.assertEqual(timing.parse_prose_deadline(ev_cc[0]["wording"])["kind"], "rolling_or_no_deadline")

    def test_workable_missing_year_24h_time_never_invents_year(self):
        html_doc = "<p>Closing date: 9th December at 1300.</p>"
        ev = timing.deadline_evidence({}, html_doc, "https://apply.workable.com/j/ABC", "2026-11-25T00:00:00Z")
        self.assertEqual(ev[0]["wording"], "Closing date: 9th December at 1300.")
        parsed = timing.parse_prose_deadline(ev[0]["wording"])
        self.assertEqual(parsed["kind"], "missing_year_date")
        self.assertIsNone(parsed["candidate_date"])
        self.assertEqual(parsed["time_wording"], "1300")

    def test_filtering_plain_strips_script_and_style(self):
        doc = "<style>.a { color: red; }</style><script>const deadline = '2026-12-01';</script><p>29<sup>th</sup> November</p>"
        self.assertEqual(filtering.plain(doc), "29th November")


class TestCorroboratorHtmlExtraction(unittest.TestCase):
    def test_anima_astro_fixture_excludes_scripts_and_extracts_ashby_link(self):
        anima_html = """<!doctype html>
        <html><head><title>Careers | Anima</title>
        <style>astro-island{display:contents}</style>
        <script>(() => { window.dataLayer = []; const fake = "Application deadline: 2026-12-31"; })();</script>
        </head><body>
        <main><h1>Your work is for better care</h1>
        <p>Join clinicians, engineers and colleagues from across healthcare.</p>
        <a href="https://jobs.ashbyhq.com/Anima">Apply for a role</a>
        </main></body></html>"""
        ext = _extract_html_evidence(anima_html, base_url="https://www.animahealth.com/company/careers")
        self.assertNotIn("dataLayer", ext["visible_text"])
        self.assertNotIn("astro-island", ext["visible_text"])
        self.assertNotIn("2026-12-31", ext["visible_text"])
        self.assertIn("Your work is for better care", ext["visible_text"])
        self.assertIn("Apply for a role", ext["visible_text"])
        self.assertNotIn("jobs.ashbyhq.com/anima", ext["visible_text"].lower())

        link_match = _links_corroborate_board(ext["links"], "ashby", "anima")
        self.assertIsNotNone(link_match)
        self.assertEqual(link_match["matched_via"], "a_href")
        self.assertEqual(link_match["matched_url"], "https://jobs.ashbyhq.com/Anima")

    def test_greenhouse_iframe_and_official_source_link_matching(self):
        html_doc = """<html><body><h1>Careers at Example Co</h1>
        <iframe src="https://boards.greenhouse.io/embed/job_board?for=exampleco"></iframe>
        <a href="/jobs/grad-2027">Graduate Roles</a>
        </body></html>"""
        ext = _extract_html_evidence(html_doc, base_url="https://www.example.co.uk/careers")
        gh = _links_corroborate_board(ext["links"], "greenhouse", "exampleco")
        self.assertIsNotNone(gh)
        self.assertEqual(gh["matched_via"], "iframe_src")
        rel = _links_match_target_url(ext["links"], "https://www.example.co.uk/jobs/grad-2027")
        self.assertIsNotNone(rel)
        self.assertEqual(rel["match_type"], "exact_url")


if __name__ == "__main__":
    unittest.main()
