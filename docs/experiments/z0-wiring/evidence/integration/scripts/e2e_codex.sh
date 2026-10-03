#!/usr/bin/env bash
# Integration e2e, Codex leg: real `codex exec` (0.153.4) with the hooks-only codex-z0intelligence plugin installed
# from the integrate tree's local marketplace into an isolated CODEX_HOME, against an OpenAI-Responses stub that
# records every request body with the arm label. Private user+net namespace (loopback only). Arms alternate on/off
# N times (off = Z0INT_CAPTURE=0, same plugin); "on" arms write into the SHARED integration Z0INT_HOME.
# usage: e2e_codex.sh <worktree> <e2e-root> <python> <port> <N>
set -euo pipefail
WT=$1; R=$2; PY=$3; PORT=$4; N=$5
S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
H=$R/codex
rm -rf "$H"; mkdir -p "$H"/{home,codex,work,tmp,pycache} "$R/z0home" "$R/z0home-off"
cat > "$H/codex/config.toml" <<EOF
model = "gpt-stub"
model_provider = "intstub"

[model_providers.intstub]
name = "intstub"
base_url = "http://127.0.0.1:$PORT/v1"
env_key = "INT_STUB_KEY"
wire_api = "responses"

[features]
hooks = true
EOF
cat > "$H/inner.sh" <<EOF
set -u
ip link set lo up
STUB_ARM_FILE="$H/arm" "$PY" "$S/openai_responses_stub.py" $PORT "$H/stub-requests.jsonl" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/work"
codexr() { Z=\$1; CAP=\$2; shift 2; env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 HOME="$H/home" TMPDIR="$H/tmp" \\
  CODEX_HOME="$H/codex" INT_STUB_KEY=dummy-integration Z0INT_PYTHON="$PY" PYTHONPYCACHEPREFIX="$H/pycache" \\
  Z0INT_HOME="\$Z" Z0INT_CAPTURE=\$CAP timeout 120 codex "\$@"; }
echo setup > "$H/arm"
echo "## codex plugin marketplace add"; codexr "$H/z0-setup" 0 plugin marketplace add "$WT" 2>&1 | tail -3
echo "## codex plugin add"; codexr "$H/z0-setup" 0 plugin add codex-z0intelligence@z0intelligence 2>&1 | tail -3
for i in \$(seq $N); do
  if [ \$((i % 2)) = 1 ]; then order="on off"; else order="off on"; fi
  for arm in \$order; do
    echo "\$arm-\$i" > "$H/arm"
    if [ \$arm = on ]; then Z="$R/z0home"; CAP=1; else Z="$R/z0home-off"; CAP=0; fi
    codexr "\$Z" \$CAP exec --skip-git-repo-check -s read-only --dangerously-bypass-hook-trust \\
      "Tidy the changelog please (CODEX-MARKER-int)" < /dev/null > "$H/out-\$arm-\$i.txt" 2> "$H/err-\$arm-\$i.txt"
    echo "codex \$arm-\$i exit=\$?"
  done
done
sleep 3
kill \$STUB 2>/dev/null || true
EOF
/mnt/zer0models/github/cua-lanes/bin/hostless unshare -rn bash "$H/inner.sh"
