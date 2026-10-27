#!/usr/bin/env python3
"""Owner-local capability custody. Never prints tokens or subscription URLs."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

FEEDS=('deadlines','actions','combined')


def create(path,feeds,domain=None):
    if not feeds or len(set(feeds))!=len(feeds) or any(f not in FEEDS for f in feeds):raise ValueError('Invalid feeds')
    if domain is not None and not re.fullmatch(r'[a-z0-9]+\.cloudfront\.net',domain):raise ValueError('Invalid distribution domain')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_mode&0o077:raise ValueError('Use a private 0700 parent')
    tokens={f:secrets.token_urlsafe(32) for f in feeds}
    value={'schema':'jobwatch.calendar-capabilities/1','tokens':tokens,
        'hashes':{f:sha256(t.encode()).hexdigest() for f,t in tokens.items()},
        'urls':{f:'https://'+domain+'/'+t+'/'+f+'.ics' for f,t in tokens.items()} if domain else {}}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n')
    return value['hashes']


def bind_domain(path,domain):
    """After distribution creation, preserve existing tokens and bind only its host."""
    if not re.fullmatch(r'[a-z0-9]+\.cloudfront\.net',domain or ''):raise ValueError('Invalid distribution domain')
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077
            or path.parent.is_symlink() or path.parent.stat().st_mode&0o077):
        raise ValueError('Use an owned private capability file')
    value=json.loads(path.read_text())
    if value.get('schema')!='jobwatch.calendar-capabilities/1' or not value.get('tokens'):
        raise ValueError('Unexpected capability custody format')
    for feed,token in value['tokens'].items():
        if feed not in FEEDS or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token) or sha256(token.encode()).hexdigest()!=value['hashes'].get(feed):
            raise ValueError('Capability integrity failure')
    value['urls']={f:'https://'+domain+'/'+t+'/'+f+'.ics' for f,t in value['tokens'].items()}
    fd,temporary=tempfile.mkstemp(prefix='.capabilities-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--private-output',type=Path,required=True)
    p.add_argument('--feeds',nargs='+',choices=FEEDS,default=['deadlines','actions'])
    p.add_argument('--domain');p.add_argument('--hashes-output',type=Path)
    p.add_argument('--bind-domain',action='store_true',help='Bind host to an existing private file without rotating tokens')
    a=p.parse_args();os.umask(0o077)
    try:
        if a.bind_domain:
            if a.hashes_output:p.error('Binding does not rewrite hash inputs')
            bind_domain(a.private_output,a.domain)
            print(json.dumps({'bound':True,'tokens_or_urls_printed':False}));return 0
        if not a.hashes_output:p.error('--hashes-output is required when creating capabilities')
        hashes=create(a.private_output,a.feeds,a.domain)
        fd=os.open(a.hashes_output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:json.dump({'capability_hashes':hashes},f,sort_keys=True,indent=2);f.write('\n')
        print(json.dumps({'created':True,'feeds':len(hashes),'tokens_or_urls_printed':False}))
    except Exception:
        print(json.dumps({'created':False,'error':'Preserve private files; use fresh private paths'}));return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
