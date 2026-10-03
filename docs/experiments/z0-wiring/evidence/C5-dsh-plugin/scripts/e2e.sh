#!/usr/bin/env bash
# C5 isolated e2e (unit-level, real `python -m z0int.hook_adapter --harness dsh` spawn, fake z0 service on 11551).
set -u
W=/mnt/zer0models/z0-wt/wiring; T=$W/wt/C5-dsh-plugin; E=$W/evidence/C5-dsh-plugin; S=$W/scratch/C5-dsh-plugin
H=$W/homes/C5-dsh-plugin; L=/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock
rm -rf $H/e2e-on $H/e2e-off; mkdir -p $S
for m in on off; do
  flock -s $L $E/scripts/run.sh $T e2e-$m --guard -- env Z0INT_HOME=$H/e2e-$m/z0home \
    C5_PLUGIN=$T/harness-adapters/dsh-z0intelligence/index.mjs C5_PORT=11551 \
    node --unhandled-rejections=strict $E/scripts/e2e_dsh.mjs $m > $S/e2e-$m.json 2> $S/e2e-$m.err
  echo "$m exit=$?"
done
flock -s $L $E/scripts/run.sh $T e2e-on --guard -- python $E/scripts/e2e_check.py $H/e2e-on/z0home $S/e2e-on.json $S/e2e-off.json
echo "check exit=$?"
echo "## state/dsh files (e2e-on):"; /bin/ls -l $H/e2e-on/z0home/state/dsh
echo "## e2e-off state: $(/bin/ls $H/e2e-off/z0home/state/dsh 2>/dev/null || echo none)"
echo "## socket guard logs:"; cat $H/e2e-on/socket-violations.log $H/e2e-off/socket-violations.log 2>/dev/null || true; echo "(end)"
echo "## failure rows by kind:"
python3 -c "
import json,collections,sys
c=collections.Counter((r['kind'],json.dumps(r.get('detail'),sort_keys=True)) for r in map(json.loads,open('$H/e2e-on/z0home/state/dsh/failures.jsonl')))
[print(n,k,d) for (k,d),n in sorted(c.items())]"
echo "## stderr on/off:"; cat $S/e2e-on.err $S/e2e-off.err; echo "(end)"
