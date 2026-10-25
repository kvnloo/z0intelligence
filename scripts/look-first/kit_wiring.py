#!/usr/bin/env python3
"""Read-only manifest-versus-live link observation. Never applies the held kit."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib
from types import SimpleNamespace


def observe(repository, ref, held_root):
    raw = subprocess.check_output(['git', '-C', str(repository), 'show', f'{ref}:stack.toml'], timeout=20)
    manifest = tomllib.loads(raw.decode())
    manifest_path = held_root / 'stack.toml'
    if manifest_path.read_bytes() != raw:
        raise ValueError('Held manifest differs from the pinned branch')
    spec = importlib.util.spec_from_file_location('held_skills_installer', held_root / 'install.py')
    installer_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer_module)
    # Constructor and destination() only read state. Never call plan/apply/uninstall.
    installer = installer_module.Installer(SimpleNamespace(home=None, manifest=str(manifest_path), harness=None))
    additional = {'omp': '~/.agents/skills', 'opencode': '~/.config/opencode/skills', 'codex-legacy': '~/.codex/skills', 'claude-legacy': '~/.claude/skills'}
    rows = []
    for name, skill in sorted(manifest['skills'].items()):
        source = skill['source']
        if source.startswith('local:'):
            expected = Path(source.removeprefix('local:')).expanduser()
        elif source.startswith('git:'):
            _, key, member = source.split(':', 2)
            expected = Path(manifest['sources'][key]['checkout']).expanduser() / member
        else:
            expected = held_root / source
        for harness in [*skill['harnesses'], *additional]:
            destination = installer.destination(harness, name) if harness in manifest['harnesses'] else Path(additional[harness]).expanduser() / name
            target = destination.resolve(strict=False)
            available = (destination / 'SKILL.md').is_file()
            exact = available and target == expected.resolve(strict=False)
            status = 'exact-source' if exact else 'different-source-present' if available else 'dangling-link' if destination.is_symlink() else 'missing'
            rows.append({'skill': name, 'pack': skill['pack'], 'harness': harness, 'destination': str(destination), 'link': str(destination.readlink()) if destination.is_symlink() else None, 'resolved': str(target), 'expected_source': str(expected), 'source_available': (expected / 'SKILL.md').is_file(), 'status': status})
    summaries = {}
    for harness in [*manifest['harnesses'], *additional]:
        selected = [row for row in rows if row['harness'] == harness]
        summaries[harness] = {'directory': manifest['harnesses'][harness]['directory'] if harness in manifest['harnesses'] else additional[harness], 'manifest_declared': harness in manifest['harnesses'], 'all_packs': dict(Counter(row['status'] for row in selected)), 'default_pack': dict(Counter(row['status'] for row in selected if row['pack'] == 'default'))}
    return {'schema': 'z0.skills_kit_wiring.v1', 'repository': str(repository), 'ref': ref, 'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'skill_count': len(manifest['skills']), 'pack_counts': dict(Counter(skill['pack'] for skill in manifest['skills'].values())), 'harnesses': summaries, 'rows': rows, 'loading_verified': False, 'note': 'Presence and source-link comparisons are not proof that a running harness loaded or used a skill.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path('/workspace/kvnloo-skills'))
    parser.add_argument('--ref', default='2b59ea3a840c07087af2e5f8ef9eb2ff932f6ab3')
    parser.add_argument('--held-root', type=Path, default=Path('/mnt/zer0models/z0-wt/salvage/skills-home/work/kvnloo-skills'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = observe(args.repository, args.ref, args.held_root)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    args.output.chmod(0o600)
    print(json.dumps({key: value for key, value in result.items() if key != 'rows'}, sort_keys=True))


if __name__ == '__main__':
    main()
