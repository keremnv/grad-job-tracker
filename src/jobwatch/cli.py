"""Read-only vacancy collection and local Markdown reports."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from .feeds import collect_board
from .config import load_config, watchlist
from .overview import read_applications, weekly, requirements
from .filtering import classify, rank, plain
from .render import text, link
from .timing import deadline_window, stale
from .state import atomic_write, load_state, merge, save_state, utcnow


def report(state: dict, events=None, failures=None, when=None, verifications=None, as_of=None) -> str:
    verifications = verifications or {}
    as_of = as_of or utcnow()
    scan_info = state.get("last_scan", {})
    events = scan_info.get("events", []) if events is None else events
    failures = scan_info.get("failures", []) if failures is None else failures
    when = when or scan_info.get("at", "unknown (no recorded scan)")
    shown = sorted((v for v in state["postings"].values() if v["feed_status"] == "listed" and v["priority"] != "excluded"),
                   key=rank)
    lines = ["# UK early-career job watch", "", f"Last scan attempt (UTC): {text(when)}", "",
             "Feed appearance is **not** confirmation that an application form is accepting submissions.",
             "Deadlines, graduation eligibility, work authorisation and sponsorship require manual review.", "",
             f"Listed candidates: **{len(shown)}** · Changes in last scan: **{len(events)}** · Failed boards: **{len(failures)}**", "",
             "## Board health", ""]
    for key, health in sorted(state.get("boards", {}).items()):
        if stale(health.get("last_success"), as_of):
            lines.append(f"- **STALE (>3 days or unknown)**: {text(key)}; checked as of {text(as_of)}.")
        lines.append(f"- {text(key)}: **{text(health['status'])}**; last successful observation: {text(health.get('last_success', 'unknown'))}; last attempt: {text(health['last_attempt'])}; last successful count: {text(health.get('count', 'unknown'))}.")
        if health.get("screening"):
            lines.append(f"  Screening judgments: {text(health['screening'])}.")
    if not state.get("boards"):
        lines.append("No board-health history recorded.")
    if failures:
        lines += ["", "## Board failures (previous observations retained)", ""] + [f"- {text(x)}" for x in failures]
    if events:
        lines += ["", "## Changes in last scan", ""]
        for e in events:
            fields = ", ".join(e.get("changes", {}))
            lines.append(f"- **{text(e['type'])}**: {text(e['company'])} — {text(e['title'])} ({text(e['key'])})" + (f"; fields: {text(fields)}" if fields else ""))
    lines += ["", "## Source-stated deadlines", "", f"Urgency evaluated as of {text(as_of)}; missing deadline remains unknown."]
    deadline_count = 0
    for key, row in state["postings"].items():
        if row["priority"] == "excluded":
            continue
        for evidence in row.get("deadlines", []) + verifications.get(key, {}).get("deadlines", []):
            deadline_count += 1
            lines.append(f"- {text(row['company'])} — {link(row['title'], evidence.get('url', row['url']))}: {text(plain(evidence['wording']))}; {text(deadline_window(evidence, as_of))}; observed {text(evidence.get('observed_at', evidence.get('verified_at', 'unknown')))}.")
    if not deadline_count:
        lines.append("No source-stated deadline evidence recorded for tracked candidates. No deadlines inferred.")
    lines += ["", "## Ranked review queue (first 15)", "", "Order: priority judgments, then early-career title signals; company/title/ID break ties. Employer prestige and personal weights are not used.", ""]
    for row in shown[:15]:
        health = state.get("boards", {}).get(f"{row['provider']}:{row['board'].lower()}", {})
        retained = "; **board failed; retained observation**" if health.get("status") == "failed" else ""
        lines.append(f"- **{text(row['priority'])}** · {text(row['company'])} — {link(row['title'], row['url'])} — {text(row['location'])}{retained}")
        verification = verifications.get(f"{row['provider']}:{row['board'].lower()}:{row['external_id']}", row.get("application_verification", {}))
        lines.append(f"  {text(row['reason'])}. First observed: {text(row['first_seen'])}; last observed: {text(row['last_seen'])}. Application status: {text(verification.get('status', 'unknown'))}.")
        if verification.get("verified_at"):
            lines.append(f"  Application page inspected: {link(verification['verified_at'], verification.get('url', ''))}; recheck before applying.")
    if len(shown) > 15:
        lines += ["", "## Remaining candidates", ""]
        lines += [f"- {text(r['priority'])} · {text(r['company'])} — {link(r['title'], r['url'])}; {text(r['reason'])}." for r in shown[15:]]
    if not shown:
        lines.append("No matching candidates currently known; inspect failed boards and screening judgments.")
    missing = [r for r in state["postings"].values() if r["feed_status"] == "not_seen"]
    if missing:
        lines += ["", "## Not seen in a successful feed (closure unknown)", ""]
        lines += [f"- {text(r['company'])} — {link(r['title'], r['url'])}; absent since {text(r.get('missing_since', 'unknown'))}." for r in missing]
    lines += ["", "## Important boundaries", "",
              "- Only configured successful feeds were observed; other UK vacancies may exist.",
              "- `not_seen` is absence from a successful feed, never verified closure.",
              "- Source dates retain provider semantics in `source_dates`; first_seen is a collector observation.",
              "- Screening is heuristic; eligibility and location uncertainty require owner review.", ""]
    return "\n".join(lines)


def load_verifications(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("postings"), dict):
        raise ValueError("invalid verification ledger")
    for row in data["postings"].values():
        if not isinstance(row, dict) or row.get("status") not in {"unknown", "open_verified", "closed_verified"}:
            raise ValueError("invalid verification status")
        if row["status"] != "unknown":
            from .config import timestamp
            from .feeds import public_url
            timestamp(row.get("verified_at"))
            if not public_url(row.get("url", "")):
                raise ValueError("verification requires source URL")
    return data["postings"]


def _bytes(path):
    return path.read_bytes() if path.exists() else None


def scan(config_path: Path, state_path: Path, report_path: Path, verification_path: Path | None = None) -> int:
    verification_path = verification_path or state_path.parent / "verifications.json"
    protected = {verification_path.resolve(), config_path.resolve(), (state_path.parent / 'applications.csv').resolve(), (state_path.parent / 'verifications.json').resolve()}
    if state_path.resolve() == report_path.resolve() or state_path.resolve() in protected or report_path.resolve() in protected:
        raise ValueError('scan output collides with protected owner/config data')
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock = state_path.with_suffix(state_path.suffix + ".scan.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        print("Scan lock exists; another scan may be running. No files changed.", file=sys.stderr)
        return 2
    try:
        os.close(fd)
        before_state, before_report = _bytes(state_path), _bytes(report_path)
        cfg = load_config(config_path)
        state = load_state(state_path)
        verifications = load_verifications(verification_path)
        good, failures, health_updates = {}, [], {}
        started_at = utcnow()
        enabled = [e for e in cfg["employers"] if e.get("enabled", False)]
        identities = [f"{e['provider']}:{e['board'].lower()}" for e in enabled]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate configured board; refusing ambiguous scan")
        for entry, key in zip(enabled, identities):
            health = dict(state.get("boards", {}).get(key, {}))
            health["last_attempt"] = utcnow()
            try:
                posts = collect_board(entry)
                good[key] = posts
                health.update(status="success", last_success=utcnow(), count=len(posts), screening=dict(Counter(classify(p)[0] for p in posts)))
                health.pop("error", None)
                print(f"OK {text(entry['company'])}: {len(posts)} feed postings", file=sys.stderr)
            except (ValueError, TypeError, KeyError, OSError, TimeoutError) as exc:
                message = f"{entry.get('company', '?')}: {type(exc).__name__}: {exc}"
                failures.append(message)
                health.update(status="failed", error=message)
                print(f"FAILED {text(message)}", file=sys.stderr)
            health_updates[key] = health
        when = utcnow()
        state, events = merge(state, good, when)
        state.setdefault("boards", {}).update(health_updates)
        if not enabled:
            failures.append("No enabled feeds; no collection attempted")
        state["last_scan"] = {"at": when, "started_at": started_at, "events": events, "failures": failures}
        rendered = report(state, verifications=verifications, as_of=when)
        # Detect edits made by the owner while network requests were running.
        if _bytes(state_path) != before_state or _bytes(report_path) != before_report:
            print("State/report changed during scan; refusing to overwrite. Rerun after reviewing edits.", file=sys.stderr)
            return 2
        save_state(state_path, state)
        atomic_write(report_path, rendered)
        print(f"Success: {len(good)} boards; {len(failures)} failures; {len(events)} changes")
        return 2 if not good else 1 if failures else 0
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["scan", "report", "watchlist", "weekly", "requirements"])
    ap.add_argument("--config", default="config/employers.json")
    ap.add_argument("--state", default="data/jobs.json")
    ap.add_argument("--report", default="reports/latest.md")
    ap.add_argument("--verifications", default="data/verifications.json")
    ap.add_argument("--applications", default="data/applications.csv")
    ap.add_argument("--output", help="Local report output; weekly defaults to ignored .local-reports/weekly.md")
    ns = ap.parse_args(argv)
    try:
        if ns.command == "scan":
            return scan(Path(ns.config), Path(ns.state), Path(ns.report), Path(ns.verifications))
        if ns.command == "watchlist":
            print(watchlist(load_config(Path(ns.config))), end="")
            return 0
        verifications = load_verifications(Path(ns.verifications))
        state = load_state(Path(ns.state))
        if ns.command in {"weekly", "requirements"}:
            now = utcnow()
            rendered = weekly(state, read_applications(Path(ns.applications)), verifications, now) if ns.command == "weekly" else requirements(state, verifications, now)
            dest = Path(ns.output or (".local-reports/weekly.md" if ns.command == "weekly" else "reports/requirements.md"))
            protected = {Path(p).resolve() for p in (ns.config, ns.state, ns.applications, ns.verifications)}
            if dest.resolve() in protected:
                raise ValueError("report output collides with protected owner/config data")
            if ns.command == "weekly" and ".local-reports" not in dest.parts:
                raise ValueError("private weekly output must be inside ignored .local-reports directory")
            atomic_write(dest, rendered)
            if ns.command == "weekly":
                dest.chmod(0o600)
            print(f"Wrote {text(dest)}")
            return 0
        print(report(state, verifications=verifications))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"ERROR: {text(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
