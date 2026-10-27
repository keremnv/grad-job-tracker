#!/usr/bin/env python3
"""Code-only model-free ARM64/Python3.12 MCP wheelhouse; no credentials or AWS calls."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
import urllib.request
import io


def build(repo,commit,output):
    repo=repo.resolve();output=output.resolve()
    if output.is_relative_to(repo) or output.exists():raise ValueError('Use a new private output outside Git')
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','--verify',commit+'^{commit}'],text=True).strip()!=commit:
        raise ValueError('Use a full explicit commit SHA')
    with tempfile.TemporaryDirectory(prefix='jobwatch-agent-build-') as tmp:
        root=Path(tmp);source=root/'source';source.mkdir()
        subprocess.run(['git','-C',str(repo),'archive','--format=tar','--output='+str(root/'code.tar'),commit,
            'src','pyproject.toml','sql/postgresql/owner_workflows.sql','requirements/agent-read.lock','requirements/tunnel-runtime.json','infra/aws/integrations/systemd'],check=True)
        with tarfile.open(root/'code.tar') as archive:
            for member in archive:
                if member.isdir():continue
                if not member.isfile() or not (source/member.name).resolve().is_relative_to(source):raise ValueError('Unsafe code member')
                p=source/member.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(archive.extractfile(member).read())
        wheels=root/'wheels';wheels.mkdir()
        subprocess.run(['uv','build','--wheel','--out-dir',str(wheels),str(source)],check=True,capture_output=True)
        subprocess.run(['uv','run','--no-project','--with','pip','python','-m','pip','download','--require-hashes',
            '--only-binary=:all:','--platform','manylinux2014_aarch64',
            '--platform','manylinux_2_28_aarch64','--platform','manylinux_2_34_aarch64',
            '--platform','manylinux_2_35_aarch64','--platform','manylinux_2_39_aarch64',
            '--python-version','312','--implementation','cp','--abi','cp312',
            '--dest',str(wheels),'-r',str(source/'requirements/agent-read.lock')],check=True,capture_output=True)
        lock=[]
        for wheel in sorted(wheels.glob('*.whl')):
            name,version=wheel.name.split('-')[:2]
            if name in ('torch','transformers','tokenizers','safetensors'):raise ValueError('Model dependency not allowed')
            lock.append(name+'=='+version+' --hash=sha256:'+sha256(wheel.read_bytes()).hexdigest())
        (root/'requirements.lock').write_text('\n'.join(lock)+'\n')
        shutil.copyfile(source/'requirements/tunnel-runtime.json',root/'tunnel-runtime.json')
        pin=json.loads((root/'tunnel-runtime.json').read_text())['linux']['arm64']
        with urllib.request.urlopen(pin['url'],timeout=90) as response: raw=response.read(100_000_001)
        if len(raw)>100_000_000 or sha256(raw).hexdigest()!=pin['archive_sha256']:raise ValueError('Tunnel archive integrity failure')
        tunnel=root/'tunnel';(tunnel/'bin').mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            body=archive.read('tunnel-client-runtime')
            if sha256(body).hexdigest()!=pin['executable_sha256'] or body[:4]!=b'\x7fELF' or int.from_bytes(body[18:20],'little')!=183:
                raise ValueError('Wrong tunnel runtime architecture/integrity')
            (tunnel/'bin/tunnel-client').write_bytes(body);(tunnel/'bin/tunnel-client').chmod(0o755)
            for name in ('LICENSE','NOTICE'):(tunnel/name).write_bytes(archive.read(name))
        shutil.copytree(source/'infra/aws/integrations/systemd',root/'systemd')
        files=[*sorted(wheels.glob('*.whl')),root/'requirements.lock',root/'tunnel-runtime.json',*sorted(p for p in tunnel.rglob('*') if p.is_file()),*sorted((root/'systemd').glob('*'))]
        manifest={'source_commit':commit,'architecture':'aarch64','python':'3.12','model_inference':False,
            'files':{str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in files}}
        (root/'MANIFEST.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n');files.append(root/'MANIFEST.json')
        output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream,tarfile.open(fileobj=stream,mode='w:gz') as archive:
            for p in files:archive.add(p,arcname=str(p.relative_to(root)),recursive=False)
    return {'commit':commit,'archive_sha256':sha256(output.read_bytes()).hexdigest(),'model_inference':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);p.add_argument('--commit',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.repo,a.commit,a.output)))
