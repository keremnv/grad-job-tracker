"""Keep deadline evidence verbatim; only calculate urgency from explicit dates."""
from datetime import date, datetime, timedelta, timezone
import re
from .config import timestamp
from .filtering import plain


def deadline_evidence(raw, description, url, observed_at):
    evidence = []
    value = raw.get('application_deadline')
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('invalid structured application_deadline')
        evidence.append({'value': value, 'wording': value, 'source_field': 'application_deadline',
                         'url': url, 'observed_at': observed_at})
    # Prose dates are evidence for human review, not an automatic interpretation.
    # Keep sentence boundaries so unrelated dates are never turned into deadlines.
    for sentence in re.split(r'(?<=[.!?])\s+|[\r\n]+', description):
        if re.search(r'\boffer deadline\b', plain(sentence), re.I):
            continue
        if re.search(r'\bdeadline\b|\bclosing date\b|\bapplications close\b|\bapply by\b', plain(sentence), re.I):
            evidence.append({'wording': sentence, 'source_field': 'description', 'url': url, 'observed_at': observed_at})
    return evidence


def deadline_window(evidence, now):
    value = evidence.get('value', '')
    if not value:
        return 'manual review: source wording only; exact deadline unknown'
    try:
        current = timestamp(now)
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            delta = (date.fromisoformat(value) - current.astimezone(timezone.utc).date()).days
            if delta < 0:
                return 'source date passed; expiry time/timezone and application status unknown'
            if delta <= 14:
                return 'within 14 calendar days (UTC reference); source supplies date only, expiry time/timezone unknown'
            return 'later than 14 calendar days (UTC reference); expiry time/timezone unknown'
        end = timestamp(value)
        delta = end - current
        if delta < timedelta(0):
            return 'source timestamp passed; application status requires recheck'
        if delta <= timedelta(days=14):
            return 'within 14 days; exact source timezone retained in value'
        return 'later than 14 days; exact source timezone retained in value'
    except (ValueError, TypeError):
        return 'manual review: unparseable or timezone-free deadline; no expiry assumed'


def stale(last_success, now, days=3):
    if not last_success:
        return True
    try:
        return timestamp(now) - timestamp(last_success) > timedelta(days=days)
    except (ValueError, TypeError):
        return True


# ---------------------------------------------------------------------------
# Isolated deadline-extraction audit & structured-prose parser prototype.
# Preserves strict separation: parsed prose candidates are NEVER promoted to
# verified calendar dates without explicit owner or independent verification.
# ---------------------------------------------------------------------------
_MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}
_WORK_DEADLINE_NOISE = re.compile(
    r'\b(?:work to|meet(?:ing)?|tight|strict|project|sprint|client|delivery|internal|release|'
    r'competing|multiple|challenging|under|on\s+a)\s+deadlines?\b|\bdeadline-driven\b',
    re.I,
)
_APPLICATION_DEADLINE_CUE = re.compile(
    r'\b(?:application(?:s)?\s+deadline|closing\s+date(?:\s+for\s+applications)?|'
    r'applications?\s+(?:will\s+)?close|apply\s+(?:by|before|no\s+later\s+than)|'
    r'submit(?:ted)?\s+(?:your\s+)?(?:complete\s+)?application[^.\n]{0,30}(?:by|before)|'
    r'accepting\s+applications\s+until|last\s+day\s+to\s+apply|deadline\s*(?::|is|for\s+applications))\b',
    re.I,
)
_START_DATE_CLAUSE = re.compile(
    r'\b(?:start\s+date|starting\s+in|commencing|cohort\s+starts?)\s*:?\s*'
    r'(?:[a-z]+\s+\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[a-z]+\s+\d{4})',
    re.I,
)
_ROLLING_OR_NO_DEADLINE = re.compile(
    r'\b(?:no\s+(?:application\s+|fixed\s+)?(?:closing\s+date|deadline)|recruit\w*\s+on\s+a\s+rolling\s+basis|rolling\s+basis)\b',
    re.I,
)
_EARLY_CLOSURE_WARNING = re.compile(
    r'\b(?:may\s+close\s+(?:applications\s+)?(?:early|before\s+the\s+advertised\s+deadline)|'
    r'close\s+early\s+if|encourage\s+you\s+to\s+apply\s+early)\b',
    re.I,
)
_DMY_FULL = re.compile(
    r'\b(\d{1,2})(?:\s*(?:st|nd|rd|th))?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|'
    r'jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r',?\s+(20\d{2})\b',
    re.I,
)
_MDY_FULL = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|'
    r'sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:\s*(?:st|nd|rd|th))?,?\s+(20\d{2})\b',
    re.I,
)
_ISO_FULL = re.compile(r'\b(20\d{2})-(\d{2})-(\d{2})\b')
_PARTIAL_NO_YEAR = re.compile(
    r'\b(\d{1,2})(?:\s*(?:st|nd|rd|th))?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|'
    r'jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b(?!,?\s+20\d{2})'
    r'|\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|'
    r'sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:\s*(?:st|nd|rd|th))?\b(?!,?\s+20\d{2})',
    re.I,
)
_TIME_TOKEN = re.compile(r'\b(midnight|noon|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{2}:\d{2})\b', re.I)
_TZ_TOKEN = re.compile(r'\b(UTC|GMT|BST|[+-]\d{2}:\d{2})\b')


def parse_prose_deadline(wording: str) -> dict:
    """Conservatively parse a prose deadline sentence without inventing missing years or timezones.

    Strips adjacent programme start-date spans so e.g. 'Start date: September 2027'
    never contaminates deadline extraction. Returns structured metadata for review.
    """
    clean = plain(wording or '')
    if _WORK_DEADLINE_NOISE.search(clean) and not _APPLICATION_DEADLINE_CUE.search(clean):
        return {'kind': 'non_application_noise', 'candidate_date': None, 'missing_year': False,
                'time_wording': None, 'timezone_wording': None, 'early_closure_warning': False, 'rolling': False}

    rolling = bool(_ROLLING_OR_NO_DEADLINE.search(clean))
    early_close = bool(_EARLY_CLOSURE_WARNING.search(clean))
    # Strip explicit programme start-date clauses before looking for deadline dates.
    scoped = _START_DATE_CLAUSE.sub(' ', clean)
    # Focus after the deadline cue if present so earlier unrelated dates are ignored.
    cue = _APPLICATION_DEADLINE_CUE.search(scoped)
    target = scoped[cue.start():] if cue else scoped

    parsed_date = None
    missing_year = False
    partial_date = None

    m_iso = _ISO_FULL.search(target)
    m_dmy = _DMY_FULL.search(target)
    m_mdy = _MDY_FULL.search(target)
    try:
        if m_iso:
            parsed_date = date(int(m_iso[1]), int(m_iso[2]), int(m_iso[3])).isoformat()
        elif m_dmy:
            parsed_date = date(int(m_dmy[3]), _MONTHS[m_dmy[2].lower()], int(m_dmy[1])).isoformat()
        elif m_mdy:
            parsed_date = date(int(m_mdy[3]), _MONTHS[m_mdy[1].lower()], int(m_mdy[2])).isoformat()
        else:
            m_part = _PARTIAL_NO_YEAR.search(target)
            if m_part:
                missing_year = True
                partial_date = m_part.group(0)
    except ValueError:
        parsed_date = None

    m_time = _TIME_TOKEN.search(target)
    m_tz = _TZ_TOKEN.search(target)
    kind = (
        'explicit_date' if parsed_date else
        'missing_year_date' if missing_year else
        'rolling_or_no_deadline' if rolling else
        'unstructured_warning'
    )
    return {
        'kind': kind,
        'candidate_date': parsed_date,
        'missing_year': missing_year,
        'partial_date': partial_date,
        'time_wording': m_time.group(1) if m_time else None,
        'timezone_wording': m_tz.group(1) if m_tz else None,
        'early_closure_warning': early_close,
        'rolling': rolling,
    }
