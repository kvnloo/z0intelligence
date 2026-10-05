"""Load harness-adapters/hermes-z0intelligence through a real Hermes PluginManager in a scratch profile.

  <hermes python> hermes_host_driver.py --plugin <dir> --home <scratch HERMES_HOME> --z0int-python <py>
                                        --section toolsets|off|disabled

Runs under the Hermes interpreter (never imports z0int). The profile is disposable: config.yaml carries only the
plugin entry and a ``toolsets`` list with the live shape (non-secret keys only). Prints one JSON object.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PLUGIN = 'hermes-z0intelligence'
EVENTS = ('on_session_start', 'pre_llm_call', 'post_llm_call', 'pre_api_request', 'post_api_request',
          'api_request_error', 'pre_auxiliary_call', 'post_auxiliary_call', 'pre_tool_call', 'post_tool_call',
          'subagent_stop', 'on_session_end', 'pre_approval_request')
TOOLSETS = ['kanban', 'memory', 'session_search', 'clarify', 'no_mcp']  # live shape (spec R3), values only

ap = argparse.ArgumentParser()
for name in ('plugin', 'home', 'z0int-python', 'section'):
    ap.add_argument('--' + name, required=True)
A = ap.parse_args()
home = Path(A.home)
bundled = home.parent / f'{home.name}-bundled'
bundled.mkdir(parents=True, exist_ok=True)
os.environ['HERMES_BUNDLED_PLUGINS'] = str(bundled)
os.environ['HERMES_HOME'] = str(home)
shutil.copytree(A.plugin, home / 'plugins' / PLUGIN, ignore=shutil.ignore_patterns('__pycache__'))

import hermes_yaml as yaml  # noqa: E402

mode = {'toolsets': 'shadow', 'off': 'off', 'disabled': 'shadow'}[A.section]
settings = {'mode': mode, 'opportunities': False, 'z0int_python': A.z0int_python,
            'z0int_home': os.environ['Z0INT_HOME']}
plugins = {'enabled': [] if A.section == 'disabled' else [PLUGIN], 'entries': {PLUGIN: {'settings': settings}}}
if A.section == 'disabled':
    plugins['disabled'] = [PLUGIN]
(home / 'config.yaml').write_text(yaml.safe_dump({'toolsets': TOOLSETS, 'plugins': plugins}))

from hermes_cli.plugins import PluginManager  # noqa: E402

manager = PluginManager()
manager.discover_and_load()
loaded = manager._plugins.get(PLUGIN)
payload = dict(session_id='host', turn_id='host:t1', task_id='task-host', platform='cli', model='m',
               user_message='hello there', conversation_history=[], assistant_response='ok', completed=True,
               tool_name='read_file', args={'path': 'x'}, result='y', status='ok')
returned = {e: repr(manager.invoke_hook(e, **payload)) for e in EVENTS}
out = {'loaded': None if loaded is None else {'enabled': loaded.enabled, 'error': loaded.error,
                                              'module_loaded': loaded.module is not None},
       'hook_callbacks': {e: len(manager._hooks.get(e, [])) for e in EVENTS},
       'has_hook_any': any(manager.has_hook(e) for e in EVENTS), 'returned': returned}
if loaded is not None and loaded.enabled:
    manager.unload(PLUGIN)
print(json.dumps(out))
