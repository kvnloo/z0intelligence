#!/usr/bin/env bash
# C8 isolated e2e: the memory seams of every harness against one synthetic substrate (frozen z0evals fb14919 cohort
# as AgentsView sessions + ledger claims, a fake-secret probe session, the C7 fake TencentDB gateway).
# Real CLIs: Claude Code (`claude -p`, Anthropic-Messages stub), Hermes (`hermes chat -q`, fork export venv from C4a,
# chat-completions stub), OMP (`omp -p --no-extensions -e omp-extensions/z0-memory`, chat-completions stub).
# DSH: the real plugin on a mock cordis host (never the dsh binary). Codex/Grok/OMO: MCP stdio round-trips only
# (their case B is live-only, never counted). Arms per cohort question: off, shadow, canary (+ a cloud-endpoint arm
# and a secret probe). Everything runs in one bwrap: private net namespace (loopback only), live Hermes home and
# live ~/.z0int masked by empty tmpfs, every home under homes/C8-memory-wiring/e2e. Synthetic prompts only.
# Round 2 adds: a cloud arm whose harness endpoint is non-loopback while Z0INT_MEMORY_ENDPOINT says loopback (real
# CC run + the CC/Codex hook command directly), a sibling-project leak canary (project zebra_other) asked in canary,
# DSH cloud-env/nocwd arms, per-shim persistence assertions and an AgentsView echo re-index probe (e2e_check.py).
# Round 3 (this copy, e2e-r3.sh): capture is ON in the CC arms (the kill switch now turns the memory seam off too, so
# the round-2 Z0INT_CAPTURE=0 is gone), OMP loads the z0int-bridge capture next to z0-memory, and a join stage runs
# capture + memory together for every harness (CC/Hermes/OMP real CLIs, Codex/Grok real hook commands, OMO the real
# bridge + memory extensions on a fake senpi API with the real worker, DSH the real plugin with capture on a mock
# host) plus a kill-switch arm; e2e_join_check.py checks opportunity_record.memory per harness.
# usage: e2e-r3.sh <worktree>     (run under flock -s quiet-lane.lock)
set -euo pipefail
WT=$1
W=/mnt/zer0models/z0-wt/wiring
S=$W/evidence/C8-memory-wiring/round2/scripts
IS=$W/evidence/integration/scripts
R=$W/homes/C8-memory-wiring/e2e-r3
V=$W/venv-integrate
HV=$W/homes/C4a-hermes-capture/hermes-venv
CLAUDE_BIN=~/.local/share/claude/versions/2.1.288
BUN=~/.bun/bin
SHA=$(git -C "$WT" rev-parse HEAD)
P_TDB=11548; P_CC=11550; P_HER=11551; P_OMP=11552
rm -rf "$R"; mkdir -p "$R"/{z0,av,tmp,pycache,results} "$R"/cc/{home,claude,tmp} "$R"/hermes/{home,hermes-home/plugins,tmp,runs} \
  "$R"/omp/{home,agent,tmp}
echo "$SHA" > "$R/sha.txt"
cat > "$R/z0int-python" <<EOF
#!/bin/sh
PYTHONPATH="$WT/src" PYTHONPYCACHEPREFIX="$R/pycache" exec "$V/bin/python" "\$@"
EOF
chmod +x "$R/z0int-python"
COMMON="PATH=$V/bin:$BUN:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb Z0INT_HOME=$R/z0 AGENTSVIEW_DATA_DIR=$R/av \
Z0INT_PYTHON=$R/z0int-python PYTHONPATH=$WT/src PYTHONPYCACHEPREFIX=$R/pycache TMPDIR=$R/tmp Z0_E2E_TDB_TOKEN=fake-e2e-token-0123 \
HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost"
env -i $COMMON HOME="$R/tmp" "$R/z0int-python" "$S/e2e_seed.py" "$WT" "$R/av/sessions.db" "$R/z0" $P_TDB > "$R/seed.json"
"$V/bin/python" - "$WT/tests/fixtures/z0evals-fb14919/cohort.json" > "$R/questions.tsv" <<'PY'
import json, sys
for q in json.load(open(sys.argv[1]))['questions']:
    print(f"{q['id']}\t{q['prompt']}")
PY
printf 'probe-secret\tquokka deploy notes tool output: what did the tool print?\n' > "$R/probe.tsv"
printf 'sibling\twhat is the zebra deploy plan?\n' > "$R/sibling.tsv"
# Hermes plugin from the committed tree
git -C "$WT" archive "$SHA" harness-adapters/hermes-z0intelligence | tar -x -C "$R/tmp" \
  && mv "$R/tmp/harness-adapters/hermes-z0intelligence" "$R/hermes/hermes-home/plugins/hermes-z0intelligence"
# Task dirs are named z0: the seam scopes memory to the cwd's project, and the substrate's sessions are project z0.
for d in "$R/cc/z0" "$R/hermes/z0" "$R/omp/z0"; do
  mkdir -p "$d" && git -C "$d" init -q -b main && echo "# synthetic fixture" > "$d/README.md" && git -C "$d" add -A \
    && git -C "$d" -c user.name=f -c user.email=f@example.invalid -c commit.gpgsign=false commit -qm init
done
# OMP model config (loopback stub)
printf 'providers:\n  sandbox:\n    baseUrl: http://127.0.0.1:%s/v1\n    apiKey: SANDBOX_FAKE_KEY\n    api: openai-completions\n    models:\n      - id: memory-fake\n        name: Memory Fake\n        reasoning: false\n        input: [text]\n        contextWindow: 32000\n        maxTokens: 1024\n        cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}\n' $P_OMP > "$R/omp/agent/models.yml"

cat > "$R/inner.sh" <<EOF
set -u
ip link set lo up 2>/dev/null || true
echo "## masks inside: hermes-home entries=\$(ls -A /workspace/hermes-home | wc -l) z0int entries=\$(ls -A ~/.z0int | wc -l)" | tee "$R/masks-inside.txt"
env -i $COMMON HOME="$R/tmp" "$V/bin/python" "$S/fake_tdb.py" "$WT" $P_TDB > "$R/tdb.log" 2>&1 &
STUB_ARM_FILE="$R/cc/arm" "$V/bin/python" "$IS/anthropic_stub.py" $P_CC "$R/cc/stub-requests.jsonl" &
"$V/bin/python" "$IS/chat_stub.py" $P_HER "$R/hermes/stub-requests.jsonl" "$R/hermes/arm" &
"$V/bin/python" "$IS/chat_stub.py" $P_OMP "$R/omp/stub-requests.jsonl" "$R/omp/arm" &
for p in $P_TDB $P_CC $P_HER $P_OMP; do for i in \$(seq 80); do (exec 3<>/dev/tcp/127.0.0.1/\$p) 2>/dev/null && break; sleep 0.1; done; done

cc() {  # cc <arm> <qid> <prompt> [extra env...]
  local arm=\$1 qid=\$2 prompt=\$3; shift 3
  echo "\$arm-\$qid" > "$R/cc/arm"
  local mode=\$arm; case "\$arm" in cloud|probe|sibling) mode=canary;; esac
  local t0=\$(date +%s%N)
  ( cd "$R/cc/z0" && env -i $COMMON HOME="$R/cc/home" CLAUDE_CONFIG_DIR="$R/cc/claude" \\
    ANTHROPIC_BASE_URL=http://127.0.0.1:$P_CC ANTHROPIC_API_KEY=sk-ant-dummy-c8-e2e CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \\
    DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1 Z0INT_MEMORY_INJECT=\$mode "\$@" \\
    timeout \${CC_TIMEOUT:-150} "$CLAUDE_BIN" -p "\$prompt" --plugin-dir "$WT/harness-adapters/claude-code-z0intelligence" \\
      --output-format json --max-turns 2 < /dev/null > "$R/cc/out-\$arm-\$qid.json" 2> "$R/cc/err-\$arm-\$qid.txt" )
  echo "{\"leg\": \"cc\", \"arm\": \"\$arm\", \"qid\": \"\$qid\", \"rc\": \$?, \"wall_ms\": \$(( (\$(date +%s%N) - t0) / 1000000 ))}" >> "$R/runs.jsonl"
}
hermes() {  # hermes <arm> <qid> <prompt>   (arm join: capture shadow + memory shadow)
  local arm=\$1 qid=\$2 prompt=\$3 mode=\$1 cap=off; case "\$arm" in probe|sibling) mode=canary;; join) mode=shadow cap=shadow;; esac
  echo "\$arm-\$qid" > "$R/hermes/arm"
  env -i PATH="$HV/bin:/usr/local/bin:/usr/bin:/bin" HOME="$R/hermes/home" HERMES_HOME="$R/hermes/hermes-home" \\
    PYTHONPYCACHEPREFIX="$R/pycache" "$HV/bin/python" "$S/e2e_hermes_config.py" "$R/hermes/hermes-home/config.yaml" $P_HER \$mode "$R/z0int-python" \$cap
  local t0=\$(date +%s%N)
  ( cd "$R/hermes/z0" && env -i PATH="$HV/bin:/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 TERM=dumb NO_COLOR=1 TZ=UTC \\
    HOME="$R/hermes/home" HERMES_HOME="$R/hermes/hermes-home" Z0INT_HOME="$R/z0" AGENTSVIEW_DATA_DIR="$R/av" \\
    Z0_E2E_TDB_TOKEN=fake-e2e-token-0123 TMPDIR="$R/hermes/tmp" PYTHONPYCACHEPREFIX="$R/pycache" \\
    HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost \\
    timeout 300 "$HV/bin/hermes" chat -Q -q "\$prompt" < /dev/null > "$R/hermes/runs/\$arm-\$qid.out" 2> "$R/hermes/runs/\$arm-\$qid.err" )
  echo "{\"leg\": \"hermes\", \"arm\": \"\$arm\", \"qid\": \"\$qid\", \"rc\": \$?, \"wall_ms\": \$(( (\$(date +%s%N) - t0) / 1000000 ))}" >> "$R/runs.jsonl"
}
omp() {  # omp <arm> <qid> <prompt>
  local arm=\$1 qid=\$2 prompt=\$3 mode=\$1; case "\$arm" in probe|sibling) mode=canary;; esac
  echo "\$arm-\$qid" > "$R/omp/arm"
  local t0=\$(date +%s%N)
  ( cd "$R/omp/z0" && env -i $COMMON HOME="$R/omp/home" XDG_CACHE_HOME="$R/omp/home/.cache" XDG_CONFIG_HOME="$R/omp/home/.config" \\
    XDG_DATA_HOME="$R/omp/home/.local/share" XDG_STATE_HOME="$R/omp/home/.local/state" PI_CODING_AGENT_DIR="$R/omp/agent" \\
    SANDBOX_FAKE_KEY=sandbox-not-a-secret PI_OFFLINE=1 Z0INT_SERVICE_URL=http://127.0.0.1:9 Z0INT_MEMORY_INJECT=\$mode \\
    timeout 180 "$BUN/omp" -p --no-extensions -e "$WT/omp-extensions/z0int-bridge" -e "$WT/omp-extensions/z0-memory" --mode json --model sandbox/memory-fake \\
      "\$prompt" < /dev/null > "$R/omp/out-\$arm-\$qid.jsonl" 2> "$R/omp/err-\$arm-\$qid.txt" )
  echo "{\"leg\": \"omp\", \"arm\": \"\$arm\", \"qid\": \"\$qid\", \"rc\": \$?, \"wall_ms\": \$(( (\$(date +%s%N) - t0) / 1000000 ))}" >> "$R/runs.jsonl"
}
n=0
while IFS=\$'\t' read -r qid prompt; do
  n=\$((n + 1))
  if [ \$((n % 2)) = 1 ]; then order="off shadow canary"; else order="canary shadow off"; fi
  for arm in \$order; do cc \$arm "\$qid" "\$prompt"; hermes \$arm "\$qid" "\$prompt"; omp \$arm "\$qid" "\$prompt"; done
done < "$R/questions.tsv"
IFS=\$'\t' read -r qid prompt < "$R/questions.tsv"
# cloud arm: the harness endpoint is non-loopback (TEST-NET-3; unreachable in this net namespace) while the generic
# env var says loopback. The hook must block (row cloud_injection_blocked, endpoint_loopback false); the CC run itself
# cannot reach a model, so its exit code is recorded, not gated.
CC_TIMEOUT=45 cc cloud "\$qid" "\$prompt" ANTHROPIC_BASE_URL=http://203.0.113.9:9 Z0INT_MEMORY_ENDPOINT=http://127.0.0.1:9
for pair in claude-code:ANTHROPIC_BASE_URL:https://api.anthropic.com codex:OPENAI_BASE_URL:https://api.openai.com/v1; do
  h=\${pair%%:*}; rest=\${pair#*:}; var=\${rest%%:*}; url=\${rest#*:}
  "$V/bin/python" -c 'import json, sys; print(json.dumps({"session_id": "cloud-" + sys.argv[1], "prompt_id": "p1", "prompt": sys.argv[2], "cwd": sys.argv[3], "hook_event_name": "UserPromptSubmit"}))' "\$h" "\$prompt" "$R/cc/z0" \\
    | env -i $COMMON HOME="$R/tmp" Z0INT_MEMORY_INJECT=canary "\$var=\$url" Z0INT_MEMORY_ENDPOINT=http://127.0.0.1:9 \\
      "$R/z0int-python" -m z0int.memory.hook --harness "\$h" prompt > "$R/results/hook-cloud-\$h.out" 2>&1
  echo "{\"leg\": \"hook-cloud\", \"arm\": \"\$h\", \"rc\": \$?}" >> "$R/runs.jsonl"
done
IFS=\$'\t' read -r qid prompt < "$R/probe.tsv"
cc probe "\$qid" "\$prompt"; hermes probe "\$qid" "\$prompt"; omp probe "\$qid" "\$prompt"
IFS=\$'\t' read -r qid prompt < "$R/sibling.tsv"
cc sibling "\$qid" "\$prompt"; hermes sibling "\$qid" "\$prompt"; omp sibling "\$qid" "\$prompt"
# DSH: real plugin, mock cordis host
( cd "$WT" && env -i $COMMON HOME="$R/tmp" node "$S/e2e_dsh_cohort.mjs" "$WT" "$WT/tests/fixtures/z0evals-fb14919/cohort.json" \\
    "$R/results/dsh-cohort.json" http://127.0.0.1:$P_OMP/v1 > "$R/dsh.log" 2>&1 )
echo "{\"leg\": \"dsh\", \"rc\": \$?}" >> "$R/runs.jsonl"
( cd "$WT" && env -i $COMMON HOME="$R/tmp" node --test tests/test-dsh-memory.mjs > "$R/dsh-node-test.txt" 2>&1 )
echo "{\"leg\": \"dsh-node-test\", \"rc\": \$?}" >> "$R/runs.jsonl"
# Pull seams: every z0-memory MCP entry over stdio
env -i $COMMON HOME="$R/tmp" "$R/z0int-python" "$S/e2e_mcp.py" "$WT" "$R/results/mcp.json" > "$R/mcp.log" 2>&1
echo "{\"leg\": \"mcp\", \"rc\": \$?}" >> "$R/runs.jsonl"
# ---- round 3: join stage (capture + memory per turn, every harness; default memory mode shadow)
n=0
while IFS=\$'\t' read -r qid prompt; do
  n=\$((n + 1)); [ \$n -gt 2 ] && break
  cc join "\$qid" "\$prompt" Z0INT_MEMORY_INJECT=shadow
  hermes join "\$qid" "\$prompt"
  for h in codex grok; do
    payload=\$("$V/bin/python" -c 'import json, sys; print(json.dumps({"session_id": "join-" + sys.argv[1] + "-" + sys.argv[2], "prompt_id": "p1", "prompt": sys.argv[3], "cwd": sys.argv[4], "hook_event_name": "UserPromptSubmit"}))' "\$h" "\$qid" "\$prompt" "$R/cc/z0")
    ( cd "$R/cc/z0" && printf '%s' "\$payload" | env -i $COMMON HOME="$R/tmp" "$R/z0int-python" -m z0int.hook_adapter --harness \$h prompt > "$R/results/join-\$h-\$qid-capture.out" 2>&1 ) &
    p1=\$!
    ( cd "$R/cc/z0" && printf '%s' "\$payload" | env -i $COMMON HOME="$R/tmp" "$R/z0int-python" -m z0int.memory.hook --harness \$h prompt > "$R/results/join-\$h-\$qid-memory.out" 2>&1 ) &
    p2=\$!; wait \$p1 \$p2  # only these two: a bare wait would also wait for the stubs
    echo "{\"leg\": \"join-hooks\", \"arm\": \"\$h\", \"qid\": \"\$qid\", \"rc\": 0}" >> "$R/runs.jsonl"
  done
done < "$R/questions.tsv"
( cd "$WT" && env -i $COMMON HOME="$R/tmp" Z0INT_MEMORY_INJECT=shadow "$BUN/bun" "$S/e2e_omo_join.ts" "$WT" "$R/questions.tsv" "$R/omp/z0" > "$R/omo-join.log" 2>&1 )
echo "{\"leg\": \"omo-join\", \"rc\": \$?}" >> "$R/runs.jsonl"
( cd "$WT" && env -i $COMMON HOME="$R/tmp" Z0INT_MEMORY_INJECT=shadow node "$S/e2e_dsh_join.mjs" "$WT" "$R/questions.tsv" "$R/omp/z0" > "$R/dsh-join.log" 2>&1 )
echo "{\"leg\": \"dsh-join\", \"rc\": \$?}" >> "$R/runs.jsonl"
# kill switch: a CC canary run with Z0INT_CAPTURE=0 writes no seam row, no opportunity, and the request has no brief
cnt() { cat "$R/z0/state/memory/seam/claude-code.jsonl" "$R/z0/state/claude-code/opportunities.jsonl" 2>/dev/null | wc -l; }
sleep 5; before=\$(cnt)
IFS=\$'\t' read -r qid prompt < "$R/questions.tsv"
cc killed "\$qid" "\$prompt" Z0INT_MEMORY_INJECT=canary Z0INT_CAPTURE=0
sleep 3; echo "{\"rows_before\": \$before, \"rows_after\": \$(cnt)}" > "$R/results/kill.json"
sleep 10  # detached shadow children and opportunity builds write their rows
# Acceptance rows while the fake TencentDB is still up (semantic layer live), then the verdict
env -i $COMMON HOME="$R/tmp" "$R/z0int-python" "$S/e2e_check.py" "$WT" "$R" > "$R/check.log" 2>&1
env -i $COMMON HOME="$R/tmp" "$R/z0int-python" "$S/e2e_join_check.py" "$WT" "$R" > "$R/join-check.log" 2>&1
echo "{\"leg\": \"check\", \"rc\": \$?}" >> "$R/check-rc.jsonl"
EOF
/mnt/zer0models/github/cua-lanes/bin/hostless bwrap --dev-bind / / --tmpfs /workspace/hermes-home --tmpfs ~/.z0int \
  --unshare-net --die-with-parent bash "$R/inner.sh"
cat "$R/check.log"; cat "$R/join-check.log"
