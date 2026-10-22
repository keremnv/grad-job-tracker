@AGENTS.md

## Claude Code specifics

- Run commands as `.venv/bin/python -m jobwatch.cli <command> --json` from the
  repository root and parse the envelope; do not scrape Markdown reports.
- Routine questions need no subagents, plans or file exploration: the commands
  in §2 answer them directly.
- Development work happens only when the owner asks for it in the current task;
  then read `docs/DEVELOPMENT.md` and run the offline tests.
