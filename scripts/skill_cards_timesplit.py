#!/usr/bin/env python3
"""Time-split promotion gate for the skill-cards router (best-way-map ROUTER-DESIGN.md, "Eval to keep").

Cases are Kevin's own turns that NAME a skill (hits.jsonl). The name is masked; a router must recover it in
its top 3. Everything a router learns from (kNN pool, popularity) comes from turns BEFORE --since; only
turns on/after --since are scored. Cards are served through the live plugin path (owner-corrections
skill_cards -> orientation.corrections over CARDS.jsonl), so this replays what the flag would inject.

Promote only if hit@3(cards+kNN) >= 2 x hit@3(skill-rank) and >= hit@3(popularity floor). Offline, no model.
"""
import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'harness-adapters/hermes-owner-corrections/__init__.py'
ORIENTATION = ROOT / 'src/z0int/orientation.py'
GOLD = [  # (mention regex, gold skill), same list as best-way-map router_eval.py
    (r'agent[- ]?reach', 'agent-reach'), (r'dream\s*loop|dreamloop', 'dream-loop'), (r'pstack|poteto', 'poteto-mode'),
    (r'fframes', 'fframes-video-via-fframestudio'), (r'maestro', 'maestro-local-android'),
    (r'taste\s+skill', 'design-taste-frontend'), (r'abab', 'abab-meta-research'), (r'asd-ste\s*100', 'asd-ste100'),
    (r'comfyui\s+skill', 'comfyui'), (r'remotion', 'fframes-video-via-fframestudio'),
    (r'hyprland\s+testing\s+skill|multi\s+cursor\s+support', 'focus-safe-desktop-automation'),
    (r'skill\s+for\s+hf', 'huggingface-hub-storage'), (r'uiautomator', 'phone-harness-android-fast-tree'),
]
SKIP_SOURCES = ('agentsview:chatgpt', 'agentsview:grok')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cases(hits, catalog=None):
    """Masked skill-naming turns: [{ts, session, q, gold}], one per distinct masked text."""
    out, seen = [], set()
    for h in hits:
        if h.get('source', '').startswith(SKIP_SOURCES):
            continue
        t = ' '.join((h.get('text') or '').split())
        for rx, gold in GOLD:
            if re.search(rx, t, re.I) and (catalog is None or gold in catalog):
                q = re.sub(rx, ' ', t, flags=re.I)
                q = re.sub(re.escape(gold.replace('-', ' ')), ' ', q, flags=re.I)
                if q.lower()[:120] not in seen:
                    seen.add(q.lower()[:120])
                    out.append({'ts': h.get('timestamp') or '', 'session': h.get('session'), 'q': q, 'gold': gold})
                break
    return out


def split(cs, since):
    """Undated turns go to the pool side only: they can teach but are never scored."""
    return [c for c in cs if c['ts'] < since], [c for c in cs if c['ts'] >= since]


class BM25:
    def __init__(self, docs, tokens):
        self.tokens, self.docs = tokens, [(lab, Counter(tokens(t))) for lab, t in docs]
        self.avg = sum(sum(c.values()) for _, c in self.docs) / max(1, len(self.docs))
        df = Counter(w for _, c in self.docs for w in c)
        n = len(self.docs)
        import math
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def rank(self, q):
        qs, scored = set(self.tokens(q)), []
        for i, (lab, c) in enumerate(self.docs):
            ln = sum(c.values())
            s = sum(self.idf[w] * c[w] * 2.2 / (c[w] + 1.2 * (0.25 + 0.75 * ln / self.avg)) for w in qs if w in c)
            if s > 0:
                scored.append((s, -i, lab))
        return [lab for _, _, lab in sorted(scored, reverse=True)]


def routers(pool, cards_path, catalog, plugin, tokens, skill_rank=None):
    knn = BM25([(c['gold'], c['q']) for c in pool], tokens)
    popular = [g for g, _ in Counter(c['gold'] for c in pool).most_common(3)]

    def cards(q):
        text = plugin.skill_cards(q, cards_path)
        names = []
        for n in re.findall(r'`([^`]+)`', text):
            if n not in names and (catalog is None or n in catalog):
                names.append(n)
        return names[:3]

    def knn_top(q):
        votes = []
        for g in knn.rank(q)[:5]:
            if g not in votes:
                votes.append(g)
        return votes[:3]

    def fused(q):
        e = knn_top(q)[:2]
        return (e + [s for s in cards(q) if s not in e])[:3]

    r = {'cards': cards, 'knn': knn_top, 'cards_knn': fused, 'popularity': lambda q: popular}
    if skill_rank is not None:
        r['skill_rank'] = lambda q: skill_rank(q)[:3]
    return r


def evaluate(test, rs):
    return {name: {'n': len(test), 'hit@3': round(sum(c['gold'] in fn(c['q']) for c in test) / max(1, len(test)), 3)}
            for name, fn in rs.items()}


def gate(res):
    fused, pop = res['cards_knn']['hit@3'], res['popularity']['hit@3']
    base = res.get('skill_rank', {}).get('hit@3')
    ok = fused >= pop and (base is None or fused >= 2 * base)
    return {'promote': bool(ok and res['cards_knn']['n'] > 0), 'cards_knn': fused, 'popularity': pop, 'skill_rank': base}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--hits', required=True)
    ap.add_argument('--cards', required=True)
    ap.add_argument('--since', default='2026-10-15')
    ap.add_argument('--catalog', help='catalog.json (skill names); omit to accept any name')
    ap.add_argument('--skill-rank', help='skill-rank plugin __init__.py for the baseline router')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    plugin = load('owner_corrections_plugin', PLUGIN)
    plugin.READER = ORIENTATION
    orientation = load('z0int_orientation_eval', ORIENTATION)
    catalog = {x['name'] for x in json.load(open(a.catalog))} if a.catalog else None
    hits = [json.loads(line) for line in open(a.hits) if line.strip()]
    pool, test = split(cases(hits, catalog), a.since)
    ranker = None
    if a.skill_rank:
        sr = load('skill_rank_eval', a.skill_rank)
        ranker = sr.index().rank
    res = evaluate(test, routers(pool, a.cards, catalog, plugin, orientation._tokens, ranker))
    out = {'since': a.since, 'pool': len(pool), 'test': len(test),
           'test_gold': dict(Counter(c['gold'] for c in test)), 'routers': res, 'gate': gate(res)}
    text = json.dumps(out, indent=1)
    if a.out:
        Path(a.out).write_text(text + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
