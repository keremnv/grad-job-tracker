"""Minimal PostgreSQL lifecycle for discovered vacancies outside monitored boards.

Lifecycle states in `jobwatch.discovery_candidates`:
  new -> observed -> verified_identity -> linked_posting
                                       -> promoted_lead
                                       -> dismissed

Evidence fidelity (migration 0011):
- Vacancy identity lives in `discovery_candidates` (current state, native/URL uniqueness,
  overall `first_discovered_at`, `last_discovered_at`, `observation_count`, `current_snapshot_id`).
- Distinct immutable evidence versions live in `discovery_candidate_snapshots`
  (deduplicated per `(candidate_key, evidence_sha256)`), retaining complete TOAST-compressed
  descriptions up to the 256 KiB safety cap independently of screening verdict.
- Repeated unchanged observations increment `observation_count` and advance `last_observed_at`
  on the existing snapshot without duplicating content.

Discovery persistence NEVER creates or alters `postings`, `posting_versions`,
`sources`, `leads`, `triage`, or `applications` without explicit owner action.
Disappearing from discovery never infers closure or board absence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re

from jobwatch import filtering
from jobwatch.discovery.workable_search import DiscoveredVacancy
from jobwatch.identity import exact_url
from .collection import Processing, iso, safe

MAX_COMPLETE_DESCRIPTION_CHARS = 262_144
MAX_EXCERPT_CHARS = 2500
VALID_COMPLETENESS = frozenset({'complete', 'partial', 'truncated', 'unavailable'})
VALID_RETENTION_POLICIES = frozenset({'complete_public_ats', 'excerpt_only_terms_restricted', 'metadata_only'})


def _resolve_evidence_fields(vacancy: DiscoveredVacancy) -> dict:
    """Derive 0011-compliant complete/excerpt description text, hashes, and policy flags."""
    policy = getattr(vacancy, 'evidence_retention_policy', None) or 'complete_public_ats'
    if policy not in VALID_RETENTION_POLICIES:
        raise ValueError(f'Unsupported evidence_retention_policy: {policy!r}')
    requested = getattr(vacancy, 'evidence_completeness', None)
    if requested is not None and requested not in VALID_COMPLETENESS:
        raise ValueError(f'Unsupported evidence_completeness: {requested!r}')

    raw_full = safe(vacancy.description_full) if vacancy.description_full else ''
    raw_excerpt = safe(vacancy.description_excerpt) if vacancy.description_excerpt else ''

    if policy == 'metadata_only':
        return {
            'description_text': None,
            'description_excerpt': '',
            'evidence_completeness': 'unavailable',
            'evidence_retention_policy': policy,
            'complete_description_sha256': None,
            'excerpt_sha256': None,
        }

    if raw_excerpt.strip():
        excerpt = raw_excerpt[:MAX_EXCERPT_CHARS]
    elif raw_full.strip():
        plain_one_line = re.sub(r'\s+', ' ', filtering.plain(raw_full)).strip()
        if len(plain_one_line) <= MAX_EXCERPT_CHARS:
            excerpt = plain_one_line
        else:
            excerpt = plain_one_line[: MAX_EXCERPT_CHARS - 1].rstrip() + '…'
    else:
        excerpt = ''

    excerpt_sha = sha256(excerpt.encode('utf-8')).hexdigest() if excerpt.strip() else None

    if policy == 'excerpt_only_terms_restricted':
        completeness = 'unavailable' if not excerpt.strip() else ('partial' if requested == 'partial' else 'truncated')
        return {
            'description_text': None,
            'description_excerpt': excerpt if completeness != 'unavailable' else '',
            'evidence_completeness': completeness,
            'evidence_retention_policy': policy,
            'complete_description_sha256': None,
            'excerpt_sha256': excerpt_sha if completeness != 'unavailable' else None,
        }

    # policy == 'complete_public_ats'
    if raw_full.strip() and requested != 'unavailable':
        if len(raw_full) <= MAX_COMPLETE_DESCRIPTION_CHARS and requested in (None, 'complete'):
            complete_sha = sha256(raw_full.encode('utf-8')).hexdigest()
            return {
                'description_text': raw_full,
                'description_excerpt': excerpt,
                'evidence_completeness': 'complete',
                'evidence_retention_policy': policy,
                'complete_description_sha256': complete_sha,
                'excerpt_sha256': excerpt_sha,
            }
        bounded = raw_full[:MAX_COMPLETE_DESCRIPTION_CHARS]
        completeness = 'partial' if requested == 'partial' else 'truncated'
        return {
            'description_text': bounded,
            'description_excerpt': excerpt,
            'evidence_completeness': completeness,
            'evidence_retention_policy': policy,
            'complete_description_sha256': None,
            'excerpt_sha256': excerpt_sha,
        }

    if excerpt.strip() and requested != 'unavailable':
        return {
            'description_text': None,
            'description_excerpt': excerpt,
            'evidence_completeness': 'partial' if requested == 'partial' else 'truncated',
            'evidence_retention_policy': policy,
            'complete_description_sha256': None,
            'excerpt_sha256': excerpt_sha,
        }

    return {
        'description_text': None,
        'description_excerpt': '',
        'evidence_completeness': 'unavailable',
        'evidence_retention_policy': policy,
        'complete_description_sha256': None,
        'excerpt_sha256': None,
    }


def _screen_vacancy(vacancy: DiscoveredVacancy, processing: Processing, evidence: dict) -> dict:
    has_complete = evidence['evidence_completeness'] == 'complete' and bool(evidence['description_text'])
    desc_for_screen = evidence['description_text'] or evidence['description_excerpt'] or None
    inputs = {
        'title': vacancy.title,
        'location': vacancy.location_text,
        'country': vacancy.country,
        'countries': list(vacancy.countries),
        'department': vacancy.department,
        'team': vacancy.team,
        'employment_type': vacancy.employment_type,
        'description': desc_for_screen,
    }
    res = processing.screen(inputs)
    if res['rules_version'] != processing.rules_version:
        raise ValueError('Screener does not match its pinned rules version')
    basis = 'full' if has_complete else ('excerpt' if desc_for_screen else 'title_metadata_only')
    return {
        'verdict': res['verdict'],
        'family': res['family'],
        'codes': res['codes'],
        'rules_version': res['rules_version'],
        'input_sha256': res['input_sha256'],
        'basis': basis,
    }


def _find_existing(conn, vacancy: DiscoveredVacancy, url_norm: str):
    if vacancy.board and vacancy.native_id:
        row = conn.execute(
            'SELECT * FROM jobwatch.discovery_candidates WHERE provider=%s AND lower(board)=lower(%s) AND native_id=%s',
            (vacancy.provider, vacancy.board, vacancy.native_id),
        ).fetchone()
        if row:
            return row
    row = conn.execute(
        'SELECT * FROM jobwatch.discovery_candidates WHERE url_exact=%s',
        (url_norm,),
    ).fetchone()
    if row:
        return row
    return conn.execute(
        'SELECT * FROM jobwatch.discovery_candidates WHERE candidate_key=%s',
        (vacancy.candidate_key,),
    ).fetchone()


def _resolve_links(conn, provider: str, board: str | None, native_id: str | None, url_norm: str):
    source_id = None
    linked_posting = None
    if board:
        src = conn.execute(
            'SELECT id FROM jobwatch.sources WHERE provider=%s AND lower(board)=lower(%s)',
            (provider, board),
        ).fetchone()
        if src:
            source_id = src['id']
    if board and native_id:
        pkey = f'{provider}:{board.lower()}:{native_id}'
        hit = conn.execute('SELECT posting_key, source_id FROM jobwatch.postings WHERE posting_key=%s', (pkey,)).fetchone()
        if hit:
            linked_posting = hit['posting_key']
            source_id = source_id or hit['source_id']
    if not linked_posting:
        # Unambiguous exact-URL match only; never auto-link ambiguous URLs.
        hits = conn.execute(
            'SELECT p.posting_key, p.source_id FROM jobwatch.postings p '
            'JOIN jobwatch.posting_versions v ON v.id=p.current_version_id WHERE v.url_exact=%s LIMIT 2',
            (url_norm,),
        ).fetchall()
        if len(hits) == 1:
            linked_posting = hits[0]['posting_key']
            source_id = source_id or hits[0]['source_id']
    return source_id, linked_posting


def _upsert_snapshot(
    conn,
    *,
    candidate_key: str,
    observed_at: datetime,
    mechanism: str,
    source_url: str,
    title: str,
    location_text: str,
    evidence: dict,
    evidence_sha256: str,
    provenance: dict,
):
    from psycopg.types.json import Jsonb

    row = conn.execute(
        'INSERT INTO jobwatch.discovery_candidate_snapshots('
        'candidate_key, first_observed_at, last_observed_at, observation_count, '
        'discovery_mechanism, source_url, evidence_completeness, evidence_retention_policy, '
        'title, location_text, description_text, description_excerpt, '
        'complete_description_sha256, excerpt_sha256, evidence_sha256, provenance'
        ') VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
        'ON CONFLICT ON CONSTRAINT discovery_snapshots_retained_identity_key DO UPDATE SET '
        'last_observed_at = GREATEST(jobwatch.discovery_candidate_snapshots.last_observed_at, EXCLUDED.last_observed_at), '
        'observation_count = jobwatch.discovery_candidate_snapshots.observation_count + 1 '
        'RETURNING snapshot_id, (observation_count = 1) AS created',
        (
            candidate_key, observed_at, observed_at, mechanism, source_url,
            evidence['evidence_completeness'], evidence['evidence_retention_policy'],
            safe(title), safe(location_text),
            evidence['description_text'], evidence['description_excerpt'],
            evidence['complete_description_sha256'], evidence['excerpt_sha256'],
            evidence_sha256, Jsonb(safe(provenance)),
        ),
    ).fetchone()
    return row['snapshot_id'], bool(row['created'])


def record_discoveries(
    conn,
    vacancies,
    *,
    observed_at: datetime | None = None,
    processing: Processing = Processing(),
) -> dict:
    """Upsert discovered public vacancies with fidelity snapshots, deduplication, and auto-linking."""
    from psycopg.types.json import Jsonb

    at = observed_at or datetime.now(timezone.utc)
    iso(at)
    counts = {
        'inserted': 0, 'updated': 0, 'linked': 0, 'verified': 0,
        'snapshots_created': 0, 'snapshots_reobserved': 0,
    }

    with conn.transaction():
        for vacancy in vacancies:
            url_norm = exact_url(vacancy.posting_url)
            evidence = _resolve_evidence_fields(vacancy)
            judgment = _screen_vacancy(vacancy, processing, evidence)
            existing = _find_existing(conn, vacancy, url_norm)
            eff_board = vacancy.board or (existing['board'] if existing else None)
            eff_native_id = vacancy.native_id or (existing['native_id'] if existing else None)
            source_id, linked_posting = _resolve_links(conn, vacancy.provider, eff_board, eff_native_id, url_norm)
            legacy_desc_sha = evidence['complete_description_sha256'] or vacancy.description_sha256 or evidence['excerpt_sha256']

            if existing is None:
                if linked_posting:
                    state = 'linked_posting'
                    counts['linked'] += 1
                elif eff_board and eff_native_id:
                    state = 'verified_identity'
                    counts['verified'] += 1
                else:
                    state = 'new'
                cand_prov = safe({vacancy.mechanism: vacancy.provenance})
                conn.execute(
                    'INSERT INTO jobwatch.discovery_candidates('
                    'candidate_key, provider, board, native_id, company, title, posting_url, location_text, '
                    'country, countries, department, team, employment_type, workplace_type, published_at, '
                    'source_dates, deadlines, description_text, description_excerpt, '
                    'evidence_completeness, evidence_retention_policy, '
                    'complete_description_sha256, excerpt_sha256, description_sha256, evidence_sha256, state, '
                    'first_discovered_at, last_discovered_at, observation_count, discovery_mechanisms, provenance, '
                    'verdict, family, codes, rules_version, screening_input_sha256, screening_basis, screened_at, '
                    'source_id, linked_posting_key) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,'
                    '%s,%s,%s,%s,%s,%s,clock_timestamp(),%s,%s)',
                    (
                        vacancy.candidate_key, vacancy.provider, eff_board, eff_native_id,
                        safe(vacancy.company), safe(vacancy.title), vacancy.posting_url, safe(vacancy.location_text),
                        safe(vacancy.country), [safe(c) for c in vacancy.countries],
                        safe(vacancy.department), safe(vacancy.team), safe(vacancy.employment_type),
                        safe(vacancy.workplace_type), safe(vacancy.published_at),
                        Jsonb(safe(vacancy.source_dates)), Jsonb(safe(vacancy.deadlines)),
                        evidence['description_text'], evidence['description_excerpt'],
                        evidence['evidence_completeness'], evidence['evidence_retention_policy'],
                        evidence['complete_description_sha256'], evidence['excerpt_sha256'],
                        legacy_desc_sha, vacancy.evidence_sha256,
                        state, at, at, [vacancy.mechanism], Jsonb(cand_prov),
                        judgment['verdict'], judgment['family'], judgment['codes'], judgment['rules_version'],
                        judgment['input_sha256'], judgment['basis'], source_id, linked_posting,
                    ),
                )
                snap_id, created = _upsert_snapshot(
                    conn,
                    candidate_key=vacancy.candidate_key,
                    observed_at=at,
                    mechanism=vacancy.mechanism,
                    source_url=vacancy.posting_url,
                    title=vacancy.title,
                    location_text=vacancy.location_text,
                    evidence=evidence,
                    evidence_sha256=vacancy.evidence_sha256,
                    provenance=vacancy.provenance,
                )
                conn.execute(
                    'UPDATE jobwatch.discovery_candidates SET current_snapshot_id=%s WHERE candidate_key=%s',
                    (snap_id, vacancy.candidate_key),
                )
                counts['inserted'] += 1
                counts['snapshots_created' if created else 'snapshots_reobserved'] += 1
            else:
                mechanisms = list(dict.fromkeys(list(existing['discovery_mechanisms']) + [vacancy.mechanism]))
                provenance = dict(existing['provenance'] or {})
                provenance[vacancy.mechanism] = safe(vacancy.provenance)
                old_state = existing['state']
                if old_state in ('promoted_lead', 'dismissed'):
                    state = old_state
                elif linked_posting or existing['linked_posting_key']:
                    state = 'linked_posting'
                    if old_state != 'linked_posting':
                        counts['linked'] += 1
                elif eff_board and eff_native_id:
                    state = 'verified_identity'
                    if old_state != 'verified_identity':
                        counts['verified'] += 1
                else:
                    state = 'observed'

                # Keep existing URL if already on authoritative native posting URL; upgrade search URL when native resolved.
                new_url = vacancy.posting_url if (vacancy.board and vacancy.native_id) else existing['posting_url']
                last_at = max(existing['last_discovered_at'], at)

                # Never degrade complete retained evidence on a metadata-only re-sighting.
                keep_existing_evidence = (
                    evidence['evidence_completeness'] == 'unavailable'
                    and existing['evidence_completeness'] != 'unavailable'
                )
                if keep_existing_evidence:
                    cand_desc_text = existing['description_text']
                    cand_excerpt = existing['description_excerpt']
                    cand_comp = existing['evidence_completeness']
                    cand_policy = existing['evidence_retention_policy']
                    cand_complete_sha = existing['complete_description_sha256']
                    cand_excerpt_sha = existing['excerpt_sha256']
                    cand_desc_sha = existing['description_sha256']
                    cand_ev_sha = existing['evidence_sha256']
                    eff_snap_id = existing['current_snapshot_id']
                else:
                    cand_desc_text = evidence['description_text']
                    cand_excerpt = evidence['description_excerpt']
                    cand_comp = evidence['evidence_completeness']
                    cand_policy = evidence['evidence_retention_policy']
                    cand_complete_sha = evidence['complete_description_sha256']
                    cand_excerpt_sha = evidence['excerpt_sha256']
                    cand_desc_sha = legacy_desc_sha or existing['description_sha256']
                    cand_ev_sha = vacancy.evidence_sha256
                    eff_snap_id = None

                snap_id, created = _upsert_snapshot(
                    conn,
                    candidate_key=existing['candidate_key'],
                    observed_at=at,
                    mechanism=vacancy.mechanism,
                    source_url=new_url,
                    title=vacancy.title,
                    location_text=vacancy.location_text,
                    evidence=evidence,
                    evidence_sha256=vacancy.evidence_sha256,
                    provenance=vacancy.provenance,
                )
                if eff_snap_id is None:
                    eff_snap_id = snap_id

                unchanged_content = (
                    keep_existing_evidence
                    or (
                        cand_desc_text == existing['description_text']
                        and cand_excerpt == existing['description_excerpt']
                        and cand_comp == existing['evidence_completeness']
                        and cand_policy == existing['evidence_retention_policy']
                        and cand_ev_sha == existing['evidence_sha256']
                        and safe(vacancy.company) == existing['company']
                        and safe(vacancy.title) == existing['title']
                        and safe(vacancy.location_text) == existing['location_text']
                        and safe(vacancy.department) == existing['department']
                        and safe(vacancy.team) == existing['team']
                    )
                )

                if unchanged_content:
                    # Omit unchanged TOAST/search columns so PostgreSQL reuses existing TOAST OIDs
                    # and avoids unnecessary GIN index rewrites on repeated observations.
                    conn.execute(
                        'UPDATE jobwatch.discovery_candidates SET '
                        'board=%s, native_id=%s, posting_url=%s, '
                        'current_snapshot_id=%s, state=%s, '
                        'last_discovered_at=%s, observation_count=observation_count+1, discovery_mechanisms=%s, '
                        'provenance=%s, verdict=%s, family=%s, codes=%s, rules_version=%s, '
                        'screening_input_sha256=%s, screening_basis=%s, screened_at=clock_timestamp(), '
                        'source_id=coalesce(%s, source_id), linked_posting_key=coalesce(%s, linked_posting_key) '
                        'WHERE candidate_key=%s',
                        (
                            eff_board, eff_native_id, new_url,
                            eff_snap_id, state,
                            last_at, mechanisms, Jsonb(provenance),
                            judgment['verdict'], judgment['family'], judgment['codes'], judgment['rules_version'],
                            judgment['input_sha256'], judgment['basis'], source_id, linked_posting,
                            existing['candidate_key'],
                        ),
                    )
                else:
                    conn.execute(
                        'UPDATE jobwatch.discovery_candidates SET '
                        'board=%s, native_id=%s, company=%s, title=%s, posting_url=%s, location_text=%s, '
                        'country=%s, countries=%s, department=%s, team=%s, employment_type=%s, workplace_type=%s, '
                        'published_at=coalesce(nullif(%s,\'\'), published_at), source_dates=%s, deadlines=%s, '
                        'description_text=%s, description_excerpt=%s, evidence_completeness=%s, '
                        'evidence_retention_policy=%s, complete_description_sha256=%s, excerpt_sha256=%s, '
                        'description_sha256=%s, evidence_sha256=%s, current_snapshot_id=%s, state=%s, '
                        'last_discovered_at=%s, observation_count=observation_count+1, discovery_mechanisms=%s, '
                        'provenance=%s, verdict=%s, family=%s, codes=%s, rules_version=%s, '
                        'screening_input_sha256=%s, screening_basis=%s, screened_at=clock_timestamp(), '
                        'source_id=coalesce(%s, source_id), linked_posting_key=coalesce(%s, linked_posting_key) '
                        'WHERE candidate_key=%s',
                        (
                            eff_board, eff_native_id, safe(vacancy.company), safe(vacancy.title), new_url,
                            safe(vacancy.location_text), safe(vacancy.country), [safe(c) for c in vacancy.countries],
                            safe(vacancy.department), safe(vacancy.team), safe(vacancy.employment_type),
                            safe(vacancy.workplace_type), safe(vacancy.published_at),
                            Jsonb(safe(vacancy.source_dates or existing['source_dates'])),
                            Jsonb(safe(vacancy.deadlines or existing['deadlines'])),
                            cand_desc_text, cand_excerpt, cand_comp, cand_policy,
                            cand_complete_sha, cand_excerpt_sha, cand_desc_sha, cand_ev_sha, eff_snap_id, state,
                            last_at, mechanisms, Jsonb(provenance),
                            judgment['verdict'], judgment['family'], judgment['codes'], judgment['rules_version'],
                            judgment['input_sha256'], judgment['basis'], source_id, linked_posting,
                            existing['candidate_key'],
                        ),
                    )
                counts['updated'] += 1
                counts['snapshots_created' if created else 'snapshots_reobserved'] += 1
    return counts


def rescreen_candidates(conn, *, processing: Processing = Processing()) -> int:
    """Offline rescreening of stored discovery candidates using complete description when retained."""
    updated = 0
    with conn.transaction():
        rows = conn.execute('SELECT * FROM jobwatch.discovery_candidates ORDER BY candidate_key FOR UPDATE').fetchall()
        for row in rows:
            has_complete = row.get('evidence_completeness') == 'complete' and bool(row.get('description_text'))
            desc = row.get('description_text') or row['description_excerpt'] or None
            res = processing.screen({
                'title': row['title'],
                'location': row['location_text'],
                'country': row['country'],
                'countries': list(row['countries'] or ()),
                'department': row['department'],
                'team': row['team'],
                'employment_type': row['employment_type'],
                'description': desc,
            })
            basis = 'full' if has_complete else ('excerpt' if desc else 'title_metadata_only')
            if (row['verdict'], row['family'], list(row['codes']), row['rules_version'], row['screening_input_sha256'], row['screening_basis']) != (
                res['verdict'], res['family'], res['codes'], res['rules_version'], res['input_sha256'], basis
            ):
                conn.execute(
                    'UPDATE jobwatch.discovery_candidates SET verdict=%s, family=%s, codes=%s, rules_version=%s, '
                    'screening_input_sha256=%s, screening_basis=%s, screened_at=clock_timestamp() WHERE candidate_key=%s',
                    (res['verdict'], res['family'], res['codes'], res['rules_version'], res['input_sha256'], basis, row['candidate_key']),
                )
                updated += 1
    return updated


def link_candidates_for_source(conn, source_id: int, *, run_id=None, linked_at: datetime | None = None) -> dict:
    """Link discovery candidates to native postings immediately following source collection.

    Links ONLY where identity is unambiguously established:
      1. Exact native ATS identity `(provider, lower(board), native_id)` matching a collected
         posting on `source_id`.
      2. Unambiguous 1-to-1 exact normalized URL match (`url_exact`) for search-only candidates
         (`board IS NULL`), refusing any URL shared by multiple postings.

    Preserves owner-curated states (`promoted_lead`, `dismissed`) and appends linkage provenance.
    """
    from psycopg.types.json import Jsonb

    at = linked_at or datetime.now(timezone.utc)
    at_iso = iso(at)
    src = conn.execute(
        'SELECT id, provider, board FROM jobwatch.sources WHERE id=%s',
        (source_id,),
    ).fetchone()
    if not src:
        return {'linked_native': 0, 'linked_exact_url': 0, 'source_associated': 0}

    linked_native = 0
    linked_url = 0
    run_str = str(run_id) if run_id is not None else None

    # Pass 1: exact native ATS identity match (provider + lower(board) + native_id).
    native_matches = conn.execute(
        "SELECT dc.candidate_key, dc.state, dc.provenance, p.posting_key "
        "FROM jobwatch.discovery_candidates dc "
        "JOIN jobwatch.postings p ON p.source_id = %s "
        "  AND p.provider = dc.provider "
        "  AND lower(p.board) = lower(dc.board) "
        "  AND p.native_id = dc.native_id "
        "WHERE dc.linked_posting_key IS NULL "
        "  AND dc.provider = %s AND lower(dc.board) = lower(%s) AND dc.native_id IS NOT NULL "
        "ORDER BY dc.candidate_key FOR UPDATE OF dc",
        (source_id, src['provider'], src['board']),
    ).fetchall()
    for row in native_matches:
        prov = dict(row['provenance'] or {})
        prov['linked_on_collection'] = {
            'posting_key': row['posting_key'],
            'source_id': source_id,
            'run_id': run_str,
            'linked_at': at_iso,
            'match_basis': 'exact_native_identity',
        }
        new_state = row['state'] if row['state'] in ('promoted_lead', 'dismissed') else 'linked_posting'
        conn.execute(
            'UPDATE jobwatch.discovery_candidates '
            'SET linked_posting_key=%s, source_id=%s, state=%s, provenance=%s '
            'WHERE candidate_key=%s',
            (row['posting_key'], source_id, new_state, Jsonb(safe(prov)), row['candidate_key']),
        )
        linked_native += 1

    # Pass 2: unambiguous 1-to-1 exact normalized URL match for search-only candidates (board IS NULL).
    url_matches = conn.execute(
        "WITH unique_urls AS ("
        "  SELECT pv.url_exact, min(p.posting_key) AS posting_key, min(p.source_id) AS source_id "
        "  FROM jobwatch.postings p "
        "  JOIN jobwatch.posting_versions pv ON pv.id = p.current_version_id "
        "  GROUP BY pv.url_exact HAVING count(DISTINCT p.posting_key) = 1"
        ") "
        "SELECT dc.candidate_key, dc.state, dc.provenance, u.posting_key "
        "FROM jobwatch.discovery_candidates dc "
        "JOIN unique_urls u ON u.url_exact = dc.url_exact AND u.source_id = %s "
        "WHERE dc.linked_posting_key IS NULL AND dc.board IS NULL "
        "ORDER BY dc.candidate_key FOR UPDATE OF dc",
        (source_id,),
    ).fetchall()
    for row in url_matches:
        prov = dict(row['provenance'] or {})
        prov['linked_on_collection'] = {
            'posting_key': row['posting_key'],
            'source_id': source_id,
            'run_id': run_str,
            'linked_at': at_iso,
            'match_basis': 'unambiguous_exact_url',
        }
        new_state = row['state'] if row['state'] in ('promoted_lead', 'dismissed') else 'linked_posting'
        conn.execute(
            'UPDATE jobwatch.discovery_candidates '
            'SET linked_posting_key=%s, source_id=%s, state=%s, provenance=%s '
            'WHERE candidate_key=%s',
            (row['posting_key'], source_id, new_state, Jsonb(safe(prov)), row['candidate_key']),
        )
        linked_url += 1

    # Associate source_id on any remaining same-board candidates without fabricating posting links.
    cur = conn.execute(
        "UPDATE jobwatch.discovery_candidates "
        "SET source_id = %s "
        "WHERE source_id IS NULL AND provider = %s AND lower(board) = lower(%s)",
        (source_id, src['provider'], src['board']),
    )
    return {
        'linked_native': linked_native,
        'linked_exact_url': linked_url,
        'source_associated': cur.rowcount or 0,
    }


def promote_to_lead(
    conn,
    candidate_key: str,
    *,
    actor: str,
    official: bool = False,
    source_note: str | None = None,
    checked_at: datetime | None = None,
    checked_by: str = 'agent_on_owner_instruction',
) -> dict:
    """Owner-only audited promotion of a discovered candidate into `jobwatch.leads`."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError('Explicit owner actor required for lead promotion')
    at = checked_at or datetime.now(timezone.utc)
    iso(at)
    with conn.transaction():
        conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (actor,))
        row = conn.execute(
            'SELECT * FROM jobwatch.discovery_candidates WHERE candidate_key=%s FOR UPDATE',
            (candidate_key,),
        ).fetchone()
        if row is None:
            raise ValueError('Unknown discovery candidate')
        if row['promoted_lead_key']:
            return {'candidate_key': candidate_key, 'lead_key': row['promoted_lead_key'], 'status': 'already_promoted'}
        slug = re.sub(r'[^a-z0-9:._-]+', '-', candidate_key.lower()).strip('-')
        lead_key = f'vacancy:{slug}'[:200]
        note = source_note or (
            f"Promoted from discovery_candidates ({','.join(row['discovery_mechanisms'])}); "
            f"evidence_sha256={row['evidence_sha256']}"
        )
        wording = f"Discovered {row['location_text'] or 'location unknown'}; published {row['published_at'] or 'unknown'}"
        conn.execute(
            "INSERT INTO jobwatch.leads(lead_key, kind, company, title, state, url, official, source_note, wording, "
            "posting_key, checked_at, checked_by) "
            "VALUES (%s, 'vacancy', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                lead_key, row['company'], row['title'],
                'listed' if official else 'unverified',
                row['posting_url'], bool(official), note, wording,
                row['linked_posting_key'], at, checked_by,
            ),
        )
        conn.execute(
            "UPDATE jobwatch.discovery_candidates SET state='promoted_lead', promoted_lead_key=%s WHERE candidate_key=%s",
            (lead_key, candidate_key),
        )
    return {'candidate_key': candidate_key, 'lead_key': lead_key, 'status': 'promoted'}
