#!/usr/bin/env bash
S=/mnt/zer0models/z0-wt/wiring/scratch/C5-dsh-plugin-verify; T=/mnt/zer0models/z0-wt/wiring/wt/C5-dsh-plugin
for m in on off; do rm -rf /mnt/zer0models/z0-wt/wiring/homes/C5-dsh-plugin-verify/r2/e2e-$m
  $S/run.sh $T e2e-$m -- node --unhandled-rejections=strict $S/e2e.mjs $T $m > $S/e2e-$m.json 2> $S/e2e-$m.err; echo "$m rc=$?"; done
