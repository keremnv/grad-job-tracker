"""Serialization of parsed evidence values; no normalization or source comparison."""
import json


def encode_evidence(record: dict) -> bytes:
    """Reproducible ASCII JSON bytes, preserving source strings (including NUL).

    This retains parsed values, not original HTTP/JSON syntax. PostgreSQL computes
    its SHA-256 from the stored bytea. Derived text sanitization is a later concern.
    """
    if not isinstance(record, dict):
        raise TypeError('Evidence must be a parsed record object')
    return json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')
