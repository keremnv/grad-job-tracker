"""Offline security, SSRF, connection-pinning, redirect, and semantic date tests for agent_evidence."""
import socket
import unittest

from jobwatch.postgres.agent_evidence import (
    CorroborationBudget,
    EvidenceValidationError,
    SSRFBlockedError,
    safe_fetch_public_https,
    validate_public_evidence_url,
    validate_semantic_iso_date,
)


class _FakeResp:
    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None):
        self.status = status
        self._body = body
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def getheader(self, name: str, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, n: int | None = None) -> bytes:
        return self._body if n is None else self._body[:n]


class _FakeConn:
    def __init__(self, host, pinned_ip, port, timeout, handler, log):
        self.host = host
        self.pinned_ip = pinned_ip
        self.port = port
        self.timeout = timeout
        self._handler = handler
        self._log = log
        self._resp = None

    def request(self, method, path, headers=None):
        self._log.append((self.host, self.pinned_ip, self.port, path, headers or {}))
        self._resp = self._handler(self.host, self.pinned_ip, path)

    def getresponse(self):
        return self._resp

    def close(self):
        pass


class AgentEvidenceSecurityTests(unittest.TestCase):
    def test_static_and_dns_ssrf_blocks_private_imds_cgnat_v4mapped_and_ports(self):
        for bad in (
            "http://example.co.uk/jobs",
            "https://example.co.uk:8443/jobs",
            "https://example.co.uk:80/jobs",
            "https://user:pass@example.co.uk/jobs",
            "https://localhost/jobs",
            "https://internal.local/jobs",
            "https://metadata.internal/latest",
            "https://127.0.0.1/admin",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.5/secret",
            "https://172.16.0.1/secret",
            "https://192.168.1.1/secret",
            "https://100.64.0.1/cgnat",
            "https://[::1]/admin",
            "https://[::ffff:127.0.0.1]/admin",
            "https://[::ffff:169.254.169.254]/latest/meta-data/",
            "file:///etc/passwd",
        ):
            with self.subTest(url=bad):
                with self.assertRaises(SSRFBlockedError):
                    validate_public_evidence_url(bad)

        # DNS returning ANY non-global IP must block even if a public IP is also returned.
        def mixed_dns(host, port, type=0):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
            ]

        with self.assertRaises(SSRFBlockedError):
            validate_public_evidence_url("https://rebind.example.co.uk/jobs", resolve_dns=True, resolver=mixed_dns)

    def test_connection_time_ip_pinning_and_redirect_ssrf_blocking(self):
        dns_map = {
            "public.example.co.uk": [("93.184.216.34", 443)],
            "careers.example.co.uk": [("93.184.216.35", 443)],
            "evil-rebind.example.co.uk": [("10.0.0.9", 443)],
        }

        def resolver(host, port, type=0):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip, _ in dns_map[host]]

        # 1. Redirect from public HTTPS host to AWS IMDS must be blocked before connecting!
        log: list = []

        def imds_redirect_handler(host, ip, path):
            if host == "public.example.co.uk":
                return _FakeResp(302, b"", {"Location": "https://169.254.169.254/latest/meta-data/"})
            raise AssertionError("Must never connect to IMDS")

        with self.assertRaises(SSRFBlockedError):
            safe_fetch_public_https(
                "https://public.example.co.uk/apply",
                resolver=resolver,
                connection_factory=lambda h, ip, p, t: _FakeConn(h, ip, p, t, imds_redirect_handler, log),
            )
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0][1], "93.184.216.34")

        # 2. Redirect to hostname resolving to RFC1918 private IP must be blocked!
        def priv_redirect_handler(host, ip, path):
            return _FakeResp(301, b"", {"Location": "https://evil-rebind.example.co.uk/secret"})

        with self.assertRaises(SSRFBlockedError):
            safe_fetch_public_https(
                "https://public.example.co.uk/apply",
                resolver=resolver,
                connection_factory=lambda h, ip, p, t: _FakeConn(h, ip, p, t, priv_redirect_handler, log),
            )

        # 3. Valid public redirect pins exact validated IPs, respects truncation and request budget!
        log.clear()

        def ok_handler(host, ip, path):
            if host == "public.example.co.uk":
                return _FakeResp(302, b"", {"Location": "https://careers.example.co.uk/grad-2027"})
            return _FakeResp(200, b"A" * 2048)

        budget = CorroborationBudget(max_requests=2, max_bytes=4096)
        res = safe_fetch_public_https(
            "https://public.example.co.uk/start",
            max_bytes=1024,
            budget=budget,
            resolver=resolver,
            connection_factory=lambda h, ip, p, t: _FakeConn(h, ip, p, t, ok_handler, log),
        )
        self.assertEqual(res.http_status, 200)
        self.assertEqual(res.final_url, "https://careers.example.co.uk/grad-2027")
        self.assertEqual(res.resolved_ip, "93.184.216.35")
        self.assertTrue(res.truncated)
        self.assertEqual(len(res.body), 1024)
        self.assertEqual(res.redirects, 1)
        self.assertEqual([entry[1] for entry in log], ["93.184.216.34", "93.184.216.35"])
        # Budget exhausted on 3rd request
        with self.assertRaises(EvidenceValidationError):
            safe_fetch_public_https(
                "https://careers.example.co.uk/grad-2027",
                budget=budget,
                resolver=resolver,
                connection_factory=lambda h, ip, p, t: _FakeConn(h, ip, p, t, ok_handler, log),
            )

    def test_semantic_calendar_dates_reject_impossible_dates(self):
        self.assertEqual(validate_semantic_iso_date("2026-11-29"), "2026-11-29")
        self.assertIsNone(validate_semantic_iso_date(None))
        for bad in ("2026-02-30", "2026-02-29", "2026-04-31", "2026-13-01", "2026-00-10", "29-11-2026"):
            with self.subTest(bad=bad):
                with self.assertRaises(EvidenceValidationError):
                    validate_semantic_iso_date(bad)


if __name__ == "__main__":
    unittest.main()
