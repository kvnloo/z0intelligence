"""Skill-cards router step 1 (best-way-map ROUTER-DESIGN.md): flag OFF by default, and the time-split replay gate."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'harness-adapters/hermes-owner-corrections/__init__.py'
ORIENTATION = ROOT / 'src/z0int/orientation.py'
SCRIPT = ROOT / 'scripts/skill_cards_timesplit.py'

CARD = {'fact': "Re-read his earlier asks before asking. When He says 'I told you' or 'check convo history' -> Use "
                '`owner-sent-resources`, `session-recovery` (not when: fresh topic)',
        'terms': ['I told you', 'check convo history', 'history'], 'source': 'card:check-history-first', 'domain': 'D3'}
OTHER = {'fact': 'Render short videos. When he asks for a video clip or frames -> Use `fframes-video-via-fframestudio`',
         'terms': ['video', 'frames', 'clip'], 'source': 'card:video', 'domain': 'D9'}
# Unrelated cards so BM25 idf looks like the real ~60-card corpus (the reader's min score is 5).
FILLER = [{'fact': f'When topic{i} alpha{i} beta{i} gamma{i} -> Use `filler-{i}`', 'terms': [f'delta{i}'],
           'source': f'card:filler-{i}'} for i in range(30)]
CORRECTION = {'fact': 'Put local models under /mnt/zer0models before downloading', 'ts': '2026-09-18', 'source': 'omp'}
PROMPT = "i told you already, check convo history before you ask me again"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self, settings):
        self.settings = settings

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, *_):
        pass


def jsonl(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return path


class SkillCardsFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cards = jsonl(self.tmp / 'CARDS.jsonl', [CARD, OTHER, *FILLER])
        self.corr = jsonl(self.tmp / 'CORRECTIONS.jsonl', [CORRECTION])
        self.env = patch.dict(os.environ, {'Z0INT_CORRECTIONS': str(self.corr)})
        self.env.start()
        self.p = load('owner_corrections_under_test', PLUGIN)
        self.p.READER, self.p.CARDS, self.p._orientation = ORIENTATION, self.cards, None

    def tearDown(self):
        self.env.stop()

    def hook(self):
        out = self.p.owner_context_hook(session_id='', user_message=PROMPT)
        return (out or {}).get('context', '')

    def test_default_off_without_settings(self):
        self.p.register(Ctx({}))
        self.assertNotIn('<skill-cards>', self.hook())

    def test_explicit_false_and_truthy_strings_stay_off(self):
        for value in (False, 'true', 1, None):
            self.p.register(Ctx({'skill_cards': value}))
            self.assertNotIn('<skill-cards>', self.hook(), value)

    def test_on_injects_matching_card_only(self):
        self.p.register(Ctx({'skill_cards': True}))
        ctx = self.hook()
        self.assertIn('<skill-cards>', ctx)
        block = ctx.split('<skill-cards>')[1]
        self.assertIn('owner-sent-resources', block)
        self.assertNotIn('fframes', block)
        self.assertNotIn('<owner-corrections>', block)

    def test_settings_path_overrides_default(self):
        other = jsonl(self.tmp / 'other.jsonl', [OTHER, *FILLER])
        self.p.register(Ctx({'skill_cards': True, 'skill_cards_path': str(other)}))
        self.assertNotIn('<skill-cards>', self.hook())

    def test_owner_text_strips_card_block(self):
        self.p.register(Ctx({'skill_cards': True}))
        self.assertEqual(self.p.owner_text(self.hook() + '\nhello'), self.p.owner_text('hello'))

    def test_missing_cards_file_injects_nothing(self):
        self.p.register(Ctx({'skill_cards': True, 'skill_cards_path': str(self.tmp / 'missing.jsonl')}))
        self.assertNotIn('<skill-cards>', self.hook())


class TimeSplitReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ts = load('skill_cards_timesplit_under_test', SCRIPT)
        hits = [  # before the split: teaches kNN and popularity
            {'source': 'hermes:clean', 'session': 's1', 'timestamp': '2026-10-01T00:00:00+00:00',
             'text': 'run the dream loop on the quackles mesh overnight'},
            {'source': 'hermes:clean', 'session': 's2', 'timestamp': '2026-10-02T00:00:00+00:00',
             'text': 'use agent reach to read that reddit thread'},
            {'source': 'hermes:clean', 'session': 's3', 'timestamp': '2026-10-03T00:00:00+00:00',
             'text': 'use agent reach to look up what people say on x'},
            {'source': 'agentsview:chatgpt', 'session': 'x', 'timestamp': '2026-10-04T00:00:00+00:00',
             'text': 'dream loop chatgpt turn is never a case'},
            # after the split: scored only
            {'source': 'hermes:clean', 'session': 's4', 'timestamp': '2026-10-20T00:00:00+00:00',
             'text': 'dreamloop the quackles mesh again'},
            {'source': 'hermes:clean', 'session': 's5', 'timestamp': '2026-10-21T00:00:00+00:00',
             'text': 'make a fframes video clip of the frames'},
            {'source': 'hermes:clean', 'session': 's6', 'timestamp': '2026-10-22T00:00:00+00:00',
             'text': 'agent-reach that reddit thread for me'},
        ]
        self.hits = jsonl(self.tmp / 'hits.jsonl', hits)
        self.cards = jsonl(self.tmp / 'cards.jsonl', [CARD, OTHER, *FILLER])

    def test_split_never_scores_pool_turns(self):
        cs = self.ts.cases([json.loads(x) for x in self.hits.read_text().splitlines()])
        pool, test = self.ts.split(cs, '2026-10-15')
        self.assertEqual([c['session'] for c in pool], ['s1', 's2', 's3'])
        self.assertEqual([c['session'] for c in test], ['s4', 's5', 's6'])
        self.assertTrue(all(c['ts'] < '2026-10-15' for c in pool))
        self.assertTrue(all('dream' not in c['q'].lower() for c in cs if c['gold'] == 'dream-loop'))

    def test_replay_scores_and_gate(self):
        out = self.tmp / 'res.json'
        self.assertEqual(self.ts.main(['--hits', str(self.hits), '--cards', str(self.cards),
                                       '--since', '2026-10-15', '--out', str(out)]), 0)
        res = json.loads(out.read_text())
        self.assertEqual((res['pool'], res['test']), (3, 3))
        r = res['routers']
        self.assertEqual(r['cards']['hit@3'], 0.333)       # only the video card matches its own case
        self.assertEqual(r['knn']['hit@3'], 0.667)         # dream-loop and agent-reach recalled from the pool
        self.assertEqual(r['cards_knn']['hit@3'], 1.0)
        self.assertEqual(r['popularity']['hit@3'], 0.667)  # fframes never appears before the split
        self.assertTrue(res['gate']['promote'])

    def test_gate_rejects_below_twice_skill_rank(self):
        res = {'cards_knn': {'n': 10, 'hit@3': 0.5}, 'popularity': {'hit@3': 0.4}, 'skill_rank': {'hit@3': 0.3}}
        self.assertFalse(self.ts.gate(res)['promote'])
        res['skill_rank']['hit@3'] = 0.25
        self.assertTrue(self.ts.gate(res)['promote'])


if __name__ == '__main__':
    unittest.main()
