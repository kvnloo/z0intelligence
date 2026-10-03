#!/usr/bin/env bash
# C3 isolated e2e: real `omp -p` (18.4.2) with the worktree's z0int-bridge + local-cognition extensions (-e, no
# discovery) against an OpenAI-compatible stub, inside a private user+net namespace (loopback only), hostless.
# PI_CODING_AGENT_DIR / HOME / Z0INT_HOME under homes/C3-omp-omo-capture/<tag>. The local-cognition shadow model is
# served "at" a closed loopback port (dead backend -> URLError -> backend_unavailable).
# usage: e2e_omp.sh <worktree> <home> <python> <stub-port> <dead-port> <runs>
set -euo pipefail
WT=$1; H=$2; PY=$3; PORT=$4; DEAD=$5; RUNS=$6
S=/mnt/zer0models/z0-wt/wiring/evidence/C3-omp-omo-capture/scripts
BUN=~/.bun/bin
rm -rf "$H"; mkdir -p "$H"/{home,omp-agent/extensions,z0home/config,tmp,pycache}
ln -s "$WT/omp-extensions/z0int-bridge" "$H/omp-agent/extensions/z0int-bridge"
ln -s "$WT/omp-extensions/local-cognition" "$H/omp-agent/extensions/local-cognition"
cat > "$H/omp-agent/models.yml" <<YML
providers:
  sandbox:
    baseUrl: http://127.0.0.1:$PORT/v1
    apiKey: SANDBOX_FAKE_KEY
    api: openai-completions
    models:
      - id: bridge-fake
        name: Bridge Fake
        reasoning: false
        input: [text]
        contextWindow: 32000
        maxTokens: 1024
        cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}
YML
cat > "$H/z0home/config/serving.json" <<JSON
{"endpoints": [{"model_id": "functiongemma_270m", "base_url": "http://127.0.0.1:$DEAD/v1", "runtime": "llama.cpp"}]}
JSON
# Z0INT_PYTHON is a logging wrapper: the module of every z0 interpreter spawn (first 3 args only, never a
# payload) goes to $H/python-argv.log, so the check can prove no z0int.automatic (routing) runs from the bridge.
printf '#!/bin/sh\necho "$1 $2 $3" >> "%s/python-argv.log"\nexec "%s" "$@"\n' "$H" "$PY" > "$H/pywrap.sh"
chmod +x "$H/pywrap.sh"
# the task repository (synthetic): the OMP turn's cwd
mkdir -p "$H/work" && git -C "$H/work" init -q && echo "synthetic fixture" > "$H/work/README.md" \
  && git -C "$H/work" -c user.name=t -c user.email=t@example.invalid add -A \
  && git -C "$H/work" -c user.name=t -c user.email=t@example.invalid commit -qm init
cat > "$H/inner.sh" <<INNER
set -u
ip link set lo up
"$PY" "$S/openai_chat_stub.py" $PORT "$H/stub-requests.jsonl" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/work"
for i in \$(seq $RUNS); do
  echo "## omp run \$i"
  start=\$(date +%s.%N)
  env -i PATH=$BUN:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb HOME="$H/home" TMPDIR="$H/tmp" \\
    XDG_CACHE_HOME="$H/home/.cache" XDG_CONFIG_HOME="$H/home/.config" XDG_DATA_HOME="$H/home/.local/share" \\
    XDG_STATE_HOME="$H/home/.local/state" PI_CODING_AGENT_DIR="$H/omp-agent" Z0INT_HOME="$H/z0home" \\
    Z0INT_PYTHON="$H/pywrap.sh" PYTHONPYCACHEPREFIX="$H/pycache" SANDBOX_FAKE_KEY=sandbox-not-a-secret \\
    KERDOIOS_ROOT="$H/no-kerdoios" KERDOIOS_PYTHON="$H/no-kerdoios-python" Z0INT_SERVICE_URL=http://127.0.0.1:9 \\
    OMP_Z0INT_COGNITION_MODELS=functiongemma_270m Z0INT_COGNITION_SHADOW_SERVER_TIMEOUT_MS=3000 \\
    timeout 180 omp -p --no-session --no-extensions -e "$WT/omp-extensions/z0int-bridge" \\
      -e "$WT/omp-extensions/local-cognition" --mode json --model sandbox/bridge-fake \\
      "E2E_PROMPT_MARKER_C3 run \$i: read the readme and summarise it" < /dev/null > "$H/omp-out-\$i.jsonl" 2> "$H/omp-err-\$i.txt"
  rc=\$?; echo "omp exit=\$rc wall_s=\$(awk "BEGIN{print \$(date +%s.%N) - \$start}")"
done
for i in \$(seq $RUNS); do
  echo "## baseline omp run \$i (no extensions)"
  start=\$(date +%s.%N)
  env -i PATH=$BUN:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb HOME="$H/home" TMPDIR="$H/tmp" \\
    XDG_CACHE_HOME="$H/home/.cache" XDG_CONFIG_HOME="$H/home/.config" XDG_DATA_HOME="$H/home/.local/share" \\
    XDG_STATE_HOME="$H/home/.local/state" PI_CODING_AGENT_DIR="$H/omp-agent" Z0INT_HOME="$H/z0home-baseline" \\
    SANDBOX_FAKE_KEY=sandbox-not-a-secret \\
    timeout 180 omp -p --no-session --no-extensions --mode json --model sandbox/bridge-fake \\
      "baseline run \$i: read the readme and summarise it" < /dev/null > "$H/base-out-\$i.jsonl" 2> "$H/base-err-\$i.txt"
  rc=\$?; echo "omp exit=\$rc wall_s=\$(awk "BEGIN{print \$(date +%s.%N) - \$start}")"
done
sleep 8  # detached opportunity builds + worker drain after omp exits
kill \$STUB 2>/dev/null || true
INNER
/mnt/zer0models/github/cua-lanes/bin/hostless unshare -rn bash "$H/inner.sh"
