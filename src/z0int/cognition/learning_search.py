"""Per-family experiment scheduling and canonical evidence publication.

The integration owner applies returned strategy configs to real predictions. A
selection is NOT a completed experiment: only observations explicitly tagged
with that strategy start its trial. This module never predicts, trains, probes
endpoints, or infers live evidence from the age of a receipt. Use the caller's
SQLite transaction/learner lock; no existing learner tables are modified.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SearchConfig:
    plateau_ticks: int = 3
    min_improvement: float = .001
    credit_bar: float = .8
    min_observations: int = 1
    finding_ttl_s: float = 14 * 86400

    def __post_init__(self):
        if (type(self.plateau_ticks) is not int or type(self.min_observations) is not int
                or self.plateau_ticks < 2 or self.min_observations < 1):
            raise ValueError('plateau_ticks >= 2 and min_observations >= 1 required')
        if not 0 <= self.min_improvement <= 1 or not 0 <= self.credit_bar <= 1:
            raise ValueError('search thresholds must be probabilities')
        if not math.isfinite(self.finding_ttl_s) or self.finding_ttl_s <= 0:
            raise ValueError('finding TTL must be positive and finite')


# These are proposals, never an assertion that a configured executor ran them.
# The owner may supply a different ordered catalogue with the same structure.
DEFAULT_STRATEGIES = (
    {'id': 'history-1', 'kind': 'bounded_projection',
     'config': {'history_width': 1, 'candidate_k': 3, 'max_options': 3, 'prompt_bytes': 3000,
                'narrowing': 'deterministic', 'decision_method': 'per_candidate_binary_logit',
                'abstain': 'top_margin'}},
    {'id': 'history-3', 'kind': 'bounded_projection',
     'config': {'history_width': 3, 'candidate_k': 3, 'max_options': 3, 'prompt_bytes': 3000,
                'narrowing': 'deterministic', 'decision_method': 'per_candidate_binary_logit',
                'abstain': 'top_margin'}},
    {'id': 'projection-recent-sessions', 'kind': 'bounded_projection',
     'config': {'history_width': 8, 'candidate_k': 3, 'max_options': 3, 'prompt_bytes': 3000,
                'candidate_source': 'recent_sessions', 'recent_sessions_n': 5,
                'score_full_legal_set': True, 'narrowing': 'deterministic',
                'decision_method': 'per_candidate_binary_logit', 'abstain': 'top_margin'}},
    {'id': 'few-shot-3', 'kind': 'few_shot_retrieval',
     'config': {'history_width': 8, 'candidate_k': 3, 'max_options': 3, 'prompt_bytes': 3000,
                'retrieval_k': 3, 'max_examples': 512, 'previous_only': True,
                'narrowing': 'deterministic', 'decision_method': 'per_candidate_binary_logit',
                'abstain': 'top_margin'}},
    {'id': 'methods-threeway-v1', 'kind': 'bounded_projection',
     'config': {'history_width': 1, 'candidate_k': 3, 'max_options': 3, 'prompt_bytes': 3000,
                'narrowing': 'deterministic', 'decision_method': 'slm_threeway_v1',
                'abstain': 'missing_logits'}},
    {'id': 'methods-hierarchy-v1', 'kind': 'bounded_projection',
     'config': {'history_width': 1, 'candidate_k': 3, 'max_options': 3, 'prompt_bytes': 3000,
                'narrowing': 'deterministic', 'decision_method': 'semantic_hierarchy_binary_v1',
                'abstain': 'top_margin'}},
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def wilson_interval(correct: int, n: int) -> tuple[float, float]:
    if type(n) is not int or type(correct) is not int or not 0 <= correct <= n:
        raise ValueError('integer counts must satisfy 0 <= correct <= n')
    if not n:
        return 0., 1.
    z = 1.959963984540054
    p = correct / n
    denominator = 1 + z*z/n
    center = (p + z*z/(2*n)) / denominator
    radius = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denominator
    return max(0., center-radius), min(1., center+radius)


def initialize(db: sqlite3.Connection) -> None:
    """Create only independent search tables, without committing caller work."""
    statements = (
        '''CREATE TABLE IF NOT EXISTS learning_search_families(
          family TEXT PRIMARY KEY,current_strategy TEXT,state TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS learning_search_trials(
          family TEXT,strategy TEXT,body TEXT NOT NULL,PRIMARY KEY(family,strategy))''',
        '''CREATE TABLE IF NOT EXISTS learning_search_curves(
          tick_id TEXT,family TEXT,strategy TEXT,arm TEXT,body TEXT NOT NULL,
          PRIMARY KEY(tick_id,family,strategy,arm))''',
        '''CREATE INDEX IF NOT EXISTS learning_search_curve_trial
          ON learning_search_curves(family,strategy)''',
        '''CREATE TABLE IF NOT EXISTS learning_search_ticks(
          tick_id TEXT PRIMARY KEY,body TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS learning_search_events(
          id INTEGER PRIMARY KEY,family TEXT,tick_id TEXT,body TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS learning_search_proposals(
          family TEXT PRIMARY KEY,body TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS learning_search_findings(
          tick_id TEXT,family TEXT,strategy TEXT,arm TEXT,evidence_class TEXT,
          finding_id TEXT NOT NULL,
          PRIMARY KEY(tick_id,family,strategy,arm,evidence_class))''',
    )
    for statement in statements:
        db.execute(statement)


def _validate_strategy(strategy: Mapping) -> dict:
    result = dict(strategy)
    if not isinstance(result.get('id'), str) or not result['id']:
        raise ValueError('strategy id required')
    kind = result.get('kind')
    if kind not in {'bounded_projection', 'few_shot_retrieval', 'larger_model'}:
        raise ValueError('unknown strategy kind')
    config = result.get('config')
    if not isinstance(config, dict):
        raise ValueError('strategy config required')
    max_options = config.get('max_options', 3)
    cap = 5 if config.get('model_size_b') == 1.7 and 'max_options' in config else 3
    if type(max_options) is not int or not 1 <= max_options <= cap:
        raise ValueError('small-model max_options cap is 3; explicit 1.7B may use 5')
    for key, ceiling in (('history_width', 64), ('candidate_k', max_options), ('prompt_bytes', 65536)):
        if type(config.get(key)) is not int or not 1 <= config[key] <= ceiling:
            raise ValueError(f'{key} must be explicitly bounded to {ceiling}')
    if config.get('candidate_source') == 'recent_sessions':
        if type(config.get('recent_sessions_n')) is not int or not 1 <= config['recent_sessions_n'] <= 64:
            raise ValueError('recent-session projection needs a bounded session count')
        if config.get('score_full_legal_set') is not True:
            raise ValueError('candidate projection must not narrow the legal scoring set')
    if kind == 'few_shot_retrieval':
        if config.get('previous_only') is not True:
            raise ValueError('retrieval must exclude current and future labels')
        if not (type(config.get('retrieval_k')) is int and
                type(config.get('max_examples')) is int and
                1 <= config['retrieval_k'] <= config['max_examples'] <= 512):
            raise ValueError('retrieval must be bounded to preceding 512 examples')
    if kind == 'larger_model' and not result.get('endpoint'):
        raise ValueError('larger_model must name an explicitly configured endpoint')
    _json(result)
    return result


def _blocked(strategy: dict, endpoints: Mapping[str, dict], availability: Mapping[str, str]) -> str | None:
    reason = availability.get(strategy['id'])
    if reason:
        return str(reason)
    if strategy['kind'] != 'larger_model':
        return None
    endpoint = endpoints.get(strategy['endpoint'])
    if not endpoint or endpoint.get('configured') is not True or not endpoint.get('url'):
        return 'larger_model_endpoint_not_explicitly_configured'
    if endpoint.get('healthy') is not True:
        return 'larger_model_endpoint_not_healthy'
    for key in ('model', 'quant', 'engine', 'backend', 'device'):
        if not isinstance(endpoint.get(key), str) or not endpoint[key].strip():
            return 'larger_model_endpoint_identity_missing:' + key
    return None


def _observation(row: Mapping) -> dict:
    result = dict(row)
    if result.get('synthetic', False) is not False:
        raise ValueError('synthetic observations cannot become real search evidence')
    for key in ('family', 'arm', 'strategy'):
        if not isinstance(result.get(key), str) or not result[key]:
            raise ValueError(f'observation {key} required')
    low, high = wilson_interval(result.get('correct'), result.get('n'))
    total = result.get('total', result['n'])
    if type(total) is not int or total < result['n']:
        raise ValueError('total must be integer >= n')
    result.update(total=total, ci_low=low, ci_high=high)
    _json(result)
    return result


def _event(db: sqlite3.Connection, family: str, tick_id: str, event: dict) -> dict:
    body = {'family': family, 'tick_id': tick_id, **event}
    db.execute('INSERT INTO learning_search_events(family,tick_id,body) VALUES(?,?,?)',
               (family, tick_id, _json(body)))
    return body


def _trial(db: sqlite3.Connection, family: str, strategy: str) -> dict | None:
    row = db.execute('SELECT body FROM learning_search_trials WHERE family=? AND strategy=?',
                     (family, strategy)).fetchone()
    return json.loads(row[0]) if row else None


def _save_trial(db: sqlite3.Connection, family: str, trial: dict) -> None:
    db.execute('INSERT OR REPLACE INTO learning_search_trials VALUES(?,?,?)',
               (family, trial['strategy'], _json(trial)))


def _flat(db: sqlite3.Connection, family: str, strategy: str, config: SearchConfig) -> bool:
    arms = set(_trial(db, family, strategy).get('arms', []))
    if not arms:
        return False
    # Persist the full curve but inspect at most N * arm_count rows per tick.
    rows = db.execute('''SELECT tick_id,arm,body FROM learning_search_curves
      WHERE family=? AND strategy=? ORDER BY rowid DESC LIMIT ?''',
                      (family, strategy, config.plateau_ticks * len(arms))).fetchall()
    ticks: dict[str, dict[str, dict]] = {}
    for row in reversed(rows):
        ticks.setdefault(row[0], {})[row[1]] = json.loads(row[2])
    if len(ticks) < config.plateau_ticks:
        return False
    window = list(ticks.values())[-config.plateau_ticks:]
    # A missing arm, including one that disappeared, is not evidence of a plateau.
    if any(set(tick) != arms for tick in window):
        return False
    # Abstaining on evaluated labels is still a real experiment: use total
    # evaluations, not only covered predictions, for the observation floor.
    return all(all(tick[arm]['total'] >= config.min_observations for tick in window)
               and max(tick[arm]['ci_low'] for tick in window) -
                   min(tick[arm]['ci_low'] for tick in window) <= config.min_improvement
               for arm in arms)


def search_tick(db: sqlite3.Connection, *, tick_id: str, now: float,
                observations: Iterable[Mapping], strategies: Iterable[Mapping] = DEFAULT_STRATEGIES,
                census_queue: Iterable[Mapping] = (), config: SearchConfig | None = None,
                endpoints: Mapping[str, dict] | None = None,
                unavailable: Mapping[str, str] | None = None) -> dict:
    """Persist one scheduling tick; return per-family configs, events and proposals.

    Observations are cumulative counts scoped to (family, strategy, arm), never
    whole-history counts relabelled as a new strategy. Each row requires those
    three strings plus integer correct/n (covered)/total. Only ONE strategy per
    family may be observed in a tick. Repeated tick_id is idempotent. A returned
    selected trial is pending until its first real observation, not a measurement.

    census_queue entries need family, exact_labels>0, label_source and source_path.
    Unknown/negative or inferred labels are not eligible for new-family proposals.
    Endpoint health is supplied by the owner's real health checker; nothing here
    contacts a server or substitutes a model. Call under the learner lock and
    transaction, and commit at the owner's tick boundary.
    """
    cfg = config or SearchConfig()
    if not tick_id or not math.isfinite(now):
        raise ValueError('nonempty tick_id and finite now required')
    catalogue = [_validate_strategy(s) for s in strategies]
    by_id = {s['id']: s for s in catalogue}
    if len(by_id) != len(catalogue):
        raise ValueError('strategy ids must be unique')
    rows = [_observation(row) for row in observations]
    groups: dict[str, list[dict]] = {}
    keys = set()
    for row in rows:
        key = row['family'], row['strategy'], row['arm']
        if key in keys:
            raise ValueError('duplicate family/strategy/arm observation')
        keys.add(key)
        groups.setdefault(row['family'], []).append(row)
        if row['strategy'] not in by_id:
            raise ValueError('observed strategy must exist in catalogue')
    if any(len({r['strategy'] for r in group}) != 1 for group in groups.values()):
        raise ValueError('one active strategy per family per tick required')
    initialize(db)
    previous = db.execute('SELECT body FROM learning_search_ticks WHERE tick_id=?', (tick_id,)).fetchone()
    if previous:
        return json.loads(previous[0])
    families = {r[0] for r in db.execute('SELECT family FROM learning_search_families')} | set(groups)
    result: dict[str, Any] = {'tick_id': tick_id, 'ts': now, 'families': {}, 'events': [], 'proposals': []}
    proposed = {r[0] for r in db.execute('SELECT family FROM learning_search_proposals')}
    queue = list(census_queue)
    for family in sorted(families):
        old = db.execute('SELECT current_strategy,state FROM learning_search_families WHERE family=?',
                         (family,)).fetchone()
        current = old[0] if old else None
        status = old[1] if old else 'awaiting_strategy'
        observed = groups.get(family, [])
        if observed:
            measured = observed[0]['strategy']
            if current is not None and measured != current:
                raise ValueError(f'{family}: observation does not match selected strategy {current}')
            current = measured
            trial = _trial(db, family, current)
            if trial is None:
                trial = {'strategy': current, 'config': by_id[current], 'selected_at': now,
                         'selected_tick': tick_id, 'trigger': 'initial_observation'}
            if trial['config'] != by_id[current]:
                raise ValueError('strategy configuration changed without a new strategy id')
            if trial.get('status') != 'plateau':
                trial.update(status='running', started_at=trial.get('started_at', now), last_tick=tick_id)
                status = 'running'
            trial['arms'] = sorted(set(trial.get('arms', [])) | {r['arm'] for r in observed})
            _save_trial(db, family, trial)
            for row in observed:
                db.execute('INSERT INTO learning_search_curves VALUES(?,?,?,?,?)',
                           (tick_id, family, current, row['arm'], _json(row)))
        plateau = bool(observed) and _flat(db, family, current, cfg)
        trial = _trial(db, family, current) if current else None
        if plateau and trial.get('status') != 'plateau':
            trial.update(status='plateau', ended_at=now, trigger='every_arm_wilson_flat',
                         ended_tick=tick_id, plateau_ticks=cfg.plateau_ticks)
            _save_trial(db, family, trial)
            result['events'].append(_event(db, family, tick_id, {
                'type': 'plateau', 'strategy': current, 'trigger': 'every_arm_wilson_flat',
                'arms': {r['arm']: r['ci_low'] for r in observed},
                'plateau_ticks': cfg.plateau_ticks}))
        # Failed availability is not a trial. An endpoint may become healthy later.
        # Remember a block's reason so unchanged failures do not flood the ledger.
        if current is None or plateau or status in {'awaiting_strategy', 'awaiting_family'}:
            used = {r[0] for r in db.execute(
                'SELECT strategy FROM learning_search_trials WHERE family=?', (family,))}
            selected = None
            for candidate in catalogue:
                if candidate.get('families') and family not in candidate['families']:
                    continue
                if candidate['id'] in used:
                    continue
                reason = _blocked(candidate, endpoints or {}, unavailable or {})
                if reason:
                    prior = db.execute('''SELECT body FROM learning_search_events WHERE family=?
                      ORDER BY id DESC''', (family,)).fetchall()
                    last = next((json.loads(r[0]) for r in prior
                                 if json.loads(r[0]).get('candidate') == candidate['id']), None)
                    if not last or last.get('reason') != reason or last.get('type') != 'blocked':
                        result['events'].append(_event(db, family, tick_id, {
                            'type': 'blocked', 'candidate': candidate['id'], 'reason': reason}))
                    continue
                selected = candidate
                break
            if selected:
                before = current
                current = selected['id']
                status = 'selected'
                trial = {'strategy': current, 'config': selected, 'status': 'selected',
                         'selected_at': now, 'selected_tick': tick_id,
                         'trigger': 'every_arm_wilson_flat' if plateau else 'available_untried_strategy'}
                _save_trial(db, family, trial)
                result['events'].append(_event(db, family, tick_id, {
                    'type': 'strategy_selected', 'from': before, 'to': current,
                    'trigger': trial['trigger'], 'config': selected}))
            else:
                status = 'awaiting_strategy'
                # Keep the last actual strategy for provenance, never mark an
                # unavailable strategy as run or leave a terminal exhausted state.
                below = bool(observed) and max(r['ci_low'] for r in observed) < cfg.credit_bar
                if below:
                    status = 'awaiting_family'
                    for entry in queue:
                        target = entry.get('family')
                        count = entry.get('exact_labels')
                        if (not isinstance(target, str) or not target or target in families or target in proposed
                                or type(count) is not int or count <= 0 or not entry.get('label_source')
                                or not entry.get('source_path') or entry.get('inferred', False)):
                            continue
                        proposal = {'family': target, 'from_family': family, 'ts': now,
                                    'tick_id': tick_id, 'status': 'proposed', 'census': dict(entry),
                                    'trigger': 'strategies_unavailable_or_tried_below_bar'}
                        db.execute('INSERT INTO learning_search_proposals VALUES(?,?)', (target, _json(proposal)))
                        proposed.add(target)
                        result['proposals'].append(proposal)
                        result['events'].append(_event(db, family, tick_id, {
                            'type': 'new_family_proposed', 'proposal': proposal}))
                        break
        db.execute('INSERT OR REPLACE INTO learning_search_families VALUES(?,?,?)', (family, current, status))
        result['families'][family] = {'current_strategy': current, 'status': status,
                                     'strategy': by_id.get(current),
                                     'trial': _trial(db, family, current) if current else None}
    db.execute('INSERT INTO learning_search_ticks VALUES(?,?)', (tick_id, _json(result)))
    return result


def emit_findings(db: sqlite3.Connection, *, tick_id: str, now: float,
                  observations: Iterable[Mapping], source_path: Path, run_id: str,
                  home: Path | None = None, config: SearchConfig | None = None) -> list[dict]:
    """Append each positive-n arm to the ONE canonical #211 findings ledger.

    Additional observation requirements: identity={kind,model,quant,engine,
    backend,device}, projection={}, scope_note, evidence_class. Historical
    inference MUST use replayed_L1. True prospective rows use live_shadow AND
    prospective=True; canonical encoding is measured + metric.live_shadow=True.
    No missing identity is synthesized. n=0 rows are returned as blocked, never
    fabricated as measured findings. The source must be the immutable private
    per-tick metrics artifact (not a source-code file); its bytes are hashed here.
    No private trace contents are written to the canonical evidence ledger.
    """
    from z0int.policy.findings import Finding, append_findings, digest

    cfg = config or SearchConfig()
    if not tick_id or not run_id or not math.isfinite(now):
        raise ValueError('tick_id, run_id and finite now required')
    path = Path(source_path).resolve(strict=True)
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    rows = [_observation(row) for row in observations]
    batch = []
    keys = []
    blocked = []
    seen = set()
    initialize(db)
    for row in rows:
        key = (tick_id, row['family'], row['strategy'], row['arm'], row.get('evidence_class'))
        if key in seen:
            raise ValueError('duplicate finding observation')
        seen.add(key)
        if row.get('promotion_eligible') is False or row.get('evidence_kind') == 'proxy':
            blocked.append({'family': row['family'], 'arm': row['arm'], 'status': 'blocked',
                            'reason': 'nonpromotable_research_observation'})
            _event(db, row['family'], tick_id, {
                'type': 'finding_blocked', 'arm': row['arm'], 'strategy': row['strategy'],
                'reason': 'nonpromotable_research_observation'})
            continue
        if not row['n']:
            blocked.append({'family': row['family'], 'arm': row['arm'], 'status': 'blocked', 'reason': 'n_zero'})
            _event(db, row['family'], tick_id, {
                'type': 'finding_blocked', 'arm': row['arm'], 'strategy': row['strategy'], 'reason': 'n_zero'})
            continue
        identity = row.get('identity')
        if not isinstance(identity, dict) or any(not isinstance(identity.get(k), str) or not identity[k].strip()
                for k in ('kind', 'model', 'quant', 'engine', 'backend', 'device')):
            raise ValueError('full measured arm identity required')
        if not isinstance(row.get('projection'), dict) or not row.get('scope_note'):
            raise ValueError('explicit projection and scope_note required')
        evidence = row.get('evidence_class')
        live = evidence == 'live_shadow'
        if live and row.get('prospective') is not True:
            raise ValueError('live_shadow requires explicitly prospective evaluation')
        if evidence not in {'replayed_L1', 'replayed_L2', 'live_shadow', 'canary'}:
            raise ValueError('explicit replayed_L1/replayed_L2/live_shadow/canary evidence required')
        if db.execute('''SELECT finding_id FROM learning_search_findings
          WHERE tick_id=? AND family=? AND strategy=? AND arm=? AND evidence_class=?''', key).fetchone():
            continue
        metric = {'name': 'acc_at_conf', 'value': row['correct']/row['n'], 'n': row['n'],
                  'ci_low': row['ci_low'], 'ci_high': row['ci_high'], 'ci_method': 'wilson95',
                  'higher_is_better': True, 'live_shadow': live, 'total': row['total'],
                  'correct': row['correct'], 'strategy': row['strategy'], 'projection': row['projection']}
        if 'scope' in row:
            if not isinstance(row['scope'], dict):
                raise ValueError('scope must be an explicit model/project mapping')
            metric['scope'] = row['scope']
        for name in ('divergence', 'aa_noise_floor'):
            if name in row:
                metric[name] = row[name]
        batch.append(asdict(Finding(
            ts=datetime.fromtimestamp(now, timezone.utc).isoformat(),
            source={'kind': 'shadow_learner', 'path': str(path), 'sha256': sha,
                    'run_id': run_id, 'tick_id': tick_id},
            subject={'family': row['family'], 'job': row['arm']}, arm=identity,
            metric=metric, evidence_class='measured' if live else evidence,
            scope_note=row['scope_note'] + '; strategy/projection scope=' + _json({
                'strategy': row['strategy'], 'projection': row['projection'], 'scope': row.get('scope', {})}),
            expires_at=datetime.fromtimestamp(now+cfg.finding_ttl_s, timezone.utc).isoformat(),
            synthetic=False)))
        keys.append(key)
    ledger = Path(home) / 'findings' / 'findings.jsonl' if home is not None else None
    written = append_findings(batch, path=ledger) if batch else []
    # The canonical writer deduplicates IDs, including a previous append whose
    # surrounding SQLite transaction rolled back. Never zip its filtered return
    # value against unfiltered keys.
    for key, finding in zip(keys, batch):
        finding_id = digest({k: v for k, v in finding.items() if k not in {'id', 'prev'}})
        db.execute('INSERT INTO learning_search_findings VALUES(?,?,?,?,?,?)', (*key, finding_id))
    return written + blocked
