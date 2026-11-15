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


class DeploymentIdentityTests(unittest.TestCase):
    def test_valid_stamp_wins_and_mismatched_stamp_fails(self):
        module = load('identity_generator', ROOT / 'scripts/state-packet/generate_packet.py')
        with tempfile.TemporaryDirectory() as tmp:
            sha = 'a' * 40
            root = Path(tmp) / sha
            child = root / 'omp-extensions/extension'
            child.mkdir(parents=True)
            stamp = root / 'DEPLOYED_SHA'
            stamp.write_text(sha + '\n')
            with patch.object(module, 'cmd', return_value=''):
                self.assertEqual(module.deployment_identity(child), (str(root), sha, 'content-stamped', str(stamp)))
                stamp.write_text('b' * 40)
                self.assertEqual(module.deployment_identity(child), ('', '', '', ''))


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
        self.assertEqual(len(groups), 12)
        self.assertTrue(groups[-1]['hooks'][0]['command'].endswith('state-packet-hook" --corrections'))
        backup = Path(receipt['backup']) / str(settings).lstrip('/')
        self.assertEqual(len(json.loads(backup.read_text())['hooks']['UserPromptSubmit']), 1)
        subprocess.run([sys.executable, str(repo / 'scripts/install-state-packet.py'), '--home', str(home)],
            text=True, capture_output=True, check=True)
        self.assertEqual(len(json.loads(settings.read_text())['hooks']['UserPromptSubmit']), 12)

    def test_installer_targets_skip_hermes_on_a_host_without_the_profile(self):
        home = Path(self.tmp.name) / 'home2'
        repo = Path(self.tmp.name) / 'committed-source2'
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
        result = subprocess.run([sys.executable, str(repo / 'scripts/install-state-packet.py'), '--home', str(home),
                                 '--targets', 'claude,omp'], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)['targets'], ['claude', 'omp'])
        self.assertFalse((home / '.hermes').exists())
        self.assertTrue((home / '.omp/agent/extensions/z0-state-packet').is_symlink())
        groups = json.loads((home / '.claude/settings.json').read_text())['hooks']['UserPromptSubmit']
        self.assertEqual(len(groups), 11)
        bad = subprocess.run([sys.executable, str(repo / 'scripts/install-state-packet.py'), '--home', str(home),
                              '--targets', 'claude,bogus'], text=True, capture_output=True)
        self.assertNotEqual(bad.returncode, 0)

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
        routing = repo / 'routing'
        routing.mkdir()
        formula = 'score_u = latency_u * (1 + λ * pace_u) / p_accept[u, task_class(t)]'
        (routing / 'REPORT.md').write_text(
            'latency_u = TTFT_u + out_tokens_t / tok/s_u\n'
            'pace_u = burn_u * tokens_t / (remaining_u / time_to_reset_u)\n'
            + formula + '\nLambda: λ=1, unmeasured\nOpenCode rule: only sourced free models.\n')
        (routing / 'catalog.json').write_text(json.dumps({'generated_at': self.now.isoformat(),
            'models': [{'provider': 'test', 'performance': {'ttft_p50_ms': {'value': None},
                        'tokens_per_second': {'value': None}}}]}))
        out = Path(self.tmp.name) / 'state/STATE-PACKET.md'
        with patch.object(module, 'OPS', ops), patch.object(module, 'HERE', here), patch.object(module, 'cmd', return_value=''), patch.dict(os.environ, {'Z0INT_POLICY_ROOT': str(repo), 'Z0INT_ROUTING_ROOT': str(routing)}), patch.object(sys, 'argv', ['generate_packet.py', '--offline', '--repo', str(repo), '--out', str(out)]), patch('sys.stdout', new=io.StringIO()):
            module.main()
        text = out.read_text()
        self.assertLessEqual(len(text.splitlines()), 223)
        self.assertTrue(all('`/' in line for line in text.splitlines()))
        self.assertIn('default 2 until ChoiceLadder findings land (law O13, z0int #212)', text)
        self.assertIn('qwen2.5-0.5b pilot v1: k2 2/2, k3 1/1, k8 0/1, k32 0/1', text)
        self.assertIn(str(capacity), text)
        self.assertIn(str(pilot), text)
        self.assertIn(formula, text)
        self.assertIn('Lambda: λ=1, unmeasured', text)
        self.assertIn('Known TTFT p50=0, decode throughput=0', text)
        self.assertIn('Quota pools are shared-account evidence', text)
        manifest = json.loads(out.with_name('packet-manifest.json').read_text())
        self.assertEqual(manifest['packet_sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertIn('generated_at', manifest)
        self.assertEqual(manifest['generator_sha256'], hashlib.sha256((ROOT / 'scripts/state-packet/generate_packet.py').read_bytes()).hexdigest())
        module.atomic_write(out, 'replacement')
        self.assertEqual(out.read_text(), 'replacement')
        self.assertEqual(list(out.parent.glob('.STATE-PACKET.md.*')), [])


if __name__ == '__main__':
    unittest.main()


class CorrectionsTests(unittest.TestCase):
    """Owner-corrections seam: the one per-prompt injection shared by Claude Code, Hermes and omp."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / 'CORRECTIONS.jsonl'
        rows = [
            {'fact': 'Android UI on the S25 is driven with uiautomator2, never raw adb taps.', 'ts': '2026-10-22',
             'harness': 'omp', 'source': 'omp:abc:12', 'terms': ['uiautomator2', 'adb']},
            {'fact': 'Bend experiments must run on the GPU with CUDA; CPU runs are useless.', 'ts': '2026-10-20',
             'harness': 'hermes-clean', 'source': 'hermes:1:2'},
        ] + [{'fact': f'Unrelated filler fact number {i} about gardening soil.'} for i in range(30)]
        self.store.write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def test_matching_prompt_gets_the_correction_with_its_source(self):
        text = orientation.corrections('tap the settings button on the S25 with adb', self.store, min_score=1.0)
        self.assertIn('uiautomator2', text)
        self.assertIn('omp:abc:12', text)
        self.assertIn('<owner-corrections>', text)
        self.assertNotIn('gardening', text.split('<owner-corrections>')[1].splitlines()[1])

    def test_unrelated_prompt_or_missing_store_injects_nothing(self):
        self.assertEqual(orientation.corrections('write a haiku about the moon', self.store), '')
        self.assertEqual(orientation.corrections('uiautomator2 adb', Path(self.tmp.name) / 'missing.jsonl'), '')
        self.store.write_text('not json\n')
        self.assertEqual(orientation.corrections('uiautomator2 adb', self.store), '')

    def _cli(self, args, stdin):
        env = {**os.environ, 'Z0INT_CORRECTIONS': str(self.store)}
        return subprocess.run([sys.executable, str(ROOT / 'src/z0int/orientation.py'), *args], input=stdin,
                              capture_output=True, text=True, env=env, timeout=20).stdout

    def test_claude_hook_reads_the_prompt_json_and_omp_reads_raw(self):
        out = self._cli(['--claude-hook', '--corrections'],
                        json.dumps({'prompt': 'run the bend benchmark with cuda on the gpu'}))
        ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
        self.assertIn('CUDA', ctx)
        raw = self._cli(['--corrections', '--raw'], 'run the bend benchmark with cuda on the gpu')
        self.assertIn('CUDA', raw)
        self.assertEqual(self._cli(['--corrections'], 'not json bend cuda gpu'), '')


class HermesCorrectionsHookTests(unittest.TestCase):
    def test_hook_reads_str_content_parts_or_history(self):
        module = load('hermes_state_packet_corr', ROOT / 'harness-adapters/hermes-state-packet/__init__.py')
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / 'c.jsonl'
            store.write_text(json.dumps({'fact': 'Bend runs on the GPU with CUDA only.', 'source': 's1'}) + '\n'
                             + ''.join(json.dumps({'fact': f'filler {i} soil'}) + '\n' for i in range(20)))
            with patch.dict(os.environ, {'Z0INT_CORRECTIONS': str(store)}):
                q = 'run the bend benchmark on cuda gpu'
                for shape in (q, [{'type': 'text', 'text': q}], {'role': 'user', 'content': q}):
                    self.assertIn('CUDA', module.corrections_hook(user_message=shape)['context'])
                self.assertIn('CUDA', module.corrections_hook(
                    user_message=None, conversation_history=[{'role': 'user', 'content': q}])['context'])
                self.assertIsNone(module.corrections_hook(user_message='write a haiku about the moon'))
