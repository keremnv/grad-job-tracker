"""Narrow public evidence-ingestion MCP adapter (isolated; no SQL/owner/collector tools).

Strictly preserves client write confirmation (`readOnlyHint=False`), separates public
evidence submission from owner decisions, and performs zero outbound network fetches.
"""
import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import StrictInt, StrictStr

from .evidence_tools import EvidenceTools


def server(core: EvidenceTools | None = None, *, port: int = 8767):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult, TextContent, ToolAnnotations

    core = core or EvidenceTools()
    mcp = FastMCP(
        "Graduate Job Watch public evidence ingestion",
        host="127.0.0.1",
        port=port,
        stateless_http=True,
        json_response=True,
        log_level="WARNING",
        max_request_body_size=65536,
        max_sessions=3,
        instructions=(
            "Narrow public evidence ingestion only. Every submission is recorded as an untrusted "
            "agent observation with authenticated provenance and transactional rate budgets, and "
            "requires explicit client user confirmation. Never submit private owner notes, personal "
            "suitability judgments, or fabricated dates. Independent corroboration runs asynchronously "
            "in a separate network-isolated worker; submissions never modify applications, triage, "
            "verifications, or calendar selections."
        ),
    )
    # Explicitly declare readOnlyHint=False so MCP clients enforce user confirmation on every write.
    write_hints = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    async def invoke(name: str, arguments: dict[str, Any]) -> CallToolResult:
        result = await asyncio.to_thread(core.call, name, arguments)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=True))],
            structuredContent=result,
            isError=not result["ok"],
        )

    @mcp.tool(
        annotations=write_hints,
        description=(
            "Submit one bounded public evidence observation (kind: deadline_wording, posting_evidence, "
            "source_identity, official_source_link, or new_vacancy) from a public https:// URL. "
            "Supply exact observed_wording quoted verbatim from the official source and an idempotency_key "
            "(8..128 chars) so retries do not duplicate rows or budget. For deadline_wording, supply "
            "posting_key/candidate_key/lead_key and optional explicit ISO candidate_deadline_date (YYYY-MM-DD) "
            "only when stated verbatim. For source_identity or official_source_link referencing an unprobed "
            "discovered board, supply board_key to prioritize bounded discovery probing without activating it. "
            "Requires explicit user confirmation; never alters owner decisions or calendar selections."
        ),
    )
    async def submit_public_observation(
        kind: StrictStr,
        logical_contributor: StrictStr,
        source_url: StrictStr,
        claim_summary: StrictStr,
        observed_at: StrictStr | None = None,
        claimed_actor: StrictStr = "",
        observed_wording: StrictStr = "",
        candidate_deadline_date: StrictStr | None = None,
        posting_key: StrictStr | None = None,
        candidate_key: StrictStr | None = None,
        board_key: StrictStr | None = None,
        source_id: StrictInt | None = None,
        lead_key: StrictStr | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: StrictStr | None = None,
    ) -> CallToolResult:
        return await invoke(
            "submit_public_observation",
            {
                "kind": kind,
                "logical_contributor": logical_contributor,
                "source_url": source_url,
                "claim_summary": claim_summary,
                "observed_at": observed_at,
                "claimed_actor": claimed_actor,
                "observed_wording": observed_wording,
                "candidate_deadline_date": candidate_deadline_date,
                "posting_key": posting_key,
                "candidate_key": candidate_key,
                "board_key": board_key,
                "source_id": source_id,
                "lead_key": lead_key,
                "payload": payload,
                "idempotency_key": idempotency_key,
            },
        )

    @mcp.tool(
        annotations=write_hints,
        description=(
            "Submit an externally discovered public vacancy (e.g., from an unsupported or custom careers portal "
            "such as Google/DeepMind, Revolut, or a direct employer site) into discovery_candidates + "
            "agent_evidence_observations atomically. Candidate enters marked unscreened-agent-submission until "
            "independently corroborated by the isolated worker and deterministically screened. Never accepts "
            "caller screening verdicts or activates monitored sources. Requires explicit user confirmation."
        ),
    )
    async def submit_public_vacancy(
        logical_contributor: StrictStr,
        company: StrictStr,
        title: StrictStr,
        posting_url: StrictStr,
        observed_at: StrictStr | None = None,
        claimed_actor: StrictStr = "",
        location_text: StrictStr = "",
        country: StrictStr = "",
        countries: list[StrictStr] | None = None,
        description_text: StrictStr | None = None,
        description_excerpt: StrictStr = "",
        evidence_completeness: StrictStr = "complete",
        evidence_retention_policy: StrictStr = "complete_public_ats",
        provider: StrictStr = "other",
        board: StrictStr | None = None,
        native_id: StrictStr | None = None,
        claim_summary: StrictStr | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: StrictStr | None = None,
    ) -> CallToolResult:
        return await invoke(
            "submit_public_vacancy",
            {
                "logical_contributor": logical_contributor,
                "company": company,
                "title": title,
                "posting_url": posting_url,
                "observed_at": observed_at,
                "claimed_actor": claimed_actor,
                "location_text": location_text,
                "country": country,
                "countries": countries,
                "description_text": description_text,
                "description_excerpt": description_excerpt,
                "evidence_completeness": evidence_completeness,
                "evidence_retention_policy": evidence_retention_policy,
                "provider": provider,
                "board": board,
                "native_id": native_id,
                "claim_summary": claim_summary,
                "payload": payload,
                "idempotency_key": idempotency_key,
            },
        )

    return mcp


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--unix-socket", type=Path)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    mcp = server(port=args.port)
    if args.unix_socket:
        if args.transport != "streamable-http":
            parser.error("Unix socket requires streamable-http")
        from .unix_mcp import run

        run(mcp, args.unix_socket)
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
