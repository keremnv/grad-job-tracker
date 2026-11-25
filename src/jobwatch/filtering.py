"""Deterministic screening as a non-destructive retrieval heuristic, with explicit uncertainty.

`screen(inputs)` returns a structured judgment: a verdict (`priority`,
`review`, `excluded`), a role family, stable inspectable reason codes and a
readable explanation derived only from those codes. Judgments are reproducible
offline from the stored source fields; they are never source facts and never
authoritative personal suitability judgments. Excluded postings remain indexed
and searchable.

Verdict policy:
- `excluded` needs an explicit negative signal (senior/student/non-engineering/
  freelance-AI-trainer title, non-software discipline, explicit non-UK location)
  or the absence of any engineering signal.
- `priority` needs a core (or software-evidenced graduate data-science/security)
  family, an early-career *title* signal, UK location evidence and no uncertainty code.
- Everything else is `review`, with every underlying signal named by explicit codes.
"""
from __future__ import annotations

from hashlib import sha256
import html
import json
import re
from .feeds import Posting

RULES_VERSION_V1 = 'screen-2026-10-23.1'
RULES_VERSION = 'screen-2026-11-25.1'
RULES_VERSION_V2 = RULES_VERSION

UK_CODES = {'uk', 'gb', 'gbr', 'united kingdom', 'great britain'}
UK_COUNTRY = re.compile(r'\b(?:united kingdom|u\.?k\.?|great britain|(?<!new )england|scotland|(?<!new south )wales|northern ireland)\b', re.I)
UK_CITY = re.compile(r'\b(?:london|manchester|sheffield|leeds|bristol|cambridge|oxford|edinburgh|glasgow|cardiff|birmingham|newcastle|nottingham|liverpool|reading|bath|exeter|belfast)\b', re.I)
FOREIGN = re.compile(
    r'\b(?:united states|usa|canada|ontario|massachusetts|new york|san francisco|washington|seattle|boston|austin|singapore|hong kong|australia|sydney|melbourne|india|bengaluru|bangalore|germany|berlin|france|paris|japan|tokyo'
    r'|(?<!northern )ireland|dublin|netherlands|amsterdam|poland|warsaw|krak[oó]w|spain|madrid|barcelona|portugal|lisbon|italy|milan'
    r'|switzerland|zurich|zürich|geneva|sweden|stockholm|denmark|copenhagen|norway|oslo|finland|helsinki|belgium|brussels|austria|vienna'
    r'|munich|hamburg|frankfurt|israel|tel aviv|toronto|vancouver|montreal|chicago|los angeles|california|texas|new jersey|brazil'
    r'|mexico|china|shanghai|beijing|korea|seoul|dubai|united arab emirates'
    r'|taiwan|taipei|philippines|manila|romania|bucharest|czech(?:ia)?|prague|hungary|budapest|colombia|bogot[aá]'
    r'|argentina|buenos aires|new zealand|auckland|south africa|cape town|estonia|tallinn|lithuania|vilnius|greece|athens)\b',
    re.I,
)
FOREIGN_CODE = re.compile(r'\b(?:US|U\.S\.)\b')  # case-sensitive: "us" is also an English word
EARLY = re.compile(r'\b(?:graduates?|grad|new[ -]grad(?:uate)?s?|junior|entry[ -]level|early[ -]careers?|associate|recent[ -]grad(?:uate)?s?|class of 20\d{2})\b', re.I)
EARLY_CONTEXT = re.compile(r'\b(?:graduates?|early[ -]careers?|campus|university|emerging talent|new grad)\b', re.I)
EARLY_DESCRIPTION = re.compile(r'\b(?:graduate (?:programme|program|scheme|role|position)|new grad(?:uate)?s?|recent(?:ly)? graduat\w*|graduating in 20\d{2}|final[- ]year (?:students?|undergraduates?)|class of 20\d{2}|early[ -]careers?|entry[- ]level)\b', re.I)
SENIOR = re.compile(r'\b(?:senior|sr\.?|staff|principal|lead|leader|manager|director|head of|vp|distinguished)\b', re.I)
STUDENT = re.compile(r'\b(?:intern|internship|apprentice|apprenticeship|placement|industrial year)\b', re.I)
# Applies whatever the family: these titles are not engineering roles.
NON_ENGINEER = re.compile(r'\b(?:sales|account executive|recruiter|marketing|customer success|product manager|project manager|solutions consultant|business develop\w*|people operations)\b', re.I)
# Applies only when the title carries no engineering family.
NON_ENGINEERING_CONTEXT = re.compile(r'\b(?:business|operations|finance|financial|legal|counsel|hr|human resources|talent|accounting|accountant|audit|procurement|communications|policy|designer|commercial)\b', re.I)
NON_SOFTWARE_DISCIPLINE = re.compile(r'\b(?:mechanical|civil|chemical|structural|manufacturing|process|mechatronics?|biomedical|aerospace|electrical|electronics?|rf|optical|optics|hardware|materials|industrial design|quality assurance engineer|test technician|technician)\b', re.I)
CUSTOMER_FACING = re.compile(r'\b(?:forward[ -]deployed|solutions? (?:engineer|architect)|support engineer|technical support|cloud support|customer engineer|developer advocate|developer relations|devrel|evangelist|implementation (?:engineer|consultant)|technical consultant|professional services|deployment strategist|pre-?sales)\b', re.I)
# Software-engineering work evidence for families outside the core ones.
SOFTWARE_EVIDENCE = re.compile(r'\b(?:software|programming|coding|developers?|python|java|javascript|typescript|c\+\+|c#|rust|golang|scala|kotlin|backend|back-end|frontend|front-end|full[- ]stack|devops|kubernetes|distributed systems|machine learning|computer science)(?!\w)', re.I)
EXPERIENCE = re.compile(r'\b(?:[3-9]|1\d)\+?\s+years?\b|strong experience|extensive experience|commercial experience|professional experience', re.I)
DOCTORAL = re.compile(r'\bph\.?d\b|doctorate', re.I)
AUTHORISATION = re.compile(r'clearance|citizenship|nationality|British national|right to work|sponsor|visa', re.I)

_V2_FREELANCE_AI_TRAINER = re.compile(
    r'\b(?:ai trainer|data annotat\w*|rlhf|prompt (?:writer|evaluator|trainer))\b',
    re.I,
)
_V2_COMMERCIAL_NON_ENGINEER = re.compile(
    r'\b(?:growth associate|partner growth|m&a|ads specialist|real estate|lawyer|attorney|'
    r'insurance broker|pension specialist|hr business partner|scale-up consultant|'
    r'strategy consultant|migration consultant|organizational design|market entry)\b',
    re.I,
)
_V2_UK_DISAMBIGUATED = re.compile(
    r'\b(?:london\s*[-–]\s*(?:sloane street|canary wharf|city|shoreditch|soho|kings? cross|paddington)|'
    r'gb-london|hatfield|dundee|bridgend|guildford|lancaster,\s*uk)\b',
    re.I,
)
_V2_BENIGN_EXPERIENCE_CLAUSE = re.compile(
    r'(?:(?:continuously\s+)?resident in the uk|uk residency|security clearance)[^.;:\n]{0,45}\b\d+\+?\s+years?\b'
    r'|(?:annual leave|holiday|rising to \d+ after)[^.;:\n]{0,30}\b\d+\+?\s+years?\b'
    r'|(?:no more than|less than|up to|at most)\s+\d+\s*(?:months?|years?)\s+(?:of\s+)?(?:(?:professional|commercial|work|industry|relevant)\s+)?experience'
    r'|up to [12]\s+years?|0\s*[-–]\s*[12]\s+years?',
    re.I,
)
_V2_DESIRABLE_CLAUSE = re.compile(
    r'\b(?:desirable|nice to have|bonus|preferred but not required|advantageous|not essential|not required|optional)\b',
    re.I,
)
_V2_INCLUSIVE_DEGREE_LADDER = re.compile(
    r"(?:bachelor'?s|bsc|beng|meng|master'?s|msc|undergraduate|an? excellent degree|degree)"
    r"[^.;:\n]{0,45}(?:or|/|,)[^.;:\n]{0,20}\b(?:ph\.?d|doctorate)\b"
    r"|\b(?:ph\.?d|doctorate)\b[^.;:\n]{0,30}\b(?:desirable|advantageous|bonus|nice to have|welcome|preferred)\b",
    re.I,
)
_V2_PRIORITY_ADJACENT = ('data_science', 'security')

CORE_FAMILIES = ('ml_ai', 'platform_systems', 'backend', 'software', 'engineering_programme')
FAMILIES = (
    ('ml_ai', r'\b(?:machine learning|mlops|ml|artificial intelligence|ai|research software|research engineer|deep learning|computer vision|nlp)\b'),
    ('platform_systems', r'\b(?:platform|infrastructure|developer tooling|developer tools|devops|systems engineer|site reliability|sre|compiler|cloud|reliability|network(?:ing)? engineer|embedded|firmware|kernel|distributed systems|performance engineer)\b'),
    ('backend', r'\b(?:backend|back.end|python|java|data engineer|analytics engineer)\b'),
    ('software', r'\b(?:software|developer|swe|full.stack|front.end|product engineer|quantitative developer|programmer)\b'),
    ('engineering_programme', r'\b(?:engineering graduate|graduate engineering)\b'),
    ('security', r'\b(?:security|cyber\w*|appsec|infosec)\b'),
    ('data_science', r'\b(?:data scien\w*|quantitative research\w*|quant research\w*|applied scien\w*|research scien\w*|data analyst)\b'),
    ('technology_programme', r'\b(?:technology|technologist|tech) (?:graduate|programme|program|scheme|analyst)\b|\bgraduate (?:technology|technologist|tech)\b|\btechnologist\b'),
    ('customer_engineering', CUSTOMER_FACING.pattern),
    ('engineering_generic', r'\b(?:engineer|engineering)\b'),
)

# Stable inspectable codes. Explanations are for humans; SQL agents should key on codes.
CODES = {
    'exclude.non_engineering_title': 'outside selected engineering role families',
    'exclude.non_software_discipline': 'non-software engineering discipline in title',
    'exclude.student_title': 'student internship/placement/apprenticeship title; outside graduate-entry queue',
    'exclude.senior_title': 'senior/managerial title',
    'exclude.non_uk_location': 'explicit non-UK country/location without UK signal',
    'exclude.no_engineering_signal': 'no engineering role family or early-career signal in title',
    'exclude.no_engineering_evidence': 'outside the core families with an early-career signal, but no software-engineering evidence in title, department/team or description',
    'exclude.adjacent_without_early_signal': 'adjacent or unspecific engineering family without any early-career signal',
    'exclude.freelance_ai_trainer': 'freelance AI trainer / data annotation gig outside graduate engineering',
    'level.early_title': 'early-career title signal',
    'level.early_department': 'early-career/graduate department or team',
    'level.early_description': 'description mentions graduate or early-career entry',
    'level.entry_description_only': 'generic engineering title with explicit entry-level description; verify role level',
    'level.unknown': 'entry-level requirement unknown',
    'level.experience_wording': 'experience wording in description needs review',
    'level.desirable_experience_only': 'experience wording appears only in desirable/optional context',
    'level.benign_tenure_or_cap_only': 'tenure/experience phrasing only appears in holiday, UK residency, or max new-grad cap context',
    'level.doctoral_wording': 'doctoral wording needs review for BSc suitability',
    'level.inclusive_degree_ladder': 'doctoral degree mentioned only in inclusive degree ladder (BSc/MSc/PhD) or optional context',
    'location.uk': 'UK source signal; location arrangements require review',
    'location.uk_multi_country': 'UK source signal (multiple countries); location arrangements require review',
    'location.unknown': 'city name alone, remote/global or conflicting country evidence; UK location unknown',
    'role.adjacent_family': 'adjacent role family; engineering fit needs owner review',
    'role.software_evidenced_adjacent': 'adjacent early-career role carrying software-engineering evidence',
    'role.discipline_unknown': 'engineering discipline unspecified; may not be software',
    'role.customer_facing': 'customer-facing or support engineering; day-to-day work needs review',
    'role.department_engineering': 'department/team indicates engineering or technology',
    'year.2027_explicit': 'title explicitly references 2027 intake/start',
    'year.title_mismatch': 'title year differs from June 2027 graduation; intake/start flexibility unknown',
    'eligibility.work_authorisation_wording': 'work-authorisation/clearance wording present; owner eligibility unknown',
    'eligibility.unverified': 'graduation eligibility, start date and application status unverified',
    'evidence.source_text_unavailable': 'source description not retained; description checks not run',
    'evidence.description_empty': 'source supplied no description; description checks not possible',
}
UNCERTAIN = {
    'level.unknown', 'level.entry_description_only', 'level.experience_wording', 'level.doctoral_wording',
    'location.unknown', 'role.adjacent_family', 'role.discipline_unknown', 'role.customer_facing',
    'year.title_mismatch', 'evidence.source_text_unavailable',
}
ENGINEERING_DEPARTMENT = re.compile(r'\b(?:engineering|software|technology|tech|infrastructure|platform|data|machine learning|ai|research|r&d|development|security)\b', re.I)


_SCRIPT_STYLE_BLOCK = re.compile(r'<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>', re.I | re.S)
_INLINE_FORMAT_TAG = re.compile(r'</?(?:strong|b|em|i|u|sup|sub|span|small|mark|abbr|code)\b[^>]*>', re.I)


def plain(value):
    text = value or ''
    if not text:
        return ''
    # Decode up to two entity passes (Greenhouse frequently double-escapes `&amp;nbsp;` / `&lt;p&gt;`).
    unescaped = html.unescape(text)
    if '&' in unescaped:
        unescaped = html.unescape(unescaped)
    unescaped = _SCRIPT_STYLE_BLOCK.sub(' ', unescaped)
    # Remove inline formatting tags without injecting spaces (preserves `29<sup>th</sup>` -> `29th`,
    # `<strong>Deadline</strong>:` -> `Deadline:`).
    unescaped = _INLINE_FORMAT_TAG.sub('', unescaped)
    stripped = re.sub(r'<[^>]*>', ' ', unescaped)
    stripped = stripped.replace('\xa0', ' ').replace('\u200b', '')
    return re.sub(r'\s+', ' ', stripped).strip()


def role_family(title):
    for name, pattern in FAMILIES:
        if re.search(pattern, title, re.I):
            return name
    return 'other'


def location_judgment(p):
    countries = {c.strip().lower() for c in (p.countries or (p.country,)) if c.strip()}
    uk = bool(countries & UK_CODES) or bool(UK_COUNTRY.search(p.location))
    foreign = bool(countries - UK_CODES) or bool(FOREIGN.search(p.location) or FOREIGN_CODE.search(p.location))
    if uk:
        return 'uk_signal', 'UK source signal; location arrangements require review' + (' (multiple countries)' if foreign else '')
    if UK_CITY.search(p.location):
        if re.search(r'\b(?:ontario|massachusetts)\b', p.location, re.I) and not re.search(r'\b(?:london|cambridge)\b.*[/;].*', p.location, re.I):
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


def _analyze_experience_and_doctoral(title: str, desc: str) -> tuple[bool, bool, bool, bool, bool]:
    """Return (req_exp, des_exp_only, benign_exp_only, req_phd, inclusive_phd_only)."""
    req_exp = False
    des_exp = False
    benign_exp = False
    req_phd = bool(DOCTORAL.search(title))
    inclusive_phd = False
    if not desc:
        return req_exp, des_exp, benign_exp, req_phd, inclusive_phd

    for sent in re.split(r'(?<=[.!?])\s+|[\r\n]+', desc):
        if EXPERIENCE.search(sent):
            cleaned = _V2_BENIGN_EXPERIENCE_CLAUSE.sub(' ', sent)
            if EXPERIENCE.search(cleaned):
                if _V2_DESIRABLE_CLAUSE.search(sent):
                    des_exp = True
                else:
                    req_exp = True
            else:
                benign_exp = True
        if DOCTORAL.search(sent):
            cleaned_phd = _V2_INCLUSIVE_DEGREE_LADDER.sub(' ', sent)
            if DOCTORAL.search(cleaned_phd):
                req_phd = True
            else:
                inclusive_phd = True
    return (
        req_exp,
        (des_exp and not req_exp),
        (benign_exp and not req_exp and not des_exp),
        req_phd,
        (inclusive_phd and not req_phd),
    )


def screen_v1(inputs: dict) -> dict:
    """Retained legacy screener (`screen-2026-10-23.1`) for offline regression comparison."""
    title = inputs.get('title') or ''
    context = ' '.join(x for x in (inputs.get('department'), inputs.get('team')) if x)
    description = inputs.get('description')
    desc = plain(description) if description else ''
    family = role_family(title)
    codes, exclusions = [], []

    location, location_reason = location_judgment(_Where(inputs))
    early_title = bool(re.search(r'\b(?:graduate|grad|new[ -]grad(?:uate)?|junior|entry[ -]level|early[ -]careers?|associate|recent[ -]grad(?:uate)?s?|class of 20\d{2})\b', title, re.I))
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
    elif family not in CORE_FAMILIES and not early:
        exclusions.append('exclude.adjacent_without_early_signal')
    if family not in CORE_FAMILIES and early and desc and not SOFTWARE_EVIDENCE.search(' '.join((title, context, desc))):
        exclusions.append('exclude.no_engineering_evidence')

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
    else:
        uncertain = any(c in UNCERTAIN for c in codes)
        verdict = 'priority' if family in CORE_FAMILIES and early_title and not uncertain else 'review'
    result = {'verdict': verdict, 'family': family, 'codes': exclusions + codes, 'rules_version': RULES_VERSION_V1,
              'basis': 'title_metadata_only' if description is None else 'full', 'input_sha256': input_fingerprint(inputs)}
    return {**result, 'explanation': explanation_of(result)}


def screen(inputs: dict) -> dict:
    """Deterministic screener (`screen-2026-11-25.1`) with inspectable atomic evidence codes."""
    title = inputs.get('title') or ''
    context = ' '.join(x for x in (inputs.get('department'), inputs.get('team')) if x)
    description = inputs.get('description')
    desc = plain(description) if description else ''
    family = role_family(title)
    codes, exclusions = [], []

    loc_str = inputs.get('location') or ''
    location, location_reason = location_judgment(_Where(inputs))
    if location == 'unknown' and _V2_UK_DISAMBIGUATED.search(loc_str):
        location = 'uk_signal'
        location_reason = 'UK source signal; location arrangements require review'

    early_title = bool(EARLY.search(title))
    early_department = bool(context and EARLY_CONTEXT.search(context))
    early_description = bool(desc and EARLY_DESCRIPTION.search(desc))
    early = early_title or early_department or early_description

    if _V2_FREELANCE_AI_TRAINER.search(title):
        exclusions.append('exclude.freelance_ai_trainer')
    if NON_ENGINEER.search(title) or _V2_COMMERCIAL_NON_ENGINEER.search(title):
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
    elif family not in CORE_FAMILIES and not early:
        exclusions.append('exclude.adjacent_without_early_signal')
    if family not in CORE_FAMILIES and early and desc and not SOFTWARE_EVIDENCE.search(' '.join((title, context, desc))):
        exclusions.append('exclude.no_engineering_evidence')

    codes.append('family.' + family)
    if context and ENGINEERING_DEPARTMENT.search(context):
        codes.append('role.department_engineering')

    software_evidenced_adjacent = (
        family in _V2_PRIORITY_ADJACENT
        and early_title
        and (not desc or bool(SOFTWARE_EVIDENCE.search(' '.join((title, context, desc)))))
    )
    if family == 'other' or family == 'engineering_generic':
        codes.append('role.discipline_unknown')
    elif family not in CORE_FAMILIES:
        codes.append('role.adjacent_family')
        if software_evidenced_adjacent:
            codes.append('role.software_evidenced_adjacent')
    if CUSTOMER_FACING.search(title):
        codes.append('role.customer_facing')
    codes.append({'uk_signal': 'location.uk_multi_country' if 'multiple' in location_reason else 'location.uk',
                  'unknown': 'location.unknown', 'non_uk': 'location.non_uk'}[location])

    years = set(re.findall(r'\b20\d{2}\b', title))
    if '2027' in years:
        codes.append('year.2027_explicit')
    if years and '2027' not in years:
        codes.append('year.title_mismatch')

    if early_title:
        codes.append('level.early_title')
    if early_department:
        codes.append('level.early_department')
    if early_description:
        codes.append('level.early_description')

    req_exp, des_exp_only, benign_exp_only, req_phd, inclusive_phd_only = _analyze_experience_and_doctoral(title, desc)
    if not early_title:
        codes.append('level.unknown')
        if early_description and not req_exp:
            codes.append('level.entry_description_only')

    if description is None:
        codes.append('evidence.source_text_unavailable')
    elif not desc:
        codes.append('evidence.description_empty')
    else:
        if req_exp:
            codes.append('level.experience_wording')
        elif des_exp_only:
            codes.append('level.desirable_experience_only')
        elif benign_exp_only:
            codes.append('level.benign_tenure_or_cap_only')
        if req_phd:
            codes.append('level.doctoral_wording')
        elif inclusive_phd_only:
            codes.append('level.inclusive_degree_ladder')
        if AUTHORISATION.search(desc):
            codes.append('eligibility.work_authorisation_wording')
    codes.append('eligibility.unverified')

    exclusions = list(dict.fromkeys(exclusions))
    if exclusions:
        verdict = 'excluded'
    else:
        uncertain = any(c in UNCERTAIN for c in codes)
        verdict = 'priority' if family in CORE_FAMILIES and early_title and not uncertain else 'review'

    result = {
        'verdict': verdict,
        'family': family,
        'codes': exclusions + codes,
        'rules_version': RULES_VERSION,
        'basis': 'title_metadata_only' if description is None else 'full',
        'input_sha256': input_fingerprint(inputs),
    }
    return {**result, 'explanation': explanation_of(result)}


# Explicit alias for callers referencing the v2 rollout directly.
screen_v2 = screen


def explanation_of(screening: dict | None) -> str:
    """Readable explanation derived from stored codes (legacy records keep their stored text)."""
    screening = screening or {}
    if screening.get('explanation') and not screening.get('codes'):
        return screening['explanation']
    codes = screening.get('codes', [])
    shown = [c for c in codes if c.startswith('exclude.')] if screening.get('verdict') == 'excluded' else codes
    return '; '.join([f"role family: {screening.get('family', 'other')}"] + [CODES[c] for c in shown if c in CODES])


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
