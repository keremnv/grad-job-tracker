"""Deterministic screening, with explicit uncertainty rather than eligibility guesses.

`screen(inputs)` returns a structured judgment: a verdict (`priority`,
`review`, `excluded`), a role family, stable reason codes and a readable
explanation derived only from those codes. Judgments are reproducible offline
from the stored source fields; they are never source facts.

Verdict policy:
- `excluded` needs an explicit negative signal (senior/student/non-engineering
  title, non-software discipline, explicit non-UK location) or the absence of
  any engineering signal. Excluded postings are still recorded in the index.
- `priority` needs a core engineering family, an early-career *title* signal,
  UK location evidence and no uncertainty code.
- Everything else is `review`, with the uncertainty named by its codes.
"""
from __future__ import annotations

from hashlib import sha256
import html
import json
import re
from .feeds import Posting

RULES_VERSION = 'screen-2026-10-22.1'

UK_CODES = {'uk', 'gb', 'gbr', 'united kingdom', 'great britain'}
UK_COUNTRY = re.compile(r'\b(?:united kingdom|u\.?k\.?|great britain|england|scotland|wales|northern ireland)\b', re.I)
UK_CITY = re.compile(r'\b(?:london|manchester|sheffield|leeds|bristol|cambridge|oxford|edinburgh|glasgow|cardiff|birmingham|newcastle|nottingham|liverpool|reading|bath|exeter|belfast)\b', re.I)
FOREIGN = re.compile(r'\b(?:united states|usa|canada|ontario|massachusetts|new york|san francisco|washington|seattle|boston|austin|singapore|hong kong|australia|sydney|india|bengaluru|germany|berlin|france|paris|japan|tokyo)\b', re.I)
EARLY = re.compile(r'\b(?:graduate|grad|new[ -]grad(?:uate)?|junior|entry[ -]level|early[ -]careers?|associate|recent[ -]grad(?:uate)?s?|class of 20\d{2})\b', re.I)
EARLY_CONTEXT = re.compile(r'\b(?:graduate|early[ -]careers?|campus|university|emerging talent|new grad)\b', re.I)
EARLY_DESCRIPTION = re.compile(r'\b(?:graduate (?:programme|program|scheme|role|position)|new grad(?:uate)?s?|recent(?:ly)? graduat\w*|graduating in 20\d{2}|final[- ]year (?:students?|undergraduates?)|class of 20\d{2}|early[ -]careers?|entry[- ]level)\b', re.I)
SENIOR = re.compile(r'\b(?:senior|sr\.?|staff|principal|lead|leader|manager|director|head of|vp|distinguished)\b', re.I)
STUDENT = re.compile(r'\b(?:intern|internship|apprentice|apprenticeship|placement|industrial year)\b', re.I)
# Applies whatever the family: these titles are not engineering roles.
NON_ENGINEER = re.compile(r'\b(?:sales|account executive|recruiter|marketing|customer success|product manager|project manager|solutions consultant|business develop\w*|people operations)\b', re.I)
# Applies only when the title carries no engineering family.
NON_ENGINEERING_CONTEXT = re.compile(r'\b(?:business|operations|finance|financial|legal|counsel|hr|human resources|talent|accounting|accountant|audit|procurement|communications|policy|designer|commercial)\b', re.I)
NON_SOFTWARE_DISCIPLINE = re.compile(r'\b(?:mechanical|civil|chemical|structural|manufacturing|process|mechatronics?|biomedical|aerospace|electrical|electronics?|rf|optical|optics|hardware|materials|industrial design|quality assurance engineer|test technician|technician)\b', re.I)
CUSTOMER_FACING = re.compile(r'\b(?:forward[ -]deployed|solutions? (?:engineer|architect)|support engineer|technical support|cloud support|customer engineer|implementation (?:engineer|consultant)|technical consultant|professional services|deployment strategist|pre-?sales)\b', re.I)
ENGINEERING_TEXT = re.compile(r'\b(?:software|engineer\w*|developer|development|programming|coding|python|java|c\+\+|rust|golang|infrastructure|machine learning|computer science|distributed systems)\b', re.I)
EXPERIENCE = re.compile(r'\b(?:[3-9]|1\d)\+?\s+years?\b|strong experience|extensive experience|commercial experience|professional experience', re.I)
DOCTORAL = re.compile(r'\bph\.?d\b|doctorate', re.I)
AUTHORISATION = re.compile(r'clearance|citizenship|nationality|British national|right to work|sponsor|visa', re.I)

CORE_FAMILIES = ('ml_ai', 'platform_systems', 'backend', 'software', 'engineering_programme')
FAMILIES = (
    ('ml_ai', r'\b(?:machine learning|mlops|ml|artificial intelligence|ai|research software|research engineer|deep learning|computer vision|nlp)\b'),
    ('platform_systems', r'\b(?:platform|infrastructure|developer tooling|developer tools|devops|systems engineer|site reliability|sre|compiler|cloud|reliability|network(?:ing)? engineer|embedded|firmware|kernel|distributed systems|performance engineer)\b'),
    ('backend', r'\b(?:backend|back.end|python|java|data engineer|analytics engineer)\b'),
    ('software', r'\b(?:software|developer|swe|full.stack|front.end|product engineer|quantitative developer|programmer)\b'),
    # Multi-discipline schemes (e.g. "Engineering Graduate Scheme") may include software; keep queued, not discarded.
    ('engineering_programme', r'\b(?:engineering graduate|graduate engineering)\b'),
    # Adjacent families: discoverable, but never `priority` and only kept with an early-career signal.
    ('security', r'\b(?:security|cyber\w*|appsec|infosec)\b'),
    ('data_science', r'\b(?:data scien\w*|quantitative research\w*|quant research\w*|applied scien\w*|research scien\w*|data analyst)\b'),
    ('technology_programme', r'\b(?:technology|technologist|tech) (?:graduate|programme|program|scheme|analyst)\b|\bgraduate (?:technology|technologist|tech)\b|\btechnologist\b'),
    ('customer_engineering', CUSTOMER_FACING.pattern),
    ('engineering_generic', r'\b(?:engineer|engineering)\b'),
)

# Stable codes. Explanations are for humans; agents should key on the codes.
CODES = {
    'exclude.non_engineering_title': 'outside selected engineering role families',
    'exclude.non_software_discipline': 'non-software engineering discipline in title',
    'exclude.student_title': 'student internship/placement/apprenticeship title; outside graduate-entry queue',
    'exclude.senior_title': 'senior/managerial title',
    'exclude.non_uk_location': 'explicit non-UK country/location without UK signal',
    'exclude.no_engineering_signal': 'no engineering role family or early-career signal in title',
    'exclude.no_engineering_evidence': 'early-career title, but neither title nor description mentions engineering work',
    'exclude.adjacent_without_early_signal': 'adjacent or unspecific engineering family without any early-career signal',
    'level.early_title': 'early-career title signal',
    'level.early_department': 'early-career/graduate department or team',
    'level.early_description': 'description mentions graduate or early-career entry',
    'level.unknown': 'entry-level requirement unknown',
    'level.experience_wording': 'experience wording in description needs review',
    'level.doctoral_wording': 'doctoral wording needs review for BSc suitability',
    'location.uk': 'UK source signal; location arrangements require review',
    'location.uk_multi_country': 'UK source signal (multiple countries); location arrangements require review',
    'location.unknown': 'city name alone, remote/global or conflicting country evidence; UK location unknown',
    'role.adjacent_family': 'adjacent role family; engineering fit needs owner review',
    'role.discipline_unknown': 'engineering discipline unspecified; may not be software',
    'role.customer_facing': 'customer-facing or support engineering; day-to-day work needs review',
    'role.department_engineering': 'department/team indicates engineering or technology',
    'year.title_mismatch': 'title year differs from June 2027 graduation; intake/start flexibility unknown',
    'eligibility.work_authorisation_wording': 'work-authorisation/clearance wording present; owner eligibility unknown',
    'eligibility.unverified': 'graduation eligibility, start date and application status unverified',
    'evidence.source_text_unavailable': 'source description not retained; description checks not run',
    'evidence.description_empty': 'source supplied no description; description checks not possible',
}
UNCERTAIN = {'level.unknown', 'level.experience_wording', 'level.doctoral_wording', 'location.unknown',
             'role.adjacent_family', 'role.discipline_unknown', 'role.customer_facing', 'year.title_mismatch',
             'evidence.source_text_unavailable'}
ENGINEERING_DEPARTMENT = re.compile(r'\b(?:engineering|software|technology|tech|infrastructure|platform|data|machine learning|ai|research|r&d|development|security)\b', re.I)


def plain(value):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', html.unescape(value))).strip()


def role_family(title):
    for name, pattern in FAMILIES:
        if re.search(pattern, title, re.I):
            return name
    return 'other'


def location_judgment(p):
    countries = {c.strip().lower() for c in (p.countries or (p.country,)) if c.strip()}
    uk = bool(countries & UK_CODES) or bool(UK_COUNTRY.search(p.location))
    foreign = bool(countries - UK_CODES) or bool(FOREIGN.search(p.location))
    if uk:
        return 'uk_signal', 'UK source signal; location arrangements require review' + (' (multiple countries)' if foreign else '')
    if UK_CITY.search(p.location):
        # London and Cambridge occur outside the UK. Conflicting evidence is not a silent discard.
        if re.search(r'\b(?:ontario|massachusetts)\b',p.location,re.I) and not re.search(r'\b(?:london|cambridge)\b.*[/;].*',p.location,re.I):
            return 'non_uk', 'explicit non-UK location without UK country evidence'
        return 'unknown', 'city name alone or conflicting country evidence; UK location unknown'
    if foreign:
        return 'non_uk', 'explicit non-UK country/location without UK signal'
    return 'unknown', 'UK location unknown; remote/global arrangements require review'


class _Where:
    """Adapter so location_judgment accepts stored records as well as Postings."""
    def __init__(self, inputs):
        self.location = inputs.get('location') or ''
        self.country = inputs.get('country') or ''
        self.countries = tuple(inputs.get('countries') or ())


def inputs_from_posting(p: Posting) -> dict:
    return {'title': p.title, 'location': p.location, 'country': p.country, 'countries': list(p.countries),
            'department': p.department, 'team': p.team, 'employment_type': p.employment_type,
            'description': p.description}


def input_fingerprint(inputs: dict) -> str:
    """Hash of everything screening reads; the description is represented by its hash."""
    description = inputs.get('description')
    basis = {k: inputs.get(k) or ('' if k != 'countries' else []) for k in
             ('title', 'location', 'country', 'countries', 'department', 'team', 'employment_type')}
    basis['description_sha256'] = None if description is None else sha256(description.encode('utf-8')).hexdigest()
    return sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def screen(inputs: dict) -> dict:
    """Screen stored or freshly collected source fields. `description=None`
    means the text was not retained (distinct from an empty source description)."""
    title = inputs.get('title') or ''
    context = ' '.join(x for x in (inputs.get('department'), inputs.get('team')) if x)
    description = inputs.get('description')
    desc = plain(description) if description else ''
    family = role_family(title)
    codes, exclusions = [], []

    location, location_reason = location_judgment(_Where(inputs))
    early_title = bool(EARLY.search(title))
    early_department = bool(context and EARLY_CONTEXT.search(context))
    early_description = bool(desc and EARLY_DESCRIPTION.search(desc))
    early = early_title or early_department or early_description

    if NON_ENGINEER.search(title):
        exclusions.append('exclude.non_engineering_title')
    if STUDENT.search(title):
        exclusions.append('exclude.student_title')
    if SENIOR.search(title):
        exclusions.append('exclude.senior_title')
    if location == 'non_uk':
        exclusions.append('exclude.non_uk_location')
    if family in ('engineering_generic', 'other') and NON_SOFTWARE_DISCIPLINE.search(title):
        exclusions.append('exclude.non_software_discipline')
    if family == 'other':
        if NON_ENGINEERING_CONTEXT.search(title):
            exclusions.append('exclude.non_engineering_title')
        elif not early:
            exclusions.append('exclude.no_engineering_signal')
        elif description and not ENGINEERING_TEXT.search(desc) and not ENGINEERING_DEPARTMENT.search(context):
            exclusions.append('exclude.no_engineering_evidence')
    elif family not in CORE_FAMILIES and not early:
        exclusions.append('exclude.adjacent_without_early_signal')

    codes.append('family.' + family)
    if context and ENGINEERING_DEPARTMENT.search(context):
        codes.append('role.department_engineering')
    if family == 'other' or family == 'engineering_generic':
        codes.append('role.discipline_unknown')
    elif family not in CORE_FAMILIES:
        codes.append('role.adjacent_family')
    if CUSTOMER_FACING.search(title):
        codes.append('role.customer_facing')
    codes.append({'uk_signal': 'location.uk_multi_country' if 'multiple' in location_reason else 'location.uk',
                  'unknown': 'location.unknown', 'non_uk': 'location.non_uk'}[location])
    years = set(re.findall(r'\b20\d{2}\b', title))
    if years and years != {'2027'}:
        codes.append('year.title_mismatch')
    if early_title:
        codes.append('level.early_title')
    if early_department:
        codes.append('level.early_department')
    if early_description:
        codes.append('level.early_description')
    if not early_title:
        codes.append('level.unknown')
    if description is None:
        codes.append('evidence.source_text_unavailable')
    elif not desc:
        codes.append('evidence.description_empty')
    else:
        if EXPERIENCE.search(desc):
            codes.append('level.experience_wording')
        if DOCTORAL.search(desc):
            codes.append('level.doctoral_wording')
        if AUTHORISATION.search(desc):
            codes.append('eligibility.work_authorisation_wording')
    codes.append('eligibility.unverified')

    exclusions = list(dict.fromkeys(exclusions))
    if exclusions:
        verdict = 'excluded'
        shown = exclusions
    else:
        uncertain = any(c in UNCERTAIN for c in codes)
        verdict = 'priority' if family in CORE_FAMILIES and early_title and not uncertain else 'review'
        shown = codes
    explanation = '; '.join([f'role family: {family}'] + [CODES[c] for c in shown if c in CODES])
    return {'verdict': verdict, 'family': family, 'codes': exclusions + codes, 'explanation': explanation,
            'rules_version': RULES_VERSION, 'basis': 'title_metadata_only' if description is None else 'full',
            'input_sha256': input_fingerprint(inputs)}


def classify(p: Posting) -> tuple[str, str]:
    """Compatibility wrapper: (verdict, explanation)."""
    result = screen(inputs_from_posting(p))
    return result['verdict'], result['explanation']


def verdict_of(row) -> str:
    """Verdict from a schema-2 record (`screening`) or a schema-1 record (`priority`)."""
    return (row.get('screening') or {}).get('verdict') or row.get('priority', 'review')


def rank(row):
    """No prestige/weight assumptions: clear early-career signals first, then stable labels."""
    return (verdict_of(row) != 'priority', not bool(EARLY.search(row['title'])),
            row['company'].casefold(), row['title'].casefold(), row['external_id'])
