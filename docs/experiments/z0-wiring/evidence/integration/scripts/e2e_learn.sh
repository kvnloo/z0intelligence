#!/usr/bin/env bash
# Integration e2e, learning stage over the SHARED integration Z0INT_HOME filled by the harness legs:
#   1 `z0int outcomes verify` (Claude Code labels from the isolated CLAUDE_CONFIG_DIR transcripts; no gh)
#   2 `z0int outcomes export` per harness state dir (training_row.v0 + manifest; assert_private inside)
#   3 the C1 cross-harness frozen bundle (harness_capture.freeze) over every state/<harness>/ record file
#   4 the evolution-lab learner, ONCE: python -m evolution_lab.verified_loop on the exported claude-code table
#     (pinned worktree exp/verified-loop-v0 @ 1b80a4e, stdlib only, read-only on PYTHONPATH; pyc redirected)
# Everything writes under <e2e-root>/learn (outside any git tree). Python socket guard on.
# usage: e2e_learn.sh <e2e-root>   (run under flock -s quiet-lane.lock)
set -uo pipefail
R=$1
L=$R/learn; rm -rf "$L"; mkdir -p "$L"/{home,tmp,tables,frozen,el}
PY=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin/python
EL=/mnt/zer0models/z0-wt/evolution-lab-verified-loop
G=/mnt/zer0models/z0-wt/wiring/scratch/integration/guard
X() { env -i PATH=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin:/usr/bin:/bin LANG=C.UTF-8 HOME="$L/home" TMPDIR="$L/tmp" \
  Z0INT_HOME="$R/z0home" CLAUDE_CONFIG_DIR="$R/claude-code/claude" PYTHONPATH="$G${EXTRA_PP:+:$EXTRA_PP}" \
  PYTHONPYCACHEPREFIX="$L/pycache" Z0INT_SOCKET_GUARD_LOG="$L/socket-violations.log" "$@"; }
echo "## 1 verify (claude-code)"
X "$PY" -m z0int.cli outcomes verify --no-gh --json --report "$L/verify-report.md" > "$L/verify.json" 2> "$L/verify.err"
echo "verify exit=$?"
echo "## 1b verify --harness for the other harnesses (C2-loop-consumers is not verified, so no such option exists here)"
X "$PY" -m z0int.cli outcomes verify --harness codex --no-gh --dry-run > "$L/verify-codex.out" 2>&1
echo "verify --harness codex exit=$? ($(tail -1 "$L/verify-codex.out" | cut -c1-160))"
echo "## 2 export per harness"
for h in claude-code codex grok hermes omp omo dsh; do
  X "$PY" -m z0int.cli outcomes export --state-dir "$R/z0home/state/$h" --out "$L/tables/$h.jsonl" --no-gh --json \
    > "$L/tables/$h.counts.json" 2> "$L/tables/$h.err"
  echo "export $h exit=$? $(tr -d '\n ' < "$L/tables/$h.counts.json" | cut -c1-200)"
done
echo "## 3 cross-harness frozen bundle (harness_capture.freeze)"
X "$PY" - "$L/frozen" <<'PYEOF'
import json, sys
from pathlib import Path
from z0int import harness_capture as hc
out = Path(sys.argv[1])
b = hc.freeze()
(out / 'rows.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in b['rows']))
(out / 'manifest.json').write_text(json.dumps(b['manifest'], indent=1, sort_keys=True) + '\n')
b2 = hc.freeze()
print(json.dumps({'rows': b['manifest']['rows'], 'by_schema': b['manifest']['by_schema'],
                  'unsupported_schemas': b['manifest']['unsupported_schemas'],
                  'rebuild_same_hash': b2['manifest']['bundle_sha256'] == b['manifest']['bundle_sha256']}))
PYEOF
echo "## 4 evolution-lab learner (verified_loop v0), one run"
git -C "$EL" rev-parse HEAD > "$L/el/learner_sha.txt"
(cd "$L/el" && EXTRA_PP="$EL" X "$PY" -m evolution_lab.verified_loop --table "$L/tables/claude-code.jsonl" \
   --out "$L/el/verified-loop.json" --md "$L/el/verified-loop.md" > "$L/el/stdout.txt" 2> "$L/el/stderr.txt")
echo "learner exit=$? sha=$(cat "$L/el/learner_sha.txt")"
"$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print('decision', d.get('decision'), 'sufficiency', json.dumps(d.get('sufficiency'))[:400])" "$L/el/verified-loop.json"
echo "## socket guard log: $(cat "$L/socket-violations.log" 2>/dev/null || echo none)"
