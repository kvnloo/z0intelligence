"""Scratch Hermes profile config for one C8 arm (runs under the isolated Hermes venv).
usage: e2e_hermes_config.py <config.yaml> <stub port> <memory_inject> <z0int python> [capture mode, default off]
Capture mode stays off (only the memory seam is under test) except in the round-3 join arm (capture shadow)."""
import sys

import hermes_yaml as yaml

path, port, memory, python = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
capture = sys.argv[5] if len(sys.argv) > 5 else 'off'
try:
    with open(path) as fh:
        config = yaml.safe_load(fh) or {}
except OSError:
    config = {}
config['model'] = {'default': 'c8-stub', 'provider': 'custom', 'base_url': f'http://127.0.0.1:{port}/v1',
                   'api_key': 'dummy-c8-e2e'}
config['toolsets'] = ['kanban', 'memory', 'session_search', 'clarify', 'no_mcp']
plugins = config.setdefault('plugins', {})
if 'hermes-z0intelligence' not in (plugins.get('enabled') or []):
    plugins['enabled'] = [*(plugins.get('enabled') or []), 'hermes-z0intelligence']
entry = plugins.setdefault('entries', {}).setdefault('hermes-z0intelligence', {})
entry['settings'] = {'mode': capture, 'memory_inject': memory, 'z0int_python': python}
with open(path, 'w') as fh:
    fh.write(yaml.safe_dump(config))
