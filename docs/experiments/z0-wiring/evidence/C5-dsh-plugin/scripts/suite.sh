#!/usr/bin/env bash
# Full suite (pytest + node --test over tests/*.mjs) on head and base, each under the shared quiet-lane lock.
E=/mnt/zer0models/z0-wt/wiring/evidence/C5-dsh-plugin
L=/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock
for tag in head base; do
  T=/mnt/zer0models/z0-wt/wiring/wt/C5-dsh-plugin; [ $tag = base ] && T=$T-base
  (cd $T && flock -s $L $E/scripts/run.sh $T suite-$tag --guard -- python -m pytest -q -p no:cacheprovider -rfE --tb=no tests) > $E/suite-$tag.raw.txt 2>&1
  (cd $T && flock -s $L $E/scripts/run.sh $T suite-$tag --guard -- node --test tests/*.mjs) > $E/suite-$tag.node.txt 2>&1
done
echo done > $E/suite.done
