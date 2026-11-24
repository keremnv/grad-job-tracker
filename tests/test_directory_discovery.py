"""Offline unit tests for bounded recurring ATS directory discovery."""
import json
import unittest

from jobwatch.discovery.directory_discovery import (
    DiscoveryRateLimitError,
    parse_directory_csv,
    probe_ats_board,
    verify_board_identity,
)


class DirectoryDiscoveryOfflineTests(unittest.TestCase):
    def test_parse_directory_csv_deduplicates_sanitizes_and_flags_uk_hints(self):
        csv_text = (
            "company,slug\n"
            "Oxford Quantum,oxfordquantum\n"
            "Oxford Quantum Dup,OXFORDQUANTUM\n"
            "Bad Slug,../etc/passwd\n"
            "Acme US,acme\n"
            "iwoca,iwoca.co.uk\n"
        )
        boards, skipped = parse_directory_csv(
            "ashby",
            csv_text,
            directory_source="test-dir",
            directory_url="https://example.org/ashby.csv",
        )
        self.assertEqual(skipped, 1)
        self.assertEqual([b.board for b in boards], ["oxfordquantum", "acme", "iwoca.co.uk"])
        self.assertTrue(boards[0].uk_hint)
        self.assertFalse(boards[1].uk_hint)
        self.assertTrue(boards[2].uk_hint)

    def test_greenhouse_two_pass_skips_content_for_non_uk_and_fetches_for_uk(self):
        calls = []

        def fake_get(url, **_kwargs):
            calls.append(url)
            if "nonuk" in url:
                body = json.dumps({
                    "jobs": [{
                        "id": 101,
                        "internal_job_id": 1,
                        "title": "Graduate Software Engineer",
                        "absolute_url": "https://job-boards.greenhouse.io/nonuk/jobs/101",
                        "location": {"name": "San Francisco, CA"},
                    }]
                }).encode()
                return 200, body, url
            if "content=true" not in url:
                body = json.dumps({
                    "jobs": [{
                        "id": 202,
                        "internal_job_id": 2,
                        "title": "Graduate Software Engineer 2027",
                        "absolute_url": "https://job-boards.eu.greenhouse.io/ukco/jobs/202",
                        "location": {"name": "London, UK"},
                    }]
                }).encode()
                return 200, body, url
            body = json.dumps({
                "jobs": [{
                    "id": 202,
                    "internal_job_id": 2,
                    "title": "Graduate Software Engineer 2027",
                    "absolute_url": "https://job-boards.eu.greenhouse.io/ukco/jobs/202",
                    "location": {"name": "London, UK"},
                    "content": "<p>Build distributed Python and C++ systems in London. 2027 graduate intake.</p>",
                    "first_published": "2026-11-20T10:00:00Z",
                }]
            }).encode()
            return 200, body, url

        non_uk = probe_ats_board("greenhouse", "nonuk", company_hint="NonUK", get_bytes=fake_get)
        self.assertEqual(non_uk.probe_status, "active_non_uk")
        self.assertEqual(non_uk.requests_made, 1)
        self.assertEqual(len(non_uk.vacancies), 0)

        uk = probe_ats_board("greenhouse", "ukco", company_hint="UKCo", get_bytes=fake_get)
        self.assertEqual(uk.probe_status, "active_uk_relevant")
        self.assertEqual(uk.region, "eu")
        self.assertEqual(uk.requests_made, 2)
        self.assertEqual(uk.priority_vacancies, 1)
        self.assertEqual(len(uk.vacancies), 1)
        self.assertEqual(uk.vacancies[0].candidate_key, "greenhouse:ukco:202")
        self.assertFalse(uk.vacancies[0].provenance["authoritative_board_observation"])
        self.assertEqual(len(calls), 3)

    def test_lever_eu_fallback_and_429_immediate_halt(self):
        def fake_eu(url, **_kwargs):
            if "api.lever.co" in url:
                return 404, b"", url
            body = json.dumps([{
                "id": "lev-1",
                "text": "Junior Machine Learning Engineer",
                "hostedUrl": "https://jobs.eu.lever.co/euco/lev-1",
                "country": "GB",
                "categories": {"location": "London, United Kingdom", "department": "Engineering"},
                "descriptionPlain": "Entry-level ML engineering role in London.",
            }]).encode()
            return 200, body, url

        res = probe_ats_board("lever", "euco", company_hint="EUCo", get_bytes=fake_eu)
        self.assertEqual(res.region, "eu")
        self.assertEqual(res.probe_status, "active_uk_relevant")
        self.assertEqual(res.priority_vacancies, 1)

        with self.assertRaises(DiscoveryRateLimitError):
            probe_ats_board("ashby", "throttled", get_bytes=lambda u, **k: (429, b"", u))

    def test_verify_board_identity_corroboration_and_hosted_only(self):
        def fake_verified(url, **_kwargs):
            if "jobs.ashbyhq.com" in url:
                return 200, b'<a href="https://acmelabs.co.uk">Website</a>', url
            if url.endswith("/careers"):
                return 200, b'<a href="https://jobs.ashbyhq.com/acmelabs">Open Roles</a>', url
            return 200, b"<html>Welcome to Acme Labs</html>", url

        ok = verify_board_identity("ashby", "acmelabs", get_bytes=fake_verified)
        self.assertEqual(ok.status, "verified_official_link")
        self.assertEqual(ok.official_website, "https://acmelabs.co.uk")
        self.assertEqual(ok.careers_url, "https://acmelabs.co.uk/careers")

        def fake_js_only(url, **_kwargs):
            if "jobs.ashbyhq.com" in url:
                return 200, b'<a href="https://spa.example">Home</a>', url
            return 200, b"<div id='root'></div>", url

        hosted = verify_board_identity("ashby", "spaco", get_bytes=fake_js_only)
        self.assertEqual(hosted.status, "hosted_link_only")
        self.assertEqual(hosted.official_website, "https://spa.example")


if __name__ == "__main__":
    unittest.main()
