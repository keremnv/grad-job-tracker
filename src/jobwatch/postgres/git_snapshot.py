"""Bounded read-only Git object access for the one-time importer, never a working tree reader."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

FILES = ('config/employers.json', 'data/jobs.json', 'data/jobs-health.json',
         'data/leads.json', 'data/verifications.json', 'data/triage.json', 'data/applications.csv')
SHA = re.compile(r'[0-9a-f]{64}')


class ImportProblem(RuntimeError):
    """Safe diagnostic. Never include source text, owner notes or driver DETAIL."""


def decode(raw, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('non-finite JSON number')
    try:
        return json.loads(raw.decode('utf-8') if isinstance(raw,bytes) else raw,
                          object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, TypeError):
        raise ImportProblem(f'Invalid JSON object: {label}') from None


class GitSnapshot:
    def __init__(self, repo, commit, *, max_commits=10000, max_bytes=256*1024*1024, max_objects=100000):
        if any(type(v) is not int or v < 1 for v in (max_commits,max_bytes,max_objects)):
            raise ImportProblem('Git recovery bounds must be positive integers')
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
            raise ImportProblem('An explicit full Git commit SHA is required')
        self.repo = Path(repo).resolve()
        self.max_bytes, self.max_objects = max_bytes, max_objects
        self.used = self.entries_used = 0
        self.cache, self.trees = {}, {}
        if self.git('config','--get-regexp',r'^(extensions\.partialclone|remote\..*\.promisor)$',absent_ok=True):
            raise ImportProblem('Partial/promisor repositories are not import inputs; obtain complete local objects first')
        resolved = self.git('rev-parse', '--verify', commit + '^{commit}').decode().strip()
        if resolved != commit or self.git('rev-parse', '--is-shallow-repository').strip() != b'false':
            raise ImportProblem('Commit must resolve exactly in a non-shallow repository; fetch full history first')
        self.commit = resolved
        commits = self.git('rev-list', '--full-history', '--topo-order', '--reverse', '--max-count=' + str(max_commits+1),
                           resolved, '--', *FILES, 'data/jobs-text').decode().splitlines()
        if len(commits) > max_commits:
            raise ImportProblem('Git history bound exceeded; explicitly raise the limit, never truncate recovery')
        self.commits = tuple(dict.fromkeys([*commits, resolved]))

    def git(self, *args, absent_ok=False):
        result = subprocess.run(['git', '--no-replace-objects', '-C', str(self.repo), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={**os.environ,'GIT_NO_LAZY_FETCH':'1','GIT_TERMINAL_PROMPT':'0','GIT_OPTIONAL_LOCKS':'0'})
        if absent_ok and result.returncode==1:
            return b''
        if result.returncode:
            raise ImportProblem('Git object read failed; verify repository, commit and complete object availability')
        return result.stdout

    def tree(self, commit):
        if commit not in self.trees:
            entries = {}
            raw = self.git('ls-tree', '-rz', commit, '--', *FILES, 'data/jobs-text')
            for line in raw.split(b'\0'):
                if not line:
                    continue
                metadata, path = line.split(b'\t', 1)
                mode, kind, oid = metadata.decode().split()
                path = path.decode('utf-8')
                if mode not in ('100644', '100755') or kind != 'blob':
                    raise ImportProblem('Import input must be a tracked regular file')
                entries[path] = oid
            self.entries_used += len(entries)
            if self.entries_used > self.max_objects:
                raise ImportProblem('Git tree object bound exceeded')
            self.trees[commit] = entries
        return self.trees[commit]

    def blob(self, commit, path, *, required=False):
        oid = self.tree(commit).get(path)
        if oid is None:
            if required:
                raise ImportProblem(f'Required frozen file is absent: {path}')
            return None
        if oid not in self.cache:
            size = int(self.git('cat-file', '-s', oid))
            if size > 32*1024*1024 or self.used+size > self.max_bytes or len(self.cache) >= self.max_objects:
                raise ImportProblem('Git evidence recovery bound exceeded')
            raw = self.git('cat-file', 'blob', oid)
            if len(raw) != size:
                raise ImportProblem('Git object size mismatch')
            self.cache[oid] = raw
            self.used += size
        return self.cache[oid]

    def document(self, commit, path, *, required=False):
        raw = self.blob(commit, path, required=required)
        return decode(raw, path) if raw is not None else None

    def time(self, commit):
        seconds = int(self.git('show', '-s', '--format=%ct', commit))
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')

    def text_objects(self):
        """Verify every retained file version, including objects no longer present at HEAD."""
        texts = {}
        for commit in self.commits:
            for path, oid in sorted(self.tree(commit).items()):
                if not path.startswith('data/jobs-text/'):
                    continue
                digest = path.rsplit('/', 1)[-1].removesuffix('.txt')
                if not SHA.fullmatch(digest) or path != f'data/jobs-text/{digest[:2]}/{digest}.txt':
                    raise ImportProblem('Invalid retained source-text path')
                raw = self.blob(commit, path)
                if sha256(raw).hexdigest() != digest:
                    raise ImportProblem(f'Description hash mismatch at {commit}:{path}')
                try:
                    value = raw.decode('utf-8')
                except UnicodeError:
                    raise ImportProblem(f'Invalid UTF-8 description: {digest}') from None
                entry = texts.setdefault(digest, {'text': value, 'provenance': []})
                if entry['text'] != value:
                    raise ImportProblem('Conflicting content for one description hash')
                entry['provenance'].append({'commit': commit, 'path': path, 'blob': oid})
        return texts
