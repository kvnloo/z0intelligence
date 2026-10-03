#!/usr/bin/env bash
# C1 e2e (3): real `codex exec` (0.153.4) with the hooks-only codex-z0intelligence plugin installed from the
# worktree's local marketplace, against an OpenAI-Responses stub (custom model provider; INFERRED config keys).
# Private user+net namespace (loopback only), CODEX_HOME / HOME / Z0INT_HOME under homes/C1-loop-core/e2e-codex.
# usage: e2e_codex.sh <worktree> <home> <python> <port>
set -euo pipefail
WT=$1; H=$2; PY=$3; PORT=$4
S=/mnt/zer0models/z0-wt/wiring/scratch/C1-loop-core
rm -rf "$H"; mkdir -p "$H"/{home,codex,z0,work,tmp,pycache}
cat > "$H/codex/config.toml" <<EOF
model = "gpt-stub"
model_provider = "c1stub"

[model_providers.c1stub]
name = "c1stub"
base_url = "http://127.0.0.1:$PORT/v1"
env_key = "C1_STUB_KEY"
wire_api = "responses"

[features]
hooks = true
EOF
cat > "$H/inner.sh" <<EOF
set -u
ip link set lo up
"$PY" "$S/openai_stub.py" $PORT "$H/stub-requests.jsonl" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/work"
run() { env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 HOME="$H/home" TMPDIR="$H/tmp" CODEX_HOME="$H/codex" \\
  C1_STUB_KEY=dummy-c1-e2e Z0INT_PYTHON="$PY" PYTHONPATH="$WT/src" PYTHONPYCACHEPREFIX="$H/pycache" Z0INT_HOME="$H/z0" \\
  timeout 120 codex "\$@"; }
echo "## codex plugin marketplace add"; run plugin marketplace add "$WT" 2>&1 | tail -5
echo "## codex plugin list"; run plugin list 2>&1 | tail -8
echo "## codex plugin add"; run plugin add codex-z0intelligence@z0intelligence 2>&1 | tail -5
echo "## codex exec"; run exec --skip-git-repo-check -s read-only --dangerously-bypass-hook-trust "Tidy the changelog please" < /dev/null > "$H/codex-out.txt" 2> "$H/codex-err.txt"
echo "codex exec exit=\$?"
sleep 1
kill \$STUB 2>/dev/null || true
EOF
/mnt/zer0models/github/cua-lanes/bin/hostless unshare -rn bash "$H/inner.sh"
