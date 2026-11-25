"""Narrow synchronous evidence-ingestion tool core for isolated MCP exposure.

Accepts ONLY bounded public evidence observations and new-vacancy submissions
through the SECURITY DEFINER SQL boundary (`jobwatch_agent_evidence`).
Never performs outbound HTTP fetches, never reads or writes owner tables, and
never accepts caller-supplied screening verdicts or personal decisions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import threading

from .agent_evidence import (
    AgentEvidenceIngestor,
    CandidateIdentityConflictError,
    EvidenceValidationError,
    IdempotencyConflictError,
    ObservationSubmission,
    SSRFBlockedError,
)
from .connection import DatabaseConfig, DatabaseError, connect
from .read_tools import wire

ROLE = "jobwatch_agent_evidence"
MAX_REQUEST = 32_768
MAX_BYTES = 32_768
TRUST = (
    "Submitted observations are untrusted public evidence claims, never authoritative "
    "verifications or owner decisions. Independent corroboration runs separately."
)


class EvidenceToolError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _parse_observed_at(value: str | None) -> datetime:
    if value is None or not str(value).strip():
        return datetime.now(timezone.utc)
    if not isinstance(value, str) or len(value) > 64:
        raise EvidenceToolError("invalid_arguments")
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceToolError("invalid_arguments") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise EvidenceToolError("invalid_arguments")
    return dt.astimezone(timezone.utc)


class EvidenceTools:
    """Narrow public evidence ingestion core bound strictly to `jobwatch_agent_evidence`."""

    def __init__(self, config: DatabaseConfig | None = None, *, timeout_ms: int = 5000, expected_role: str = ROLE):
        if type(timeout_ms) is not int or not 50 <= timeout_ms <= 5000:
            raise ValueError("Invalid timeout_ms")
        self.config = config
        self.timeout_ms = timeout_ms
        self.expected_role = expected_role
        self.slots = threading.BoundedSemaphore(3)
        # Never perform DNS/HTTP network calls inside the MCP process.
        self.ingestor = AgentEvidenceIngestor(resolve_dns=False)

    @contextmanager
    def session(self):
        if not self.slots.acquire(blocking=False):
            raise EvidenceToolError("busy")
        try:
            config = self.config or DatabaseConfig(
                os.environ.get("JOBWATCH_EVIDENCE_DATABASE_URL", ""),
                statement_timeout_ms=self.timeout_ms,
                lock_timeout_ms=1000,
                idle_transaction_timeout_ms=10_000,
            )
            with connect(config) as conn:
                role = conn.execute(
                    """SELECT current_user AS current, session_user AS login,
                        rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,
                        rolinherit, rolconnlimit,
                        (SELECT count(*) FROM pg_auth_members WHERE member = r.oid) AS memberships
                       FROM pg_roles r WHERE rolname = current_user"""
                ).fetchone()
                if (
                    role is None
                    or role["current"] != self.expected_role
                    or role["login"] != self.expected_role
                    or role["memberships"]
                    or role["rolinherit"]
                    or any(role[k] for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls"))
                    or not 1 <= role["rolconnlimit"] <= 5
                ):
                    raise EvidenceToolError("wrong_database_identity")
                yield conn
        finally:
            self.slots.release()

    def call(self, tool: str, arguments: dict) -> dict:
        try:
            if not isinstance(arguments, dict) or len(json.dumps(arguments, ensure_ascii=True, allow_nan=False).encode()) > MAX_REQUEST:
                raise EvidenceToolError("invalid_arguments")
            if tool == "submit_public_observation":
                data = self.submit_public_observation(**arguments)
            elif tool == "submit_public_vacancy":
                data = self.submit_public_vacancy(**arguments)
            else:
                raise EvidenceToolError("unknown_tool")
            payload = wire({"schema": "jobwatch.evidence/1", "ok": True, "trust": TRUST, "data": data})
            if len(json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()) > MAX_BYTES:
                raise EvidenceToolError("response_too_large")
            return payload
        except EvidenceToolError as exc:
            code, state = exc.code, None
        except SSRFBlockedError:
            code, state = "ssrf_refused", None
        except IdempotencyConflictError:
            code, state = "idempotency_conflict", None
        except CandidateIdentityConflictError:
            code, state = "candidate_identity_conflict", None
        except EvidenceValidationError as exc:
            code = "budget_exceeded" if "budget" in str(exc).lower() else "invalid_arguments"
            state = None
        except (TypeError, ValueError, KeyError, OverflowError):
            code, state = "invalid_arguments", None
        except DatabaseError:
            code, state = "connection_failed", None
        except Exception as exc:
            state = getattr(exc, "sqlstate", None)
            code = (
                "timeout" if state == "57014"
                else "permission_denied" if state == "42501"
                else "submission_failed"
            )
        return {"schema": "jobwatch.evidence/1", "ok": False, "error": {"code": code, "sqlstate": state}}

    def submit_public_observation(
        self,
        *,
        kind: str,
        logical_contributor: str,
        source_url: str,
        claim_summary: str,
        observed_at: str | None = None,
        claimed_actor: str = "",
        observed_wording: str = "",
        candidate_deadline_date: str | None = None,
        posting_key: str | None = None,
        candidate_key: str | None = None,
        board_key: str | None = None,
        source_id: int | None = None,
        lead_key: str | None = None,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        if source_id is not None and (type(source_id) is not int or source_id <= 0):
            raise EvidenceToolError("invalid_arguments")
        sub = ObservationSubmission(
            kind=kind,
            logical_contributor=logical_contributor,
            claimed_actor=claimed_actor or "",
            observed_at=_parse_observed_at(observed_at),
            source_url=source_url,
            claim_summary=claim_summary,
            observed_wording=observed_wording or "",
            candidate_deadline_date=candidate_deadline_date,
            posting_key=posting_key,
            candidate_key=candidate_key,
            board_key=board_key,
            source_id=source_id,
            lead_key=lead_key,
            payload=payload or {},
            idempotency_key=idempotency_key,
        )
        with self.session() as conn:
            return self.ingestor.record_observation(conn, sub)

    def submit_public_vacancy(
        self,
        *,
        logical_contributor: str,
        company: str,
        title: str,
        posting_url: str,
        observed_at: str | None = None,
        claimed_actor: str = "",
        location_text: str = "",
        country: str = "",
        countries: list[str] | None = None,
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
    ) -> dict:
        if countries is not None and (not isinstance(countries, list) or any(not isinstance(c, str) for c in countries)):
            raise EvidenceToolError("invalid_arguments")
        with self.session() as conn:
            return self.ingestor.record_new_vacancy(
                conn,
                logical_contributor=logical_contributor,
                claimed_actor=claimed_actor or "",
                observed_at=_parse_observed_at(observed_at),
                company=company,
                title=title,
                posting_url=posting_url,
                location_text=location_text or "",
                country=country or "",
                countries=tuple(countries or ()),
                description_text=description_text,
                description_excerpt=description_excerpt or "",
                evidence_completeness=evidence_completeness,
                evidence_retention_policy=evidence_retention_policy,
                provider=provider,
                board=board,
                native_id=native_id,
                claim_summary=claim_summary,
                payload=payload or {},
                idempotency_key=idempotency_key,
            )
