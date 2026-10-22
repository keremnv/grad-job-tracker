"""Owner application management, read-only vacancy collection and local reports."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from .feeds import collect_board
from .config import load_config, watchlist
from .overview import weekly, requirements
from .applications import read_applications, configure_parser, run_cli
from . import calendar
from . import commands as agent_commands
from .leads import load_leads, render_leads
from .filtering import RULES_VERSION, rank, plain, verdict_of
from .render import text, link
from .timing import deadline_window, stale
from .verifications import load_verifications
from .state import atomic_write, health_path, last_seen, load_state, merge, save_state, utcnow


def _explanation(row):
    return (row.get("screening") or {}).get("explanation") or row.get("reason", "")


def _fields(event):
    return event.get("fields") or list(event.get("changes", {}))


def report(state: dict, events=None, failures=None, when=None, verifications=None, as_of=None) -> str:
    verifications = verifications or {}
    as_of = as_of or utcnow()
    boards = state.get("boards", {})
    scan_info = state.get("last_scan", {})
    events = scan_info.get("events", []) if events is None else events
    failures = scan_info.get("failures", []) if failures is None else failures
    when = when or scan_info.get("at", "unknown (no recorded scan)")
    postings = state["postings"]
    shown = sorted((v for v in postings.values() if v["feed_status"] == "listed" and verdict_of(v) != "excluded"),
                   key=rank)
    excluded = [v for v in postings.values() if v["feed_status"] == "listed" and verdict_of(v) == "excluded"]
    candidate = lambda e: e.get("key") in postings and verdict_of(postings[e["key"]]) != "excluded"
    source = [e for e in events if e.get("category", "source") == "source" and e["type"] != "backfilled" and candidate(e)]
    backfilled = [e for e in events if e["type"] == "backfilled"]
    screening = [e for e in events if e.get("category") == "screening"]
    lines = ["# UK early-career job watch", "", f"Last scan attempt (UTC): {text(when)}", "",
             "Feed appearance is **not** confirmation that an application form is accepting submissions.",
             "Deadlines, graduation eligibility, work authorisation and sponsorship require manual review.", "",
             f"Listed candidates: **{len(shown)}** · Excluded but recorded: **{len(excluded)}** · "
             f"Source changes in last scan: **{len(source)}** · Screening changes: **{len(screening)}** · Failed boards: **{len(failures)}**", "",
             "## Board health", ""]
    for key, health in sorted(boards.items()):
        if stale(health.get("last_success"), as_of):
            lines.append(f"- **STALE (>3 days or unknown)**: {text(key)}; checked as of {text(as_of)}.")
        lines.append(f"- {text(key)}: **{text(health.get('status', 'unknown'))}**; last successful observation: {text(health.get('last_success', 'unknown'))}; last attempt: {text(health.get('last_attempt', 'unknown'))}; last successful count: {text(health.get('count', 'unknown'))}.")
        if health.get("screening"):
            lines.append(f"  Screening judgments: {text(health['screening'])}.")
    if not boards:
        lines.append("No board-health history recorded.")
    if failures:
        lines += ["", "## Board failures (previous observations retained)", ""] + [f"- {text(x)}" for x in failures]
    if source or backfilled:
        lines += ["", "## Source changes in last scan", "", "Employer-side observations only: appeared, edited, absent or reappeared.", ""]
        for e in source:
            fields = ", ".join(_fields(e))
            lines.append(f"- **{text(e['type'])}**: {text(e['company'])} — {text(e['title'])} ({text(e['key'])})" + (f"; fields: {text(fields)}" if fields else ""))
        if backfilled:
            lines.append(f"- {len(backfilled)} posting(s) recorded for the first time by complete indexing; they were listed earlier but not retained, so their first listing date is unknown.")
    if screening:
        moved = [e for e in screening if "excluded" in {e.get("before", {}).get("verdict"), e.get("after", {}).get("verdict")}
                 and e.get("before", {}).get("verdict") != e.get("after", {}).get("verdict")]
        lines += ["", "## Screening changes in last scan", "",
                  f"Our judgment changed for {len(screening)} posting(s); these are **not** employer edits. "
                  "Entering or leaving the queue:", ""]
        lines += [f"- {text(e['company'])} — {text(e['title'])}: {text(e['before'].get('verdict'))} → {text(e['after'].get('verdict'))} ({text(e.get('cause', 'unknown'))})" for e in moved[:30]] or ["- None."]
        if len(moved) > 30:
            lines.append(f"- … {len(moved) - 30} more; run `jobwatch changes --category screening`.")
    lines += ["", "## Source-stated deadlines", "", f"Urgency evaluated as of {text(as_of)}; missing deadline remains unknown."]
    deadline_count = 0
    for key, row in postings.items():
        if verdict_of(row) == "excluded":
            continue
        for evidence in row.get("deadlines", []) + verifications.get(key, {}).get("deadlines", []):
            deadline_count += 1
            lines.append(f"- {text(row['company'])} — {link(row['title'], evidence.get('url', row['url']))}: {text(plain(evidence['wording']))}; {text(deadline_window(evidence, as_of))}; observed {text(evidence.get('observed_at', evidence.get('verified_at', 'unknown')))}.")
    if not deadline_count:
        lines.append("No source-stated deadline evidence recorded for tracked candidates. No deadlines inferred.")
    lines += ["", "## Ranked review queue (first 15)", "", "Order: priority judgments, then early-career title signals; company/title/ID break ties. Employer prestige and personal weights are not used.", ""]
    for row in shown[:15]:
        health = boards.get(f"{row['provider']}:{row['board'].lower()}", {})
        retained = "; **board failed; retained observation**" if health.get("status") == "failed" else ""
        lines.append(f"- **{text(verdict_of(row))}** · {text(row['company'])} — {link(row['title'], row['url'])} — {text(row['location'])}{retained}")
        verification = verifications.get(f"{row['provider']}:{row['board'].lower()}:{row['external_id']}", row.get("application_verification", {}))
        lines.append(f"  {text(_explanation(row))}. First observed: {text(row['first_seen'])}; last observed: {text(last_seen(row, boards) or 'unknown')}. Application status: {text(verification.get('status', 'unknown'))}.")
        if verification.get("verified_at"):
            lines.append(f"  Application page inspected: {link(verification['verified_at'], verification.get('url', ''))}; recheck before applying.")
    if len(shown) > 15:
        lines += ["", "## Remaining candidates", ""]
        lines += [f"- {text(verdict_of(r))} · {text(r['company'])} — {link(r['title'], r['url'])}; {text(_explanation(r))}." for r in shown[15:]]
    if not shown:
        lines.append("No matching candidates currently known; inspect failed boards and screening judgments.")
    missing = [r for r in postings.values() if r["feed_status"] == "not_seen" and verdict_of(r) != "excluded"]
    if missing:
        lines += ["", "## Not seen in a successful feed (closure unknown)", ""]
        lines += [f"- {text(r['company'])} — {link(r['title'], r['url'])}; absent since {text(r.get('missing_since', 'unknown'))}." for r in missing]
    lines += ["", "## Important boundaries", "",
              "- Only configured successful feeds were observed; other UK vacancies may exist.",
              "- `not_seen` is absence from a successful feed, never verified closure.",
              "- Source dates retain provider semantics in `source_dates`; first_seen is a collector observation.",
              "- Screening is heuristic; eligibility and location uncertainty require owner review.",
              f"- {len(excluded)} listed posting(s) were excluded by screening but are recorded with reason codes; inspect them with `jobwatch excluded`.", ""]
    return "\n".join(lines)


def _bytes(path):
    return path.read_bytes() if path.exists() else None


def _compact(event):
    """Scan summaries keep identities and field names, not before/after values."""
    out = {k: event[k] for k in ("type", "category", "key", "title", "company", "at", "cause") if k in event}
    if event.get("changes"):
        out["fields"] = sorted(event["changes"])
    if event.get("category") == "screening":
        out["before"] = {"verdict": event["before"]["verdict"]}
        out["after"] = {"verdict": event["after"]["verdict"]}
    return out


def scan(config_path: Path, state_path: Path, report_path: Path, verification_path: Path | None = None,
         application_path: Path | None = None, lead_path: Path | None = None) -> int:
    verification_path = verification_path or state_path.parent / "verifications.json"
    protected = {verification_path.resolve(), config_path.resolve(), (state_path.parent / 'applications.csv').resolve(), (state_path.parent / 'verifications.json').resolve()}
    protected.update(p.resolve() for p in (Path('data/applications.csv'), Path('data/leads.json'), Path('data/triage.json'),
                                          application_path or state_path.parent / 'applications.csv',
                                          lead_path or state_path.parent / 'leads.json', state_path.parent / 'leads.json',
                                          state_path.parent / 'triage.json'))
    outputs = (state_path.resolve(), health_path(state_path).resolve())
    if report_path.resolve() in outputs or set(outputs) & protected or report_path.resolve() in protected:
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
        watched = (state_path, health_path(state_path), report_path)
        before = [_bytes(p) for p in watched]
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
            health = {"last_attempt": utcnow()}
            try:
                stats = {}
                posts = collect_board(entry, stats=stats)
                good[key] = posts
                health.update(status="success", fetched_at=utcnow(), count=len(posts),
                              source_records=stats.get("source_records", len(posts)),
                              skipped_non_vacancies=stats.get("skipped_non_vacancies", 0))
                print(f"OK {text(entry['company'])}: {len(posts)} feed postings", file=sys.stderr)
            except (ValueError, TypeError, KeyError, OSError, TimeoutError) as exc:
                message = f"{entry.get('company', '?')}: {type(exc).__name__}: {exc}"
                failures.append(message)
                health.update(status="failed", error=message)
                print(f"FAILED {text(message)}", file=sys.stderr)
            health_updates[key] = health
        when = utcnow()
        state, events = merge(state, good, when)
        for key, health in health_updates.items():
            current = state["boards"].setdefault(key, {})
            if health["status"] == "success":
                current.pop("error", None)
                listed = [r for r in state["postings"].values()
                          if f"{r['provider']}:{r['board'].lower()}" == key and r["feed_status"] == "listed"]
                if len(listed) != health["count"]:
                    raise ValueError(f"index accounting mismatch for {key}; refusing to write")
                health["screening"] = dict(sorted(Counter(verdict_of(r) for r in listed).items()))
            current.update(health)
        if not enabled:
            failures.append("No enabled feeds; no collection attempted")
        state["last_scan"] = {"at": when, "started_at": started_at, "rules_version": RULES_VERSION,
                              "events": [_compact(e) for e in events], "failures": failures}
        rendered = report(state, verifications=verifications, as_of=when)
        # Detect edits made by the owner while network requests were running.
        if [_bytes(p) for p in watched] != before:
            print("State/report changed during scan; refusing to overwrite. Rerun after reviewing edits.", file=sys.stderr)
            return 2
        save_state(state_path, state, when)
        atomic_write(report_path, rendered)
        print(f"Success: {len(good)} boards; {len(failures)} failures; {len(events)} changes")
        return 2 if not good else 1 if failures else 0
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest="command", required=True)
    for command in ("scan", "report", "watchlist", "weekly", "requirements", "leads"):
        p = commands.add_parser(command)
        p.add_argument("--config", default="config/employers.json")
        p.add_argument("--state", default="data/jobs.json")
        p.add_argument("--report", default="reports/latest.md")
        p.add_argument("--verifications", default="data/verifications.json")
        p.add_argument("--applications", default="data/applications.csv")
        p.add_argument("--leads", default="data/leads.json")
        p.add_argument("--output", help="Local report output; weekly defaults to ignored .local-reports/weekly.md")
    configure_parser(commands)
    calendar.configure_parser(commands)
    agent_commands.configure(commands)
    ns = ap.parse_args(argv)
    if ns.command in agent_commands.HANDLED:
        return agent_commands.run(ns)
    try:
        if ns.command == "calendar":
            return calendar.run_cli(ns)
        if ns.command == "applications":
            return run_cli(ns)
        if ns.command == "scan":
            return scan(Path(ns.config), Path(ns.state), Path(ns.report), Path(ns.verifications),
                        Path(ns.applications), Path(ns.leads))
        if ns.command == "watchlist":
            print(watchlist(load_config(Path(ns.config))), end="")
            return 0
        if ns.command == "leads":
            dest = Path(ns.output or "reports/leads.md")
            if dest.resolve() in {Path(p).resolve() for p in (ns.config, ns.state, ns.applications, ns.verifications, ns.leads)}:
                raise ValueError("report output collides with protected owner/config data")
            atomic_write(dest, render_leads(load_leads(Path(ns.leads)), utcnow()))
            print(f"Wrote {text(dest)}")
            return 0
        verifications = load_verifications(Path(ns.verifications))
        state = load_state(Path(ns.state))
        if ns.command in {"weekly", "requirements"}:
            now = utcnow()
            rendered = weekly(state, read_applications(Path(ns.applications)), verifications, now,
                              load_leads(Path(ns.leads)) if Path(ns.leads).exists() else None) if ns.command == "weekly" else requirements(state, verifications, now)
            dest = Path(ns.output or (".local-reports/weekly.md" if ns.command == "weekly" else "reports/requirements.md"))
            protected = {Path(p).resolve() for p in (ns.config, ns.state, ns.applications, ns.verifications, ns.leads)}
            if dest.resolve() in protected:
                raise ValueError("report output collides with protected owner/config data")
            if ns.command == "weekly" and ".local-reports" not in dest.resolve().parts:
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
