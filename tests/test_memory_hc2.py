"""HC-2 (Z19): memory search speedups must not change results.

Scrub: a pattern is skipped only when a substring every match needs is absent, so output stays byte-identical.
Temporal layer: parsed events are cached per (path, size, mtime_ns) and re-read after any append.
Synthetic fixtures only.
"""
from __future__ import annotations

import random

from z0int.memory import scrub
from z0int.memory import surface as ms
from z0int.memory.event_log import EventLog


def _reference(text: str) -> tuple[str, int]:
    n = 0
    for rx in scrub._SECRET_RES:
        text, k = rx.subn(scrub.REDACTED, text)
        n += k
    return text, n


EDGE = [
    '', 'plain words only', 'max_tokens: 600', 'HF_TOKEN=abcdefgh12345678', '"access_token": "zzzzzzzzzzzz"',
    'Bearer abcdefghijklmnopqrstuvwx', 'sk-' + 'a' * 20, 'ghp_' + 'b' * 24, 'AKIA' + 'C' * 16, 'AIza' + 'd' * 22,
    'xoxb-1234567890ab', 'gsk_' + 'e' * 24, 'github_pat_' + 'f' * 24, '0.12345678-1234-1234-1234-123456789012.abcdefgh:abcdefgh',
    '-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----', '-----BEGIN PRIVATE KEY-----\nMIIBcut',
    'ſecret=abcdefghijk',        # long s matches (?i)s in re but not str.lower()
    'api\u212aey=abcdefghijk',     # Kelvin sign matches (?i)k
    'PASSWD: hunter2hunter2', 'TOKEN = [redacted:credential] then token=realvalue123',
]
FRAG = ['key', 'token', 'secret', 'passwd', 'bearer ', 'sk-', 'gh', 'p_', 'AKIA', 'AIza', 'xox', 'b-', 'hf_', '0.',
        '-', ':', '=', '"', ' ', 'ſ', '\u212a', 'é', 'a1B2c3D4e5F6g7H8', 'ZZZZZZZZZZZZZZZZ', '\n', '-----BEGIN ', 'PRIVATE KEY-----']


def test_scrub_identical_to_reference():
    rng = random.Random(19)
    corpus = EDGE + [''.join(rng.choice(FRAG) for _ in range(rng.randint(1, 40))) for _ in range(4000)]
    for t in corpus:
        assert scrub.scrub_text(t) == _reference(t), repr(t)


def test_scrub_runs_no_pattern_on_clean_text(monkeypatch):
    calls = []

    class Counting:
        def __init__(self, rx):
            self.rx = rx

        def subn(self, *a, **k):
            calls.append(self.rx.pattern)
            return self.rx.subn(*a, **k)

    monkeypatch.setattr(scrub, '_SECRET_RES', tuple(Counting(rx) for rx in scrub._SECRET_RES))
    assert scrub.scrub_text('the gateway reloads plugins at restart') == ('the gateway reloads plugins at restart', 0)
    assert calls == []
    assert scrub.scrub_text('HF_TOKEN=abcdefgh12345678')[1] == 1


def _ledger(tmp_path, n):
    log = EventLog(tmp_path / 'ledger')
    for i in range(n):
        log.append('note', {'text': f'physarum note {i}'}, source='test', session_id='s1')
    return log


def test_temporal_cache_hit_and_invalidation(tmp_path, monkeypatch):
    log = _ledger(tmp_path, 3)
    reads = []
    orig = EventLog.iter_events

    def counting(self, *a, **k):
        reads.append(1)
        return orig(self, *a, **k)

    monkeypatch.setattr(EventLog, 'iter_events', counting)
    _, first, _ = ms._temporal_candidates('physarum', ledger_root=log.root, join_uids=set())
    _, again, _ = ms._temporal_candidates('physarum', ledger_root=log.root, join_uids=set())
    assert again == first and len(first) == 3
    assert len(reads) == 1                      # unchanged log: served from cache
    log.append('note', {'text': 'physarum note new'}, source='test', session_id='s1')
    _, after, _ = ms._temporal_candidates('physarum', ledger_root=log.root, join_uids=set())
    assert len(after) == 4 and len(reads) == 2  # append invalidates
