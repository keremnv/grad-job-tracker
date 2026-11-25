"""Hardened isolated prototype for agent-contributed public evidence ingestion & corroboration.

NOT enabled in production. Enforces:
1. Controlled DB boundary: calls SECURITY DEFINER `submit_agent_observation` and
   `submit_agent_vacancy` (zero raw table INSERT/UPDATE/DELETE grants for
   `jobwatch_agent_evidence`). All hashes (`submission_sha256`, `payload_sha256`,
   `complete_description_sha256`, `excerpt_sha256`, `evidence_sha256`) and
   `candidate_key` are derived strictly inside PostgreSQL — never trusted from callers.
2. Untrusted screening separation: `submit_agent_vacancy` never accepts caller-supplied
   screening verdicts, reason codes, or rules versions; new agent vacancies enter with
   `rules_version='unscreened-agent-submission'` until the trusted collector screener runs.
3. Scoped idempotency & transactional budgets: `UNIQUE (db_login, idempotency_key)` and
   advisory-locked hourly/daily budgets keyed on authenticated `session_user`.
4. Network security (real SSRF protection beyond static URL checks):
   - HTTPS-only, port allowlist (`443` only), no URL credentials, public FQDN.
   - DNS resolution checked for EVERY IP (`ip.is_global`, including `ipv4_mapped` IPv6).
   - Connection-time IP pinning (`socket.create_connection((pinned_ip, port))` with TLS
     `server_hostname=host`), completely defeating DNS rebinding TOCTOU.
   - Hop-by-hop redirect validation (`max_redirects=3`), bounded read (`<= 512 KiB`),
     and per-run `CorroborationBudget`.
5. Append-only 6-state corroboration history (`agent_evidence_corroborations`) with
   strict target-vacancy identity binding (`bound_exact_url` / `bound_host_and_identity` /
   `unbound_target`), explicit `evidence_completeness`, `evidence_excerpt`, and `provenance`.
   An unrelated page containing similar wording can NEVER corroborate a vacancy!
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from jobwatch import filtering
from jobwatch.discovery.directory_discovery import _html_corroborates_board
from jobwatch.identity import exact_url
from jobwatch.timing import parse_prose_deadline
from .collection import iso, safe
from .evidence import encode_evidence

ALLOWED_KINDS = frozenset({
    "new_vacancy",
    "official_source_link",
    "deadline_wording",
    "posting_evidence",
    "source_identity",
})
ALLOWED_COMPLETENESS = frozenset({"complete", "partial", "truncated", "unavailable"})
ALLOWED_RETENTION_POLICIES = frozenset({"complete_public_ats", "excerpt_only_terms_restricted", "metadata_only"})
MAX_WORDING_CHARS = 4000
MAX_SUMMARY_CHARS = 500
MAX_COMPLETE_DESC_CHARS = 262_144
MAX_EXCERPT_CHARS = 2500
MAX_PAYLOAD_BYTES = 8192
MAX_CORROBORATION_FETCH_BYTES = 512 * 1024
ALLOWED_PORTS = frozenset({443})
LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
JS_SHELL_MARKERS = re.compile(
    r"enable javascript|javascript is required|you need to enable javascript|checking your browser|just a moment",
    re.I,
)
_MULTI_PART_TLDS = frozenset({"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.nz", "co.jp"})


class EvidenceValidationError(ValueError):
    """Raised on invalid, untrusted, SSRF-risk, semantically invalid, or rate-limited input."""


class IdempotencyConflictError(EvidenceValidationError):
    """Raised when an idempotency_key is reused with materially different content."""


class CandidateIdentityConflictError(EvidenceValidationError):
    """Raised when a submission conflicts with an existing discovery_candidates identity."""


class SSRFBlockedError(EvidenceValidationError):
    """Raised when a URL, port, redirect target, or resolved IP fails SSRF checks."""


def _is_public_global_ip(raw_ip: str) -> bool:
    """Strictly require globally routable public IP (blocks RFC1918, loopback, IMDS, CGNAT, v4-mapped v6)."""
    ip_obj = ipaddress.ip_address(raw_ip)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped
    return bool(
        ip_obj.is_global
        and not ip_obj.is_private
        and not ip_obj.is_loopback
        and not ip_obj.is_link_local
        and not ip_obj.is_multicast
        and not ip_obj.is_reserved
        and not ip_obj.is_unspecified
    )


def _registrable_domain(host: str) -> str:
    h = (host or "").lower().strip(".")
    parts = [p for p in h.split(".") if p]
    if len(parts) <= 2:
        return h
    tail2 = ".".join(parts[-2:])
    if tail2 in _MULTI_PART_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail2


def validate_public_evidence_url(
    url: str,
    *,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
    resolve_dns: bool = False,
    resolver=socket.getaddrinfo,
) -> tuple[str, str, int, list[str]]:
    """Validate URL syntax/scheme/port/host and (optionally) resolve + validate EVERY IP."""
    if not isinstance(url, str):
        raise SSRFBlockedError("URL must be a string")
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 2048:
        raise SSRFBlockedError("URL missing or exceeds 2048 characters")
    parsed = urlsplit(cleaned)
    if parsed.scheme != "https":
        raise SSRFBlockedError("Only https:// public URLs are accepted")
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise SSRFBlockedError("Credentials in URL are forbidden")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise SSRFBlockedError("Invalid URL port") from exc
    if port not in allowed_ports:
        raise SSRFBlockedError(f"Port {port} is not in allowed ports {sorted(allowed_ports)}")
    host = (parsed.hostname or "").lower().strip(".")
    if not host or host in LOCAL_HOSTNAMES or host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        raise SSRFBlockedError("Local or internal hostnames are forbidden")
    if "." not in host:
        raise SSRFBlockedError("Hostname must be a public FQDN")

    try:
        ipaddress.ip_address(host)
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False

    if is_literal_ip:
        if not _is_public_global_ip(host):
            raise SSRFBlockedError(f"Non-global IP literal blocked: {host}")
        return cleaned, host, port, [host]

    validated_ips: list[str] = []
    if resolve_dns:
        try:
            infos = resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise SSRFBlockedError("DNS resolution failed") from exc
        if not infos:
            raise SSRFBlockedError("DNS returned no addresses")
        seen: set[str] = set()
        for _fam, _typ, _proto, _canon, sockaddr in infos:
            ip_str = sockaddr[0]
            if not _is_public_global_ip(ip_str):
                raise SSRFBlockedError(f"Host {host} resolved to non-global IP {ip_str} (SSRF blocked)")
            if ip_str not in seen:
                seen.add(ip_str)
                validated_ips.append(ip_str)
    return cleaned, host, port, validated_ips


def validate_semantic_iso_date(value: str | None) -> str | None:
    """Validate YYYY-MM-DD semantically (rejects 2026-02-30, 2026-13-01, etc.)."""
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        raise EvidenceValidationError("candidate_deadline_date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceValidationError(f"Semantically invalid calendar date: {value}") from exc
    return parsed.isoformat()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection connecting directly to a pre-validated IP with TLS SNI/cert verification."""

    def __init__(self, host: str, pinned_ip: str, port: int = 443, *, timeout: float = 10.0, context=None):
        super().__init__(host, port=port, timeout=timeout, context=context or ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self):
        raw_sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw_sock, server_hostname=self.host)
        except Exception:
            raw_sock.close()
            raise


@dataclass
class CorroborationBudget:
    max_requests: int = 20
    max_bytes: int = 4 * 1024 * 1024
    requests_used: int = 0
    bytes_used: int = 0

    def consume_request(self):
        if self.requests_used >= self.max_requests:
            raise EvidenceValidationError("Corroboration request budget exhausted")
        self.requests_used += 1

    def consume_bytes(self, n: int):
        self.bytes_used += n
        if self.bytes_used > self.max_bytes:
            raise EvidenceValidationError("Corroboration byte transfer budget exhausted")


@dataclass(frozen=True)
class SafeFetchResult:
    http_status: int
    body: bytes
    final_url: str
    resolved_ip: str
    truncated: bool
    redirects: int
    error_kind: str = ""  # "", "timeout", "network_error", "ssrf_refused"


def safe_fetch_public_https(
    url: str,
    *,
    max_bytes: int = MAX_CORROBORATION_FETCH_BYTES,
    timeout: float = 10.0,
    max_redirects: int = 3,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
    budget: CorroborationBudget | None = None,
    resolver=socket.getaddrinfo,
    connection_factory=None,
) -> SafeFetchResult:
    """SSRF-hardened HTTPS fetcher with connection-time IP pinning and hop-by-hop redirect validation.

    Intended ONLY for an isolated, network-restricted corroboration worker (never a privileged DB/owner process).
    """
    current_url = url
    redirects = 0
    while True:
        cleaned, host, port, ips = validate_public_evidence_url(
            current_url, allowed_ports=allowed_ports, resolve_dns=True, resolver=resolver,
        )
        pinned_ip = ips[0]
        if budget is not None:
            budget.consume_request()
        parsed = urlsplit(cleaned)
        path_q = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        conn = (
            connection_factory(host, pinned_ip, port, timeout)
            if connection_factory is not None
            else _PinnedHTTPSConnection(host, pinned_ip, port, timeout=timeout)
        )
        try:
            conn.request(
                "GET",
                path_q,
                headers={
                    "Host": host if port == 443 else f"{host}:{port}",
                    "User-Agent": "GraduateJobWatch-Corroborator/2.0",
                    "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                },
            )
            resp = conn.getresponse()
            status = int(resp.status)
            location = resp.getheader("Location")
            if status in (301, 302, 303, 307, 308) and location:
                resp.read(1024)
                if redirects >= max_redirects:
                    return SafeFetchResult(status, b"", cleaned, pinned_ip, False, redirects, error_kind="network_error")
                current_url = urljoin(cleaned, location.strip())
                redirects += 1
                continue
            raw = resp.read(max_bytes + 1)
            truncated = len(raw) > max_bytes
            body = raw[:max_bytes]
            if budget is not None:
                budget.consume_bytes(len(body))
            return SafeFetchResult(status, body, cleaned, pinned_ip, truncated, redirects)
        except TimeoutError:
            return SafeFetchResult(0, b"", cleaned, pinned_ip, False, redirects, error_kind="timeout")
        except OSError:
            return SafeFetchResult(0, b"", cleaned, pinned_ip, False, redirects, error_kind="network_error")
        finally:
            try:
                conn.close()
            except Exception:
                pass


@dataclass(frozen=True)
class ObservationSubmission:
    kind: str
    logical_contributor: str
    observed_at: datetime
    source_url: str
    claim_summary: str
    claimed_actor: str = ""
    observed_wording: str = ""
    candidate_deadline_date: str | None = None
    posting_key: str | None = None
    candidate_key: str | None = None
    board_key: str | None = None
    source_id: int | None = None
    lead_key: str | None = None
    payload: dict | None = None
    idempotency_key: str | None = None


class AgentEvidenceIngestor:
    """Controlled ingestor invoking SECURITY DEFINER SQL boundaries under `jobwatch_agent_evidence`.

    All cryptographic hashes, candidate keys, and unscreened markers are derived inside PostgreSQL.
    """

    def __init__(self, *, resolve_dns: bool = False, resolver=socket.getaddrinfo):
        self.resolve_dns = resolve_dns
        self.resolver = resolver

    def _validate_observation(self, sub: ObservationSubmission, now: datetime) -> dict:
        if sub.kind not in ALLOWED_KINDS:
            raise EvidenceValidationError(f"Unsupported evidence kind: {sub.kind!r}")
        logical = safe(sub.logical_contributor or "").strip()
        if not 3 <= len(logical) <= 128:
            raise EvidenceValidationError("logical_contributor must be 3..128 characters")
        claimed = safe(sub.claimed_actor or "").strip()
        if len(claimed) > 128:
            raise EvidenceValidationError("claimed_actor exceeds 128 characters")
        iso(sub.observed_at)
        if sub.observed_at > now + timedelta(minutes=5):
            raise EvidenceValidationError("Future observation timestamps are forbidden")
        cleaned_url, _host, _port, _ips = validate_public_evidence_url(
            sub.source_url, resolve_dns=self.resolve_dns, resolver=self.resolver,
        )
        summary = safe(sub.claim_summary or "").strip()
        if not 3 <= len(summary) <= MAX_SUMMARY_CHARS:
            raise EvidenceValidationError("claim_summary must be 3..500 characters")
        wording = safe(sub.observed_wording or "").strip()
        if len(wording) > MAX_WORDING_CHARS:
            raise EvidenceValidationError(f"observed_wording exceeds {MAX_WORDING_CHARS} characters")
        if sub.kind == "deadline_wording" and not wording:
            raise EvidenceValidationError("deadline_wording requires verbatim observed_wording")
        if sub.kind in ("posting_evidence", "deadline_wording") and not any((sub.posting_key, sub.candidate_key, sub.lead_key)):
            raise EvidenceValidationError("posting_evidence and deadline_wording require a target key")

        cand_date = validate_semantic_iso_date(sub.candidate_deadline_date)
        if cand_date is None and sub.kind == "deadline_wording":
            cand_date = validate_semantic_iso_date(parse_prose_deadline(wording).get("candidate_date"))

        payload = safe(sub.payload or {})
        if not isinstance(payload, dict):
            raise EvidenceValidationError("payload must be a JSON object")
        encoded_payload = encode_evidence(payload)
        if len(encoded_payload) > MAX_PAYLOAD_BYTES:
            raise EvidenceValidationError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")

        idem = (sub.idempotency_key or "").strip()
        if not idem:
            # Include observed_at so genuine repeat observations at later times remain distinct.
            idem = sha256(encode_evidence({
                "kind": sub.kind, "url": cleaned_url, "posting_key": sub.posting_key,
                "candidate_key": sub.candidate_key, "board_key": sub.board_key,
                "source_id": sub.source_id, "lead_key": sub.lead_key,
                "summary": summary, "wording": wording, "date": cand_date,
                "payload": payload, "logical": logical, "observed_at": iso(sub.observed_at),
            })).hexdigest()
        elif not 8 <= len(idem) <= 128:
            raise EvidenceValidationError("idempotency_key must be 8..128 characters")

        return {
            "logical": logical, "claimed": claimed, "url": cleaned_url, "summary": summary,
            "wording": wording, "cand_date": cand_date, "payload": payload, "idem": idem,
        }

    def record_observation(self, conn, sub: ObservationSubmission, *, now: datetime | None = None) -> dict:
        import psycopg
        from psycopg.types.json import Jsonb

        at = now or datetime.now(timezone.utc)
        v = self._validate_observation(sub, at)
        try:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM jobwatch.submit_agent_observation("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        v["idem"], sub.kind, v["logical"], v["claimed"],
                        sub.observed_at, v["url"], v["summary"], v["wording"], v["cand_date"],
                        sub.posting_key, sub.candidate_key, sub.board_key, sub.source_id, sub.lead_key,
                        Jsonb(v["payload"]),
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise IdempotencyConflictError("idempotency_key already bound to a materially different submission") from exc
        except (psycopg.errors.RaiseException, psycopg.errors.InvalidParameterValue, psycopg.errors.DatetimeFieldOverflow) as exc:
            raise EvidenceValidationError(str(exc).splitlines()[0]) from exc

        return {
            "observation_id": str(row["observation_id"]),
            "idempotency_key": v["idem"],
            "submission_sha256": row["submission_sha256"],
            "status": row["status"],
            "overall_state": row["overall_state"],
            "recorded_at": iso(row["recorded_at"]),
        }

    def record_new_vacancy(
        self,
        conn,
        *,
        logical_contributor: str,
        observed_at: datetime,
        company: str,
        title: str,
        posting_url: str,
        claimed_actor: str = "",
        location_text: str = "",
        country: str = "",
        countries: tuple[str, ...] = (),
        description_text: str | None = None,
        description_excerpt: str = "",
        evidence_completeness: str = "complete",
        evidence_retention_policy: str = "complete_public_ats",
        provider: str = "other",
        board: str | None = None,
        native_id: str | None = None,
        claim_summary: str | None = None,
        payload: dict | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Atomically persist candidate + snapshot + observation via SECURITY DEFINER.

        All hashes, candidate keys, and neutral unscreened markers are computed inside SQL.
        """
        import psycopg
        from psycopg.types.json import Jsonb

        at = now or datetime.now(timezone.utc)
        iso(observed_at)
        if observed_at > at + timedelta(minutes=5):
            raise EvidenceValidationError("Future observation timestamps are forbidden")
        logical = safe(logical_contributor or "").strip()
        if not 3 <= len(logical) <= 128:
            raise EvidenceValidationError("logical_contributor must be 3..128 characters")
        claimed = safe(claimed_actor or "").strip()
        url, _host, _port, _ips = validate_public_evidence_url(
            posting_url, resolve_dns=self.resolve_dns, resolver=self.resolver,
        )
        comp = safe(company or "").strip()
        tit = safe(title or "").strip()
        if not comp or not tit:
            raise EvidenceValidationError("company and title are required")
        if provider not in ("greenhouse", "lever", "ashby", "workable", "other"):
            raise EvidenceValidationError("Unsupported provider")
        if (board is None) != (native_id is None):
            raise EvidenceValidationError("board and native_id must both be provided or both omitted")
        if evidence_completeness not in ALLOWED_COMPLETENESS:
            raise EvidenceValidationError(f"Invalid evidence_completeness: {evidence_completeness!r}")
        if evidence_retention_policy not in ALLOWED_RETENTION_POLICIES:
            raise EvidenceValidationError(f"Invalid evidence_retention_policy: {evidence_retention_policy!r}")

        clean_desc: str | None = safe(description_text) if description_text is not None else None
        if clean_desc is not None and len(clean_desc) > MAX_COMPLETE_DESC_CHARS:
            raise EvidenceValidationError(f"description_text exceeds safety cap ({MAX_COMPLETE_DESC_CHARS} chars)")
        clean_excerpt = safe(description_excerpt or "").strip()
        if not clean_excerpt and clean_desc and evidence_completeness != "unavailable":
            clean_excerpt = filtering.plain(clean_desc)[:MAX_EXCERPT_CHARS]
        if len(clean_excerpt) > MAX_EXCERPT_CHARS:
            clean_excerpt = clean_excerpt[:MAX_EXCERPT_CHARS]

        clean_payload = safe(payload or {})
        if not isinstance(clean_payload, dict):
            raise EvidenceValidationError("payload must be a JSON object")

        idem = (idempotency_key or "").strip() or sha256(encode_evidence({
            "kind": "new_vacancy", "provider": provider, "board": board, "native_id": native_id,
            "url": url, "company": comp, "title": tit, "completeness": evidence_completeness,
            "policy": evidence_retention_policy, "logical": logical, "observed_at": iso(observed_at),
        })).hexdigest()

        try:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM jobwatch.submit_agent_vacancy("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        idem, logical, claimed, observed_at,
                        provider, board, native_id, comp, tit, url,
                        safe(location_text), safe(country), list(countries),
                        evidence_completeness, evidence_retention_policy,
                        clean_desc, clean_excerpt,
                        safe(claim_summary) if claim_summary is not None else None,
                        Jsonb(clean_payload),
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            msg = str(exc)
            if "candidate_identity_conflict" in msg:
                raise CandidateIdentityConflictError("Submission conflicts with an existing candidate identity") from exc
            raise IdempotencyConflictError("idempotency_key already bound to a materially different submission") from exc
        except (psycopg.errors.RaiseException, psycopg.errors.InvalidParameterValue, psycopg.errors.CheckViolation) as exc:
            raise EvidenceValidationError(str(exc).splitlines()[0]) from exc

        return {
            "candidate_key": row["candidate_key"],
            "observation_id": str(row["observation_id"]),
            "idempotency_key": idem,
            "submission_sha256": row["submission_sha256"],
            "evidence_completeness": evidence_completeness,
            "evidence_retention_policy": evidence_retention_policy,
            "complete_description_sha256": row["complete_description_sha256"],
            "excerpt_sha256": row["excerpt_sha256"],
            "screening_status": "unscreened_agent_submission",
            "status": row["status"],
            "overall_state": row["overall_state"],
            "recorded_at": iso(row["recorded_at"]),
        }


def _load_target_context(conn, obs: dict) -> dict | None:
    if obs.get("posting_key"):
        row = conn.execute(
            "SELECT p.posting_key, p.provider, p.board, p.native_id, pv.company, pv.title, pv.posting_url, "
            "s.validation_url, e.careers_url "
            "FROM jobwatch.postings p "
            "JOIN jobwatch.posting_versions pv ON pv.id = p.current_version_id "
            "LEFT JOIN jobwatch.sources s ON s.id = p.source_id "
            "LEFT JOIN jobwatch.employers e ON e.id = s.employer_id "
            "WHERE p.posting_key = %s",
            (obs["posting_key"],),
        ).fetchone()
        if row:
            return dict(row)
    if obs.get("candidate_key"):
        row = conn.execute(
            "SELECT candidate_key, provider, board, native_id, company, title, posting_url "
            "FROM jobwatch.discovery_candidates WHERE candidate_key = %s",
            (obs["candidate_key"],),
        ).fetchone()
        if row:
            return dict(row)
    if obs.get("lead_key"):
        row = conn.execute(
            "SELECT lead_key, company, title, url AS posting_url FROM jobwatch.leads WHERE lead_key = %s",
            (obs["lead_key"],),
        ).fetchone()
        if row:
            return dict(row)
    return None


def _evaluate_target_binding(obs: dict, target: dict | None, final_url: str, body_text: str, plain_low: str) -> tuple[str, dict]:
    """Ensure an independently retrieved source actually identifies the target vacancy.

    Prevents an unrelated page that happens to share generic wording (e.g. 'Applications close
    29 November 2026') from falsely corroborating a target vacancy.
    """
    kind = obs["kind"]
    if kind in ("source_identity", "official_source_link") and target is None:
        return "not_applicable", {"reason": "non_vacancy_source_observation"}
    if target is None:
        return "unbound_target", {"reason": "no_resolvable_target_vacancy"}

    target_url = target.get("posting_url") or ""
    allowed_urls = [u for u in (target_url, target.get("validation_url"), target.get("careers_url")) if u]
    try:
        final_exact = exact_url(final_url) if final_url else ""
        target_exact = exact_url(target_url) if target_url else ""
    except ValueError:
        final_exact, target_exact = "", ""

    if final_exact and target_exact and final_exact == target_exact:
        return "bound_exact_url", {"matched_url": final_exact}

    final_host = (urlsplit(final_url).hostname or "").lower()
    final_reg = _registrable_domain(final_host)
    allowed_regs = {_registrable_domain((urlsplit(u).hostname or "").lower()) for u in allowed_urls if u}
    same_domain = bool(final_reg and final_reg in allowed_regs)

    # Check if retrieved page explicitly references the target ATS board + native_id or exact role title.
    provider = (target.get("provider") or "").lower()
    board = (target.get("board") or "").lower()
    native_id = (target.get("native_id") or "").lower()
    title_norm = re.sub(r"\s+", " ", (target.get("title") or "").strip()).casefold()
    company_norm = re.sub(r"\s+", " ", (target.get("company") or "").strip()).casefold()

    id_in_page = bool(native_id and len(native_id) >= 4 and (native_id in final_url.lower() or native_id in body_text.lower()))
    board_corrob = bool(provider and board and _html_corroborates_board(body_text, provider, board, native_id))
    title_in_page = bool(title_norm and len(title_norm) >= 6 and title_norm in plain_low)
    company_in_page = bool(company_norm and company_norm in plain_low)

    if same_domain and (id_in_page or board_corrob or (title_in_page and (company_in_page or len(title_norm) >= 14))):
        return "bound_host_and_identity", {
            "final_domain": final_reg,
            "id_in_page": id_in_page,
            "board_corroborated": board_corrob,
            "title_in_page": title_in_page,
            "company_in_page": company_in_page,
        }

    return "unbound_target", {
        "final_domain": final_reg,
        "expected_domains": sorted(allowed_regs),
        "same_domain": same_domain,
        "id_in_page": id_in_page,
        "title_in_page": title_in_page,
        "reason": "Retrieved page did not match target posting URL or employer domain + vacancy identity.",
    }


def _extract_corroboration_excerpt(plain_body: str, quoted_plain: str) -> str:
    clean = re.sub(r"\s+", " ", plain_body or "").strip()
    if not clean:
        return ""
    if quoted_plain:
        idx = clean.casefold().find(quoted_plain.casefold())
        if idx >= 0:
            start = max(0, idx - 300)
            end = min(len(clean), idx + len(quoted_plain) + 300)
            return clean[start:end][:MAX_EXCERPT_CHARS]
    return clean[:MAX_EXCERPT_CHARS]


def corroborate_observation(
    verifier_conn,
    observation_id: str,
    *,
    checked_by: str = "collector:corroborator-v2",
    fetcher=safe_fetch_public_https,
    budget: CorroborationBudget | None = None,
    now: datetime | None = None,
) -> dict:
    """Append an independent corroboration attempt with vacancy binding, completeness, and provenance.

    Must run under `jobwatch_collector` or `jobwatch_owner` (NEVER `jobwatch_agent_evidence`).
    Preserves all prior corroboration attempts append-only; NEVER mutates `verifications`,
    `calendar_selections`, `leads`, or `posting_versions`.
    """
    from psycopg.types.json import Jsonb

    at = now or datetime.now(timezone.utc)
    iso(at)
    obs = verifier_conn.execute(
        "SELECT observation_id, kind, source_url, observed_wording, candidate_deadline_date, "
        "posting_key, candidate_key, board_key, source_id, lead_key, payload "
        "FROM jobwatch.agent_evidence_observations WHERE observation_id=%s",
        (observation_id,),
    ).fetchone()
    if not obs:
        raise ValueError(f"Unknown observation_id: {observation_id}")

    target = _load_target_context(verifier_conn, obs)
    target_binding_status = "not_applicable"
    binding_info: dict = {}
    evidence_completeness = "unavailable"
    evidence_excerpt = ""

    try:
        res: SafeFetchResult = fetcher(obs["source_url"], budget=budget)
    except SSRFBlockedError as exc:
        retrieval_status = "ssrf_refused"
        wording_status = "not_checked"
        claim_status = "unavailable"
        overall_state = "evidence_unavailable"
        details = {"reason": str(exc)}
        res = SafeFetchResult(0, b"", obs["source_url"], "", False, 0, error_kind="ssrf_refused")
    else:
        body_text = res.body.decode("utf-8", errors="replace") if res.body else ""
        plain_body = filtering.plain(body_text)
        plain_low = plain_body.casefold()
        quoted_plain = filtering.plain(obs["observed_wording"] or "")
        quoted_low = quoted_plain.casefold()
        details = {"redirects": res.redirects, "truncated": res.truncated}

        if res.error_kind == "timeout":
            retrieval_status, wording_status, claim_status, overall_state = "timeout", "not_checked", "unavailable", "evidence_unavailable"
        elif res.error_kind == "network_error":
            retrieval_status, wording_status, claim_status, overall_state = "network_error", "not_checked", "unavailable", "evidence_unavailable"
        elif res.http_status in (401, 403, 429):
            retrieval_status, wording_status, claim_status, overall_state = "blocked_or_403", "not_checked", "unavailable", "evidence_unavailable"
        elif res.http_status != 200:
            retrieval_status, wording_status, claim_status, overall_state = "http_error", "not_checked", "unavailable", "evidence_unavailable"
        elif len(plain_body) < 40 and JS_SHELL_MARKERS.search(body_text):
            retrieval_status, wording_status, claim_status, overall_state = "js_shell_or_empty", "not_checked", "unavailable", "evidence_unavailable"
        else:
            retrieval_status = "retrieved_200"
            evidence_completeness = "truncated" if res.truncated else "complete"
            evidence_excerpt = _extract_corroboration_excerpt(plain_body, quoted_plain)
            target_binding_status, binding_info = _evaluate_target_binding(obs, target, res.final_url, body_text, plain_low)
            details["target_binding"] = binding_info

            wording_matched = bool(quoted_low and quoted_low in plain_low)
            if not quoted_low:
                wording_status = "not_applicable"
            elif wording_matched:
                wording_status = "matched"
            elif res.truncated:
                retrieval_status = "truncated_before_match"
                wording_status = "not_found"
                claim_status = "unavailable"
                overall_state = "evidence_unavailable"
                evidence_completeness = "unavailable"
                details["note"] = "Response truncated at byte cap before quoted wording was found."
            else:
                wording_status = "not_found"

            if retrieval_status == "retrieved_200":
                kind = obs["kind"]
                if kind == "deadline_wording":
                    parsed_page = parse_prose_deadline(plain_body)
                    parsed_quote = parse_prose_deadline(quoted_plain)
                    claimed_iso = obs["candidate_deadline_date"].isoformat() if obs["candidate_deadline_date"] else None
                    page_date = parsed_page.get("candidate_date")
                    details["parsed_page"] = parsed_page
                    details["parsed_quote"] = parsed_quote

                    if target_binding_status == "unbound_target":
                        # Unrelated page MUST NEVER corroborate (or contradict) the target vacancy!
                        claim_status = "unverified"
                        overall_state = "wording_matched" if wording_matched else "source_retrieved"
                        details["binding_failure"] = "Source page is not bound to the target vacancy."
                    elif wording_matched:
                        if claimed_iso and parsed_quote.get("candidate_date") == claimed_iso:
                            claim_status, overall_state = "corroborated", "claim_corroborated"
                        elif claimed_iso and parsed_quote.get("candidate_date") and parsed_quote.get("candidate_date") != claimed_iso:
                            claim_status, overall_state = "contradicted", "contradictory_evidence"
                            details["contradiction"] = f"Quoted wording parses to {parsed_quote.get('candidate_date')}, not {claimed_iso}"
                        else:
                            claim_status, overall_state = "unverified", "wording_matched"
                    else:
                        if claimed_iso and page_date and page_date != claimed_iso:
                            claim_status, overall_state = "contradicted", "contradictory_evidence"
                            details["contradiction"] = f"Official page states {page_date}, contradicting claimed {claimed_iso}"
                        else:
                            claim_status, overall_state = "unverified", "source_retrieved"

                elif kind == "source_identity":
                    prov = (obs["payload"] or {}).get("provider") or ""
                    board = (obs["payload"] or {}).get("board") or ""
                    corrob = _html_corroborates_board(body_text, prov, board) if (prov and board) else None
                    if corrob:
                        details["bidirectional_match"] = corrob
                        claim_status, overall_state = "corroborated", "claim_corroborated"
                    elif wording_matched:
                        claim_status, overall_state = "unverified", "wording_matched"
                    else:
                        claim_status, overall_state = "unverified", "source_retrieved"

                elif kind == "new_vacancy":
                    if target_binding_status == "unbound_target":
                        claim_status = "unverified"
                        overall_state = "wording_matched" if wording_matched else "source_retrieved"
                        details["binding_failure"] = "Retrieved source did not bind to the submitted vacancy URL/identity."
                    elif re.search(r"\b(?:this (?:role|job|position) (?:is no longer|has been) (?:open|filled|closed)|applications are now closed)\b", plain_low):
                        claim_status, overall_state = "contradicted", "contradictory_evidence"
                        details["contradiction"] = "Retrieved source explicitly indicates vacancy is closed."
                    elif wording_matched:
                        claim_status, overall_state = "corroborated", "claim_corroborated"
                    else:
                        claim_status, overall_state = "unverified", "source_retrieved"

                else:
                    if wording_matched:
                        claim_status, overall_state = "unverified", "wording_matched"
                    else:
                        claim_status, overall_state = "unverified", "source_retrieved"

    body_sha = sha256(res.body).hexdigest() if res.body else None
    provenance = {
        "requested_url": obs["source_url"],
        "final_url": res.final_url,
        "resolved_ip": res.resolved_ip,
        "redirects": res.redirects,
        "target_posting_url": (target or {}).get("posting_url"),
        "target_binding_status": target_binding_status,
        "checked_at": iso(at),
        "checked_by": checked_by,
    }
    with verifier_conn.transaction():
        row = verifier_conn.execute(
            "INSERT INTO jobwatch.agent_evidence_corroborations("
            "observation_id, checked_at, checked_by, retrieval_status, target_binding_status, "
            "wording_status, claim_status, overall_state, evidence_completeness, evidence_excerpt, "
            "http_status, final_url, resolved_ip, body_sha256, bytes_read, "
            "response_truncated, provenance, evidence_details"
            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING corroboration_id, overall_state, recorded_at",
            (
                observation_id, at, checked_by, retrieval_status, target_binding_status,
                wording_status, claim_status, overall_state, evidence_completeness, evidence_excerpt,
                res.http_status or None, res.final_url, res.resolved_ip,
                body_sha, len(res.body), res.truncated, Jsonb(provenance), Jsonb(details),
            ),
        ).fetchone()
    return {
        "corroboration_id": str(row["corroboration_id"]),
        "observation_id": str(observation_id),
        "retrieval_status": retrieval_status,
        "target_binding_status": target_binding_status,
        "wording_status": wording_status,
        "claim_status": claim_status,
        "overall_state": row["overall_state"],
        "evidence_completeness": evidence_completeness,
        "evidence_excerpt": evidence_excerpt,
        "http_status": res.http_status or None,
        "resolved_ip": res.resolved_ip,
        "body_sha256": body_sha,
        "response_truncated": res.truncated,
        "provenance": provenance,
        "evidence_details": details,
    }


def corroborate_pending(
    verifier_conn,
    *,
    limit: int = 10,
    checked_by: str = "corroborator:isolated-v2",
    fetcher=safe_fetch_public_https,
    budget: CorroborationBudget | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Corroborate up to `limit` uncorroborated observations under a shared network budget."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    run_budget = budget or CorroborationBudget(max_requests=limit * 3, max_bytes=limit * MAX_CORROBORATION_FETCH_BYTES)
    rows = verifier_conn.execute(
        "SELECT o.observation_id "
        "FROM jobwatch.agent_evidence_observations o "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM jobwatch.agent_evidence_corroborations c "
        "  WHERE c.observation_id = o.observation_id"
        ") ORDER BY o.recorded_at ASC LIMIT %s",
        (limit,),
    ).fetchall()
    results = []
    for r in rows:
        try:
            results.append(corroborate_observation(
                verifier_conn, str(r["observation_id"]),
                checked_by=checked_by, fetcher=fetcher, budget=run_budget, now=now,
            ))
        except EvidenceValidationError as exc:
            results.append({"observation_id": str(r["observation_id"]), "halted": str(exc)})
            break
    return results


def admit_corroborated_vacancies(conn, *, processing=None) -> dict:
    """Deterministically screen corroborated agent-submitted vacancies (`jobwatch_collector`).

    Only promotes candidates whose latest `new_vacancy` observation has an independent,
    vacancy-bound `claim_corroborated` record. Leaves uncorroborated agent candidates
    marked `unscreened-agent-submission`. Never writes owner tables (`leads`, `applications`,
    `triage`, `calendar_selections`, `verifications`).
    """
    from .collection import Processing

    proc = processing or Processing()
    admitted = []
    with conn.transaction():
        rows = conn.execute(
            "SELECT dc.* "
            "FROM jobwatch.discovery_candidates dc "
            "WHERE dc.rules_version = 'unscreened-agent-submission' "
            "  AND EXISTS ("
            "    SELECT 1 FROM jobwatch.agent_evidence_observations o "
            "    JOIN LATERAL ("
            "      SELECT c.overall_state, c.target_binding_status "
            "      FROM jobwatch.agent_evidence_corroborations c "
            "      WHERE c.observation_id = o.observation_id "
            "      ORDER BY c.checked_at DESC, c.recorded_at DESC LIMIT 1"
            "    ) latest ON true "
            "    WHERE o.kind = 'new_vacancy' "
            "      AND o.candidate_key = dc.candidate_key "
            "      AND latest.overall_state = 'claim_corroborated' "
            "      AND latest.target_binding_status IN ('bound_exact_url', 'bound_host_and_identity')"
            "  ) "
            "ORDER BY dc.candidate_key FOR UPDATE OF dc"
        ).fetchall()
        for row in rows:
            has_complete = row["evidence_completeness"] == "complete" and bool(row["description_text"])
            desc = row["description_text"] or row["description_excerpt"] or None
            res = proc.screen({
                "title": row["title"],
                "location": row["location_text"],
                "country": row["country"],
                "countries": list(row["countries"] or ()),
                "department": row["department"],
                "team": row["team"],
                "employment_type": row["employment_type"],
                "description": desc,
            })
            basis = "full" if has_complete else ("excerpt" if desc else "title_metadata_only")
            conn.execute(
                "UPDATE jobwatch.discovery_candidates "
                "SET verdict=%s, family=%s, codes=%s, rules_version=%s, "
                "    screening_input_sha256=%s, screening_basis=%s, screened_at=clock_timestamp() "
                "WHERE candidate_key=%s",
                (res["verdict"], res["family"], res["codes"], res["rules_version"],
                 res["input_sha256"], basis, row["candidate_key"]),
            )
            admitted.append({
                "candidate_key": row["candidate_key"],
                "verdict": res["verdict"],
                "family": res["family"],
                "rules_version": res["rules_version"],
                "screening_basis": basis,
            })
    return {"admitted_count": len(admitted), "admitted": admitted}


def main(argv=None) -> int:
    import argparse
    import json
    from .connection import DatabaseConfig, connect

    parser = argparse.ArgumentParser(description="Local-only controlled agent evidence ingestion & isolated corroboration CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    so = sub.add_parser("submit-observation")
    so.add_argument("--kind", required=True, choices=sorted(ALLOWED_KINDS))
    so.add_argument("--contributor", required=True)
    so.add_argument("--claimed-actor", default="")
    so.add_argument("--url", required=True)
    so.add_argument("--summary", required=True)
    so.add_argument("--wording", default="")
    so.add_argument("--deadline-date", default=None)
    so.add_argument("--posting-key", default=None)
    so.add_argument("--candidate-key", default=None)
    so.add_argument("--board-key", default=None)
    so.add_argument("--source-id", type=int, default=None)
    so.add_argument("--lead-key", default=None)
    so.add_argument("--idempotency-key", default=None)
    so.add_argument("--payload-json", default="{}")

    sv = sub.add_parser("submit-vacancy")
    sv.add_argument("--contributor", required=True)
    sv.add_argument("--claimed-actor", default="")
    sv.add_argument("--company", required=True)
    sv.add_argument("--title", required=True)
    sv.add_argument("--url", required=True)
    sv.add_argument("--location", default="")
    sv.add_argument("--country", default="")
    sv.add_argument("--provider", default="other")
    sv.add_argument("--board", default=None)
    sv.add_argument("--native-id", default=None)
    sv.add_argument("--completeness", default="complete", choices=sorted(ALLOWED_COMPLETENESS))
    sv.add_argument("--retention-policy", default="complete_public_ats", choices=sorted(ALLOWED_RETENTION_POLICIES))
    sv.add_argument("--description", default=None)
    sv.add_argument("--excerpt", default="")
    sv.add_argument("--idempotency-key", default=None)

    co = sub.add_parser("corroborate")
    co.add_argument("--observation-id", default=None)
    co.add_argument("--limit", type=int, default=10)
    co.add_argument("--checked-by", default="corroborator:isolated-v2")

    sub.add_parser("admit-vacancies")

    args = parser.parse_args(argv)
    cfg = DatabaseConfig.from_env()
    with connect(cfg) as conn:
        if args.cmd == "submit-observation":
            ing = AgentEvidenceIngestor(resolve_dns=False)
            res = ing.record_observation(conn, ObservationSubmission(
                kind=args.kind, logical_contributor=args.contributor, claimed_actor=args.claimed_actor,
                observed_at=datetime.now(timezone.utc), source_url=args.url, claim_summary=args.summary,
                observed_wording=args.wording, candidate_deadline_date=args.deadline_date,
                posting_key=args.posting_key, candidate_key=args.candidate_key,
                board_key=args.board_key, source_id=args.source_id, lead_key=args.lead_key,
                payload=json.loads(args.payload_json), idempotency_key=args.idempotency_key,
            ))
        elif args.cmd == "submit-vacancy":
            ing = AgentEvidenceIngestor(resolve_dns=False)
            res = ing.record_new_vacancy(
                conn, logical_contributor=args.contributor, claimed_actor=args.claimed_actor,
                observed_at=datetime.now(timezone.utc), company=args.company, title=args.title,
                posting_url=args.url, location_text=args.location, country=args.country,
                countries=(args.country,) if args.country else (),
                description_text=args.description, description_excerpt=args.excerpt,
                evidence_completeness=args.completeness, evidence_retention_policy=args.retention_policy,
                provider=args.provider, board=args.board, native_id=args.native_id,
                idempotency_key=args.idempotency_key,
            )
        elif args.cmd == "corroborate":
            if args.observation_id:
                res = corroborate_observation(conn, args.observation_id, checked_by=args.checked_by)
            else:
                res = corroborate_pending(conn, limit=args.limit, checked_by=args.checked_by)
        else:
            res = admit_corroborated_vacancies(conn)
    print(json.dumps(res, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
