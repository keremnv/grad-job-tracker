"""One-time mapping of frozen legacy records. No network, writes, rescreening or native reconstruction."""
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import unicodedata
from uuid import UUID, uuid5

from jobwatch import applications, config, feeds, identity, leads, state, triage
from .collection import content, iso, safe
from .evidence import encode_evidence
from .git_snapshot import FILES, SHA, GitSnapshot, ImportProblem

NAMESPACE = UUID('49b783dd-723a-5af1-82cb-4288a663db41')
TABLES = ('employers', 'sources', 'collection_runs', 'postings', 'posting_versions',
          'posting_events', 'leads', 'verifications', 'triage', 'applications')


def digest(value):
    return sha256(encode_evidence(value if isinstance(value, dict) else {'value': value})).hexdigest()


def stamp(value):
    return iso(config.timestamp(value)) if value is not None else None


def slug(value):
    ascii_text = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower()
    result = re.sub('[^a-z0-9]+', '-', ascii_text).strip('-')
    if not result:
        raise ImportProblem('Empty legacy identity after slug conversion')
    return result


class FrozenFile:
    def __init__(self, raw):
        self.raw = raw
    def read_text(self, encoding='utf-8'):
        return self.raw.decode(encoding)


def references(value, pointer=''):
    """Only recorded description references/inline legacy text; never reverse a native parser."""
    if not isinstance(value, dict):
        return
    for name, entry in value.items():
        here = pointer + '/' + name
        if name == 'raw':
            continue
        if name == 'description_sha256':
            values = entry.items() if isinstance(entry, dict) else [('', entry)]
            for side, sha in values:
                if sha is not None:
                    if not isinstance(sha, str) or not SHA.fullmatch(sha):
                        raise ImportProblem('Invalid recorded description SHA-256')
                    yield sha, None, here + ('/' + side if side else '')
        elif name == 'description':
            values = entry.items() if isinstance(entry, dict) else [('', entry)]
            for side, text in values:
                if isinstance(text, str):
                    yield state.text_hash(text), text, here + ('/' + side if side else '')
        if isinstance(entry, dict):
            yield from references(entry, here)
        elif isinstance(entry, list):
            for index, item in enumerate(entry):
                yield from references(item, here + '/' + str(index))


def normalized(row, texts):
    sha = row.get('description_sha256')
    return {'company': row['company'], 'title': row['title'], 'posting_url': row['url'],
        'location_text': row.get('location', ''), 'country': row.get('country', ''),
        'countries': row.get('countries', []), 'department': row.get('department', ''),
        'team': row.get('team', ''), 'employment_type': row.get('employment_type', ''),
        'salary': row.get('salary', ''), 'published_at': row.get('published_at', ''),
        'source_dates': row.get('source_dates', {}), 'deadlines': row.get('deadlines', []),
        'description_text': texts[sha]['text'] if sha in texts else None, 'extra_fields': {}}


@dataclass(frozen=True)
class ImportPlan:
    commit: str
    rows: dict = field(repr=False)
    manifest: dict = field(repr=False)

    @property
    def sha256(self):
        return digest({'commit': self.commit, 'rows': self.rows, 'manifest': self.manifest})

    def report(self):
        return {**self.manifest, 'plan_sha256': self.sha256,
                'counts': {t: len(rows) for t, rows in self.rows.items()}}


def build_plan(repo, commit, **bounds):
    """Validate and prepare all source data before opening a target transaction."""
    git = GitSnapshot(repo, commit, **bounds)
    try:
        return _build(git)
    except ImportProblem:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise ImportProblem('Invalid or unsupported legacy structure (' + type(exc).__name__ + '); no import performed') from None


def _build(git):
    commit = git.commit
    def uid(kind, value):
        return str(uuid5(NAMESPACE, encode_evidence({'commit':commit, 'kind':kind, 'identity':value}).decode('ascii')))
    tables = {t: [] for t in TABLES}
    files = {p: {'present': (raw := git.blob(commit, p)) is not None,
                 'sha256': sha256(raw).hexdigest() if raw is not None else None} for p in FILES}
    current = git.document(commit, 'data/jobs.json', required=True)
    cfg_raw = git.blob(commit, 'config/employers.json', required=True)
    # Strict JSON decoding precedes reuse of existing semantic validators.
    cfg = git.document(commit, 'config/employers.json', required=True)
    config.load_config(FrozenFile(cfg_raw))
    lead_raw = git.blob(commit, 'data/leads.json')
    lead_doc = git.document(commit, 'data/leads.json')
    if lead_doc is not None:
        leads.load_leads(FrozenFile(lead_raw))
    app_raw = git.blob(commit, 'data/applications.csv')
    apps = applications.parse_csv(app_raw) if app_raw is not None else []
    decisions = triage.parse(git.blob(commit, 'data/triage.json'))
    texts = git.text_objects()
    refs, snapshots, healths = defaultdict(lambda: defaultdict(list)), [], []
    verification_docs, archive, seen_documents, index_roots = [], [], set(), []
    limitations, transformations = [], []
    events = {}
    for revision_index, revision in enumerate(git.commits):
        doc = git.document(revision, 'data/jobs.json')
        health = git.document(revision, 'data/jobs-health.json')
        if health is not None and (health.get('schema') != 1 or not isinstance(health.get('boards'), dict)):
            raise ImportProblem('Invalid frozen health document')
        if doc is not None:
            state._validate(doc)
            index_roots.append({'commit':revision, 'root':{k:v for k,v in doc.items() if k!='postings'}})
            upgraded = state.upgrade(deepcopy(doc))
            scan = (health or doc).get('last_scan') or {}
            for key, row in doc['postings'].items():
                if not identity.is_posting_key(key) or key != f"{row['provider']}:{row['board'].lower()}:{row['external_id']}":
                    raise ImportProblem('Posting identity disagrees with retained namespace/native ID')
                if isinstance(row.get('raw'),dict):
                    if feeds._identity(row['raw'].get('id')) != row['external_id']:
                        raise ImportProblem('Native evidence ID disagrees with its legacy posting')
                if key not in current['postings']:
                    raise ImportProblem('Historical-only posting requires explicit reconciliation; refusing to drop it')
                where = {'commit': revision, 'path': 'data/jobs.json', 'pointer': '/postings/' + key}
                for sha, inline, pointer in references(row):
                    refs[key][sha].append({**where, 'pointer': where['pointer'] + pointer})
                    if inline is not None:
                        old = texts.setdefault(sha, {'text': inline, 'provenance': []})
                        if old['text'] != inline:
                            raise ImportProblem('Conflicting inline description hash')
                        old['provenance'].append({**where, 'pointer': where['pointer'] + pointer, 'kind': 'inline'})
                snap = upgraded['postings'][key]
                if snap.get('description_sha256') in texts and 'description_chars' in snap:
                    if snap['description_chars'] != len(texts[snap['description_sha256']]['text']):
                        raise ImportProblem('Description character count mismatch')
                snapshots.append((revision_index, revision, key, row, snap, scan))
                if isinstance(row.get('application_verification'),dict):
                    verification_docs.append((revision,{'postings':{key:row['application_verification']}},
                                              'data/jobs.json','/application_verification'))
                for name, category in (('history', 'mixed'), ('legacy_history', 'mixed'),
                                       ('source_events', 'source'), ('screening_events', 'screening')):
                    occurrences = Counter()
                    for index, event in enumerate(row.get(name, [])):
                        stamp(event['at'])
                        h = digest(event); occurrence = occurrences[h]; occurrences[h] += 1
                        ek = (key, category, h, occurrence)
                        item = events.setdefault(ek, {'original_json': encode_evidence(event).decode('ascii'),
                            'legacy_category': category, 'event_sha256': h, 'legacy_id': uid('event', ek),
                            'provenance': [], 'order': (revision_index, index), 'event': event})
                        item['provenance'].append({**where, 'pointer': where['pointer'] + f'/{name}/{index}'})
            for source_key, record in (health or doc).get('boards', {}).items():
                healths.append((revision, source_key, record, scan))
        elif health:
            raise ImportProblem('Health exists without a retained index')
        ver = git.document(revision, 'data/verifications.json')
        if ver is not None:
            if ver.get('schema') != 1 or not isinstance(ver.get('postings'), dict):
                raise ImportProblem('Invalid verification ledger')
            verification_docs.append((revision, ver, 'data/verifications.json', ''))
            for key, row in ver['postings'].items():
                if key not in current['postings']:
                    raise ImportProblem('Verification references an unknown posting')
                for sha, inline, pointer in references(row):
                    refs[key][sha].append({'commit': revision, 'path': 'data/verifications.json', 'pointer': '/postings/' + key + pointer})
        # Private historical owner/control files are retained as labelled import archives,
        # not replayed as guessed owner actions. Current fields are also projected below.
        for path in ('config/employers.json', 'data/leads.json', 'data/triage.json', 'data/applications.csv',
                     'data/verifications.json', 'data/jobs-health.json'):
            raw = git.blob(revision, path)
            if raw is not None and (path, sha256(raw).hexdigest()) not in seen_documents:
                seen_documents.add((path, sha256(raw).hexdigest()))
                archive.append({'commit': revision, 'path': path, 'sha256': sha256(raw).hexdigest(),
                                'content_utf8': raw.decode('utf-8')})
    state._validate(current)
    current_decoded = state.upgrade(deepcopy(current))
    missing = [{'posting_key': key, 'sha256': sha, 'references': places, 'available': False}
               for key, hashes in sorted(refs.items()) for sha, places in sorted(hashes.items()) if sha not in texts]
    if missing:
        limitations.append({'code': 'unavailable_description', 'references': missing})
    used_texts = {sha for hashes in refs.values() for sha in hashes}
    orphans = sorted(set(texts) - used_texts)
    if orphans:
        raise ImportProblem('Unattributed retained description objects require explicit mapping; refusing to discard them')
    if any((git.document(r, 'data/jobs.json') or {}).get('schema') == 1 for r in git.commits):
        limitations.append({'code': 'schema1_exclusions_not_retained', 'detail': 'Unindexed rejected roles before schema 2 cannot be reconstructed.'})
    # Keep complete original root metadata, not a second writable legacy runtime.
    roots = {'commit': commit, 'files': files, 'index_root': {k: v for k, v in current.items() if k != 'postings'},
             'config_root': {k: v for k, v in cfg.items() if k != 'employers'},
             'leads_root': {k: v for k, v in (lead_doc or {}).items() if k not in ('vacancies', 'programmes')},
             'index_roots':index_roots, 'private_control_archive': archive}
    by_company, used_slugs, source_cfg = {}, set(), {}
    for row in sorted(cfg['employers'], key=lambda r: r['company'].casefold()):
        name, eid = row['company'], slug(row['company'])
        if name.casefold() in by_company or eid in used_slugs:
            raise ImportProblem('Ambiguous employer identity/slug collision')
        by_company[name.casefold()] = eid; used_slugs.add(eid)
        details = {'legacy_config': row}
        if not tables['employers']:
            details['legacy_import_json'] = encode_evidence(roots).decode('ascii')
        tables['employers'].append({'slug': eid, 'name': name, 'careers_url': row.get('careers_url'),
            'watch_status': row.get('watch_status', 'unverified'), 'watch_checked_at': stamp(row.get('watch_checked_at')),
            'notes': row.get('relevance_notes'), 'details': safe(details)})
        if row.get('provider') in ('greenhouse', 'lever', 'ashby') and row.get('board'):
            sk = row['provider'] + ':' + row['board'].lower()
            if sk in source_cfg:
                raise ImportProblem('Duplicate source namespace, including disabled configurations')
            source_cfg[sk] = row
    required_sources = {r['provider'] + ':' + r['board'].lower() for r in current['postings'].values()} | {s for _, s, _, _ in healths}
    if not required_sources <= source_cfg.keys():
        raise ImportProblem('Posting/health source has no unambiguous current employer mapping')
    activation = []
    for sk, row in sorted(source_cfg.items()):
        provider, board = sk.split(':')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', row['board']) or row.get('region', 'global') not in (('global',) if provider == 'ashby' else ('global', 'eu')):
            raise ImportProblem('Invalid source identity/region')
        # Legacy notes are real association evidence, but an unrecorded verifier remains labelled legacy.
        evidence = row.get('mapping_evidence') or row.get('verification_notes')
        enabled = row.get('enabled', False)
        checked = row.get('endpoint_checked_at')
        eligible = all((row.get('validation_url'), checked, row.get('mapping_verified_at'), evidence))
        if enabled and not eligible:
            activation.append({'source': sk, 'legacy_enabled': True,
                'missing': [k for k,v in {'validation_url':row.get('validation_url'), 'endpoint_checked_at':checked,
                    'mapping_verified_at':row.get('mapping_verified_at'), 'mapping_evidence':evidence}.items() if not v]})
        tables['sources'].append({'identity': sk, 'provider': provider, 'board': row['board'],
            'region': row.get('region', 'global'), 'employer_id': by_company[row['company'].casefold()],
            'status': 'monitored' if enabled and eligible else 'candidate' if enabled else 'paused',
            'validation_url': row.get('validation_url') or None, 'endpoint_checked_at': stamp(checked),
            'mapping_verified_at': stamp(row.get('mapping_verified_at')),
            'mapping_verified_by': row.get('mapping_verified_by') or ('unrecorded_legacy' if row.get('mapping_verified_at') else None),
            'mapping_evidence': evidence or None, 'discovered_by': 'import:' + commit,
            'processing_revision': 'pipeline-v1', 'last_published_run_id': None})
    # Distinct health snapshots retain complete original counters/status/scan summaries and their provenance.
    run_map = {}
    for revision, sk, record, scan in healths:
        marker = (sk, digest({'health': record, 'scan': scan}))
        rid = uid('health', marker)
        provenance = {'commit': revision, 'path': 'data/jobs-health.json' if git.blob(revision, 'data/jobs-health.json') is not None else 'data/jobs.json'}
        if marker in run_map:
            run_map[marker]['details']['provenance'].append(provenance)
            continue
        for field in ('count', 'source_records', 'skipped_non_vacancies'):
            if field in record and record[field] is not None and (type(record[field]) is not int or record[field] < 0):
                raise ImportProblem('Invalid legacy health counter')
        row = {'id': rid, 'source_id': sk, 'mode': 'import', 'status': 'imported',
            'fetched_at': stamp(record.get('last_success')), 'request_version': 'legacy-import-v1',
            'processing_revision': 'legacy-import-v1', 'source_records': record.get('source_records'),
            'vacancies': record.get('count'), 'skipped_non_vacancies': record.get('skipped_non_vacancies'),
            'absence_safe': False, 'error': None, 'ci_run': None, 'payload_sha256': None,
            'actor': 'import:' + commit, 'details': {'legacy_health_json': encode_evidence(record).decode('ascii'),
                'legacy_scan_json': encode_evidence(scan).decode('ascii'), 'provenance': [provenance],
                'legacy_order_time':stamp(record.get('last_attempt') or scan.get('at') or record.get('last_success'))}}
        run_map[marker] = row
    tables['collection_runs'] = sorted(run_map.values(), key=lambda r: (r['details']['legacy_order_time'] or '', r['source_id'], r['id']))
    for src in tables['sources']:
        candidates = [r for r in tables['collection_runs'] if r['source_id'] == src['identity'] and r['fetched_at']]
        if candidates:
            # The latest known success, not a new published collection or a claim about posting last_seen.
            src['last_published_run_id'] = max(candidates, key=lambda r: (r['fetched_at'], tables['collection_runs'].index(r)))['id']
    versions, current_versions, positive = {}, {}, defaultdict(list)
    last_seen_bases = Counter()
    def make_version(key, original, row, revision, scan, native=False):
        n = normalized(row, texts); sanitized = safe(n)
        descs = {sha: {'available': sha in texts, **(texts[sha] if sha in texts else {}), 'references': places}
                 for sha, places in sorted(refs[key].items())}
        blob = original['raw'] if native else {'legacy_row': original, 'descriptions': descs}
        normalizer = 'legacy-schema1-retained' if native else 'legacy-index-v' + str(1 if 'screening' not in original else 2)
        marker = [key, digest(blob), normalizer, digest(content(sanitized))]
        vid = uid('version', marker)
        # The legacy upgrade removes listed-row last_seen. Retain the original
        # explicit sighting before falling back to the snapshot's reference clock.
        observed = original.get('last_seen') or row.get('last_seen') or scan.get('at') or git.time(revision)
        basis = 'row.last_seen' if original.get('last_seen') or row.get('last_seen') else 'legacy_scan_reference' if scan.get('at') else 'git_commit_time_not_fetch'
        capture = {'commit': revision, 'path': 'data/jobs.json', 'pointer': '/postings/' + key,
                   'observed_at_basis': basis}
        if native:
            capture['legacy_representation_json'] = encode_evidence({k:v for k,v in original.items() if k!='raw'}).decode('ascii')
        if vid not in versions:
            versions[vid] = {'id': vid, 'posting_key': key, 'source_id': row['provider'] + ':' + row['board'].lower(),
                'origin': 'git_history' if native else 'legacy_index', 'evidence': encode_evidence(blob).decode('ascii'),
                'evidence_sha256': digest(blob), 'first_run_id': None, 'observed_at': stamp(observed),
                'provenance': {'captures': []}, 'normalizer_version': normalizer,
                'normalized_sha256': digest(content(sanitized)), 'nul_replaced': n != sanitized, **sanitized}
        if capture not in versions[vid]['provenance']['captures']:
            versions[vid]['provenance']['captures'].append(capture)
        return vid
    for _, revision, key, original, row, scan in snapshots:
        vid = make_version(key, original, row, revision, scan)
        if revision == commit:
            current_versions[key] = vid
        if isinstance(original.get('raw'), dict):
            make_version(key, original, row, revision, scan, native=True)
        if original.get('last_seen'):
            positive[key].append((stamp(original['last_seen']), 'retained_row.last_seen'))
    for key, row in sorted(current_decoded['postings'].items()):
        if row.get('first_seen_basis') in ('observed','index_backfill'):
            positive[key].append((stamp(row['first_seen']), 'first_observation'))
        for event in row.get('source_events', []):
            if event['type'] in ('changed','reappeared','new','backfilled'):
                positive[key].append((stamp(event['at']), 'source_event'))
        # Never derive a per-posting sighting from board.last_success.
        last = stamp(row.get('last_seen')) or (max(positive[key])[0] if positive[key] else None)
        last_seen_bases['retained_current_row' if row.get('last_seen') else max(positive[key])[1] if positive[key] else 'unknown'] += 1
        s = row['screening']
        if row['feed_status'] == 'not_seen' and not row.get('missing_since'):
            raise ImportProblem('not_seen with unknown missing_since is unsupported by the current schema')
        tables['postings'].append({'posting_key': key, 'source_id': row['provider'] + ':' + row['board'].lower(),
            'provider': row['provider'], 'board': row['board'], 'native_id': row['external_id'], 'status': row['feed_status'],
            'first_seen_at': stamp(row['first_seen']), 'first_seen_basis': row.get('first_seen_basis','legacy_retained'),
            'last_seen_at': last, 'missing_since': stamp(row.get('missing_since')), 'current_version_id': current_versions[key],
            'verdict': s['verdict'], 'family': s['family'], 'codes': s['codes'], 'rules_version': s['rules_version'],
            'screening_input_sha256': s.get('input_sha256'), 'screening_basis': s['basis'],
            'screened_at': stamp(s['screened_at']), 'last_seen_run_id': None})
    tables['posting_versions'] = sorted(versions.values(), key=lambda r: (r['posting_key'],r['id']))
    legacy_current_count = sum(not isinstance(row.get('raw'),dict) for row in current['postings'].values())
    if legacy_current_count:
        limitations.append({'code':'current_native_not_archived','count':legacy_current_count,
            'commit':commit,'path':'data/jobs.json',
            'detail':'Normalized current snapshots are retained honestly; older native captures do not establish current native wording.'})
    no_text = [key for key,vid in sorted(current_versions.items()) if versions[vid]['description_text'] is None]
    if no_text:
        limitations.append({'code':'current_description_not_retained','posting_keys':no_text})
    for (key, category, h, occurrence), entry in sorted(events.items(), key=lambda pair: (stamp(pair[1]['event']['at']),pair[1]['order'],pair[0])):
        original = entry['event']
        linked = [{'sha256': sha, 'available': sha in texts, 'version_id': current_versions[key]}
                  for sha, _, _ in references(original)]
        details = {k:v for k,v in entry.items() if k not in ('event','order')}
        details['description_references'] = linked
        tables['posting_events'].append({'posting_key': key, 'source_id': key.rsplit(':',1)[0], 'run_id': None,
            'observed_at': stamp(original['at']), 'category': 'import', 'kind': 'legacy_event',
            'before_version_id': None, 'after_version_id': None, 'comparison_version': None,
            'changes': details, 'actor': 'import:' + commit})
    # Independently observed verification versions, including checks overwritten in later ledgers.
    checks = {}
    for revision, doc, path, suffix in verification_docs:
        for key, row in sorted(doc['postings'].items()):
            candidates = []
            if row.get('status') not in ('unknown','open_verified','closed_verified'):
                raise ImportProblem('Unknown verification status')
            at = row.get('verified_at') or row.get('checked_at')
            if at:
                candidates.append(('application_status', {'checked_at': at, 'url':row.get('url'),
                    'method':row.get('method'), 'status':row['status'], 'checked_by':row.get('verified_by','unrecorded_legacy')}))
            elif row.get('status') != 'unknown':
                limitations.append({'code':'verification_without_check_time', 'posting_key':key, 'commit':revision})
            for d in row.get('deadlines',[]):
                candidates.append(('deadline', {'checked_at':d['verified_at'], 'url':d['url'], 'wording':d['wording'],
                    'value':d.get('value'), 'checked_by':d.get('verified_by','unrecorded_legacy')}))
            if row.get('description_verified_at'):
                candidates.append(('description', {'checked_at':row['description_verified_at'], 'url':row.get('description_url'),
                    'description_sha256':row['description_sha256'], 'checked_by':row.get('description_verified_by','unrecorded_legacy')}))
            for kind, fields in candidates:
                marker = [key, kind, fields]
                vid = uid('verification', marker)
                if vid not in checks:
                    check = {'id':vid, 'posting_key':key,'kind':kind,'actor':'import:'+commit,
                        'checked_at':stamp(fields['checked_at']), 'checked_by':fields['checked_by'], 'url':fields['url'],
                        'method':fields.get('method'), 'status':fields.get('status'), 'wording':fields.get('wording'),
                        'value':fields.get('value'), 'description_sha256':fields.get('description_sha256'),
                        'version_id': current_versions[key] if kind=='description' and fields['description_sha256'] in texts else None,
                        'legacy_details': {'provenance':[], 'records':[]}}
                    checks[vid] = check
                p = {'commit':revision, 'path':path, 'pointer':'/postings/'+key+suffix}
                if p not in checks[vid]['legacy_details']['provenance']:
                    checks[vid]['legacy_details']['provenance'].append(p)
                encoded = encode_evidence(row).decode('ascii')
                if encoded not in checks[vid]['legacy_details']['records']:
                    checks[vid]['legacy_details']['records'].append(encoded)
    tables['verifications'] = sorted(checks.values(),key=lambda r:(r['posting_key'],r['checked_at'],r['kind'],r['id']))
    vacancy_urls, programmes = set(), set()
    for kind, collection in (('vacancy','vacancies'),('programme','programmes')):
        for row in (lead_doc or {}).get(collection,[]):
            employer = by_company.get(row['company'].casefold())
            if kind == 'vacancy':
                url = identity.exact_url(row['url'])
                if url in vacancy_urls:
                    raise ImportProblem('Duplicate exact vacancy lead URL')
                vacancy_urls.add(url)
                key = 'vacancy:'+sha256(url.encode()).hexdigest(); programme_key = None
                year = int(row['intake']) if re.fullmatch(r'20\d{2}',row['intake']) else None
            else:
                base = re.sub(r'20\d{2}', '', row['programme'].split('(')[0]).strip()
                programme_key = (employer or slug(row['company'])) + ':' + slug(base)
                year = int(row['cycle']) if re.fullmatch(r'20\d{2}',row['cycle']) else None
                if (programme_key,year) in programmes:
                    raise ImportProblem('Programme identity/cycle collision; provide an explicit distinction')
                programmes.add((programme_key,year)); key = 'programme:'+programme_key+':'+str(year or 'unspecified')
            deadline = row.get('deadline') or {}
            tables['leads'].append({'lead_key':key,'kind':kind,'employer_id':employer,'company':row['company'],
                'title':row['title'] if kind=='vacancy' else row['programme'], 'programme_key':programme_key,
                'cycle_year':year,'cycle_wording':row.get('cycle') if kind=='programme' else row.get('intake_wording'),
                'state':row['status'] if kind=='vacancy' else 'open' if row['state']=='open_2027' else row['state'],
                'url':row['url'],'official':row['official'],'source_note':row['source'],'wording':row.get('wording'),
                'deadline_date':deadline.get('value'),'deadline_wording':deadline.get('wording'),
                'posting_key':row.get('posting_key') if kind=='vacancy' else None,
                'checked_at':stamp(row['checked_at']),'checked_by':row.get('checked_by','unrecorded_legacy'),
                'details':{'legacy_json':encode_evidence(row).decode('ascii'), **({'window':row['window']} if 'window' in row else {})}})
    tables['leads'].sort(key=lambda r:r['lead_key'])
    tables['triage'] = [{'posting_key':key, **{k: stamp(v) if k=='decided_at' else v for k,v in row.items()}}
                        for key,row in sorted(decisions.items())]
    for row in sorted(apps,key=lambda r:r['job_key']):
        # Explicit stable posting keys only. Exact URL fallback remains a conservative SQL-view join.
        linked = row['job_key'] if row['job_key'] in current['postings'] else None
        tables['applications'].append({**row, 'posting_key':linked,
            'date_applied':row['date_applied'] or None,'next_action_date':row['next_action_date'] or None})
    if any(r['posting_key'] not in current['postings'] for r in tables['triage']):
        raise ImportProblem('Triage references unknown posting')
    transformations += [{'code': 'legacy_events', 'detail':'Original source/screening/mixed categories and exact JSON retained in import/legacy_event; no new native provider claims.'},
        {'code':'health_baselines','detail':'Imported mode/status, unknown counters remain NULL; original health/scan chronology retained.'},
        {'code':'owner_import_audit','detail':'Trigger records import actor/login, never a fabricated original owner action.'},
        {'code':'date_and_identity_projection','detail':'UTC timestamps, blank application dates to NULL; manual keys preserved; exact URL fallback remains a SQL join.'},
        {'code':'legacy_snapshots','detail':'Complete original row plus all available description references, not invented provider data.'},
        {'code':'last_positive_observation','detail':'Use explicit retained per-posting times, source events or first observations. No board success inference; a later unrecorded sighting remains unknown.'}]
    unknown_fields = sorted({k for row in current['postings'].values() for k in row} - {
        'company','provider','board','external_id','title','location','url','salary','country','countries','department','team',
        'employment_type','published_at','source_dates','deadlines','description_sha256','description_chars','first_seen',
        'first_seen_basis','feed_status','last_seen','missing_since','screening','source_events','screening_events','legacy_history',
        'application_verification','description','raw','priority','role_family','reason','location_judgment','history'})
    manifest = {'schema':'jobwatch.import/1','source_commit':commit,'files':files,'history_commits':list(git.commits),
        'activation_required':activation,'expected_transformations':transformations,'legacy_limitations':limitations,
        'missing_evidence':missing,'unprojected_posting_fields':unknown_fields,
        'archived_unprojected_fields': {
            'employer_config': sorted({k for r in cfg['employers'] for k in r} - {'company','provider','board','region','enabled',
                'careers_url','validation_url','watch_status','watch_checked_at','mapping_verified_at','mapping_verified_by',
                'endpoint_checked_at','mapping_evidence','verification_notes','relevance_notes'}),
            'vacancies':sorted({k for r in (lead_doc or {}).get('vacancies',[]) for k in r} - {'company','title','url','source','official','checked_at',
                'checked_by','status','wording','deadline','intake_wording','posting_key'}),
            'programmes':sorted({k for r in (lead_doc or {}).get('programmes',[]) for k in r} - {'company','programme','cycle','state','wording','url','source','official','checked_at','checked_by','window'})},
        'description_recovery':{'distinct_texts':len(texts),'current_text_files':sum(p.startswith('data/jobs-text/') for p in git.tree(commit)),
            'current_distinct_texts':len({r.get('description_sha256') for r in current_decoded['postings'].values()} & texts.keys()),
            'historical_only_texts':len(texts.keys() - {r.get('description_sha256') for r in current_decoded['postings'].values()}),
            'references':sum(len(v) for hashes in refs.values() for v in hashes.values()),
            'current_available':sum(r['description_text'] is not None for r in versions.values() if r['id'] in current_versions.values()),
            'current_unavailable':[{'posting_key':key,'recorded_sha256':current_decoded['postings'][key].get('description_sha256')}
                for key,vid in sorted(current_versions.items()) if versions[vid]['description_text'] is None],
            'native_versions':sum(r['origin']=='git_history' for r in versions.values()),
            'native_postings':len({r['posting_key'] for r in versions.values() if r['origin']=='git_history'})},
        'distributions':{'status':dict(Counter(r['status'] for r in tables['postings'])),
            'verdict':dict(Counter(r['verdict'] for r in tables['postings'])),
            'first_seen_basis':dict(Counter(r['first_seen_basis'] for r in tables['postings'])),
            'last_seen_basis':dict(last_seen_bases),
            'codes':dict(Counter(c for r in tables['postings'] for c in r['codes'])),
            'legacy_events':dict(Counter(r['changes']['legacy_category'] for r in tables['posting_events']))}}
    return ImportPlan(commit, tables, manifest)
