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


def families_of(skills):
    fams = {}
    for name, desc in skills.items():
        fams.setdefault(family(name, desc), []).append(name)
    return fams


def named_families(prompt, skills):
    """The lexical 'named' rule of select(): a family or one of its skills is named in the prompt."""
    lowered = prompt.lower()
    return {fam for fam, names in families_of(skills).items()
            if fam in lowered or any(re.search(r'(^|[\s/])' + re.escape(n) + r'\b', lowered) for n in names)}


def _clean(desc):
    return desc.lstrip('>|"\' ').rstrip('"\' ')


def _first_sentence(desc, cap):
    text = _clean(desc)
    cut = re.split(r'(?<=[.!?])\s', text, maxsplit=1)[0]
    return (cut if len(cut) <= cap else cut[:cap].rsplit(' ', 1)[0]).rstrip('.:;, ')


def family_description(fam, names, skills, cap=110):
    """Compact, mechanically derived family description (no hand-written per-family text)."""
    if len(names) == 1:
        return _first_sentence(skills[names[0]], 240)
    parts = [f"{n}: {_first_sentence(skills[n], cap)}" for n in sorted(names)]
    return '; '.join(parts)


def semantic_questions(skills, mode='family'):
    """DecisionQuestions keyed back to families. mode='family': one boolean per family;
    mode='skill': one boolean per skill (family score = max over its skills)."""
    from z0int.backends.base import DecisionQuestion
    no = 'no, the task can be completed without these skills'
    yes = 'yes, these skills would plausibly be needed'
    qs, owner = [], {}
    for i, (fam, names) in enumerate(sorted(families_of(skills).items())):
        if mode == 'family':
            units = [(f'f{i}', fam, family_description(fam, names, skills))]
        else:
            units = [(f'f{i}s{j}', n, _clean(skills[n])[:300]) for j, n in enumerate(sorted(names))]
        for qid, label, desc in units:
            qs.append(DecisionQuestion(
                id=qid, type='boolean', false_criterion=no, true_criterion=yes,
                instructions=f"Would completing this task plausibly require the {label} skills ({desc})?"))
            owner[qid] = fam
    return tuple(qs), owner


def semantic_scores(prompt, skills, backend, mode='family'):
    """{family: P(yes)} from one DecisionBackend request, plus the DecisionResult."""
    from z0int.backends.base import DecisionRequest
    qs, owner = semantic_questions(skills, mode)
    state = f"Task given to a coding agent (Claude Code): {prompt.strip()}"
    result = backend.evaluate(DecisionRequest(state=state, questions=qs))
    scores = {}
    for a in result.answers:
        fam = owner[a.question_id]
        scores[fam] = max(scores.get(fam, 0.0), float(a.probabilities.get('true', 0.0)))
    return scores, result


def decide_semantic(prompt, skills, scores, threshold, hybrid=True):
    """Pure thresholding over precomputed scores. scores=None means backend failure -> expose all."""
    fams = families_of(skills)
    if scores is None or set(scores) != set(fams):
        return sorted(skills), [], {'*': 'abstain: backend unavailable'}
    named = named_families(prompt, skills) if hybrid else set()
    exposed, reasons = set(), {}
    for fam, names in fams.items():
        if fam in named:
            exposed.update(names); reasons[fam] = 'named'
        elif scores[fam] >= threshold:
            exposed.update(names); reasons[fam] = f'p={scores[fam]:.3f}'
    return sorted(exposed), sorted(set(skills) - exposed), reasons


_BACKEND = None


def get_backend(name='decider_2b'):
    global _BACKEND
    if _BACKEND is None:
        from z0int.backends.registry import create_backend
        _BACKEND = create_backend(name)
    return _BACKEND


def select_semantic(prompt, skills, threshold=0.05, backend=None, mode='family', hybrid=True):
    """Semantic family gate via a DecisionBackend: expose a family when P(yes) >= threshold
    (or, with hybrid, when the lexical 'named' rule fires). Any backend failure abstains and
    exposes everything, so the selector can only ever cost tokens, never hide on error."""
    try:
        scores, _ = semantic_scores(prompt, skills, backend or get_backend(), mode)
    except Exception as exc:  # noqa: BLE001 - fail open by design
        print(f'claude_code_skills: semantic selector abstained: {exc!r}', file=sys.stderr)
        scores = None
    return decide_semantic(prompt, skills, scores, threshold, hybrid)


def settings(prompt, skills=None, selector='lexical', **kw):
    skills = skills if skills is not None else load()
    if selector == 'lexical':
        _, hidden, _ = select(prompt, skills)
    else:
        _, hidden, _ = select_semantic(prompt, skills, hybrid=(selector == 'hybrid'), **kw)
    return {'skillOverrides': {name: 'user-invocable-only' for name in hidden}}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Print a Claude Code --settings JSON hiding unneeded skill families.')
    ap.add_argument('--selector', choices=['lexical', 'semantic', 'hybrid'], default='lexical')
    ap.add_argument('--threshold', type=float, default=0.05)
    ap.add_argument('--mode', choices=['family', 'skill'], default='family')
    ap.add_argument('prompt', nargs='*')
    args = ap.parse_args(argv)
    prompt = ' '.join(args.prompt) if args.prompt else sys.stdin.read()
    kw = {} if args.selector == 'lexical' else {'threshold': args.threshold, 'mode': args.mode}
    print(json.dumps(settings(prompt, selector=args.selector, **kw)))


if __name__ == '__main__':
    main()
