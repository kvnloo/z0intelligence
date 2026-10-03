#!/usr/bin/env bash
# Integration e2e, Claude Code leg: real `claude -p` (2.1.288) with the integrate tree's plugin (--plugin-dir), no
# subscription and no network. Private user+net namespace (loopback only); Anthropic-Messages stub on a private port
# that records every request body with the arm label. Arms alternate on/off N times (off = Z0INT_CAPTURE=0, same
# plugin), so the model-visible requests can be compared. "on" arms write into the SHARED integration Z0INT_HOME.
# usage: e2e_claude.sh <worktree> <e2e-root> <python> <port> <N>
set -euo pipefail
WT=$1; R=$2; PY=$3; PORT=$4; N=$5
S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
CLAUDE_BIN=~/.local/share/claude/versions/2.1.288
H=$R/claude-code
rm -rf "$H"; mkdir -p "$H"/{home,claude,work,tmp,pycache} "$R/z0home" "$R/z0home-off"
cat > "$H/inner.sh" <<EOF
set -u
ip link set lo up
STUB_ARM_FILE="$H/arm" "$PY" "$S/anthropic_stub.py" $PORT "$H/stub-requests.jsonl" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/work"
run() {  # run <arm> <idx>
  echo "\$1-\$2" > "$H/arm"
  if [ "\$1" = on ]; then Z="$R/z0home"; CAP=1; else Z="$R/z0home-off"; CAP=0; fi
  env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 HOME="$H/home" TMPDIR="$H/tmp" \\
    CLAUDE_CONFIG_DIR="$H/claude" ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT ANTHROPIC_API_KEY=sk-ant-dummy-integration \\
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1 \\
    Z0INT_PYTHON="$PY" PYTHONPYCACHEPREFIX="$H/pycache" Z0INT_HOME="\$Z" Z0INT_CAPTURE=\$CAP \\
    timeout 150 "$CLAUDE_BIN" -p "MAIN-MARKER-c1: delegate one small subtask to a subagent, then report." \\
      --plugin-dir "$WT/harness-adapters/claude-code-z0intelligence" --output-format json --max-turns 6 \\
      --permission-mode default --allowedTools Agent Task > "$H/out-\$1-\$2.json" 2> "$H/err-\$1-\$2.txt"
  echo "claude \$1-\$2 exit=\$?"
}
for i in \$(seq $N); do
  if [ \$((i % 2)) = 1 ]; then order="on off"; else order="off on"; fi
  for arm in \$order; do run \$arm \$i; done
done
sleep 5  # detached opportunity builds finish before the stub and namespace go away
kill \$STUB 2>/dev/null || true
EOF
# Z0INT_CAPTURE=1 is not the kill switch value ("0" is), so "on" = capture enabled.
/mnt/zer0models/github/cua-lanes/bin/hostless unshare -rn bash "$H/inner.sh"
