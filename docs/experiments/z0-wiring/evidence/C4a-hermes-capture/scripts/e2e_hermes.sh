#!/usr/bin/env bash
# C4a isolated e2e: real `hermes chat -q` (fork exported with git archive; venv in the C4a home) with
# hermes-z0intelligence installed (the spec's `hermes plugins install <path>` form is parsed as GitHub shorthand by
# this Hermes and the file:// form needs network for Hermes PM's runtime sync, so after recording that attempt the
# same git-archive tree is laid out in plugins/ and enabled in config.yaml), against a recording chat-completions stub on a private port. Arms alternate off/shadow N times, then
# one fail-open arm (z0int_python missing). bwrap masks the live Hermes homes and gives a private net namespace
# (loopback only). Everything else lives under homes/C4a-hermes-capture/<tag>.
# usage: e2e_hermes.sh <worktree> <tag> <N> <port>
set -euo pipefail
WT=$1; TAG=$2; N=$3; PORT=$4
B=/mnt/zer0models/z0-wt/wiring/homes/C4a-hermes-capture
E=/mnt/zer0models/z0-wt/wiring/evidence/C4a-hermes-capture/scripts
H=$B/$TAG
HV=$B/hermes-venv
ZPY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
SHA=$(git -C "$WT" rev-parse HEAD)   # the install clones committed content only, pinned to this SHA
rm -rf "$H"; mkdir -p "$H"/{home,hermes-home,z0home,tmp,pycache,runs}
# the task repo: the Hermes CLI (local terminal backend) defines the task cwd as its launch directory and exports
# it as TERMINAL_CWD; the plugin reads it through Hermes's own resolver (P-7 vs os.getcwd() is unit-tested)
mk() { git init -q -b "$2" "$1" && printf '# Fixture\n\n## P0\n- [ ] c4a-e2e-open-item\n' > "$1/README.md" && mkdir -p "$1/data" \
       && echo "release: pending" > "$1/data/release.txt" && git -C "$1" add -A \
       && git -C "$1" -c user.name=f -c user.email=f@example.invalid -c commit.gpgsign=false commit -qm "$3"; }
mk "$H/task-repo" c4a-e2e-branch "c4a-e2e-subject"
cat > "$H/z0int-python" <<EOF
#!/bin/sh
export PYTHONPATH="$WT/src"
exec "$ZPY" "\$@"
EOF
chmod +x "$H/z0int-python"
cat > "$H/inner.sh" <<EOF
set -u
"$ZPY" "$E/chat_stub.py" $PORT "$H/stub-requests.jsonl" "$H/arm" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/task-repo"
R() { env -i PATH="$HV/bin:/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 TERM=dumb NO_COLOR=1 TZ=UTC HOME="$H/home" \\
  HERMES_HOME="$H/hermes-home" Z0INT_HOME="$H/z0home" TMPDIR="$H/tmp" PYTHONPYCACHEPREFIX="$H/pycache" \\
  HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \\
  NO_PROXY=127.0.0.1,localhost timeout 300 "\$@"; }
echo "## hermes --version"; R "$HV/bin/hermes" --version 2>&1 | head -2
echo "## hermes plugins install file://<wt>#harness-adapters/hermes-z0intelligence --ref $SHA --enable --no-deps"
R "$HV/bin/hermes" plugins install "file://$WT#harness-adapters/hermes-z0intelligence" --ref $SHA --enable --no-deps 2>&1 | tail -4
echo "install exit=\${PIPESTATUS[0]} (offline: Hermes PM cannot sync its runtime environment without network)"
echo "## fallback: the same plugin tree at the pinned SHA in plugins/, enabled in config.yaml (no Python deps to sync)"
rm -rf "$H/hermes-home/plugins/hermes-z0intelligence"; mkdir -p "$H/hermes-home/plugins"
git -C "$WT" archive $SHA harness-adapters/hermes-z0intelligence | tar -x -C "$H/tmp" \
  && mv "$H/tmp/harness-adapters/hermes-z0intelligence" "$H/hermes-home/plugins/hermes-z0intelligence"
echo "## masks inside: hermes-home entries=\$(ls -A /workspace/hermes-home | wc -l) z0int entries=\$(ls -A ~/.z0int | wc -l)" | tee "$H/masks-inside.txt"
echo "## installed tree"; (cd "$H/hermes-home/plugins" && find . -maxdepth 2 | sort)
setmode() { R "$HV/bin/python" "$E/e2e_config.py" "$H/hermes-home/config.yaml" $PORT "\$1" "\$2"; }
run() {  # run <arm> <idx>
  echo "\$1-\$2" > "$H/arm"
  local t0=\$(date +%s%N)
  R "$HV/bin/hermes" chat -Q -q "c4a-e2e-canary-7f3a: what is the status of the release checklist?" \\
     > "$H/runs/\$1-\$2.out" 2> "$H/runs/\$1-\$2.err"
  local rc=\$?
  local t1=\$(date +%s%N)
  echo "{\"arm\": \"\$1\", \"idx\": \$2, \"rc\": \$rc, \"wall_ms\": \$(( (t1 - t0) / 1000000 ))}" >> "$H/runs.jsonl"
}
for i in \$(seq 1 $N); do
  if [ \$((i % 2)) = 1 ]; then order="off shadow"; else order="shadow off"; fi
  for arm in \$order; do setmode \$arm "$H/z0int-python"; run \$arm \$i; done
done
setmode shadow "$H/no-such-z0int-python"; run unavailable 1
sleep 5   # a projection never writes after its Hermes process closed the plugin: nothing may arrive in this wait
kill \$STUB 2>/dev/null || true
EOF
# The live Hermes home and the live z0int home are masked by fixed paths (no stat/readlink/ls of live paths; the
# inner shell proves the masks by listing the empty tmpfs). HOME, HERMES_HOME and Z0INT_HOME all point into $H.
MASK=(--tmpfs /workspace/hermes-home --tmpfs ~/.z0int)
echo "## masks: ${MASK[*]}"
/mnt/zer0models/github/cua-lanes/bin/hostless bwrap --dev-bind / / "${MASK[@]}" --unshare-net --die-with-parent \
  bash "$H/inner.sh"
