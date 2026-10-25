# Stage 4F — bounded connectivity qualification

Base: Stage 4E `5d5c8f88bce1789ff78638765eeb893e1c723240`.
Local/disposable implementation, not hosting or V1 activation approval.

1. Reuse the five Stage 4C tools and dedicated read identity; test a composed
   research session through actual MCP stdio and loopback Streamable HTTP against
   PostgreSQL 17 with synthetic imported data, history, exclusions and owner state.
   Report checks/hashes, never database values or credentials.
2. Package a hash-locked model-free MCP runtime. Make query inference disabled by
   default in the MCP executable and explicitly selectable as local. SQL/BM25/FTS
   remain usable; vectors/hybrid return a visible fixed error when inference is
   disabled. Core/direct SQL and the existing pinned local encoder remain intact.
3. Supply a small optional official-SDK connection probe and inert client example;
   no new query language, tool, write facade, service or database migration.
4. Document current official custom-plugin/tunnel support separately from actual
   account eligibility. Verify a synthetic online call only after owner approval,
   Platform/workspace access and credentials exist; do not create them now.
5. Prefer a trusted operator machine running private MCP and optional EmbeddingGemma
   query inference, using existing SSM database forwarding after scoped access is
   approved. Measured 1.84 GiB model RSS does not fit safely beside PostgreSQL on
   a 2 GiB EC2 host. No host resize/inference service is planned.
6. Treat Proton HTTPS feed hosting independently: reuse Stage 4E snapshots, minimal
   scope and revocation; OpenAI's MCP tunnel cannot serve Proton. Approve the stable
   endpoint/auth/domain and access changes separately before implementation/deploy.

Acceptance: negotiated five-tool sessions, ordinary bound multi-table SQL,
schema/exclusion/history/native evidence reads, lexical search, private-owner
read permission without transcript contents, weekly/calendar report reuse, mutation
denials and safe errors, bounded results, model-free startup, explicit unavailable
inference and optional real local vector/hybrid interop. Existing offline,
PostgreSQL/import/restore/owner/calendar tests remain green. No AWS calls, hosted
credentials/endpoints, real data, live ATS, schedules, workflow/main changes or
cutover are included.
