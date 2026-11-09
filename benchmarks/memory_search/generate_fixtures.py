"""Generate the frozen HC-2 benchmark fixtures (synthetic only, no owner text).

Run once with a fixed seed to produce ``fixtures/*.json``. The committed JSON output, not this
script, is the frozen fixture: re-running only needs to happen if the fixture shape changes, and
any such re-run must be reviewed like any other fixture update.

Usage: python3 benchmarks/memory_search/generate_fixtures.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 1234
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / 'fixtures'

VOCAB = [
    'quokka', 'gateway', 'ledger', 'bridge', 'kernel', 'scrub', 'temporal', 'lexical', 'snapshot', 'claim',
    'receipt', 'brief', 'policy', 'harness', 'worker', 'capsule', 'token', 'cache', 'index', 'vector',
    'manifest', 'profile', 'router', 'cluster', 'shard', 'replica', 'queue', 'signal', 'beacon', 'orbit',
    'lattice', 'prism', 'courier', 'anchor', 'relay', 'vault', 'drift', 'tether', 'ember', 'coral',
]

# Credential *shapes* only; every value below is an obviously synthetic placeholder, never a real secret.
SECRET_SHAPES = [
    'sk-' + 'FAKE' * 6,
    'AKIA' + 'FAKEFAKEFAKEFAKE',
    'ghp_' + 'F4K3F4K3F4K3F4K3F4K3',
    'xai-' + 'F4K3TOKENVALUE0000000000',
    'Bearer ' + 'fakebearer' + 'x' * 20,
    'api_key: ' + 'FakeApiKeyValue1234567890',
]


def _sentence(rng: random.Random, n: int) -> str:
    words = [rng.choice(VOCAB) for _ in range(n)]
    return ' '.join(words)


def build_corpus(rng: random.Random, n: int = 220) -> list[str]:
    out = []
    for i in range(n):
        sentence = _sentence(rng, rng.randint(4, 9))
        if i % 17 == 0:  # a minority of entries carry a synthetic credential shape (scrub coverage)
            sentence = f'{sentence} {SECRET_SHAPES[i % len(SECRET_SHAPES)]} {_sentence(rng, 3)}'
        out.append(sentence)
    return out


def build_queries(rng: random.Random, n: int = 50) -> list[dict]:
    out = []
    for _ in range(n):
        terms = rng.sample(VOCAB, rng.choice((1, 2, 3)))
        out.append({'query': ' '.join(terms), 'project': 'z0'})
    return out


def build_event_payloads(rng: random.Random, corpus: list[str], n: int = 6400) -> list[dict]:
    out = []
    for i in range(n):
        out.append({
            'event_type': 'memory.note',
            'source': 'bench',
            'payload': {'text': corpus[i % len(corpus)], 'seq': i},
        })
    return out


def build_av_messages(rng: random.Random, corpus: list[str], n_sessions: int = 150) -> list[dict]:
    agents = ('claude', 'hermes', 'codex', 'omp', 'deepseek-harness')
    out = []
    for s in range(n_sessions):
        n_msgs = rng.randint(1, 5)
        msgs = []
        for m in range(n_msgs):
            role = 'user' if m % 2 == 0 else 'assistant'
            msgs.append([role, corpus[(s * 7 + m) % len(corpus)]])
        out.append({'session_id': f'bench-s{s}', 'agent': agents[s % len(agents)], 'project': 'z0',
                    'cwd': '/w/z0', 'messages': msgs})
    return out


def main() -> None:
    rng = random.Random(SEED)
    corpus = build_corpus(rng)
    queries = build_queries(rng)
    events = build_event_payloads(rng, corpus)
    av_messages = build_av_messages(rng, corpus)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / 'corpus.json').write_text(json.dumps(corpus, indent=0), encoding='utf-8')
    (FIXTURES / 'queries.json').write_text(json.dumps(queries, indent=0), encoding='utf-8')
    (FIXTURES / 'event_payloads.json').write_text(json.dumps(events, indent=0), encoding='utf-8')
    (FIXTURES / 'av_messages.json').write_text(json.dumps(av_messages, indent=0), encoding='utf-8')
    print(f'wrote {len(corpus)} corpus entries, {len(queries)} queries, {len(events)} events, '
          f'{len(av_messages)} sessions to {FIXTURES}')


if __name__ == '__main__':
    main()
