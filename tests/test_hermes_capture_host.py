"""C4a host tests: the plugin through a real Hermes PluginManager (opt-in).

Set Z0INT_TEST_HERMES_PYTHON to an interpreter that imports ``hermes_cli`` (an isolated venv built from a git-archive
export of the fork, never the live install). Each run uses a scratch HERMES_HOME under tmp_path.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / 'harness-adapters' / 'hermes-z0intelligence'
DRIVER = ROOT / 'tests' / 'fixtures' / 'hermes_host_driver.py'
HERMES_PYTHON = os.environ.get('Z0INT_TEST_HERMES_PYTHON')
CAPTURE_HOOKS = {'on_session_start', 'pre_llm_call', 'post_llm_call', 'on_session_end', 'pre_approval_request',
                 'post_tool_call', 'pre_api_request', 'post_api_request', 'api_request_error', 'pre_auxiliary_call',
                 'post_auxiliary_call', 'subagent_stop'}

pytestmark = pytest.mark.skipif(not HERMES_PYTHON, reason='Z0INT_TEST_HERMES_PYTHON not set (no isolated Hermes)')


def drive(section, tmp_path):
    home, z0 = tmp_path / f'hermes-{section}', tmp_path / 'z0'
    env = dict(os.environ, HOME=str(tmp_path), HERMES_HOME=str(home), Z0INT_HOME=str(z0),
               PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1')
    proc = subprocess.run([HERMES_PYTHON, str(DRIVER), '--plugin', str(PLUGIN_DIR), '--home', str(home),
                           '--z0int-python', sys.executable, '--section', section],
                          capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1]), z0


def test_toolsets_shape_profile_loads_the_plugin_and_registers_its_capture_hooks(tmp_path):
    out, z0 = drive('toolsets', tmp_path)
    assert out['loaded'] == {'enabled': True, 'error': None, 'module_loaded': True}
    assert {e for e, n in out['hook_callbacks'].items() if n} == CAPTURE_HOOKS
    assert out['hook_callbacks']['pre_tool_call'] == 0
    # inertness through Hermes's own dispatch: no hook returns a value (no context, no block directive)
    assert set(out['returned'].values()) == {'[]'}
    assert (z0 / 'state' / 'hermes' / 'events.jsonl').exists()


def test_mode_off_and_disabled_leave_dispatch_untouched(tmp_path):
    off, z0 = drive('off', tmp_path)
    assert off['loaded']['enabled'] and off['loaded']['error'] is None
    assert not any(off['hook_callbacks'].values()) and off['has_hook_any'] is False
    disabled, _ = drive('disabled', tmp_path)
    assert disabled['loaded'] is None or not disabled['loaded']['module_loaded']
    assert not any(disabled['hook_callbacks'].values())
    assert not z0.exists()
