"""``python -m z0int.memory.hook --harness <claude-code|codex> prompt``: the memory push seam for Claude-compatible
hooks, registered as its own ``UserPromptSubmit`` entry next to (never inside) the capture hook, so a capture
failure cannot cancel memory and the other way round.

In canary/on mode it prints ``hookSpecificOutput.additionalContext`` (the z0 memory brief) for the next model
request; in shadow (the default) it detaches the brief and prints nothing. The model endpoint the cloud-egress gate
sees is the harness's own setting only: its base-URL variable, else its public API (cloud: injection then needs
``allow_cloud_injection`` for that harness in memory.json; no other variable can mark a turn loopback). The 300 ms
canary/on budget counts from this process's start, like the JS and Hermes shims. There is no SessionStart seam: a
session start carries no user query to brief (recorded deviation). Grok has no push seam (pull-only via MCP; it
ignores UserPromptSubmit stdout): ``--harness grok`` only records the turn's shadow receipt (whatever mode is set,
unless off), so its capture opportunity carries it, and never prints. Always exits 0 and fails open.
"""

from __future__ import annotations

import time

IMPORTED_AT = time.time()

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Mapping  # noqa: E402

from . import seam  # noqa: E402

ENDPOINTS = {'claude-code': ('ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
             'codex': ('OPENAI_BASE_URL', 'https://api.openai.com/v1')}
SHADOW_ONLY = ('grok',)  # no model-visible push seam: receipts only


def endpoint(harness: str, env: Mapping[str, str]) -> str:
    var, default = ENDPOINTS[harness]
    return env.get(var) or default


def process_started_at() -> float:
    """Wall-clock start of this process (Linux /proc), else the time this module was imported."""
    try:
        with open('/proc/self/stat', encoding='ascii') as fh:
            ticks = int(fh.read().rsplit(')', 1)[1].split()[19])
        with open('/proc/uptime', encoding='ascii') as fh:
            uptime = float(fh.read().split()[0])
        return min(IMPORTED_AT, time.time() - (uptime - ticks / os.sysconf('SC_CLK_TCK')))
    except (OSError, ValueError, IndexError):
        return IMPORTED_AT


def handle(event: str, raw: str, harness: str, *, env: Mapping[str, str] | None = None,
           started_at: float | None = None) -> str | None:
    """One hook event -> stdout text (None prints nothing)."""
    env = os.environ if env is None else env
    if event != 'prompt' or harness not in (*ENDPOINTS, *SHADOW_ONLY):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    from ..harness_capture import is_harness_message, prompt_of, session_id_of, turn_id
    from ..harness_id import turn_key
    text = prompt_of(payload)
    if not text.strip() or is_harness_message(text):
        return None
    key = turn_key(harness, session_id_of(payload, env), turn_id(payload))
    if harness in SHADOW_ONLY:
        if seam.settings(harness, env=env)['mode'] != 'off':
            seam.turn(harness, turn_key=key, query=text, mode='shadow', cwd=payload.get('cwd'))
        return None
    out = seam.turn(harness, turn_key=key, query=text, endpoint=endpoint(harness, env), cwd=payload.get('cwd'),
                    started_at=started_at)
    if not out.get('context'):
        return None
    return json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                              'additionalContext': out['context']}}, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    started = process_started_at()
    ap = argparse.ArgumentParser(prog='python -m z0int.memory.hook', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--harness', required=True)
    ap.add_argument('event', choices=['prompt'])
    args = ap.parse_args(argv)
    try:
        out: Any = handle(args.event, sys.stdin.read(), args.harness, started_at=started)
    except Exception:  # noqa: BLE001 - a memory failure never blocks or alters the native turn
        out = None
    if out:
        sys.stdout.write(out + '\n')
        sys.stdout.flush()
    os._exit(0)  # a resolver thread past its deadline must not hold the hook open


if __name__ == '__main__':
    main()
