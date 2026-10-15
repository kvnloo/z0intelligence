#!/usr/bin/env bash
# S3 failure injection (z0evals#69): every z0 hook must fail open. Each scenario runs a real
# headless Claude Code session with the z0 plugin (lean flags) and a trivial prompt, and must
# answer normally. Receipts are expected only where z0 can run and write.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$(mktemp -d)"; PY="${Z0INT_PYTHON:-$ROOT/.venv/bin/python}"; PL="$ROOT/harness-adapters/claude-code-z0intelligence"
mkdir -p "$S/nogit" "$S/ro" "$S/home/config" && chmod 500 "$S/ro"
echo '{"claude-code":{"enabled":true}}' > "$S/home/config/automatic.json"
G="$S/repo"; mkdir "$G"; (cd "$G" && git init -q && echo x > f && git add -A && git -c user.name=t -c user.email=t@t commit -qm i)
run() {
  local name=$1 dir=$2; shift 2
  (cd "$dir" && env "$@" claude -p "Reply with just OK." --output-format json --model sonnet --effort low \
     --plugin-dir "$PL" --setting-sources project --strict-mcp-config --disable-slash-commands </dev/null 2>"$S/$name.err") |
  python3 -c "import json,sys
d=json.load(sys.stdin); ok=d.get('result','').strip().startswith('OK') and not d.get('is_error')
print('$name', 'ok' if ok else 'FAIL', 'wall_ms', d.get('duration_ms'))"
  [[ -s "$S/$name.err" ]] && echo "  stderr: $(head -c 200 "$S/$name.err")"
}
run control      "$G"       Z0INT_PYTHON="$PY" Z0INT_HOME="$S/home"
run svc-down     "$G"       Z0INT_PYTHON="$PY" Z0INT_HOME="$S/home" Z0INT_SERVICE_URL=http://127.0.0.1:9 Z0INT_CLAUDE_CODE_SHADOW=0
run bad-python   "$G"       Z0INT_PYTHON=/nonexistent/python Z0INT_HOME="$S/home"
run packet-nogit "$S/nogit" Z0INT_PYTHON="$PY" Z0INT_HOME="$S/home" Z0INT_CLAUDE_CODE_PACKET=1
run ro-home      "$G"       Z0INT_PYTHON="$PY" Z0INT_HOME="$S/ro/z0" Z0INT_CLAUDE_CODE_PACKET=1
python3 -c "import json;r=[json.loads(l) for l in open('$S/home/tokenomics/events.jsonl')];print('usage receipts', len(r), '(expected 3: control, svc-down, packet-nogit)')"
