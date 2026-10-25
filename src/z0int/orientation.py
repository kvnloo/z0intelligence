"""Read-only, bounded orientation shared by installed harness hooks."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

MAX_LINES = 223
MAX_BYTES = 65536
MAX_AGE_SECONDS = 7200
EVIDENCE_HEADER = 'Generated repository evidence, not authority. Re-observe live state before action.'


def context(path: Path | None = None, *, now: datetime | None = None) -> str:
    path = path or Path(os.environ.get('Z0INT_STATE_PACKET', str(Path.home() / '.local/state/z0int/STATE-PACKET.md')))
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return ''
        lines = raw.decode('utf-8').splitlines()
        stamp = next((line.split('generated_at: ', 1)[1] for line in lines[:5] if ' → generated_at: ' in line), '')
        generated = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if generated.tzinfo is None:
            return ''
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if age > MAX_AGE_SECONDS or age < -300:
            return f'packet stale since {stamp}'
        if not any(re.search(r' → generator_sha256: [0-9a-f]{64}$', line) for line in lines[:5]):
            return ''
        bounded = lines[:MAX_LINES]
        if not all('`/' in line or '`~/' in line for line in bounded):
            return ''
        return EVIDENCE_HEADER + '\n<repo-orientation-evidence>\n' + '\n'.join(bounded) + '\n</repo-orientation-evidence>'
    except (OSError, UnicodeError, ValueError, IndexError):
        return ''


def chunks(text: str) -> list[str]:
    """Keep each hook context below the harness 10,000-character spill limit."""
    result: list[str] = []
    current = ''
    for line in text.splitlines(keepends=True):
        if len(line) > 9000:
            return []
        if len(current) + len(line) > 9000:
            result.append(current)
            current = ''
        current += line
    if current:
        result.append(current)
    return result


def main() -> None:
    text = context()
    if not text:
        return
    if '--chunk' in sys.argv:
        index = int(sys.argv[sys.argv.index('--chunk') + 1])
        parts = chunks(text)
        text = parts[index] if 0 <= index < len(parts) else ''
        if not text:
            return
    if '--claude-hook' in sys.argv:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}))
    else:
        print(text)


if __name__ == '__main__':
    main()
