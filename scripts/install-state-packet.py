#!/usr/bin/env python3
"""Install committed orientation files without linking any live worktree."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def backup(path, root):
    if path.exists() or path.is_symlink():
        target = root / str(path).lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            target.symlink_to(os.readlink(path))
        elif path.is_dir():
            shutil.copytree(path, target, symlinks=True)
        else:
            shutil.copy2(path, target)


def link(path, target, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path, root)
    if path.exists() and path.is_dir() and not path.is_symlink():
        raise RuntimeError(f'Refuse to replace existing directory: {path}')
    temporary = path.with_name(path.name + '.state-packet-new')
    temporary.symlink_to(target)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--targets', default='claude,omp,hermes',
                        help='comma list of harness hooks to install (default all). A host without the Hermes '
                             '`clean` profile (e.g. mbp) passes claude,omp so no stray profile directory is created.')
    args = parser.parse_args()
    targets = {t.strip() for t in args.targets.split(',') if t.strip()}
    unknown = targets - {'claude', 'omp', 'hermes'}
    if unknown:
        parser.error(f'unknown targets: {sorted(unknown)}')
    repo = Path(__file__).resolve().parents[1]
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    home = args.home
    base = home / '.local/lib/z0-state-packet'
    dest = base / sha
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backups = home / '.local/state/z0int/startup-packet-backups' / stamp
    base.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        with tempfile.TemporaryDirectory(dir=base, prefix='.install-') as tmp:
            archive = subprocess.check_output(['git', '-C', str(repo), 'archive', sha,
                'src/z0int/orientation.py', 'scripts/state-packet', 'omp-extensions/z0-state-packet/index.ts',
                'harness-adapters/hermes-state-packet', 'bin/generate-packet', 'bin/state-packet-hook'])
            import io
            with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
                tf.extractall(tmp, filter='data')
            (Path(tmp) / 'DEPLOYED_SHA').write_text(sha + '\n')
            os.rename(tmp, dest)
    link(base / 'current', dest, backups)
    if 'omp' in targets:
        link(home / '.omp/agent/extensions/z0-state-packet', dest / 'omp-extensions/z0-state-packet', backups)
    if 'hermes' in targets:
        link(home / '.hermes/profiles/clean/plugins/hermes-state-packet', dest / 'harness-adapters/hermes-state-packet',
             backups)
    if 'claude' in targets:
        install_claude(home, base, backups)
    if 'hermes' in targets:
        backup(home / '.hermes/profiles/clean/config.yaml', backups)
    print(json.dumps({'sha': sha, 'install': str(dest), 'backup': str(backups), 'targets': sorted(targets),
        **({'hermes_enable': 'hermes -p clean plugins enable hermes-state-packet'} if 'hermes' in targets else {})}))


def install_claude(home, base, backups):
    settings = home / '.claude/settings.json'
    backup(settings, backups)
    value = json.loads(settings.read_text()) if settings.exists() else {}
    groups = value.setdefault('hooks', {}).setdefault('UserPromptSubmit', [])
    groups[:] = [group for group in groups if not any('z0-state-packet/' in hook.get('command', '') for hook in group.get('hooks', []))]
    for index in range(10):
        groups.append({'hooks': [{'type': 'command', 'command': f'"{base}/current/bin/state-packet-hook" --chunk {index}', 'timeout': 5}]})
    groups.append({'hooks': [{'type': 'command', 'command': f'"{base}/current/bin/state-packet-hook" --corrections', 'timeout': 5}]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.with_name('settings.state-packet-new.json')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, settings)


if __name__ == '__main__':
    main()
