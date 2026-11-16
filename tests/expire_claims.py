#!/usr/bin/env python3
"""Offline check for .github/scripts/expire-claims.sh.

Runs the script in --dry-run mode against a fake `gh` on PATH, so no network
or token is needed. Zero dependencies.

Usage:
  python3 tests/expire_claims.py
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "expire-claims.sh"

COMMENTS = {
    # explicit expiry in the past
    "1": [{"body": "Claiming for worker-a\nexpires_at: 2020-01-01T00:00:00Z", "created_at": "2019-12-31T00:00:00Z"}],
    # explicit expiry far in the future
    "2": [{"body": "Claiming for worker-b\nexpires_at: 2999-01-01T00:00:00Z", "created_at": "2020-01-01T00:00:00Z"}],
    # no expiry, old claim comment -> falls back to max age
    "3": [{"body": "claimant: worker-c", "created_at": "2020-01-01T00:00:00Z"}],
    # no claim timestamp at all
    "4": [{"body": "just a note", "created_at": "2020-01-01T00:00:00Z"}],
    # Renewal beyond REST's first default 30-comment page: active lease must win.
    "5": (
        [{"body": "claiming for worker-old\nexpires: 2020-01-01T00:00:00Z", "created_at": "2019-12-31T00:00:00Z"}]
        + [{"body": f"ordinary discussion {i}", "created_at": "2026-10-13T00:00:00Z"} for i in range(30)]
        + [{"body": "claiming for worker-new\nexpires: 2999-01-01T00:00:00Z", "created_at": "2026-10-15T00:00:00Z"}]
    ),
    # z0-claim lease marker from the claim bot, expired -> expire
    "6": [{"user": {"login": "github-actions[bot]"},
           "body": 'Claim accepted <!-- z0-claim {"expires":"2020-01-01T00:00:00Z","login":"a","status":"active","task":"t-1"} -->',
           "created_at": "2019-12-31T00:00:00Z"}],
    # active bot marker -> keep (the legacy max-age rule must not expire it)
    "7": [{"user": {"login": "github-actions[bot]"},
           "body": 'Claim accepted <!-- z0-claim {"expires":"2999-01-01T00:00:00Z","login":"a","status":"active","task":"t-1"} -->',
           "created_at": "2020-01-01T00:00:00Z"}],
    # forged active marker from a non-bot user is ignored; the bot's marker expired -> expire
    "8": [{"user": {"login": "github-actions[bot]"},
           "body": 'ok <!-- z0-claim {"expires":"2020-01-01T00:00:00Z","login":"a","status":"active","task":"t-1"} -->',
           "created_at": "2019-12-31T00:00:00Z"},
          {"user": {"login": "mallory"},
           "body": 'mine <!-- z0-claim {"expires":"2999-01-01T00:00:00Z","login":"mallory","status":"active","task":"t-1"} -->',
           "created_at": "2020-01-02T00:00:00Z"}],
    # released by its holder -> expire (returns the issue to claimable)
    "9": [{"user": {"login": "github-actions[bot]"},
           "body": 'ok <!-- z0-claim {"expires":"2999-01-01T00:00:00Z","login":"a","status":"active","task":"t-1"} -->',
           "created_at": "2020-01-01T00:00:00Z"},
          {"user": {"login": "github-actions[bot]"},
           "body": 'released <!-- z0-claim {"expires":"2026-01-01T00:00:00Z","login":"a","status":"released","task":"t-1"} -->',
           "created_at": "2026-01-01T00:00:00Z"}],
}

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
data = json.loads(os.environ["FAKE_GH_COMMENTS"])
if args[:2] == ["issue", "list"]:
    print("\n".join(data))
    sys.exit(0)
if args and args[0] == "api":
    num = args[1].split("/issues/")[1].split("/")[0]
    # Match gh's raw page arrays and reject unsupported formatting flags.
    if "--slurp" in args and ("--jq" in args or "--template" in args):
        sys.stderr.write("the --slurp option is not supported with --jq or --template\n")
        sys.exit(1)
    selected = data[num] if "--paginate" in args else data[num][:30]
    if "--slurp" in args:
        print(json.dumps([selected[i:i + 30] for i in range(0, len(selected), 30)]))
    elif "--jq" in args:
        print(json.dumps([{"body": c["body"], "createdAt": c["created_at"]} for c in selected]))
    else:
        print(json.dumps(selected))
    sys.exit(0)
sys.stderr.write("fake gh: unexpected call %r\n" % (args,))
sys.exit(3)
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        gh = Path(tmp) / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        env = dict(os.environ)
        env["PATH"] = f"{tmp}{os.pathsep}{env.get('PATH', '')}"
        env["FAKE_GH_COMMENTS"] = json.dumps(COMMENTS)
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--dry-run"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    out = proc.stdout
    failures = []
    if proc.returncode != 0:
        failures.append(f"exit {proc.returncode}: {proc.stderr.strip()}")
    for want in ("expire #1 ", "keep #2 ", "expire #3 ", "keep #4 (no claim timestamp)", "keep #5 ",
                 "expire #6 (no active z0-claim lease)", "keep #7 (1 active z0-claim lease(s))",
                 "expire #8 (no active z0-claim lease)", "expire #9 (no active z0-claim lease)",
                 "released 5 lease(s)"):
        if want not in out:
            failures.append(f"missing {want!r}")
    if failures:
        print("expire-claims: FAIL")
        for f in failures:
            print(f"  - {f}")
        print(out)
        return 1
    print("expire-claims: OK (9 fixture issues, 5 released in dry-run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
