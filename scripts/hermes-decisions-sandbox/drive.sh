#!/usr/bin/env bash
# Sandboxed Hermes proof: stub model, sandbox HERMES_HOME + Z0INT_HOME, installed Hermes code (read-only).
# Setup (once): see ~/.z0int/research/factory/hermes-opportunities.md "Sandbox proof".
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
S=${Z0INT_HERMES_SANDBOX:-$HOME/.cache/z0int-hermes-sandbox}
export HERMES_HOME=$S/home Z0INT_HOME=$S/z0 PYTHONDONTWRITEBYTECODE=1
export Z0INT_PYTHON=${Z0INT_PYTHON:-$REPO/.venv/bin/python}
unset OPENAI_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY PYTHONPATH PYTHONHOME
HPY=$HOME/.hermes/hermes-agent/venv/bin/python
H="$HPY $HOME/.hermes/hermes-agent/hermes"
cd $S/work
run() { echo "== $*"; timeout 300 $H chat -Q --yolo "$@" 2>&1 | grep -v tirith | tail -3; }
run -q "what branch am I on?"
run -q "please use a tool to plan this refactor"
run -q "ask me which environment before deploying"
run -q "delegate a greeting to a helper"
echo "== cron-platform turn"
timeout 300 $HPY - <<'PY' 2>&1 | tail -2
import sys, time; sys.path.insert(0, '/home/kvn/.hermes/hermes-agent')
from hermes_cli.plugins import discover_plugins; discover_plugins()
from run_agent import AIAgent
a = AIAgent(model='stub-model', provider='custom', base_url='http://127.0.0.1:18765/v1', api_key='stub-local',
            platform='cron', quiet_mode=True, skip_memory=True, skip_context_files=True)
print(a.run_conversation('daily digest of open PRs')['final_response'])
time.sleep(0.5)
PY
echo "== 10-turn cli session in one process (steady-state hook overhead)"
timeout 300 $HPY - <<'PY' 2>&1 | tail -2
import sys, time; sys.path.insert(0, '/home/kvn/.hermes/hermes-agent')
from hermes_cli.plugins import discover_plugins; discover_plugins()
from run_agent import AIAgent
a = AIAgent(model='stub-model', provider='custom', base_url='http://127.0.0.1:18765/v1', api_key='stub-local',
            platform='cli', quiet_mode=True, skip_memory=True, skip_context_files=True)
hist = None
for i in range(10):
    msg = f'please use a tool for step {i}' if i % 3 == 0 else f'status of item {i}'
    r = a.run_conversation(msg, conversation_history=hist); hist = r['messages']
print('turns done', len(hist))
time.sleep(4)
PY
