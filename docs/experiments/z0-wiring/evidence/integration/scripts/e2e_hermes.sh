#!/usr/bin/env bash
# Integration e2e, Hermes leg (adapted from C4a's e2e_hermes.sh): real `hermes chat -q` from the Hermes fork export
# that C4a built (homes/C4a-hermes-capture/hermes-venv, used read-only; pyc redirected), with harness-adapters/
# hermes-z0intelligence laid out from `git archive <integrate HEAD>` in an isolated HERMES_HOME, against a recording
# chat-completions stub. Arms alternate off/shadow N times, then one fail-open arm (z0int python missing).
# bwrap masks the live Hermes home and the live z0int home and gives a private net namespace (loopback only).
# Shadow arms write into the SHARED integration Z0INT_HOME; z0int runs from venv-integrate (editable integrate tree).
# usage: e2e_hermes.sh <worktree> <e2e-root> <N> <port>
set -euo pipefail
WT=$1; R=$2; N=$3; PORT=$4
S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
C4=/mnt/zer0models/z0-wt/wiring/evidence/C4a-hermes-capture/scripts
HV=/mnt/zer0models/z0-wt/wiring/homes/C4a-hermes-capture/hermes-venv
ZPY=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin/python
SHA=$(git -C "$WT" rev-parse HEAD)
H=$R/hermes
rm -rf "$H"; mkdir -p "$H"/{home,hermes-home,tmp,pycache,runs} "$R/z0home"
mk() { git init -q -b "$2" "$1" && printf '# Fixture\n\n## P0\n- [ ] int-e2e-open-item\n' > "$1/README.md" && mkdir -p "$1/data" \
       && echo "release: pending" > "$1/data/release.txt" && git -C "$1" add -A \
       && git -C "$1" -c user.name=f -c user.email=f@example.invalid -c commit.gpgsign=false commit -qm "$3"; }
mk "$H/task-repo" int-e2e-branch "int-e2e-subject"
cat > "$H/z0int-python" <<EOF
#!/bin/sh
exec "$ZPY" "\$@"
EOF
chmod +x "$H/z0int-python"
mkdir -p "$H/hermes-home/plugins"
git -C "$WT" archive $SHA harness-adapters/hermes-z0intelligence | tar -x -C "$H/tmp" \
  && mv "$H/tmp/harness-adapters/hermes-z0intelligence" "$H/hermes-home/plugins/hermes-z0intelligence"
echo "$SHA" > "$H/plugin-sha.txt"
cat > "$H/inner.sh" <<EOF
set -u
"$ZPY" "$S/chat_stub.py" $PORT "$H/stub-requests.jsonl" "$H/arm" &
STUB=\$!
for i in \$(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
cd "$H/task-repo"
R() { env -i PATH="$HV/bin:/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 TERM=dumb NO_COLOR=1 TZ=UTC HOME="$H/home" \\
  HERMES_HOME="$H/hermes-home" Z0INT_HOME="$R/z0home" TMPDIR="$H/tmp" PYTHONPYCACHEPREFIX="$H/pycache" \\
  HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \\
  NO_PROXY=127.0.0.1,localhost timeout 300 "\$@"; }
echo "## hermes --version"; R "$HV/bin/hermes" --version 2>&1 | head -2
echo "## masks inside: hermes-home entries=\$(ls -A /workspace/hermes-home | wc -l) z0int entries=\$(ls -A ~/.z0int | wc -l)" | tee "$H/masks-inside.txt"
setmode() { R "$HV/bin/python" "$C4/e2e_config.py" "$H/hermes-home/config.yaml" $PORT "\$1" "\$2"; }
run() {  # run <arm> <idx>
  echo "\$1-\$2" > "$H/arm"
  local t0=\$(date +%s%N)
  R "$HV/bin/hermes" chat -Q -q "HERMES-MARKER-int: what is the status of the release checklist?" \\
     > "$H/runs/\$1-\$2.out" 2> "$H/runs/\$1-\$2.err"
  local rc=\$?
  local t1=\$(date +%s%N)
  echo "{\"arm\": \"\$1\", \"idx\": \$2, \"rc\": \$rc, \"wall_ms\": \$(( (t1 - t0) / 1000000 ))}" >> "$H/runs.jsonl"
  echo "hermes \$1-\$2 exit=\$rc"
}
for i in \$(seq 1 $N); do
  if [ \$((i % 2)) = 1 ]; then order="off shadow"; else order="shadow off"; fi
  for arm in \$order; do setmode \$arm "$H/z0int-python"; run \$arm \$i; done
done
setmode shadow "$H/no-such-z0int-python"; run unavailable 1
sleep 5
kill \$STUB 2>/dev/null || true
EOF
MASK=(--tmpfs /workspace/hermes-home --tmpfs ~/.z0int)
/mnt/zer0models/github/cua-lanes/bin/hostless bwrap --dev-bind / / "${MASK[@]}" --unshare-net --die-with-parent \
  bash "$H/inner.sh"
