"""Isolated prototype for narrowly permissioned agent-contributed public evidence ingestion.

NOT enabled in production. Enforces:
- Strict separation from owner personal decisions (no writes to applications, triage,
  calendar_selections, leads, verifications, sources, or postings).
- SSRF / URL safety checks (HTTPS-only public hosts; rejects loopback, RFC1918, link-local /
  AWS IMDS 169.254.169.254, IPv6 ULA/loopback/link-local, URL credentials).
- Bounded payloads, bounded wording, non-future observation timestamps, per-contributor
  rolling hourly rate budgets, and idempotency keys.
- Contradiction preservation (append-only observations; never silently overwrites).
- Separate independent corroboration (`corroborate_observation`, collector/owner role only).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from jobwatch import filtering
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
MAX_WORDING_CHARS = 4000
MAX_SUMMARY_CHARS = 500
MAX_PAYLOAD_BYTES = 8192
MAX_CORROBORATION_FETCH_BYTES = 512 * 1024
DEFAULT_HOURLY_BUDGET = 60
ISO_DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})


class EvidenceValidationError(ValueError):
    """Raised on untrusted, unsafe, SSRF-risk, oversized, or rate-limited submissions."""


def _is_forbidden_ip(ip_obj) -> bool:
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_public_evidence_url(url: str, *, resolve_dns: bool = False) -> str:
    """Validate that an evidence URL is strictly public HTTPS and SSRF-safe."""
    if not isinstance(url, str):
        raise EvidenceValidationError("URL must be a string")
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 2048:
        raise EvidenceValidationError("URL missing or exceeds 2048 characters")
    parsed = urlsplit(cleaned)
    if parsed.scheme != "https":
        raise EvidenceValidationError("Only https:// public URLs are accepted")
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise EvidenceValidationError("Credentials in URL are forbidden")
    host = (parsed.hostname or "").lower().strip(".")
    if not host or host in LOCAL_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise EvidenceValidationError("Local or internal hostnames are forbidden")
    if "." not in host:
        raise EvidenceValidationError("Hostname must be a public FQDN")
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        ip_obj = None
    if ip_obj is not None:
        if _is_forbidden_ip(ip_obj):
            raise EvidenceValidationError("Private, loopback, or link-local IP addresses are forbidden")
    elif resolve_dns:
        try:
            infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise EvidenceValidationError("Host DNS resolution failed") from exc
        if not infos:
            raise EvidenceValidationError("Host resolved to no addresses")
        for _fam, _typ, _proto, _canon, sockaddr in infos:
            resolved_ip = ipaddress.ip_address(sockaddr[0])
            if _is_forbidden_ip(resolved_ip):
                raise EvidenceValidationError("Host resolved to a private or link-local IP (SSRF blocked)")
    return cleaned


@dataclass(frozen=True)
class ObservationSubmission:
    kind: str
    contributor: str
    observed_at: datetime
    source_url: str
    claim_summary: str
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
    """Bounded append-only ingestor meant to run under `jobwatch_agent_evidence`."""

    def __init__(self, *, hourly_budget: int = DEFAULT_HOURLY_BUDGET, resolve_dns: bool = False):
        self.hourly_budget = hourly_budget
        self.resolve_dns = resolve_dns

    def _validate(self, sub: ObservationSubmission, now: datetime) -> tuple[str, dict, str, str]:
        if sub.kind not in ALLOWED_KINDS:
            raise EvidenceValidationError(f"Unsupported evidence kind: {sub.kind!r}")
        contributor = safe(sub.contributor or "").strip()
        if not 3 <= len(contributor) <= 128:
            raise EvidenceValidationError("Contributor must be 3..128 characters")
        iso(sub.observed_at)
        if sub.observed_at > now + timedelta(minutes=5):
            raise EvidenceValidationError("Future observation timestamps are forbidden")
        url = validate_public_evidence_url(sub.source_url, resolve_dns=self.resolve_dns)
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

        cand_date = sub.candidate_deadline_date
        if cand_date is not None:
            if not isinstance(cand_date, str) or not ISO_DATE_RE.fullmatch(cand_date):
                raise EvidenceValidationError("candidate_deadline_date must be YYYY-MM-DD or None")
        elif sub.kind == "deadline_wording":
            parsed = parse_prose_deadline(wording)
            cand_date = parsed.get("candidate_date")

        payload = safe(sub.payload or {})
        if not isinstance(payload, dict):
            raise EvidenceValidationError("payload must be a JSON object")
        encoded_payload = encode_evidence(payload)
        if len(encoded_payload) > MAX_PAYLOAD_BYTES:
            raise EvidenceValidationError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        payload_sha = sha256(encoded_payload).hexdigest()

        idem = (sub.idempotency_key or "").strip()
        if not idem:
            basis = {
                "kind": sub.kind, "contributor": contributor, "source_url": url,
                "posting_key": sub.posting_key, "candidate_key": sub.candidate_key,
                "board_key": sub.board_key, "source_id": sub.source_id, "lead_key": sub.lead_key,
                "claim_summary": summary, "observed_wording": wording, "payload_sha256": payload_sha,
            }
            idem = sha256(encode_evidence(basis)).hexdigest()
        elif not 8 <= len(idem) <= 128:
            raise EvidenceValidationError("idempotency_key must be 8..128 characters")
        return url, payload, payload_sha, idem

    def record_observation(
        self,
        conn,
        sub: ObservationSubmission,
        *,
        now: datetime | None = None,
    ) -> dict:
        """Append one validated observation idempotently. Never self-corroborates."""
        from psycopg.types.json import Jsonb

        at = now or datetime.now(timezone.utc)
        url, payload, payload_sha, idem = self._validate(sub, at)
        contributor = safe(sub.contributor).strip()
        summary = safe(sub.claim_summary).strip()
        wording = safe(sub.observed_wording or "").strip()
        cand_date = sub.candidate_deadline_date
        if cand_date is None and sub.kind == "deadline_wording":
            cand_date = parse_prose_deadline(wording).get("candidate_date")

        with conn.transaction():
            existing = conn.execute(
                "SELECT observation_id, corroboration_status, recorded_at "
                "FROM jobwatch.agent_evidence_observations WHERE idempotency_key=%s",
                (idem,),
            ).fetchone()
            if existing:
                return {
                    "observation_id": str(existing["observation_id"]),
                    "idempotency_key": idem,
                    "status": "duplicate_replayed",
                    "corroboration_status": existing["corroboration_status"],
                }

            recent_count = conn.execute(
                "SELECT count(*) AS n FROM jobwatch.agent_evidence_observations "
                "WHERE contributor=%s AND recorded_at >= clock_timestamp() - interval '1 hour'",
                (contributor,),
            ).fetchone()["n"]
            if int(recent_count) >= self.hourly_budget:
                raise EvidenceValidationError("Contributor hourly submission budget exceeded")

            oid = uuid4()
            row = conn.execute(
                "INSERT INTO jobwatch.agent_evidence_observations("
                "observation_id, idempotency_key, kind, contributor, observed_at, source_url, "
                "posting_key, candidate_key, board_key, source_id, lead_key, "
                "claim_summary, observed_wording, candidate_deadline_date, payload, payload_sha256"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING observation_id, corroboration_status, recorded_at",
                (
                    oid, idem, sub.kind, contributor, sub.observed_at, url,
                    sub.posting_key, sub.candidate_key, sub.board_key, sub.source_id, sub.lead_key,
                    summary, wording, cand_date, Jsonb(payload), payload_sha,
                ),
            ).fetchone()
        return {
            "observation_id": str(row["observation_id"]),
            "idempotency_key": idem,
            "status": "recorded",
            "corroboration_status": row["corroboration_status"],
            "recorded_at": iso(row["recorded_at"]),
        }

    def record_new_vacancy(
        self,
        conn,
        *,
        contributor: str,
        observed_at: datetime,
        company: str,
        title: str,
        posting_url: str,
        location_text: str = "",
        country: str = "",
        countries: tuple[str, ...] = (),
        description_excerpt: str = "",
        provider: str = "other",
        board: str | None = None,
        native_id: str | None = None,
        now: datetime | None = None,
        processing: Processing = Processing(),
    ) -> dict:
        """Record a newly discovered public vacancy into `discovery_candidates` + append-only log."""
        from psycopg.types.json import Jsonb

        at = now or datetime.now(timezone.utc)
        url = validate_public_evidence_url(posting_url, resolve_dns=self.resolve_dns)
        comp = safe(company or "").strip()
        tit = safe(title or "").strip()
        if not comp or not tit:
            raise EvidenceValidationError("company and title are required")
        if provider not in ("greenhouse", "lever", "ashby", "workable", "other"):
            raise EvidenceValidationError("Unsupported provider")
        if (board is None) != (native_id is None):
            raise EvidenceValidationError("board and native_id must both be provided or both omitted")
        excerpt = safe(description_excerpt or "")[:2500]
        desc_sha = sha256(excerpt.encode("utf-8")).hexdigest() if excerpt else None
        ev_payload = {"company": comp, "title": tit, "posting_url": url, "location_text": safe(location_text),
                      "country": safe(country), "countries": list(countries), "description_excerpt": excerpt}
        ev_sha = sha256(encode_evidence(ev_payload)).hexdigest()

        ckey = (
            f"{provider}:{board.lower()}:{native_id}"
            if (board and native_id)
            else f"agent:{sha256(url.encode('utf-8')).hexdigest()[:24]}"
        )
        screened = filtering.screen({
            "title": tit, "location": safe(location_text), "country": safe(country),
            "countries": list(countries), "department": "", "team": "",
            "employment_type": "", "description": excerpt,
        })

        with conn.transaction():
            exists = conn.execute(
                "SELECT candidate_key FROM jobwatch.research_discovery_candidates WHERE candidate_key=%s",
                (ckey,),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO jobwatch.discovery_candidates("
                    "candidate_key, provider, board, native_id, company, title, posting_url, "
                    "location_text, country, countries, description_excerpt, description_sha256, "
                    "evidence_sha256, state, discovery_mechanisms, provenance, "
                    "verdict, family, codes, rules_version, screening_input_sha256, screening_basis"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',%s,%s,%s,%s,%s,%s,%s,'excerpt')",
                    (
                        ckey, provider, board, native_id, comp, tit, url,
                        safe(location_text), safe(country), list(countries), excerpt, desc_sha,
                        ev_sha, ["agent_submission"],
                        Jsonb({"contributor": safe(contributor).strip(), "observed_at": iso(observed_at), "unverified_agent_submission": True}),
                        screened["verdict"], screened["family"], screened["codes"],
                        screened["rules_version"], screened["input_sha256"],
                    ),
                )

        obs = self.record_observation(
            conn,
            ObservationSubmission(
                kind="new_vacancy",
                contributor=contributor,
                observed_at=observed_at,
                source_url=url,
                candidate_key=ckey,
                claim_summary=f"Agent-submitted public vacancy: {comp} - {tit}"[:MAX_SUMMARY_CHARS],
                observed_wording=excerpt[:MAX_WORDING_CHARS],
                payload={"provider": provider, "board": board, "native_id": native_id, "screening_verdict": screened["verdict"]},
            ),
            now=at,
        )
        return {"candidate_key": ckey, "verdict": screened["verdict"], **obs}


def _default_safe_fetch(url: str) -> tuple[int, bytes]:
    validate_public_evidence_url(url, resolve_dns=True)
    req = Request(url, headers={"User-Agent": "GraduateJobWatch-Corroborator/1.0"})
    with urlopen(req, timeout=15) as resp:
        body = resp.read(MAX_CORROBORATION_FETCH_BYTES + 1)
        if len(body) > MAX_CORROBORATION_FETCH_BYTES:
            body = body[:MAX_CORROBORATION_FETCH_BYTES]
        return int(resp.status), body


def corroborate_observation(
    verifier_conn,
    observation_id: str,
    *,
    corroborated_by: str = "collector:corroborator-v1",
    fetch_url=_default_safe_fetch,
    now: datetime | None = None,
) -> dict:
    """Independently fetch the official URL (SSRF-guarded) and corroborate an observation.

    Must run under `jobwatch_collector` or `jobwatch_owner` (NEVER `jobwatch_agent_evidence`).
    """
    from psycopg.types.json import Jsonb

    at = now or datetime.now(timezone.utc)
    iso(at)
    row = verifier_conn.execute(
        "SELECT observation_id, kind, source_url, observed_wording, corroboration_status "
        "FROM jobwatch.agent_evidence_observations WHERE observation_id=%s",
        (observation_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown observation_id: {observation_id}")

    url = validate_public_evidence_url(row["source_url"], resolve_dns=False)
    http_status, body = fetch_url(url)
    body_sha = sha256(body).hexdigest()
    plain_body = filtering.plain(body.decode("utf-8", errors="replace")).casefold()
    quoted = filtering.plain(row["observed_wording"] or "").casefold()

    if http_status == 200 and (not quoted or quoted in plain_body):
        new_status = "corroborated"
        matched = True
    else:
        new_status = "unresolved_conflict"
        matched = False

    evidence = {
        "http_status": http_status,
        "body_sha256": body_sha,
        "bytes_read": len(body),
        "wording_matched": matched,
        "checked_url": url,
    }
    with verifier_conn.transaction():
        verifier_conn.execute(
            "UPDATE jobwatch.agent_evidence_observations "
            "SET corroboration_status=%s, corroborated_by=%s, corroborated_at=%s, corroboration_evidence=%s "
            "WHERE observation_id=%s",
            (new_status, corroborated_by, at, Jsonb(evidence), observation_id),
        )
    return {"observation_id": str(observation_id), "corroboration_status": new_status, "evidence": evidence}
