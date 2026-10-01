"""ObservationPack for Claude Code (after NVlabs/SoL-Pi, MIT; kvnloo/sol-pi-hermes mapping).

PostToolUse on Bash: a large result is archived verbatim under Z0INT_HOME and the model
sees head + tail + a stable handle; `z0obs` gives exact paged/grep recall. A result
identical to one already archived this session collapses to a reference. Evidence is
never lost: the archive is byte-exact and failures leave the original result unchanged.
"""
import argparse
import hashlib
import json
import os
import re
import sys

from . import paths

THRESHOLD = int(os.environ.get('Z0INT_OBS_THRESHOLD', '6000'))
HEAD, TAIL = 40, 20


def archive_dir(session):
    return paths.ensure_layout()['state'] / 'obs' / re.sub(r'[^A-Za-z0-9_-]', '_', session or 'none')


def pack(text, session):
    """Return the replacement text, or None to leave the result unchanged."""
    if len(text) <= THRESHOLD:
        return None
    handle = hashlib.sha256(text.encode()).hexdigest()[:12]
    folder = archive_dir(session)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{handle}.txt'
    lines = text.splitlines()
    recall = f'z0obs {handle} <start_line> <count>   |   z0obs {handle} --grep <regex>'
    if path.exists():
        return (f'[z0 ObservationPack] Output identical to archived obs-{handle} '
                f'({len(lines)} lines, {len(text)} chars) already returned earlier in this session. Exact recall: {recall}')
    path.write_text(text)
    if len(lines) <= HEAD + TAIL:
        return None
    omitted = len(lines) - HEAD - TAIL
    return '\n'.join(lines[:HEAD] + [
        f'[z0 ObservationPack] {omitted} middle lines ({len(text)} chars total, {len(lines)} lines) archived verbatim as obs-{handle}. '
        f'Nothing was discarded. Exact recall: {recall}'] + lines[-TAIL:])


def on_post_tool_use(hook):
    if hook.get('tool_name') != 'Bash':
        return None
    response = hook.get('tool_response')
    if not isinstance(response, dict) or not isinstance(response.get('stdout'), str):
        return None
    packed = pack(response['stdout'], hook.get('session_id'))
    if packed is None:
        return None
    return {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'updatedToolOutput': {**response, 'stdout': packed}}}


def recall(handle, start=None, count=None, grep=None, session=None):
    folder = paths.ensure_layout()['state'] / 'obs'
    matches = sorted(folder.glob(f'*/{handle}.txt'))
    if session:
        matches = [m for m in matches if m.parent == archive_dir(session)] or matches
    if not matches:
        return f'z0obs: no archived observation {handle}'
    lines = matches[0].read_text().splitlines()
    if grep:
        rx = re.compile(grep)
        return '\n'.join(f'{i + 1}: {l}' for i, l in enumerate(lines) if rx.search(l)) or 'z0obs: no matching lines'
    start = max(1, start or 1)
    count = count or 200
    return '\n'.join(f'{i}: {lines[i - 1]}' for i in range(start, min(len(lines), start + count - 1) + 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('hook')
    r = sub.add_parser('recall')
    r.add_argument('handle')
    r.add_argument('start', nargs='?', type=int)
    r.add_argument('count', nargs='?', type=int)
    r.add_argument('--grep')
    args = ap.parse_args()
    if args.cmd == 'recall':
        print(recall(args.handle.removeprefix('obs-'), args.start, args.count, args.grep))
        return
    try:
        out = on_post_tool_use(json.load(sys.stdin))
    except Exception:
        out = None  # fail open: the original result stands
    if out:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main()
