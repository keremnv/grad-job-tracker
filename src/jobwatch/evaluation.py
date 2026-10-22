"""Offline screening diagnostics. Labels come from three explicitly separated sources:

- ``synthetic``: ``evaluation/synthetic_cases.json``, written and labelled by the
  sprint author from the owner's stated targets. Useful as a sanity check;
  **not** independent evidence of real-world accuracy.
- ``owner``: a file of owner labels for real indexed postings, produced from a
  blind stratified template (``eval template``). Private.
- ``triage``: owner triage decisions reinterpreted as labels: ``interested`` →
  relevant; ``dismissed`` with a relevance reason (not_engineering,
  not_entry_level) → not relevant. Preference-based dismissals are ignored.

Regression cases (tests/fixtures/screening_regressions.json) pin behaviour and
are never scored. Unlabelled postings are never counted as negatives; the
``uncertain`` label is reported separately and excluded from precision/recall.
"""
from __future__ import annotations

import json
from pathlib import Path
import random

from .contract import JobwatchError
from .filtering import EARLY, RULES_VERSION, screen
from .search import BM25, document
from .state import description_of, stored_inputs
from .triage import RELEVANCE_REASONS

LABELS = ('relevant', 'not_relevant', 'uncertain')
SYNTHETIC = Path(__file__).resolve().parents[2] / 'evaluation' / 'synthetic_cases.json'
TARGET_QUERY = ('graduate junior entry level new grad early careers software engineer developer backend '
                'infrastructure platform systems cloud reliability machine learning ml ai engineer python java')
CUTOFFS = (5, 10, 20)


def synthetic_cases(path=SYNTHETIC):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema') != 1 or data.get('kind') != 'synthetic_author_labelled':
        raise JobwatchError('invalid_state', 'synthetic evaluation file has an unexpected schema/kind')
    cases = []
    for c in data['cases']:
        if c.get('label') not in LABELS:
            raise JobwatchError('invalid_state', f"synthetic case {c.get('id')} has no valid label")
        cases.append({'id': c['id'], 'category': c['category'], 'label': c['label'], 'label_source': 'author_synthetic',
                      'inputs': {'title': c['title'], 'location': c.get('location', 'London, United Kingdom'),
                                 'country': c.get('country', 'GB'), 'countries': c.get('countries', [c['country']] if c.get('country') else ([] if 'country' in c else ['GB'])),
                                 'department': c.get('department', ''), 'team': c.get('team', ''), 'employment_type': '',
                                 'description': c.get('description', '')}})
    return cases


def _real_case(state, key, label, source):
    row = state['postings'][key]
    return {'id': key, 'category': (row.get('screening') or {}).get('family') or 'unknown', 'label': label,
            'label_source': source, 'inputs': stored_inputs(state, row)}


def owner_cases(state, path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema') != 1 or data.get('kind') not in ('owner_labels', 'owner_label_template'):
        raise JobwatchError('invalid_state', 'owner label file must come from `eval template`')
    cases, missing = [], 0
    for item in data['items']:
        label = item.get('label') or None
        if label is None:
            continue  # unlabelled: never a negative
        if label not in LABELS:
            raise JobwatchError('invalid_state', f"label for {item.get('key')} must be one of {', '.join(LABELS)} or blank")
        if item['key'] not in state['postings']:
            missing += 1
            continue
        cases.append(_real_case(state, item['key'], label, 'owner'))
    return cases, {'template_items': len(data['items']), 'labelled': len(cases), 'missing_from_index': missing}


def triage_cases(state, decisions):
    cases, ignored = [], 0
    for key, row in sorted(decisions.items()):
        if key not in state['postings']:
            continue
        if row['decision'] == 'interested':
            cases.append(_real_case(state, key, 'relevant', 'triage'))
        elif row['decision'] == 'dismissed' and row.get('reason_code') in RELEVANCE_REASONS:
            cases.append(_real_case(state, key, 'not_relevant', 'triage'))
        else:
            ignored += 1
    return cases, {'decisions': len(decisions), 'used_as_labels': len(cases), 'ignored_preference_or_later': ignored}


def template(state, per_stratum=15, seed=2027):
    """Blind stratified sample of real postings: verdicts are hidden from the labeller."""
    strata = {'priority': [], 'review': [], 'excluded': []}
    for key, row in sorted(state['postings'].items()):
        verdict = screen(stored_inputs(state, row))['verdict']
        strata[verdict].append(key)
    rng = random.Random(seed)
    items = []
    for verdict, keys in strata.items():
        for key in sorted(rng.sample(keys, min(per_stratum, len(keys)))):
            row = state['postings'][key]
            items.append({'key': key, 'company': row['company'], 'title': row['title'], 'location': row.get('location', ''),
                          'url': row['url'], 'label': ''})
    rng.shuffle(items)
    return {'schema': 1, 'kind': 'owner_label_template', 'rules_version': RULES_VERSION, 'seed': seed,
            'per_stratum': per_stratum, 'strata_sizes': {k: len(v) for k, v in strata.items()},
            'instructions': ('Label each item relevant / not_relevant / uncertain for YOUR target (UK graduate SWE, backend, '
                             'infrastructure, systems, applied ML) from the title and the employer page. Leave blank if unsure '
                             'whether to label; blank items are ignored. Screening verdicts are hidden deliberately. Private file.'),
            'items': items}


def _queue_order(case, result):
    return ({'priority': 0, 'review': 1, 'excluded': 2}[result['verdict']], not bool(EARLY.search(case['inputs']['title'])),
            case['inputs']['title'].casefold(), case['id'])


def _ratio(n, d):
    return None if d == 0 else round(n / d, 3)


def _at_k(ordered, k):
    top = ordered[:k]
    judged = [c for c in top if c['label'] in ('relevant', 'not_relevant')]
    return {'k': k, 'returned': len(top), 'judged': len(judged),
            'precision': _ratio(sum(c['label'] == 'relevant' for c in judged), len(judged))}


def evaluate(cases, baseline=False, screener=screen):
    """Score any screener with the `filtering.screen` signature: inputs dict ->
    {verdict, codes, rules_version, ...}. Alternative screeners (lexical, model-based)
    plug in here without changing identity, evidence or label handling."""
    results = {c['id']: screener(c['inputs']) for c in cases}
    relevant = [c for c in cases if c['label'] == 'relevant']
    negative = [c for c in cases if c['label'] == 'not_relevant']
    verdict = lambda c: results[c['id']]['verdict']
    confusion = {}
    for c in cases:
        confusion.setdefault(verdict(c), {l: 0 for l in LABELS})[c['label']] += 1
    queue = sorted((c for c in cases if verdict(c) != 'excluded'), key=lambda c: _queue_order(c, results[c['id']]))
    false_negatives = [{'id': c['id'], 'title': c['inputs']['title'], 'category': c['category'],
                        'codes': [x for x in results[c['id']]['codes'] if x.startswith('exclude.')]}
                       for c in relevant if verdict(c) == 'excluded']
    false_positives = [{'id': c['id'], 'title': c['inputs']['title'], 'category': c['category'], 'verdict': verdict(c)}
                       for c in negative if verdict(c) != 'excluded']
    per_category = {}
    for c in cases:
        bucket = per_category.setdefault(c['category'], {'cases': 0, 'false_negatives': 0, 'false_positives': 0, 'uncertain_labels': 0, 'review_verdicts': 0})
        bucket['cases'] += 1
        bucket['false_negatives'] += c['label'] == 'relevant' and verdict(c) == 'excluded'
        bucket['false_positives'] += c['label'] == 'not_relevant' and verdict(c) != 'excluded'
        bucket['uncertain_labels'] += c['label'] == 'uncertain'
        bucket['review_verdicts'] += verdict(c) == 'review'
    out = {
        'rules_version': next(iter(results.values()))['rules_version'] if results else RULES_VERSION, 'cases': len(cases),
        'labels': {l: sum(c['label'] == l for c in cases) for l in LABELS},
        'label_sources': sorted({c['label_source'] for c in cases}),
        'verdicts': {v: sum(verdict(c) == v for c in cases) for v in ('priority', 'review', 'excluded')},
        'confusion_verdict_by_label': confusion,
        'relevant_recall_kept': _ratio(sum(verdict(c) != 'excluded' for c in relevant), len(relevant)),
        'relevant_recall_priority': _ratio(sum(verdict(c) == 'priority' for c in relevant), len(relevant)),
        'not_relevant_excluded_rate': _ratio(sum(verdict(c) == 'excluded' for c in negative), len(negative)),
        'queue_precision_at_k': [_at_k(queue, k) for k in CUTOFFS],
        'abstention': {'review_verdicts': sum(verdict(c) == 'review' for c in cases),
                       'uncertain_labels': sum(c['label'] == 'uncertain' for c in cases)},
        'false_negatives': false_negatives, 'false_positives_kept': false_positives,
        'per_category': dict(sorted(per_category.items())),
        'notes': ['Uncertain labels are excluded from recall/precision denominators.',
                  'Precision@k counts only judged (relevant/not_relevant) items among the top k of the rule queue.'],
    }
    if baseline:
        docs = {c['id']: document(c['inputs'], c['inputs'].get('description')) for c in cases}
        ranked = [key for key, _ in BM25(docs).rank(TARGET_QUERY)]
        by_id = {c['id']: c for c in cases}
        ordered = [by_id[k] for k in ranked]
        scored = set(ranked)
        out['bm25_baseline'] = {'query': TARGET_QUERY, 'scored': len(ranked),
                                'precision_at_k': [_at_k(ordered, k) for k in CUTOFFS],
                                'relevant_recall_at_k': [{'k': k, 'recall': _ratio(sum(c['label'] == 'relevant' for c in ordered[:k]), len(relevant))} for k in CUTOFFS],
                                'relevant_with_zero_score': sum(1 for c in relevant if c['id'] not in scored),
                                'note': 'Lexical ranking against a fixed target query; no thresholds, no exclusion. A baseline, not a proposal.'}
    return out


def index_coverage(state):
    postings = state['postings']
    retained = sum(1 for r in postings.values() if description_of(state, r) is not None)
    return {'indexed': len(postings), 'text_retained': retained}
