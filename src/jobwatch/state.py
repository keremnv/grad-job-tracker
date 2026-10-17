"""Observation history is independent of screening and owner application state."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from .feeds import Posting
from .filtering import classify, role_family, location_judgment
from .timing import deadline_evidence


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema": 1, "postings": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("postings"), dict):
        raise ValueError("unknown or invalid state schema; refusing to overwrite")
    for key, row in data["postings"].items():
        if not isinstance(row, dict) or any(k not in row for k in ("provider", "board", "title", "company", "first_seen", "feed_status")):
            raise ValueError(f"invalid posting state: {key}; refusing to overwrite")
    return data


def merge(state: dict, successful: dict[str, list[Posting]], now: str) -> tuple[dict, list[dict]]:
    existing, events = state["postings"], []
    for boardkey, posts in sorted(successful.items()):
        seen = set()
        for p in posts:
            if f"{p.provider}:{p.board.lower()}" != boardkey:
                raise ValueError("posting belongs to another board")
            seen.add(p.key)  # feed presence must never depend on relevance
            priority, reason = classify(p)
            prior = existing.get(p.key)
            if priority == "excluded" and prior is None:
                continue
            snapshot = {
                "company": p.company, "provider": p.provider, "board": p.board,
                "external_id": p.external_id, "title": p.title, "location": p.location,
                "url": p.url, "priority": priority, "reason": reason,
                "role_family": role_family(p.title), "location_judgment": location_judgment(p)[0],
                "published_at": p.published_at, "source_dates": p.source_dates,
                "salary": p.salary, "country": p.country, "countries": list(p.countries),
                "description": p.description, "raw": p.raw,
                "deadlines": deadline_evidence(p.raw, p.description, p.url, now),
                "feed_status": "listed", "last_seen": now,
            }
            if prior is None:
                snapshot.update(first_seen=now, application_verification={"status": "unknown"})
                existing[p.key] = snapshot
                events.append({"type": "new", "key": p.key, "title": p.title, "company": p.company, "at": now})
            else:
                snapshot["first_seen"] = prior["first_seen"]
                fields = ("title", "location", "url", "priority", "reason", "salary", "source_dates", "description", "country", "countries")
                changes = {k: {"before": prior.get(k), "after": snapshot.get(k)} for k in fields if prior.get(k) != snapshot.get(k)}
                old_deadlines = [{k: v for k, v in d.items() if k != "observed_at"} for d in prior.get("deadlines", [])]
                new_deadlines = [{k: v for k, v in d.items() if k != "observed_at"} for d in snapshot["deadlines"]]
                if old_deadlines != new_deadlines:
                    changes["deadlines"] = {"before": prior.get("deadlines", []), "after": snapshot["deadlines"]}
                event_type = "reappeared" if prior.get("feed_status") == "not_seen" else "changed" if changes else None
                if event_type:
                    event = {"type": event_type, "key": p.key, "title": p.title, "company": p.company, "at": now, "changes": changes}
                    events.append(event)
                    # Keep actual changes, not daily copies. Owner fields stay untouched.
                    prior.setdefault("history", []).append(event.copy())
                existing[p.key] = {**prior, **snapshot}
                existing[p.key].pop("missing_since", None)
        for key, prior in existing.items():
            if (f"{prior.get('provider')}:{str(prior.get('board')).lower()}" == boardkey
                    and key not in seen and prior.get("feed_status") == "listed"):
                prior["feed_status"], prior["missing_since"] = "not_seen", now
                event = {"type": "not_seen", "key": key, "title": prior["title"], "company": prior["company"], "at": now}
                prior.setdefault("history", []).append(event.copy())
                events.append(event)
    return state, events


def save_state(path: Path, state: dict) -> None:
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".jobwatch-", delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(content)
            f.flush()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
