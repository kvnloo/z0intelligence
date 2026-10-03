#!/usr/bin/env bash
L=/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock; S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
flock -s $L $S/suite.sh /mnt/zer0models/z0-wt/wiring/wt/integrate head
flock -s $L $S/suite.sh /mnt/zer0models/z0-wt/wiring/wt/integrate-base base
echo done
