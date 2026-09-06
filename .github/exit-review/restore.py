"""Restore exact reviewed commits; publish only a verified fast-forward series."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TARGET = 'refs/heads/release/5.0.0rc6'
STAGING = 'ops/rc6-exits-20260906'
REPOS = {'s7cret/openpine', 's7cret/backtest_engine'}


def git(*args, data=None):
    return subprocess.check_output(['git', *args], input=data).decode().strip()


def load(path):
    plan = json.loads(path.read_text())
    if plan['repository'] not in REPOS or os.environ.get('GITHUB_REPOSITORY', plan['repository']) != plan['repository']:
        raise ValueError('repository mismatch')
    if plan['target'] != TARGET.removeprefix('refs/heads/'):
        raise ValueError('wrong target')
    parent = plan['base']
    for item in plan['commits']:
        if any(not re.fullmatch('[0-9a-f]{40}', item[k]) for k in ('sha', 'parent', 'tree')) or item['parent'] != parent:
            raise ValueError('invalid commit identity or series order')
        raw = item['raw'].encode()
        if not raw.startswith(f"tree {item['tree']}\nparent {parent}\nauthor ".encode()):
            raise ValueError('commit header mismatch')
        if hashlib.sha1(b'commit ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != item['sha']:
            raise ValueError('raw commit hash mismatch')
        parent = item['sha']
    if parent != plan['head']:
        raise ValueError('head mismatch')
    return plan


def restore(path, evidence):
    plan = load(path)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    patches = []
    for item in plan['commits']:
        name = item['patch']
        if not re.fullmatch(r'[0-9]{2}\.patch', name):
            raise ValueError('invalid patch path')
        patch = (path.parent / name).read_bytes()
        if len(patch) > 2_000_000 or hashlib.sha256(patch).hexdigest() != item['sha256']:
            raise ValueError('patch checksum mismatch: ' + name)
        patches.append(patch)
        (evidence / name).write_bytes(patch)
    git('checkout', '--detach', plan['base'])
    for item, patch in zip(plan['commits'], patches, strict=True):
        if git('rev-parse', 'HEAD') != item['parent']:
            raise ValueError('working parent mismatch')
        git('apply', '--check', '--index', '-', data=patch)
        git('apply', '--index', '--whitespace=nowarn', '-', data=patch)
        if git('write-tree') != item['tree']:
            raise ValueError('restored tree mismatch')
        if git('hash-object', '-t', 'commit', '-w', '--stdin', data=item['raw'].encode()) != item['sha']:
            raise ValueError('restored commit mismatch')
        git('checkout', '--detach', item['sha'])
    if git('status', '--porcelain', '--untracked-files=no'):
        raise ValueError('dirty reviewed source')
    git('update-ref', 'refs/heads/review-candidate', plan['head'])
    git('bundle', 'create', str(evidence / 'verified.bundle'), 'refs/heads/review-candidate')
    git('bundle', 'verify', str(evidence / 'verified.bundle'))
    (evidence / 'source-head.txt').write_text(plan['head'] + '\n')


def refs():
    return {ref: sha for sha, ref in (line.split() for line in git('ls-remote', '--refs', 'origin').splitlines())}


def publish(evidence):
    plan = load(evidence / 'plan.json')
    branch, tag = 'refs/heads/' + STAGING, 'refs/tags/' + STAGING
    if os.environ.get('GITHUB_REF') != branch:
        raise ValueError('wrong publication branch')
    sha = os.environ['GITHUB_SHA']
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('invalid maintenance identity')
    git('fetch', str(evidence / 'verified.bundle'), 'refs/heads/review-candidate')
    if git('rev-parse', 'FETCH_HEAD') != plan['head']:
        raise ValueError('bundle mismatch')
    git('merge-base', '--is-ancestor', plan['base'], plan['head'])
    before = refs()
    if before.get(TARGET) not in (plan['base'], plan['head']):
        raise ValueError('release changed concurrently')
    if before[TARGET] != plan['head']:
        git('push', 'origin', plan['head'] + ':' + TARGET)
    if refs().get(TARGET) != plan['head']:
        raise ValueError('publication did not advance target')
    # Exact leases apply only to archival/deletion; never force the release.
    git('push', '--atomic', f'--force-with-lease={branch}:{sha}', f'--force-with-lease={tag}:',
        'origin', sha + ':' + tag, ':' + branch)
    after = refs()
    expected_heads = {k: v for k, v in before.items() if k.startswith('refs/heads/') and k != branch}
    expected_heads[TARGET] = plan['head']
    if {k: v for k, v in after.items() if k.startswith('refs/heads/')} != expected_heads or after.get(tag) != sha:
        raise ValueError('archive or branch preservation failed')
    for name, data in [('before', before), ('after', after)]:
        (evidence / (name + '.json')).write_text(json.dumps(data, indent=2) + '\n')
    (evidence / 'published-head.txt').write_text(plan['head'] + '\n')


if __name__ == '__main__':
    if sys.argv[1] == 'restore':
        restore(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
    elif sys.argv[1] == 'publish':
        publish(Path(sys.argv[2]).resolve())
    else:
        raise ValueError('unknown operation')
