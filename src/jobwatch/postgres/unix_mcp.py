"""Private MCP HTTP over a permissioned Unix socket, never a TCP listener."""
import os
from pathlib import Path
import socket
import stat


def listener(path):
    path=Path(path)
    if len(str(path).encode())>100 or path.is_symlink():raise ValueError('Unsafe MCP socket path')
    parent=path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or parent.st_mode&0o022:
        raise ValueError('Use an owned, non-writable private runtime directory')
    if path.exists():
        old=path.lstat()
        if not stat.S_ISSOCK(old.st_mode) or old.st_uid!=os.geteuid():raise ValueError('Unexpected socket occupant')
        probe=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            probe.connect(str(path))
        except ConnectionRefusedError:path.unlink()
        else:raise ValueError('MCP socket already active')
        finally:probe.close()
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        server.bind(str(path));path.chmod(0o660);server.listen(16)
    except BaseException:
        server.close();raise
    return server


def run(server,path):
    import uvicorn
    sock=listener(path);identity=Path(path).lstat()
    try:
        config=uvicorn.Config(server.streamable_http_app(),log_level='warning',access_log=False,lifespan='on')
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        sock.close()
        try:
            actual=Path(path).lstat()
            if (actual.st_dev,actual.st_ino)==(identity.st_dev,identity.st_ino):Path(path).unlink()
        except FileNotFoundError:pass
