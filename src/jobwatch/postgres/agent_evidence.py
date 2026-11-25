"""Hardened isolated prototype for agent-contributed public evidence ingestion & corroboration.

NOT enabled in production. Enforces:
1. Controlled DB boundary: calls SECURITY DEFINER `submit_agent_observation` and
   `submit_agent_vacancy` (zero raw table INSERT grants for `jobwatch_agent_evidence`).
2. Attribution & transactional budgets: authenticated `db_login = session_user`
   separated from `logical_contributor` and untrusted `claimed_actor`; hourly/daily
   budgets enforced transactionally per `db_login` under `pg_advisory_xact_lock`.
3. Observation integrity: semantic ISO date validation (`date.fromisoformat` + SQL
   `make_date`), strict idempotency payload-collision detection (`submission_sha256`),
   atomic candidate + historical snapshot + observation creation, explicit
   `evidence_completeness` and separate complete vs excerpt SHA-256 hashes.
4. Network security (real SSRF protection beyond static URL checks):
   - HTTPS-only, port allowlist (`443` only), no URL credentials, public FQDN.
   - DNS resolution checked for EVERY IP (`ip.is_global`, including `ipv4_mapped` IPv6).
   - Connection-time IP pinning (`socket.create_connection((pinned_ip, port))` with TLS
     `server_hostname=host`), completely defeating DNS rebinding TOCTOU.
   - Hop-by-hop redirect validation (`max_redirects=3`), bounded read (`<= 512 KiB`),
     and per-run `CorroborationBudget`.
5. Append-only 6-state corroboration history (`agent_evidence_corroborations`):
   distinguishes `submission_recorded`, `source_retrieved`, `wording_matched`,
   `claim_corroborated`, `evidence_unavailable`, and `contradictory_evidence`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
from jobwatch.timing import parse_prose_deadline
from .collection import Processing, iso, safe
from .evidence import encode_evidence

ALLOWED_KINDS = frozenset({
    "new_vacancy",
    "official_source_link",
    "deadline_wording",
    "posting_evidence",
    "source_identity",
})
ALLOWED_COMPLETENESS = frozenset({"complete", "partial", "truncated", "unavailable"})
MAX_WORDING_CHARS = 4000
MAX_SUMMARY_CHARS = 500
MAX_COMPLETE_DESC_CHARS = 262_144
MAX_EXCERPT_CHARS = 2500
MAX_PAYLOAD_BYTES = 8192
MAX_CORROBORATION_FETCH_BYTES = 512 * 1024
ALLOWED_PORTS = frozenset({443})
LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
JS_SHELL_MARKERS = re.compile(r'enable javascript|javascript is required|you need to enable javascript|checking your browser|just a moment', re.I)


class EvidenceValidationError(ValueError):
    """Raised on invalid, untrusted, SSRF-risk, semantically invalid, or rate-limited input."""


class IdempotencyConflictError(EvidenceValidationError):
    """Raised when an idempotency_key is reused with materially different content."""


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


def validate_public_evidence_url(
    url: str,
    *,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
    resolve_dns: bool = False,
    resolver=socket.getaddrinfo,
) -> tuple[str, str, int, list[str]]:
    """Validate URL syntax/scheme/port/host and (optionally) resolve + validate EVERY IP.

    Returns (cleaned_url, hostname, port, validated_global_ips).
    """
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

    # Literal IP check
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
    """HTTPSConnection that connects directly to a pre-validated IP with SNI/cert verification for `host`.

    Eliminates DNS rebinding TOCTOU between resolution validation and TCP connect.
    """

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

    Intended ONLY for an isolated, network-restricted corroboration worker (never a privileged DB/root process).
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
    """Controlled ingestor invoking SECURITY DEFINER SQL boundaries under `jobwatch_agent_evidence`."""

    def __init__(self, *, resolve_dns: bool = False, resolver=socket.getaddrinfo):
        self.resolve_dns = resolve_dns
        self.resolver = resolver

    def _validate_and_hash(self, sub: ObservationSubmission, now: datetime) -> dict:
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
        payload_sha = sha256(encoded_payload).hexdigest()

        material_basis = {
            "kind": sub.kind, "source_url": cleaned_url,
            "posting_key": sub.posting_key, "candidate_key": sub.candidate_key,
            "board_key": sub.board_key, "source_id": sub.source_id, "lead_key": sub.lead_key,
            "claim_summary": summary, "observed_wording": wording,
            "candidate_deadline_date": cand_date, "payload_sha256": payload_sha,
        }
        submission_sha = sha256(encode_evidence(material_basis)).hexdigest()

        idem = (sub.idempotency_key or "").strip()
        if not idem:
            # Include observed_at so genuine repeat observations at later times are preserved!
            idem = sha256(encode_evidence({**material_basis, "logical_contributor": logical, "observed_at": iso(sub.observed_at)})).hexdigest()
        elif not 8 <= len(idem) <= 128:
            raise EvidenceValidationError("idempotency_key must be 8..128 characters")

        return {
            "logical": logical, "claimed": claimed, "url": cleaned_url, "summary": summary,
            "wording": wording, "cand_date": cand_date, "payload": payload,
            "payload_sha": payload_sha, "submission_sha": submission_sha, "idem": idem,
        }

    def record_observation(self, conn, sub: ObservationSubmission, *, now: datetime | None = None) -> dict:
        import psycopg
        from psycopg.types.json import Jsonb

        at = now or datetime.now(timezone.utc)
        v = self._validate_and_hash(sub, at)
        try:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM jobwatch.submit_agent_observation("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        v["idem"], v["submission_sha"], sub.kind, v["logical"], v["claimed"],
                        sub.observed_at, v["url"], v["summary"], v["wording"], v["cand_date"],
                        sub.posting_key, sub.candidate_key, sub.board_key, sub.source_id, sub.lead_key,
                        Jsonb(v["payload"]), v["payload_sha"],
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise IdempotencyConflictError("idempotency_key already bound to a materially different submission") from exc
        except psycopg.errors.RaiseException as exc:
            raise EvidenceValidationError(str(exc).splitlines()[0]) from exc

        return {
            "observation_id": str(row["observation_id"]),
            "idempotency_key": v["idem"],
            "submission_sha256": v["submission_sha"],
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
        evidence_completeness: str = "complete",
        provider: str = "other",
        board: str | None = None,
        native_id: str | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Atomically validate & persist candidate + historical snapshot + observation via SECURITY DEFINER."""
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

        clean_desc: str | None = safe(description_text) if description_text is not None else None
        if clean_desc is not None and len(clean_desc) > MAX_COMPLETE_DESC_CHARS:
            raise EvidenceValidationError(f"description_text exceeds safety cap ({MAX_COMPLETE_DESC_CHARS} chars)")
        if evidence_completeness == "unavailable":
            clean_desc = None
            excerpt = ""
            complete_sha = None
            excerpt_sha = None
            basis = "title_metadata_only"
        else:
            if evidence_completeness == "complete" and not clean_desc:
                raise EvidenceValidationError("completeness='complete' requires non-empty description_text")
            plain_desc = filtering.plain(clean_desc or "")
            excerpt = plain_desc[:MAX_EXCERPT_CHARS]
            excerpt_sha = sha256(excerpt.encode("utf-8")).hexdigest() if excerpt else None
            complete_sha = (
                sha256(clean_desc.encode("utf-8")).hexdigest()
                if (evidence_completeness == "complete" and clean_desc is not None)
                else None
            )
            basis = "full" if evidence_completeness == "complete" else "excerpt"

        ckey = (
            f"{provider}:{board.lower()}:{native_id}"
            if (board and native_id)
            else f"agent:{sha256(url.encode('utf-8')).hexdigest()[:24]}"
        )
        screened = filtering.screen({
            "title": tit, "location": safe(location_text), "country": safe(country),
            "countries": list(countries), "department": "", "team": "",
            "employment_type": "", "description": clean_desc if clean_desc is not None else (excerpt or None),
        })
        ev_payload = {
            "company": comp, "title": tit, "posting_url": url, "location_text": safe(location_text),
            "country": safe(country), "countries": list(countries),
            "evidence_completeness": evidence_completeness,
            "complete_description_sha256": complete_sha, "excerpt_sha256": excerpt_sha,
        }
        ev_sha = sha256(encode_evidence(ev_payload)).hexdigest()
        payload = {
            "provider": provider, "board": board, "native_id": native_id,
            "evidence_completeness": evidence_completeness, "screening_verdict": screened["verdict"],
        }
        payload_sha = sha256(encode_evidence(payload)).hexdigest()
        summary = f"Agent-submitted public vacancy: {comp} - {tit}"[:MAX_SUMMARY_CHARS]
        sub_sha = sha256(encode_evidence({
            "kind": "new_vacancy", "source_url": url, "candidate_key": ckey,
            "claim_summary": summary, "evidence_sha256": ev_sha, "payload_sha256": payload_sha,
        })).hexdigest()
        idem = (idempotency_key or "").strip() or sha256(encode_evidence({
            "sub_sha": sub_sha, "logical": logical, "observed_at": iso(observed_at),
        })).hexdigest()

        try:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM jobwatch.submit_agent_vacancy("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        idem, sub_sha, logical, claimed, observed_at,
                        ckey, provider, board, native_id, comp, tit, url,
                        safe(location_text), safe(country), list(countries),
                        evidence_completeness, clean_desc, excerpt,
                        complete_sha, excerpt_sha, ev_sha,
                        screened["verdict"], screened["family"], screened["codes"],
                        screened["rules_version"], screened["input_sha256"], basis,
                        summary, Jsonb(payload), payload_sha,
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise IdempotencyConflictError("idempotency_key already bound to a materially different submission") from exc
        except psycopg.errors.RaiseException as exc:
            raise EvidenceValidationError(str(exc).splitlines()[0]) from exc

        return {
            "candidate_key": row["candidate_key"],
            "observation_id": str(row["observation_id"]),
            "idempotency_key": idem,
            "submission_sha256": sub_sha,
            "evidence_completeness": evidence_completeness,
            "complete_description_sha256": complete_sha,
            "excerpt_sha256": excerpt_sha,
            "verdict": screened["verdict"],
            "status": row["status"],
            "overall_state": row["overall_state"],
            "recorded_at": iso(row["recorded_at"]),
        }


def corroborate_observation(
    verifier_conn,
    observation_id: str,
    *,
    checked_by: str = "collector:corroborator-v2",
    fetcher=safe_fetch_public_https,
    budget: CorroborationBudget | None = None,
    now: datetime | None = None,
) -> dict:
    """Append an independent corroboration attempt distinguishing all 6 semantic states.

    Must run under `jobwatch_collector` or `jobwatch_owner` (NEVER `jobwatch_agent_evidence`).
    Preserves all prior corroboration attempts append-only.
    """
    from psycopg.types.json import Jsonb

    at = now or datetime.now(timezone.utc)
    iso(at)
    obs = verifier_conn.execute(
        "SELECT observation_id, kind, source_url, observed_wording, candidate_deadline_date, "
        "posting_key, candidate_key, board_key, source_id, payload "
        "FROM jobwatch.agent_evidence_observations WHERE observation_id=%s",
        (observation_id,),
    ).fetchone()
    if not obs:
        raise ValueError(f"Unknown observation_id: {observation_id}")

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
            wording_matched = bool(quoted_low and quoted_low in plain_low)
            if not quoted_low:
                wording_status = "not_applicable"
            elif wording_matched:
                wording_status = "matched"
            elif res.truncated:
                # Never call a truncated response a contradiction!
                retrieval_status = "truncated_before_match"
                wording_status = "not_found"
                claim_status = "unavailable"
                overall_state = "evidence_unavailable"
                details["note"] = "Response truncated at byte cap before quoted wording was found."
            else:
                wording_status = "not_found"

            if retrieval_status == "retrieved_200":
                kind = obs["kind"]
                if kind == "deadline_wording":
                    # Check explicit parsed dates in the retrieved official source!
                    parsed_page = parse_prose_deadline(plain_body)
                    parsed_quote = parse_prose_deadline(quoted_plain)
                    claimed_iso = obs["candidate_deadline_date"].isoformat() if obs["candidate_deadline_date"] else None
                    page_date = parsed_page.get("candidate_date")
                    details["parsed_page"] = parsed_page
                    details["parsed_quote"] = parsed_quote

                    if wording_matched:
                        # Wording found: does it ACTUALLY corroborate the claimed date?
                        if claimed_iso and parsed_quote.get("candidate_date") == claimed_iso:
                            claim_status, overall_state = "corroborated", "claim_corroborated"
                        elif claimed_iso and parsed_quote.get("candidate_date") and parsed_quote.get("candidate_date") != claimed_iso:
                            # Agent quoted real wording, but misstated the date!
                            claim_status, overall_state = "contradicted", "contradictory_evidence"
                            details["contradiction"] = f"Quoted wording parses to {parsed_quote.get('candidate_date')}, not {claimed_iso}"
                        else:
                            # Wording matched, but no unambiguous explicit date verified -> wording_matched ONLY!
                            claim_status, overall_state = "unverified", "wording_matched"
                    else:
                        # Wording absent: ONLY contradictory if official source explicitly asserts a DIFFERENT date!
                        if claimed_iso and page_date and page_date != claimed_iso:
                            claim_status, overall_state = "contradicted", "contradictory_evidence"
                            details["contradiction"] = f"Official page states {page_date}, contradicting claimed {claimed_iso}"
                        else:
                            # Page changed / wording absent without explicit contrary date -> source_retrieved (NOT contradiction!)
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
                    # Minimal vacancy corroboration requires retrieved 200 + explicit title/wording match (and no explicit closed banner).
                    if re.search(r'\b(?:this (?:role|job|position) (?:is no longer|has been) (?:open|filled|closed)|applications are now closed)\b', plain_low):
                        claim_status, overall_state = "contradicted", "contradictory_evidence"
                        details["contradiction"] = "Retrieved source explicitly indicates vacancy is closed."
                    elif wording_matched:
                        claim_status, overall_state = "corroborated", "claim_corroborated"
                    else:
                        claim_status, overall_state = "unverified", "source_retrieved"

                else:
                    # official_source_link / posting_evidence: HTTP 200 alone is NEVER corroboration!
                    if wording_matched:
                        claim_status, overall_state = "unverified", "wording_matched"
                    else:
                        claim_status, overall_state = "unverified", "source_retrieved"

    body_sha = sha256(res.body).hexdigest() if res.body else None
    with verifier_conn.transaction():
        row = verifier_conn.execute(
            "INSERT INTO jobwatch.agent_evidence_corroborations("
            "observation_id, checked_at, checked_by, retrieval_status, wording_status, claim_status, "
            "overall_state, http_status, final_url, resolved_ip, body_sha256, bytes_read, "
            "response_truncated, evidence_details"
            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING corroboration_id, overall_state, recorded_at",
            (
                observation_id, at, checked_by, retrieval_status, wording_status, claim_status,
                overall_state, res.http_status or None, res.final_url, res.resolved_ip,
                body_sha, len(res.body), res.truncated, Jsonb(details),
            ),
        ).fetchone()
    return {
        "corroboration_id": str(row["corroboration_id"]),
        "observation_id": str(observation_id),
        "retrieval_status": retrieval_status,
        "wording_status": wording_status,
        "claim_status": claim_status,
        "overall_state": row["overall_state"],
        "http_status": res.http_status or None,
        "resolved_ip": res.resolved_ip,
        "body_sha256": body_sha,
        "response_truncated": res.truncated,
        "evidence_details": details,
    }
