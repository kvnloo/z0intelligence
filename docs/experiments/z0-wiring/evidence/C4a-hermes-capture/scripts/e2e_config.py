"""Write the scratch profile config for one e2e arm (runs under the isolated Hermes venv).
usage: e2e_config.py <config.yaml> <stub port> <off|shadow> <z0int python>
The model points at the recording stub with a dummy key; toolsets have the live shape (non-secret keys only)."""
import sys

import hermes_yaml as yaml

path, port, mode, python = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
try:
    with open(path) as fh:
        config = yaml.safe_load(fh) or {}
except OSError:
    config = {}
config['model'] = {'default': 'c4a-stub', 'provider': 'custom', 'base_url': f'http://127.0.0.1:{port}/v1',
                   'api_key': 'dummy-c4a-e2e'}
config['toolsets'] = ['kanban', 'memory', 'session_search', 'clarify', 'no_mcp']
plugins = config.setdefault('plugins', {})
if 'hermes-z0intelligence' not in (plugins.get('enabled') or []):
    plugins['enabled'] = [*(plugins.get('enabled') or []), 'hermes-z0intelligence']
entry = plugins.setdefault('entries', {}).setdefault('hermes-z0intelligence', {})
entry['settings'] = {'mode': mode, 'opportunities': True, 'z0int_python': python}
with open(path, 'w') as fh:
    fh.write(yaml.safe_dump(config))
