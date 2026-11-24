#!/usr/bin/env python3
"""Research MCP availability health. Native socket probe; no tunnel, no secrets.

Runs as the read tunnel identity, which may connect only to the Research MCP
Unix socket. Emits one allowlisted summary through the existing operational
log publisher; raw errors, SQL and responses are never logged.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_operation import publish  # noqa: E402

SOCKET = '/run/jobwatch-agent-read/mcp.sock'
STATE_NAME = 'state.json'


def evaluate(probe_ok, active_state, restarts, previous_restarts):
    """Pure health decision; previous_restarts=None means first observation."""
    delta = 0 if previous_restarts is None else max(0, restarts - previous_restarts)
    healthy = bool(probe_ok) and active_state == 'active' and delta == 0
    return {'mcp_healthy': int(healthy), 'restarts_delta': delta}


async def probe(socket_path):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    transport = httpx.AsyncHTTPTransport(uds=socket_path)
    async with httpx.AsyncClient(transport=transport, timeout=30.0, follow_redirects=False) as client:
        async with streamable_http_client('http://127.0.0.1:8765/mcp', http_client=client) as (rx, tx, _):
            async with ClientSession(rx, tx) as session:
                await session.initialize()
                tools = await session.list_tools()
                if not tools.tools:
                    return False
                result = await session.call_tool('sql_query', {'sql': 'SELECT 1 AS ok', 'limit': 1})
                payload = result.structuredContent or {}
                return payload.get('ok') is True and not result.isError


def service_facts():
    out = subprocess.run(['systemctl', 'show', 'jobwatch-agent-read.service',
                          '-p', 'ActiveState', '-p', 'NRestarts', '-p', 'MemoryCurrent'],
                         capture_output=True, text=True, timeout=15)
    facts = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition('=')
        facts[key] = value
    return facts.get('ActiveState', 'unknown'), int(facts.get('NRestarts') or 0)


def load_previous(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if os.fstat(stream.fileno()).st_size > 4096:
                return None
            return int(json.loads(stream.read(4097))['restarts'])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_state(path, restarts):
    parent = path.parent
    st = parent.stat()
    if parent.is_symlink() or st.st_mode & 0o022:
        raise ValueError('unsafe_state_directory')
    fd = os.open(path.with_suffix('.tmp'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'restarts': restarts}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path.with_suffix('.tmp'), path)
    except BaseException:
        path.with_suffix('.tmp').unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=SOCKET)
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/jobwatch-mcp-health'))
    parser.add_argument('--config', type=Path, default=Path('/etc/jobwatch/aws.json'))
    args = parser.parse_args(argv)
    summary = {'operation': 'mcp_health', 'mcp_healthy': 0, 'probe_ok': 0,
               'restarts': 0, 'restarts_delta': 0}
    try:
        state_path = args.state_dir / STATE_NAME
        previous = load_previous(state_path)
        active_state, restarts = service_facts()
        try:
            probe_ok = asyncio.run(probe(args.socket))
        except Exception:
            probe_ok = False  # Sanitised: availability failure, no detail logged.
        summary.update(evaluate(probe_ok, active_state, restarts, previous),
                       probe_ok=int(probe_ok), restarts=restarts)
        save_state(state_path, restarts)
        delivered = publish(summary, json.loads(args.config.read_text()))
        return 0 if delivered and summary['mcp_healthy'] else 2
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('MCP health check failed; private diagnostics omitted', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
