#!/usr/bin/env bash
# C1 e2e (2): real `claude -p` (2.1.288) with the worktree plugin, no subscription and no network.
# Private user+net namespace (loopback only), Anthropic-Messages stub on a private port, dummy key,
# CLAUDE_CONFIG_DIR / HOME / Z0INT_HOME under homes/C1-loop-core/e2e-claude.
# usage: e2e_claude.sh <worktree> <home> <python> <port> [background:0|1]
set -euo pipefail
WT=$1; H=$2; PY=$3; PORT=$4; BG=${5:-0}
S=/mnt/zer0models/z0-wt/wiring/scratch/C1-loop-core
CLAUDE_BIN=~/.local/share/claude/versions/2.1.288
rm -rf "$H"; mkdir -p "$H"/{home,claude,z0,work,tmp,pycache,payloads}
# Test-only: tee every hook payload Claude Code sends (isolated user settings, next to the plugin's own hooks).
python3 - "$H" <<'PYEOF'
import json, sys
h = sys.argv[1]
events = ['SessionStart', 'UserPromptSubmit', 'SubagentStart', 'SubagentStop', 'Stop', 'SessionEnd']
hooks = {e: [{'hooks': [{'type': 'command', 'command': f'cat >> {h}/payloads/{e}.jsonl; echo >> {h}/payloads/{e}.jsonl'}]}]
         for e in events}
json.dump({'hooks': hooks}, open(f'{h}/claude/settings.json', 'w'), indent=1)
PYEOF
cat > "$H/inner.sh" <<EOF
set -u
ip link set lo up
C1_STUB_BACKGROUND=$BG "$PY" "$S/anthropic_stub.py" $PORT "$H/stub-requests.jsonl" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/work"
env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 HOME="$H/home" TMPDIR="$H/tmp" \\
  CLAUDE_CONFIG_DIR="$H/claude" ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT ANTHROPIC_API_KEY=sk-ant-dummy-c1-e2e \\
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1 \\
  Z0INT_PYTHON="$PY" PYTHONPATH="$WT/src" PYTHONPYCACHEPREFIX="$H/pycache" Z0INT_HOME="$H/z0" \\
  timeout 150 "$CLAUDE_BIN" -p "MAIN-MARKER-c1: delegate one small subtask to a subagent, then report." \\
    --plugin-dir "$WT/harness-adapters/claude-code-z0intelligence" --output-format json --max-turns 6 --permission-mode default --allowedTools Agent Task \\
    > "$H/claude-out.json" 2> "$H/claude-err.txt"
echo "claude exit=\$?"
sleep 4  # detached opportunity builds (1-3 s) finish before the stub and namespace go away
kill \$STUB 2>/dev/null || true
EOF
/mnt/zer0models/github/cua-lanes/bin/hostless unshare -rn bash "$H/inner.sh"
