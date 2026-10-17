"""Deterministic screening, with explicit uncertainty rather than eligibility guesses."""
from __future__ import annotations

import html
import re
from .feeds import Posting

UK_CODES = {'uk', 'gb', 'gbr', 'united kingdom', 'great britain'}
UK_COUNTRY = re.compile(r'\b(?:united kingdom|u\.?k\.?|great britain|england|scotland|wales|northern ireland)\b', re.I)
UK_CITY = re.compile(r'\b(?:london|manchester|sheffield|leeds|bristol|cambridge|oxford|edinburgh|glasgow|cardiff|birmingham|newcastle|nottingham|liverpool|reading|bath|exeter|belfast)\b', re.I)
FOREIGN = re.compile(r'\b(?:united states|usa|canada|ontario|massachusetts|new york|san francisco|washington|seattle|boston|austin|singapore|australia|sydney|india|bengaluru|germany|berlin|france|paris|japan|tokyo)\b', re.I)
EARLY = re.compile(r'\b(?:graduate|new[ -]grad|junior|entry[ -]level|early[ -]career|associate|recent[ -]grad)\b', re.I)
SENIOR = re.compile(r'\b(?:senior|sr\.?|staff|principal|lead|manager|director|head of|vp|distinguished)\b', re.I)
STUDENT = re.compile(r'\b(?:intern|internship|apprentice|apprenticeship|placement|industrial year)\b', re.I)
NON_ENGINEER = re.compile(r'\b(?:sales|account executive|recruiter|marketing|customer success|product manager|project manager|solutions consultant)\b', re.I)
FAMILIES = (
    ('ml_ai', r'\b(?:machine learning|mlops|ml|artificial intelligence|ai|research software|research engineer)\b'),
    ('platform_systems', r'\b(?:platform|infrastructure|developer tooling|developer tools|devops|systems engineer|site reliability|sre|compiler)\b'),
    ('backend', r'\b(?:backend|back.end|python|java|data engineer)\b'),
    ('software', r'\b(?:software|developer|swe|full.stack|front.end|product engineer|quantitative developer)\b'),
)


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


def classify(p: Posting) -> tuple[str, str]:
    family = role_family(p.title)
    if family == 'other' or NON_ENGINEER.search(p.title):
        return 'excluded', 'outside selected engineering role families'
    if STUDENT.search(p.title):
        return 'excluded', 'student internship/placement/apprenticeship title; outside graduate-entry queue'
    if SENIOR.search(p.title):
        return 'excluded', 'senior/managerial title'
    location, location_reason = location_judgment(p)
    if location == 'non_uk':
        return 'excluded', location_reason
    reasons = [f'role family: {family}', location_reason]
    uncertain = location == 'unknown'
    years = set(re.findall(r'\b20\d{2}\b',p.title))
    if years and years != {'2027'}:
        uncertain = True
        reasons.append('title year differs from June 2027 graduation; intake/start flexibility unknown')
    early = bool(EARLY.search(p.title))
    if early:
        reasons.append('early-career title signal')
    else:
        uncertain = True
        reasons.append('entry-level requirement unknown')
    desc = plain(p.description)
    if re.search(r'\b(?:[3-9]|1\d)\+?\s+years?\b|strong experience|extensive experience|commercial experience|professional experience', desc, re.I):
        uncertain = True
        reasons.append('experience wording in description needs review')
    if re.search(r'\bph\.?d\b|doctorate',desc,re.I):
        uncertain = True
        reasons.append('doctoral wording needs review for BSc suitability')
    if re.search(r'clearance|citizenship|nationality|British national|right to work|sponsor|visa',desc,re.I):
        reasons.append('work-authorisation/clearance wording present; owner eligibility unknown')
    reasons.append('graduation eligibility, start date and application status unverified')
    return ('priority' if early and not uncertain else 'review'), '; '.join(reasons)


def rank(row):
    """No prestige/weight assumptions: clear early-career signals first, then stable labels."""
    return (row['priority'] != 'priority', not bool(EARLY.search(row['title'])),
            row['company'].casefold(), row['title'].casefold(), row['external_id'])
