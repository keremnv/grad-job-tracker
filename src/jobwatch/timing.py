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
