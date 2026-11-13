#!/bin/sh
set -eu
O=operations/z21-opus-infinite
printf 'Host: '; hostname
python3 --version
sha256sum -c "$O/evidence/source-sha256.txt"
python3 -m venv .z21-venv
.z21-venv/bin/python -m pip install --disable-pip-version-check 'pytest==8.4.2'
PYTHONPATH=src .z21-venv/bin/python -m pytest -q tests/test_z21_shadow.py
(cd "$O" && sh audit.sh > evidence/audit-output.log)
cmp "$O/evidence/expected-audit.json" "$O/evidence/current-z21/audit.json"
printf '%s\n' 'PASS: unchanged labeler and tests; frozen aggregate audit reproduced byte-for-byte.'
printf '%s\n' 'INCOMPLETE: synthetic HTTP/CLI tests are not Jev/d1 inference or equal-pass Opus A/B.'
