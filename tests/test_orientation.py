from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from z0int import orientation

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OrientationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packet = Path(self.tmp.name) / 'STATE-PACKET.md'
        self.now = datetime.now(timezone.utc)
        self.text = self.render(self.now)
        self.packet.write_text(self.text)

    def render(self, stamp, rows=220):
        return '\n'.join([f'`/generator.py` → generated_at: {stamp.isoformat()}',
            '`/generator.py` → generator_sha256: ' + 'a' * 64,
            *[f'`/source/{n}` → fact {n}' for n in range(rows)]]) + '\n'

    def test_fresh_bounded_and_provenance(self):
        self.packet.write_text(self.render(self.now, 300))
        text = orientation.context(self.packet, now=self.now)
        self.assertIn('fact 220', text)
        self.assertNotIn('fact 221', text)
        self.assertIn('not authority', text)

    def test_stale_replaces_all_facts(self):
        self.packet.write_text(self.render(self.now - timedelta(hours=3)))
        text = orientation.context(self.packet, now=self.now)
        self.assertTrue(text.startswith('packet stale since '))
        self.assertEqual(len(text.splitlines()), 1)
        self.assertNotIn('fact', text)

    def test_bad_missing_oversized_or_unlinked_fail_open(self):
        for text in ('bad', self.render(self.now).replace('generator_sha256', 'bad'),
                     self.render(self.now).replace('`/source/0`', 'unlinked'), 'x' * 65537):
            self.packet.write_text(text)
            self.assertEqual(orientation.context(self.packet, now=self.now), '')
        self.packet.unlink()
        self.assertEqual(orientation.context(self.packet), '')

    def test_timestamp_not_mtime_controls_freshness(self):
        os.utime(self.packet, (1, 1))
        self.assertIn('fact', orientation.context(self.packet, now=self.now))
        self.packet.write_text(self.render(self.now + timedelta(hours=1)))
        self.assertTrue(orientation.context(self.packet, now=self.now).startswith('packet stale since '))

    def test_chunks_preserve_all_context_below_spill_limit(self):
        text = '\n'.join(f'`/source/{n}` → ' + 'x' * 250 for n in range(223))
        parts = orientation.chunks(text)
        self.assertEqual(''.join(parts), text)
        self.assertLessEqual(len(parts), 10)
        self.assertTrue(all(len(part) <= 9000 for part in parts))

    def test_claude_envelope_executable(self):
        result = subprocess.run([sys.executable, str(ROOT / 'src/z0int/orientation.py'), '--claude-hook', '--chunk', '0'],
            env={**os.environ, 'Z0INT_STATE_PACKET': str(self.packet)}, input='{}', text=True, capture_output=True, check=True)
        out = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(out['hookEventName'], 'UserPromptSubmit')
        self.assertIn('fact 0', out['additionalContext'])
        self.assertLessEqual(len(out['additionalContext']), 9000)

    def test_stamped_installer_preserves_unrelated_hooks_and_backup(self):
        home = Path(self.tmp.name) / 'home'
        repo = Path(self.tmp.name) / 'committed-source'
        for relative in ('src/z0int/orientation.py', 'scripts/state-packet/generate_packet.py',
                         'omp-extensions/z0-state-packet/index.ts', 'harness-adapters/hermes-state-packet/__init__.py',
                         'harness-adapters/hermes-state-packet/plugin.yaml', 'bin/generate-packet',
                         'bin/state-packet-hook', 'scripts/install-state-packet.py'):
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], check=True)
        settings = home / '.claude/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'hooks': {'UserPromptSubmit': [
            {'hooks': [{'type': 'command', 'command': 'unrelated-hook'}]}]}}))
        result = subprocess.run([sys.executable, str(repo / 'scripts/install-state-packet.py'), '--home', str(home)],
            text=True, capture_output=True, check=True)
        receipt = json.loads(result.stdout)
        install = Path(receipt['install'])
        self.assertTrue(install.is_dir())
        self.assertFalse(install.is_symlink())
        extension = home / '.omp/agent/extensions/z0-state-packet'
        self.assertEqual(extension.resolve(), install / 'omp-extensions/z0-state-packet')
        self.assertNotIn(str(ROOT), str(extension.resolve()))
        groups = json.loads(settings.read_text())['hooks']['UserPromptSubmit']
        self.assertEqual(groups[0]['hooks'][0]['command'], 'unrelated-hook')
        self.assertEqual(len(groups), 11)
        backup = Path(receipt['backup']) / str(settings).lstrip('/')
        self.assertEqual(len(json.loads(backup.read_text())['hooks']['UserPromptSubmit']), 1)
        subprocess.run([sys.executable, str(repo / 'scripts/install-state-packet.py'), '--home', str(home)],
            text=True, capture_output=True, check=True)
        self.assertEqual(len(json.loads(settings.read_text())['hooks']['UserPromptSubmit']), 11)

    def test_hermes_registered_context_and_stale(self):
        module = load('hermes_state_packet', ROOT / 'harness-adapters/hermes-state-packet/__init__.py')
        callbacks = []
        class Context:
            def register_hook(self, event, callback):
                self_event = event
                if self_event != 'pre_llm_call':
                    raise AssertionError(event)
                callbacks.append(callback)
        module.register(Context())
        with patch.dict(os.environ, {'Z0INT_STATE_PACKET': str(self.packet)}):
            pieces = [callback(user_message='question') for callback in callbacks]
            joined = ''.join(piece['context'] for piece in pieces if piece)
            self.assertEqual(joined, orientation.context(self.packet))
            self.packet.write_text(self.render(self.now - timedelta(hours=3)))
            pieces = [callback() for callback in callbacks]
            self.assertEqual(sum(piece is not None for piece in pieces), 1)
            self.assertTrue(pieces[0]['context'].startswith('packet stale since '))

    def test_generator_atomic_output_contract(self):
        module = load('packet_generator', ROOT / 'scripts/state-packet/generate_packet.py')
        ops = Path(self.tmp.name) / 'ops'
        here = ops / 'repo-distill'
        here.mkdir(parents=True)
        (here / 'issues.json').write_text('[]')
        (here / 'prs.json').write_text('[]')
        repo = Path(self.tmp.name) / 'repo'
        repo.mkdir()
        (repo / 'README.md').write_text('**A provenance-backed cognition plane.**\n')
        capacity = repo / 'src/z0int/fsm/policy_guard.py'
        capacity.parent.mkdir(parents=True)
        capacity.write_text('def allowed_k(): return 2\n')
        pilot = ops / 'choice-ladder/pilot-v1-qwen25-05/qwen25-05/rows.jsonl'
        pilot.parent.mkdir(parents=True)
        pilot.write_text('\n'.join(json.dumps({'task': task, 'k': k, 'correct': correct})
            for task, k, correct in [('T1', 2, True), ('T2', 2, True), ('T3', 3, True), ('T2', 8, False), ('T1', 32, False)]))
        out = Path(self.tmp.name) / 'state/STATE-PACKET.md'
        with patch.object(module, 'OPS', ops), patch.object(module, 'HERE', here), patch.object(module, 'cmd', return_value=''), patch.dict(os.environ, {'Z0INT_POLICY_ROOT': str(repo)}), patch.object(sys, 'argv', ['generate_packet.py', '--offline', '--repo', str(repo), '--out', str(out)]), patch('sys.stdout', new=io.StringIO()):
            module.main()
        text = out.read_text()
        self.assertLessEqual(len(text.splitlines()), 223)
        self.assertTrue(all('`/' in line for line in text.splitlines()))
        self.assertIn('default 2 until ChoiceLadder findings land (law O13, z0int #212)', text)
        self.assertIn('qwen2.5-0.5b pilot v1: k2 2/2, k3 1/1, k8 0/1, k32 0/1', text)
        self.assertIn(str(capacity), text)
        self.assertIn(str(pilot), text)
        manifest = json.loads(out.with_name('packet-manifest.json').read_text())
        self.assertEqual(manifest['packet_sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertIn('generated_at', manifest)
        self.assertEqual(manifest['generator_sha256'], hashlib.sha256((ROOT / 'scripts/state-packet/generate_packet.py').read_bytes()).hexdigest())
        module.atomic_write(out, 'replacement')
        self.assertEqual(out.read_text(), 'replacement')
        self.assertEqual(list(out.parent.glob('.STATE-PACKET.md.*')), [])


if __name__ == '__main__':
    unittest.main()
