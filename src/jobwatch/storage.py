"""Owner-file write discipline: exclusive lock, file-identity snapshot, atomic replace.

The same protocol as the application ledger, for JSON owner records (triage,
verifications). A held lock is refused, never waited on. A file changed between
read and replace is refused, never merged."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile

from .contract import JobwatchError


@contextmanager
def locked(path: Path, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise JobwatchError('lock_held', f'{label} lock exists ({lock.name}); another write may be running; nothing written') from None
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def snapshot(path: Path, label: str):
    """Return (bytes, identity) or None when absent; refuse symlinks and mid-read changes."""
    if path.is_symlink():
        raise JobwatchError('invalid_state', f'{label} must not be a symbolic link')
    try:
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
    except FileNotFoundError:
        return None
    signature = lambda s: (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size)
    if signature(before) != signature(after):
        raise JobwatchError('concurrent_edit', f'{label} changed while reading; retry')
    return content, signature(after)


def replace_checked(path: Path, content: str, before, label: str) -> None:
    """Atomically replace `path` (mode 0600) only if it is unchanged since `before`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.jobwatch-', delete=False) as f:
            tmp = Path(f.name)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if snapshot(path, label) != before:
            raise JobwatchError('concurrent_edit', f'{label} changed during the command; nothing written; review and retry')
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
