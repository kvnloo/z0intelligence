#!/usr/bin/env bash
# Integration e2e, OMP / OMO leg: the real CLI in print mode with the integrate tree's z0int-bridge capture
# extension loaded by -e (no discovery), against a recording chat-completions stub, inside a private user+net
# namespace (loopback only), hostless. Arms per run index: on (extension, capture on, SHARED integration Z0INT_HOME),
# off (extension, Z0INT_CAPTURE=0, z0home-off) and none (no extension at all). The model-visible requests of all
# three arms are compared by the check. OMP also loads local-cognition with its shadow model "served" at a closed
# loopback port (dead backend -> backend_unavailable). Synthetic prompts only.
# usage: e2e_omp.sh <omp|omo> <worktree> <e2e-root> <python> <stub-port> <dead-port> <N>
set -euo pipefail
HN=$1; WT=$2; R=$3; PY=$4; PORT=$5; DEAD=$6; N=$7
S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
BUN=~/.bun/bin
H=$R/$HN
rm -rf "$H"; mkdir -p "$H"/{home,agent,tmp,pycache} "$R/z0home/config" "$R/z0home-off/config"
if [ "$HN" = omp ]; then
  BIN=~/.local/bin/omp
  EXT=(-e "$WT/omp-extensions/z0int-bridge" -e "$WT/omp-extensions/local-cognition")
  EXTRA=()
else
  BIN=~/.bun/bin/omo
  EXT=(-e "$WT/omp-extensions/z0int-bridge/omo.ts")
  EXTRA=(--omo-senpi-disabled)
  mkdir -p "$H/home/.omo"; ln -s "$H/agent" "$H/home/.omo/agent"
fi
cat > "$H/agent/models.yml" <<YML
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
# senpi (OMO) reads <agentDir>/models.json (senpi docs/models.md), OMP reads models.yml
printf '{"providers": {"sandbox": {"baseUrl": "http://127.0.0.1:%s/v1", "api": "openai-completions", "apiKey": "sandbox-not-a-secret", "models": [{"id": "bridge-fake", "name": "Bridge Fake", "reasoning": false, "input": ["text"], "contextWindow": 200000, "maxTokens": 1024}]}}}\n' "$PORT" > "$H/agent/models.json"
for z in "$R/z0home" "$R/z0home-off"; do
  [ -f "$z/config/serving.json" ] || printf '{"endpoints": [{"model_id": "functiongemma_270m", "base_url": "http://127.0.0.1:%s/v1", "runtime": "llama.cpp"}]}\n' "$DEAD" > "$z/config/serving.json"
done
# Z0INT_PYTHON logging wrapper: the module of every z0 interpreter spawn (first 3 args only) -> python-argv.log
printf '#!/bin/sh\necho "$1 $2 $3" >> "%s/python-argv.log"\nexec "%s" "$@"\n' "$H" "$PY" > "$H/pywrap.sh"
chmod +x "$H/pywrap.sh"
mkdir -p "$H/work" && git -C "$H/work" init -q && echo "synthetic fixture" > "$H/work/README.md" \
  && git -C "$H/work" -c user.name=t -c user.email=t@example.invalid add -A \
  && git -C "$H/work" -c user.name=t -c user.email=t@example.invalid commit -qm init
EXTS="${EXT[*]@Q}"; EXTRAS="${EXTRA[*]@Q}"
cat > "$H/inner.sh" <<INNER
set -u
ip link set lo up
"$PY" "$S/chat_stub.py" $PORT "$H/stub-requests.jsonl" "$H/arm" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/work"
run() {  # run <arm> <idx>
  echo "\$1-\$2" > "$H/arm"
  case \$1 in on) Z="$R/z0home"; CAP=1; X=($EXTS);; off) Z="$R/z0home-off"; CAP=0; X=($EXTS);; none) Z="$H/z0home-none"; CAP=0; X=();; esac
  start=\$(date +%s%N)
  env -i PATH=$BUN:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb HOME="$H/home" TMPDIR="$H/tmp" \\
    XDG_CACHE_HOME="$H/home/.cache" XDG_CONFIG_HOME="$H/home/.config" XDG_DATA_HOME="$H/home/.local/share" \\
    XDG_STATE_HOME="$H/home/.local/state" PI_CODING_AGENT_DIR="$H/agent" Z0INT_HOME="\$Z" Z0INT_CAPTURE=\$CAP \\
    Z0INT_PYTHON="$H/pywrap.sh" PYTHONPYCACHEPREFIX="$H/pycache" SANDBOX_FAKE_KEY=sandbox-not-a-secret \\
    KERDOIOS_ROOT="$H/no-kerdoios" KERDOIOS_PYTHON="$H/no-kerdoios-python" Z0INT_SERVICE_URL=http://127.0.0.1:9 \\
    OMP_Z0INT_COGNITION_MODELS=functiongemma_270m Z0INT_COGNITION_SHADOW_SERVER_TIMEOUT_MS=3000 PI_OFFLINE=1 \\
    timeout 180 "$BIN" -p --no-session --no-extensions "\${X[@]}" $EXTRAS --mode json --model sandbox/bridge-fake \\
      "${HN^^}-MARKER-int: read the readme and summarise it" < /dev/null > "$H/out-\$1-\$2.jsonl" 2> "$H/err-\$1-\$2.txt"
  rc=\$?
  echo "{\"arm\": \"\$1\", \"idx\": \$2, \"rc\": \$rc, \"wall_ms\": \$(( (\$(date +%s%N) - start) / 1000000 ))}" >> "$H/runs.jsonl"
  echo "$HN \$1-\$2 exit=\$rc"
}
for i in \$(seq $N); do
  if [ \$((i % 2)) = 1 ]; then order="on off none"; else order="none off on"; fi
  for arm in \$order; do run \$arm \$i; done
done
sleep 8  # detached opportunity builds + worker drain after the CLI exits
kill \$STUB 2>/dev/null || true
INNER
/mnt/zer0models/github/cua-lanes/bin/hostless unshare -rn bash "$H/inner.sh"
