"""``python -m z0int.memory.hook --harness <claude-code|codex> prompt``: the memory push seam for Claude-compatible
hooks, registered as its own ``UserPromptSubmit`` entry next to (never inside) the capture hook, so a capture
failure cannot cancel memory and the other way round.

In canary/on mode it prints ``hookSpecificOutput.additionalContext`` (the z0 memory brief) for the next model
request; in shadow (the default) it detaches the brief and prints nothing. The model endpoint is
``Z0INT_MEMORY_ENDPOINT``, else the harness's base-URL variable, else its public API (cloud: injection then needs
``allow_cloud_injection`` for that harness). Grok has no push seam (pull-only via MCP): nothing runs for it.
Always exits 0 and fails open.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Mapping

from . import seam

ENDPOINTS = {'claude-code': ('ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
             'codex': ('OPENAI_BASE_URL', 'https://api.openai.com/v1')}


def endpoint(harness: str, env: Mapping[str, str]) -> str | None:
    var, default = ENDPOINTS[harness]
    return env.get('Z0INT_MEMORY_ENDPOINT') or env.get(var) or default


def handle(event: str, raw: str, harness: str, *, env: Mapping[str, str] | None = None) -> str | None:
    """One hook event -> stdout text (None prints nothing)."""
    env = os.environ if env is None else env
    if event != 'prompt' or harness not in ENDPOINTS:
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
    out = seam.turn(harness, turn_key=turn_key(harness, session_id_of(payload, env), turn_id(payload)), query=text,
                    endpoint=endpoint(harness, env), cwd=payload.get('cwd'))
    if not out.get('context'):
        return None
    return json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                              'additionalContext': out['context']}}, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='python -m z0int.memory.hook', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--harness', required=True)
    ap.add_argument('event', choices=['prompt'])
    args = ap.parse_args(argv)
    try:
        out: Any = handle(args.event, sys.stdin.read(), args.harness)
    except Exception:  # noqa: BLE001 - a memory failure never blocks or alters the native turn
        out = None
    if out:
        sys.stdout.write(out + '\n')
        sys.stdout.flush()
    os._exit(0)  # a resolver thread past its deadline must not hold the hook open


if __name__ == '__main__':
    main()
