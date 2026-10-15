"""Temporal, lineage-aware split of routing episodes (z0int#143, z0evals#92). No random rows.

    train  < dev_start                    observed + synthetic (teacher rows only when every parent is in train)
    dev    [dev_start, eval_start)        observed only
    eval   [eval_start, eval_end)         observed only; the LOCKED window, fixed by date in a lock file
    recent >= eval_end                    observed only; "unseen recent replay" for the #143 gate

Rules, applied in order and every removal counted by reason:
  1. undated episodes and synthetic episodes whose parent is held out are quarantined;
  2. lineage: a group (session lineage) lives in one split only. Its episodes stay in their date window; when a group
     reaches a later held-out split, its episodes in earlier splits are quarantined (held-out windows stay date-pure);
  3. contamination: a content identity (``evidence.semantic_key``) present in a held-out split is removed from every
     lower-priority split (priority eval > dev > recent > train);
  4. near-duplicates: within a split the first episode per semantic hash is kept.

The manifest records the window, the counts, the contamination report and a residual-overlap check that must be 0.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .episodes import is_labelled

SCHEMA = 'z0int.episode_split_manifest.v0'
LOCK_SCHEMA = 'z0int.episode_eval_lock.v0'
SPLITS = ('train', 'dev', 'eval', 'recent')
HELD_OUT_PRIORITY = ('eval', 'dev', 'recent', 'train')  # who keeps a contested content identity


def _d(s: str | date) -> date:
    return s if isinstance(s, date) else date.fromisoformat(str(s))


def window_of(day: str | None, dev_start: date, eval_start: date, eval_end: date) -> str | None:
    if not day:
        return None
    d = _d(day[:10])
    if d < dev_start:
        return 'train'
    if d < eval_start:
        return 'dev'
    if d < eval_end:
        return 'eval'
    return 'recent'


class LockConflict(ValueError):
    pass


def resolve_lock(lock_path: Path, eval_start: str | None, eval_end: str | None) -> dict[str, Any]:
    """The locked eval window. The first split writes the lock; later splits must name the same window or none."""
    lock = None
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text())
        if (eval_start and eval_start != lock['eval_start']) or (eval_end and eval_end != lock['eval_end']):
            raise LockConflict(f"eval window is locked to [{lock['eval_start']}, {lock['eval_end']}) by "
                               f"{lock_path.name}; a different window needs a new lock (owner decision)")
        return lock
    if not (eval_start and eval_end):
        raise LockConflict('no eval lock yet: pass --eval-start and --eval-end (dates) to create it')
    if _d(eval_end) <= _d(eval_start):
        raise LockConflict('eval_end must be after eval_start')
    lock = {'schema': LOCK_SCHEMA, 'eval_start': str(_d(eval_start)), 'eval_end': str(_d(eval_end)),
            'locked_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'rule': 'fixed by date; held-out rows never feed training, prompt search or thresholds'}
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=1, sort_keys=True) + '\n')
    return lock


def split(episodes: Iterable[Mapping[str, Any]], *, eval_start: str, eval_end: str,
          dev_days: int = 7, provenance: str = 'observed') -> tuple[dict[str, list[str]], dict[str, Any]]:
    """One provenance per split: ``observed`` (with its synthetic teacher rows, train only) or ``reconstructed``
    (replay_reconstruct; its own split, never pooled with observed -- a mixed input is refused)."""
    if provenance not in ('observed', 'reconstructed'):
        raise ValueError(f'cannot split provenance {provenance!r}')
    eps = sorted(episodes, key=lambda e: (e.get('decided_at') or '~', e['episode_id']))
    if provenance == 'reconstructed' and any(e['provenance']['kind'] != 'reconstructed' for e in eps):
        raise ValueError('refusing to split reconstructed episodes together with observed/synthetic ones')
    es, ee = _d(eval_start), _d(eval_end)
    ds = es - timedelta(days=dev_days)
    removed: Counter = Counter()
    assign: dict[str, str] = {}
    by_id = {e['episode_id']: e for e in eps}

    # 1. date windows (observed first; synthetic follow their parents)
    for e in eps:
        if e['provenance']['kind'] != provenance:
            if e['provenance']['kind'] not in ('synthetic', provenance):
                removed[f"other_provenance:{e['provenance']['kind']}"] += 1
            continue
        w = window_of(e.get('day'), ds, es, ee)
        if w is None:
            removed['undated'] += 1
            continue
        assign[e['episode_id']] = w

    # 2. lineage: a group lives in its latest held-out split; earlier parts are quarantined
    group_splits: dict[str, set[str]] = {}
    for eid, w in assign.items():
        g = by_id[eid]['lineage'].get('group')
        if g:
            group_splits.setdefault(g, set()).add(w)
    order = {s: i for i, s in enumerate(SPLITS)}
    for eid in list(assign):
        g = by_id[eid]['lineage'].get('group')
        if not g or len(group_splits[g]) == 1:
            continue
        keep = max(group_splits[g], key=order.get)
        if assign[eid] != keep:
            removed[f'lineage_straddle:{assign[eid]}->{keep}'] += 1
            del assign[eid]

    for e in eps:  # synthetic: train only, and only when every parent landed in train
        if e['provenance']['kind'] != 'synthetic':
            continue
        parents = e['lineage'].get('parent_episode_ids') or []
        if any(assign.get(p) not in (None, 'train') for p in parents):
            removed['synthetic_parent_held_out'] += 1
            continue
        if parents and any(p not in assign for p in parents):
            removed['synthetic_parent_quarantined'] += 1
            continue
        w = window_of(e.get('day'), ds, es, ee)
        if w != 'train':
            removed['synthetic_outside_train_window' if w else 'undated'] += 1
            continue
        assign[e['episode_id']] = 'train'

    # 3. contamination by content identity across splits
    owner: dict[str, str] = {}
    for s in HELD_OUT_PRIORITY:
        for eid, w in assign.items():
            k = by_id[eid]['evidence'].get('semantic_key')
            if w == s and k and k not in owner:
                owner[k] = s
    for eid in list(assign):
        k = by_id[eid]['evidence'].get('semantic_key')
        if k and owner.get(k) != assign[eid]:
            removed[f'content_contamination:{assign[eid]}->{owner[k]}'] += 1
            del assign[eid]

    # 4. near-duplicates within a split
    seen: set = set()
    for e in eps:
        eid = e['episode_id']
        if eid not in assign:
            continue
        key = (assign[eid], e['hashes']['semantic'])
        if key in seen:
            removed[f'semantic_dup:{assign[eid]}'] += 1
            del assign[eid]
            continue
        seen.add(key)

    ids = {s: [eid for eid in by_id if assign.get(eid) == s] for s in SPLITS}
    residual = _residual_overlap(ids, by_id, provenance)
    manifest = {
        'schema': SCHEMA, 'locked_eval_window': {'start': str(es), 'end': str(ee)},
        'dev_window': {'start': str(ds), 'end': str(es)}, 'train_window': {'end': str(ds)},
        'recent_window': {'start': str(ee)},
        'counts': {s: _counts([by_id[i] for i in ids[s]]) for s in SPLITS},
        'removed': dict(sorted(removed.items())), 'residual_overlap': residual,
        'ids_sha256': {s: hashlib.sha256('\n'.join(ids[s]).encode()).hexdigest() for s in SPLITS},
        'rules': ['temporal windows by decided_at (UTC day)', 'lineage group in one split only',
                  'content identity owned by the highest-priority split (eval>dev>recent>train)',
                  'first episode per semantic hash within a split', 'synthetic rows: train only, parents in train'],
    }
    if any(residual.values()):
        raise AssertionError(f'split leaked across held-out boundary: {residual}')
    return ids, manifest


def _residual_overlap(ids: Mapping[str, list[str]], by_id: Mapping[str, Mapping[str, Any]],
                      provenance: str = 'observed') -> dict[str, int]:
    def keys(s: str, f) -> set:
        return {f(by_id[i]) for i in ids[s]} - {None}
    group = lambda e: e['lineage'].get('group')  # noqa: E731
    content = lambda e: e['evidence'].get('semantic_key')  # noqa: E731
    out = {}
    for held in ('dev', 'eval', 'recent'):
        out[f'train∩{held}:group'] = len(keys('train', group) & keys(held, group))
        out[f'train∩{held}:content'] = len(keys('train', content) & keys(held, content))
    out['dev∩eval:group'] = len(keys('dev', group) & keys('eval', group))
    out['eval:synthetic'] = sum(1 for i in ids['eval'] if by_id[i]['provenance']['kind'] != provenance)
    out['any:mixed_provenance'] = len({by_id[i]['provenance']['kind'] for s in SPLITS for i in ids[s]}
                                      - {provenance, 'synthetic' if provenance == 'observed' else provenance})
    return out


def _counts(eps: list[Mapping[str, Any]]) -> dict[str, Any]:
    days = sorted(e['day'] for e in eps if e.get('day'))
    return {'episodes': len(eps), 'labelled': sum(map(is_labelled, eps)),
            'outcome_observed': sum(1 for e in eps if (e.get('outcome') or {}).get('observed')),
            'synthetic': sum(1 for e in eps if e['provenance']['kind'] == 'synthetic'),
            'groups': len({e['lineage'].get('group') for e in eps}),
            'by_family': dict(sorted(Counter(e['family'] for e in eps).items())),
            'labelled_by_family': dict(sorted(Counter(e['family'] for e in eps if is_labelled(e)).items())),
            'first_day': days[0] if days else None, 'last_day': days[-1] if days else None}
