"""Versioned machine-readable output envelope and error codes for agent-facing commands.

Every command run with ``--json`` prints exactly one JSON object::

    {"schema": "jobwatch.cli/1", "command": "queue", "ok": true,
     "generated_at": "...Z", "data": {...}, "warnings": [...], "errors": [...]}

``warnings`` and ``errors`` are lists of ``{"code", "message", ...}``. Codes are
stable identifiers; messages are for humans and may change. Exit status is 0
when ``ok`` is true (warnings allowed) and 2 otherwise.
"""
from __future__ import annotations

import json

ENVELOPE = 'jobwatch.cli/1'

ERROR_CODES = {
    'invalid_argument': 'An argument is missing, malformed or contradictory.',
    'unknown_opportunity': 'No opportunity with that identity exists in the discovery index.',
    'ambiguous_identity': 'The supplied reference matches more than one opportunity.',
    'triage_conflict': 'A different owner decision already exists; pass --replace to change it.',
    'invalid_state': 'A data file failed validation; nothing was written.',
    'lock_held': 'Another writer holds the lock; nothing was written.',
    'concurrent_edit': 'The file changed while the command ran; nothing was written.',
    'verification_conflict': 'Existing verification evidence differs; pass --replace to change it.',
    'io_error': 'A file could not be read or written.',
}
WARNING_CODES = {
    'board_stale': 'A board has no successful observation in the last three days.',
    'board_failed': 'The latest attempt for a board failed; its records are retained observations.',
    'board_never_scanned': 'An enabled board has no recorded observation.',
    'index_incomplete': 'The board was last scanned before complete indexing; excluded postings are not yet recorded.',
    'no_scan_recorded': 'No scan has been recorded; the index may be empty.',
    'judgments_from_older_rules': 'Some judgments were made by older screening rules; the next scan rescreens them.',
    'possible_application_match': 'A ledger row matches only by URL without fragment; it was not linked.',
    'triage_application_conflict': 'An owner triage decision disagrees with the application stage.',
    'orphan_owner_record': 'An owner record refers to an identity absent from the index.',
    'source_text_unavailable': 'Source text was not retained for at least one record.',
    'results_truncated': 'More results exist than were returned; raise --limit.',
    'application_exists': 'An application ledger row already exists for this opportunity.',
    'undocumented_watchlist_entry': 'Watchlist entries lack verification_notes.',
    'migration_pending': 'State is schema 1 on disk; the next scan migrates it and starts complete indexing.',
    'stale_description_check': 'A description check no longer matches the indexed text; recheck before using it.',
    'calendar_evidence_problem': 'Deadline evidence was omitted from calendar export (conflict, provenance or format).',
}


class JobwatchError(ValueError):
    """A ValueError carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code = code
        self.detail = detail


def warning(code: str, message: str | None = None, **detail) -> dict:
    return {'code': code, 'message': message or WARNING_CODES[code], **detail}


def envelope(command: str, now: str, data=None, warnings=(), errors=(), partial=False) -> dict:
    """`data` is null when there are errors, unless the command reports a partial result."""
    return {'schema': ENVELOPE, 'command': command, 'ok': not errors, 'generated_at': now,
            'data': data if not errors or partial else None, 'warnings': list(warnings), 'errors': list(errors)}


def error_from(exc: Exception) -> dict:
    if isinstance(exc, JobwatchError):
        return {'code': exc.code, 'message': str(exc), **exc.detail}
    if isinstance(exc, OSError):
        return {'code': 'io_error', 'message': f'{type(exc).__name__}: {exc.strerror or exc}'}
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return {'code': 'invalid_state', 'message': str(exc)}
    raise exc


def dumps(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
