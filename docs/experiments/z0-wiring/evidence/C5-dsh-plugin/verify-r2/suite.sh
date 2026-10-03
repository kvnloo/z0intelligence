#!/usr/bin/env bash
S=/mnt/zer0models/z0-wt/wiring/scratch/C5-dsh-plugin-verify
L=/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock
for tag in head base; do
  T=/mnt/zer0models/z0-wt/wiring/wt/C5-dsh-plugin; [ $tag = base ] && T=$T-verify-base
  BT=/mnt/zer0models/z0-wt/wiring/homes/C5-dsh-plugin-verify/r2/suite-$tag/pt
  flock -s $L $S/run.sh $T suite-$tag -- python -m pytest -q -p no:cacheprovider --basetemp=$BT -rfE --tb=no tests > $S/suite-$tag.py.txt 2>&1
  flock -s $L $S/run.sh $T suite-$tag -- sh -c 'node --test tests/*.mjs' > $S/suite-$tag.node.txt 2>&1
done
echo done > $S/suite.done
