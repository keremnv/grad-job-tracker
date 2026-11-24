"""Offline unit tests for the isolated ATS discovery feasibility experiments."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from experiments.ats_discovery import workable_discovery as wd
from jobwatch import filtering


SAMPLE_SEARCH_JOB = {
    "id": "11111111-2222-3333-4444-555555555555",
    "title": "Graduate Machine Learning Engineer",
    "state": "published",
    "department": "Engineering",
    "employmentType": "Full-time",
    "workplace": "hybrid",
    "locations": ["London, England, United Kingdom"],
    "location": {"city": "London", "subregion": "England", "countryName": "United Kingdom"},
    "created": "2026-11-20T10:00:00.000Z",
    "updated": "2026-11-20T10:00:00.000Z",
    "url": "https://jobs.workable.com/view/abc123/graduate-machine-learning-engineer-at-acme-ai",
    "description": "<p>Build Python machine learning systems for 2027 graduates.</p>",
    "requirementsSection": "<p>Computer Science degree or equivalent.</p>",
    "benefitsSection": "<p>Competitive salary.</p>",
    "company": {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "title": "Acme AI",
        "website": "https://www.acmeai.co.uk",
        "url": "https://jobs.workable.com/company/xyz789/jobs-at-acme-ai",
    },
}

SAMPLE_WIDGET_RESPONSE = {
    "name": "Acme AI Ltd",
    "description": "AI company",
    "jobs": [
        {
            "title": "Graduate Machine Learning Engineer",
            "shortcode": "AB12CD34EF",
            "employment_type": "Full-time",
            "telecommuting": False,
            "department": "Engineering",
            "url": "https://apply.workable.com/j/AB12CD34EF",
            "shortlink": "https://apply.workable.com/j/AB12CD34EF",
            "application_url": "https://apply.workable.com/j/AB12CD34EF/apply",
            "published_on": "2026-11-20",
            "created_at": "2026-11-20",
            "country": "United Kingdom",
            "city": "London",
            "state": "England",
            "locations": [{"country": "United Kingdom", "countryCode": "GB", "city": "London", "region": "England"}],
            "description": "<p>Build Python machine learning systems for 2027 graduates.</p>",
        }
    ],
}


class WorkableDiscoveryTests(unittest.TestCase):
    def test_search_hit_screens_with_unchanged_production_screener(self):
        inputs = wd.search_hit_to_screening_inputs(SAMPLE_SEARCH_JOB)
        result = filtering.screen(inputs)
        self.assertEqual(result["verdict"], "priority")
        self.assertEqual(result["family"], "ml_ai")
        self.assertIn("location.uk", result["codes"])
        self.assertIn("level.early_title", result["codes"])

    def test_candidate_account_slugs_extracts_jobs_at_and_domain_stem(self):
        slugs = wd.candidate_account_slugs(SAMPLE_SEARCH_JOB["company"])
        self.assertEqual(slugs, ["acme-ai", "acmeai"])

    def test_match_widget_posting_accepts_corroborated_match_and_rejects_mismatch(self):
        matched, basis = wd.match_widget_posting(SAMPLE_SEARCH_JOB, "acme-ai", SAMPLE_WIDGET_RESPONSE)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["shortcode"], "AB12CD34EF")
        self.assertEqual(basis, "exact_title_and_company")

        wrong_company = {**SAMPLE_WIDGET_RESPONSE, "name": "Unrelated Corp"}
        no_match, reason = wd.match_widget_posting(SAMPLE_SEARCH_JOB, "acme-ai", wrong_company)
        self.assertIsNone(no_match)
        self.assertEqual(reason, "company_name_mismatch")

    def test_group_widget_jobs_merges_multi_location_duplicate_shortcodes(self):
        ireland_entry = {
            **SAMPLE_WIDGET_RESPONSE["jobs"][0],
            "country": "Ireland",
            "city": "",
            "state": "",
            "locations": [{"country": "Ireland", "countryCode": "IE", "city": "", "region": None}],
        }
        uk_entry = {
            **SAMPLE_WIDGET_RESPONSE["jobs"][0],
            "country": "United Kingdom",
            "city": "",
            "state": "",
            "locations": [{"country": "United Kingdom", "countryCode": "GB", "city": "", "region": None}],
        }
        multi_payload = {"name": "Acme AI Ltd", "jobs": [ireland_entry, uk_entry]}
        matched, basis = wd.match_widget_posting(SAMPLE_SEARCH_JOB, "acme-ai", multi_payload)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["shortcode"], "AB12CD34EF")
        self.assertEqual(basis, "exact_title_company_merged_locations")
        self.assertEqual(len(matched["locations"]), 2)
        posting = wd.widget_job_to_posting("acme-ai", "Acme AI Ltd", matched)
        self.assertIn("United Kingdom", posting.countries)
        self.assertIn("Ireland", posting.countries)
        scr = filtering.screen(filtering.inputs_from_posting(posting))
        self.assertEqual(scr["verdict"], "priority")
        self.assertIn("location.uk_multi_country", scr["codes"])

    def test_widget_job_to_posting_validates_url_and_builds_canonical_key(self):
        posting = wd.widget_job_to_posting("acme-ai", "Acme AI Ltd", SAMPLE_WIDGET_RESPONSE["jobs"][0])
        self.assertEqual(posting.key, "workable:acme-ai:AB12CD34EF")
        self.assertEqual(posting.url, "https://apply.workable.com/j/AB12CD34EF")
        self.assertIn("United Kingdom", posting.countries)

        bad = {**SAMPLE_WIDGET_RESPONSE["jobs"][0], "url": "https://evil.example/j/AB12CD34EF"}
        with self.assertRaises(ValueError):
            wd.widget_job_to_posting("acme-ai", "Acme AI Ltd", bad)

    def test_default_queries_are_unseeded(self):
        serialized = str(wd.DEFAULT_QUERIES).lower()
        self.assertNotIn("stalis", serialized)
        self.assertNotIn("1c9ccea6b4", serialized)

    def test_run_experiment_deduplicates_and_stops_cleanly_on_429(self):
        calls = []

        def fake_get(url, telemetry=None):
            calls.append(url)
            if telemetry is not None:
                telemetry.requests += 1
            if "jobs.workable.com/api/v1/jobs" in url:
                return 200, {"totalSize": 1, "jobs": [SAMPLE_SEARCH_JOB], "nextPageToken": None}
            if "apply.workable.com/api/v1/widget/accounts/acme-ai" in url:
                return 200, SAMPLE_WIDGET_RESPONSE
            if telemetry is not None:
                telemetry.rate_limited = True
                telemetry.http_errors["429"] = 1
            return 429, {}

        queries = (
            {"query": "graduate ai", "location": "United Kingdom", "max_pages": 1},
            {"query": "graduate ml", "location": "United Kingdom", "max_pages": 1},
        )
        res = wd.run_experiment(
            queries=queries,
            max_search_requests=5,
            max_widget_requests=5,
            max_resolve_candidates=5,
            get_json=fake_get,
            sleep_fn=lambda _: None,
        )
        self.assertEqual(res["metrics"]["raw_search_hits"], 2)
        self.assertEqual(res["metrics"]["duplicate_hits"], 1)
        self.assertEqual(res["metrics"]["unique_vacancies"], 1)
        self.assertEqual(res["metrics"]["widget_resolution_succeeded"], 1)
        self.assertEqual(res["resolved_records"][0]["posting_key"], "workable:acme-ai:AB12CD34EF")


if __name__ == "__main__":
    unittest.main()
