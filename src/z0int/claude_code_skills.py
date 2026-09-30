"""Task-conditioned skill exposure for Claude Code launches (capability selection, z0int#23).

Every exposed skill description is re-read on every request and rewritten on every cold
session. When the task prompt is known at launch (headless runs, agent factories), expose
only skill families the prompt plausibly needs; hide the rest as ``user-invocable-only``
so ``/name`` still works. Deterministic baseline: family lexical gate, abstain = expose all.
"""
import json
import os
from pathlib import Path
import re
import sys

STOP = set('a an and or the to of for in on with when use this that is are be it as by from any your you user skill skills via not into at can'.split())


def tokens(text):
    return {t for t in re.findall(r'[a-z][a-z0-9]+', text.lower()) if t not in STOP and len(t) > 2}


def load(root=None):
    root = Path(root or os.path.expanduser('~/.claude/skills'))
    skills = {}
    for path in sorted(root.glob('*/SKILL.md')):
        text = path.read_text(errors='ignore')
        match = re.search(r'^---\n(.*?)\n---', text, re.S)
        desc = re.search(r'description:\s*(.*?)(?:\n[\w-]+:|\Z)', match.group(1) if match else '', re.S)
        skills[path.parent.name] = ' '.join((desc.group(1) if desc else '').split())
    return skills


def family(name, desc=''):
    # Families are semantic: a skill that declares itself part of HyperFrames joins it.
    if name.startswith('hyperframes') or 'hyperframes' in desc.lower():
        return 'hyperframes'
    if name.startswith('pstack'):
        return 'pstack'
    return name


def select(prompt, skills, threshold=1):
    """Return (exposed names, hidden names, reason). Exposes a whole family or nothing of it."""
    words = tokens(prompt)
    lowered = prompt.lower()
    families = {}
    for name, desc in skills.items():
        families.setdefault(family(name, desc), []).append(name)
    vocab = {fam: set().union(*(tokens(skills[n] + ' ' + n.replace('-', ' ')) for n in names)) for fam, names in families.items()}
    exposed, reasons = set(), {}
    for fam, names in families.items():
        if any(re.search(r'(^|[\s/])' + re.escape(n) + r'\b', lowered) for n in names) or fam in lowered:
            exposed.update(names); reasons[fam] = 'named'; continue
        # Distinctive vocabulary only: words no other family's descriptions use.
        others = set().union(*(v for f, v in vocab.items() if f != fam))
        hits = words & (vocab[fam] - others)
        if len(hits) >= threshold:
            exposed.update(names); reasons[fam] = sorted(hits)[:6]
    hidden = sorted(set(skills) - exposed)
    return sorted(exposed), hidden, reasons


def settings(prompt, skills=None):
    skills = skills if skills is not None else load()
    _, hidden, _ = select(prompt, skills)
    return {'skillOverrides': {name: 'user-invocable-only' for name in hidden}}


def main():
    prompt = sys.stdin.read() if len(sys.argv) < 2 else ' '.join(sys.argv[1:])
    print(json.dumps(settings(prompt)))


if __name__ == '__main__':
    main()
