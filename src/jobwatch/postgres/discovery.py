"""Minimal PostgreSQL lifecycle for discovered vacancies outside monitored boards.

Lifecycle states in `jobwatch.discovery_candidates`:
  new -> observed -> verified_identity -> linked_posting
                                       -> promoted_lead
                                       -> dismissed

Discovery persistence NEVER creates or alters `postings`, `posting_versions`,
`sources`, `leads`, `triage`, or `applications` without explicit owner action.
Disappearing from discovery never infers closure or board absence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from jobwatch import filtering
from jobwatch.discovery.workable_search import DiscoveredVacancy
from jobwatch.identity import exact_url
from .collection import Processing, iso, safe


def _screen_vacancy(vacancy: DiscoveredVacancy, processing: Processing) -> dict:
    has_full = bool(vacancy.description_full)
    inputs = {
        'title': vacancy.title,
        'location': vacancy.location_text,
        'country': vacancy.country,
        'countries': list(vacancy.countries),
        'department': vacancy.department,
        'team': vacancy.team,
        'employment_type': vacancy.employment_type,
        'description': vacancy.description_full if has_full else (vacancy.description_excerpt or None),
    }
    res = processing.screen(inputs)
    if res['rules_version'] != processing.rules_version:
        raise ValueError('Screener does not match its pinned rules version')
    basis = 'full' if has_full else ('excerpt' if vacancy.description_excerpt else 'title_metadata_only')
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


def record_discoveries(
    conn,
    vacancies,
    *,
    observed_at: datetime | None = None,
    processing: Processing = Processing(),
) -> dict:
    """Upsert discovered public vacancies with cross-mechanism deduplication and auto-linking."""
    from psycopg.types.json import Jsonb

    at = observed_at or datetime.now(timezone.utc)
    iso(at)
    counts = {'inserted': 0, 'updated': 0, 'linked': 0, 'verified': 0}

    with conn.transaction():
        for vacancy in vacancies:
            url_norm = exact_url(vacancy.posting_url)
            judgment = _screen_vacancy(vacancy, processing)
            existing = _find_existing(conn, vacancy, url_norm)
            eff_board = vacancy.board or (existing['board'] if existing else None)
            eff_native_id = vacancy.native_id or (existing['native_id'] if existing else None)
            source_id, linked_posting = _resolve_links(conn, vacancy.provider, eff_board, eff_native_id, url_norm)

            if existing is None:
                if linked_posting:
                    state = 'linked_posting'
                    counts['linked'] += 1
                elif eff_board and eff_native_id:
                    state = 'verified_identity'
                    counts['verified'] += 1
                else:
                    state = 'new'
                conn.execute(
                    'INSERT INTO jobwatch.discovery_candidates('
                    'candidate_key, provider, board, native_id, company, title, posting_url, location_text, '
                    'country, countries, department, team, employment_type, workplace_type, published_at, '
                    'source_dates, deadlines, description_excerpt, description_sha256, evidence_sha256, state, '
                    'first_discovered_at, last_discovered_at, observation_count, discovery_mechanisms, provenance, '
                    'verdict, family, codes, rules_version, screening_input_sha256, screening_basis, screened_at, '
                    'source_id, linked_posting_key) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,'
                    '%s,%s,%s,%s,%s,%s,clock_timestamp(),%s,%s)',
                    (
                        vacancy.candidate_key, vacancy.provider, eff_board, eff_native_id,
                        safe(vacancy.company), safe(vacancy.title), vacancy.posting_url, safe(vacancy.location_text),
                        safe(vacancy.country), [safe(c) for c in vacancy.countries],
                        safe(vacancy.department), safe(vacancy.team), safe(vacancy.employment_type),
                        safe(vacancy.workplace_type), safe(vacancy.published_at),
                        Jsonb(safe(vacancy.source_dates)), Jsonb(safe(vacancy.deadlines)),
                        safe(vacancy.description_excerpt), vacancy.description_sha256, vacancy.evidence_sha256,
                        state, at, at, [vacancy.mechanism], Jsonb(safe({vacancy.mechanism: vacancy.provenance})),
                        judgment['verdict'], judgment['family'], judgment['codes'], judgment['rules_version'],
                        judgment['input_sha256'], judgment['basis'], source_id, linked_posting,
                    ),
                )
                counts['inserted'] += 1
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
                new_excerpt = safe(vacancy.description_excerpt) or existing['description_excerpt']
                new_desc_sha = vacancy.description_sha256 or existing['description_sha256']
                last_at = max(existing['last_discovered_at'], at)

                conn.execute(
                    'UPDATE jobwatch.discovery_candidates SET '
                    'board=%s, native_id=%s, company=%s, title=%s, posting_url=%s, location_text=%s, '
                    'country=%s, countries=%s, department=%s, team=%s, employment_type=%s, workplace_type=%s, '
                    'published_at=coalesce(nullif(%s,\'\'), published_at), source_dates=%s, deadlines=%s, '
                    'description_excerpt=%s, description_sha256=%s, evidence_sha256=%s, state=%s, '
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
                        new_excerpt, new_desc_sha, vacancy.evidence_sha256, state,
                        last_at, mechanisms, Jsonb(provenance),
                        judgment['verdict'], judgment['family'], judgment['codes'], judgment['rules_version'],
                        judgment['input_sha256'], judgment['basis'], source_id, linked_posting,
                        existing['candidate_key'],
                    ),
                )
                counts['updated'] += 1
    return counts


def rescreen_candidates(conn, *, processing: Processing = Processing()) -> int:
    """Offline rescreening of stored discovery candidates from retained metadata and excerpt."""
    updated = 0
    with conn.transaction():
        rows = conn.execute('SELECT * FROM jobwatch.discovery_candidates ORDER BY candidate_key FOR UPDATE').fetchall()
        for row in rows:
            desc = row['description_excerpt'] or None
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
            basis = 'excerpt' if desc else 'title_metadata_only'
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
