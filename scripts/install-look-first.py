#!/usr/bin/env python3
"""Install an explicit, immutable LookFirst release. This does not approve AODL actions."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def install(source, repository, destination, extensions, expected_commit):
    source, repository = source.resolve(), repository.resolve()
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != expected_commit:
        raise ValueError('Source commit differs from the reviewed commit')
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=normal'], text=True)
    if dirty.strip():
        raise ValueError('Source checkout contains uncommitted files')
    subprocess.run(['git', '-C', str(repository), 'rev-parse', '--git-common-dir'], check=True, capture_output=True)
    release = destination / 'releases' / actual
    files = ['src/z0int/look_first.py', 'scripts/look-first/generate.py', 'scripts/look-first/collect.py', 'scripts/look-first/roots.json', 'omp-extensions/z0-look-first/index.js']
    link = extensions / 'z0-look-first'
    current = destination / 'current'
    if link.exists() or link.is_symlink():
        raise ValueError('Extension path already exists; refusing to replace user configuration')
    if current.exists() or current.is_symlink():
        raise ValueError('Current release already exists; explicit reviewed upgrade required')
    for name in files:
        if not (source / name).is_file():
            raise ValueError(f'Missing release input: {name}')
    if release.exists():
        raise ValueError('Release already exists; do not overwrite installed code')
    release.mkdir(parents=True, mode=0o700)
    hashes = {}
    for name in files:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    bindir = release / 'bin'
    bindir.mkdir()
    python = shlex.quote(sys.executable)
    for name, target, extra in [('look-first', 'src/z0int/look_first.py', ''), ('generate-index', 'scripts/look-first/generate.py', ' --checkout ' + shlex.quote(str(repository)))]:
        launcher = bindir / name
        launcher.write_text('#!/bin/sh\nexec ' + python + ' ' + shlex.quote(str(release / target)) + extra + ' "$@"\n')
        launcher.chmod(0o700)
    (release / 'install-receipt.json').write_text(json.dumps({'source_commit': actual, 'repository': str(repository), 'python': sys.executable, 'files': hashes}, sort_keys=True, indent=2) + '\n')
    extensions.mkdir(parents=True, exist_ok=True)
    current.symlink_to(release, target_is_directory=True)
    link.symlink_to(current / 'omp-extensions/z0-look-first', target_is_directory=True)
    print(json.dumps({'installed': str(release), 'extension': str(link), 'timer_installed': False}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--repository', type=Path, required=True, help='Durable canonical repository checkout, not a disposable worktree')
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--destination', type=Path, default=Path.home() / '.local/lib/z0-look-first')
    parser.add_argument('--extensions', type=Path, default=Path.home() / '.omp/agent/extensions')
    args = parser.parse_args()
    install(args.source, args.repository, args.destination, args.extensions, args.expected_commit)


if __name__ == '__main__':
    main()
