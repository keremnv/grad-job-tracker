#!/usr/bin/env python3
"""Approved code-only ARM64 integration install. Never configures secrets/SQL or starts units."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile

from deploy_release import verify,make_code_readable

UNITS=('jobwatch-agent-read.service','jobwatch-agent-owner.service','jobwatch-calendar.service',
       'jobwatch-calendar.timer','jobwatch-calendar.path','jobwatch-tunnel@.service')
RUNNING_UNITS=(*UNITS[:-1],'jobwatch-tunnel@read.service','jobwatch-tunnel@owner.service')


def install(archive,sha256,commit,*,installation_root=Path('/'),run_command=subprocess.run):
    if os.geteuid()!=0 or platform.machine()!='aarch64':raise ValueError('Root on the approved ARM64 host is required')
    for unit in RUNNING_UNITS:
        if run_command(['systemctl','is-active','--quiet',unit]).returncode==0 or run_command(['systemctl','is-enabled','--quiet',unit],capture_output=True).returncode==0:
            raise ValueError('Explicitly pause/disable integration units first')
    manifest=verify(archive,sha256,commit)
    if manifest.get('model_inference') is not False:raise ValueError('Only model-free runtime is allowed')
    allowed={'MANIFEST.json','requirements.lock','tunnel-runtime.json','tunnel/LICENSE','tunnel/NOTICE','tunnel/bin/tunnel-client'}|{'systemd/'+n for n in UNITS}
    if not allowed-{'MANIFEST.json'}<=set(manifest['files']):raise ValueError('Incomplete integration artifact')
    if any(n not in allowed and not (n.startswith('wheels/') and n.endswith('.whl')) for n in manifest['files']):
        raise ValueError('Unexpected integration artifact content')
    destination=installation_root/'opt/jobwatch-agent/releases'/commit
    for parent in (installation_root/'opt',installation_root/'opt/jobwatch-agent',destination.parent,destination):
        if parent.is_symlink() or (parent.exists() and parent.stat().st_uid!=0):raise ValueError('Unsafe integration code path')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        if not (destination/'INSTALL_COMPLETE').is_file() or (destination/'INSTALL_COMPLETE').read_text()!=sha256+'\n':
            raise ValueError('Existing/incomplete runtime differs; preserve it')
        for name,digest in manifest['files'].items():
            if (destination/name).lstat().st_uid!=0:raise ValueError('Installed runtime ownership changed')
            if hashlib.sha256((destination/name).read_bytes()).hexdigest()!=digest:raise ValueError('Installed runtime content differs')
    else:
        with tempfile.TemporaryDirectory(dir=destination.parent,prefix='.staging-') as tmp:
            stage=Path(tmp)
            with tarfile.open(archive) as source:
                for item in source:
                    target=stage/item.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.extractfile(item).read())
            stage.rename(destination)
        # Final venv pathname before pip writes console-script shebangs.
        run_command(['python3.12','-m','venv',str(destination/'venv')],check=True)
        run_command([str(destination/'venv/bin/pip'),'install','--no-index','--no-deps','--require-hashes',
            '--find-links',str(destination/'wheels'),'-r',str(destination/'requirements.lock')],check=True)
        for name in ('jobwatch-read-mcp','jobwatch-owner-mcp'):
            run_command([str(destination/'venv/bin'/name),'--help'],check=True,stdout=subprocess.DEVNULL)
        # No inference imports or downloads are allowed in this runtime.
        run_command([str(destination/'venv/bin/python'),'-c',
            "import importlib.util; assert importlib.util.find_spec('torch') is None; import jobwatch.postgres.owner_tools"],check=True)
        (destination/'INSTALL_COMPLETE').write_text(sha256+'\n')
    for parent in (installation_root/'opt/jobwatch-agent',destination.parent):parent.chmod(0o755)
    # Artifact modes are regular files. Set only the pinned runtime executable explicitly.
    (destination/'tunnel/bin/tunnel-client').chmod(0o755)
    make_code_readable(destination)
    for name,target in [('jobwatch-agent-runtime',destination/'venv'),('jobwatch-tunnel',destination/'tunnel')]:
        link=installation_root/'opt'/name
        if link.exists() and not link.is_symlink():raise ValueError('Unexpected active runtime path')
        temporary=link.with_name('.'+name+'-next')
        if temporary.exists() or temporary.is_symlink():raise ValueError('Unexpected pending runtime link')
        temporary.symlink_to(target);temporary.replace(link)
    for name in UNITS:shutil.copyfile(destination/'systemd'/name,installation_root/'etc/systemd/system'/name)
    run_command(['systemctl','daemon-reload'],check=True)
    return {'commit':commit,'installed':True,'units_started':False,'database_or_secret_changes':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path);p.add_argument('--sha256',required=True);p.add_argument('--commit',required=True)
    a=p.parse_args();print(json.dumps(install(a.archive,a.sha256,a.commit)))
