"""Effectful glue for private atomic experiments and the canonical policy ledger."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote

from .adapters.transport import OpenAICompatTransport, ServerConfig
from .learning_families import derive_jobs, run_jobs, family_metrics
from .learning_search import DEFAULT_STRATEGIES, SearchConfig, search_tick, emit_findings
from .registry import load_serving


def serving_identity(endpoint) -> tuple[dict, dict]:
    transport = OpenAICompatTransport(ServerConfig(endpoint.base_url, endpoint.served_model,
        timeout_s=max(10, endpoint.timeout_s), runtime=endpoint.runtime,
        quantization=endpoint.quantization))
    root = transport._get('/props')
    props = transport._get('/props?model=' + quote(endpoint.served_model))
    slots = transport._get('/slots?model=' + quote(endpoint.served_model))
    models = transport._get('/models')
    model = next((m for m in models.get('data', []) if m.get('id') == endpoint.served_model), {})
    args = model.get('status', {}).get('args', [])
    device = args[args.index('--device')+1] if '--device' in args else 'unreported'
    backend = 'cuda' if device.startswith('CUDA') else 'cpu' if device in ('none', 'CPU') else device
    build = root.get('build_info') or props.get('build_info')
    if not build or backend == 'unreported' or not props.get('model_ftype'):
        raise ValueError('serving endpoint lacks measured build/backend/quant identity')
    n_ctx = min([s['n_ctx'] for s in slots] or [props['default_generation_settings']['n_ctx']])
    identity = {'kind': 'slm', 'model': props['model_alias'], 'quant': props['model_ftype'],
                'engine': 'llama.cpp@' + build, 'backend': backend, 'device': 'groot'}
    scope = {'base_url': endpoint.base_url, 'served_model': endpoint.served_model,
             'n_ctx': n_ctx, 'slots': len(slots), 'revision': 'not_reported_by_endpoint'}
    return identity, scope


def choice_predictor(db, endpoint, identity: dict, scope: dict, *, home: Path | None = None):
    """Per-candidate binary logits; retain every RPC result and failure privately."""
    from .shadow_binary import score_choices
    transport = OpenAICompatTransport(ServerConfig(endpoint.base_url, endpoint.served_model,
        timeout_s=max(10, endpoint.timeout_s), runtime=endpoint.runtime,
        quantization=endpoint.quantization))
    db.execute('''CREATE TABLE IF NOT EXISTS learning_rpc(
        id INTEGER PRIMARY KEY,ts REAL,family TEXT,strategy TEXT,body TEXT)''')

    def predict(prompt: str, choices: list[str], strategy: dict) -> dict:
        rpc_records = []
        def chat(messages, **kwargs):
            from .shadow import failure_class
            started = time.time()
            row = {'identity': identity, 'scope': scope,
                   'prompt_bytes': len(json.dumps(messages).encode()),
                   'max_tokens': 1, 'synthetic': False}
            try:
                outcome = transport.chat(messages, **kwargs)
                row.update(raw_response=outcome.raw, latency_ms=outcome.latency_ms,
                           answered=bool(outcome.content))
                return outcome
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
                row.update(error=error, failure_class=failure_class(error))
                raise
            finally:
                rpc_records.append((started, strategy.get('family'),
                                    strategy.get('strategy_id'), json.dumps(row)))
        try:
            methods = {'methods-hierarchy-v1':'hierarchical',
                       'methods-threeway-v1':'tournament3'}
            method = methods.get(strategy.get('strategy_id'))
            if method:
                from .slm_methods import predict_method
                if home is not None:
                    from .choice_capacity import effective_cap
                    if effective_cap(.6, identity, home) < 3:
                        return {'selected_action':None,'abstained':True,
                                'error':'option_capacity_insufficient'}
                return predict_method(prompt, choices, chat, method=method,
                                      n_ctx=scope['n_ctx'])
            return score_choices(prompt, [{'id': choice, 'description': choice} for choice in choices],
                chat, n_ctx=scope['n_ctx'], min_margin=.25, policy_identity=identity, policy_home=home)
        finally:
            # Batched RPCs complete on worker threads; SQLite belongs to caller.
            db.executemany('INSERT INTO learning_rpc(ts,family,strategy,body) VALUES(?,?,?,?)',
                           rpc_records)
    return predict


def _current(db) -> dict:
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_search_families'").fetchone()
    if not exists:
        return {}
    return {r[0]: r[1] for r in db.execute('SELECT family,current_strategy FROM learning_search_families')}


def _configs(current: dict, catalogue_rows=DEFAULT_STRATEGIES) -> dict:
    catalogue = {s['id']: s['config'] for s in catalogue_rows}
    result = {}
    for family, strategy in current.items():
        config = catalogue.get(strategy, catalogue['history-1'])
        result[family] = {'strategy_id': strategy, 'history_width': min(8, config['history_width']),
            'candidate_k': min(3, config['candidate_k']), 'prompt_bytes': min(3000, config['prompt_bytes']),
            'retrieval_k': config.get('retrieval_k', 64),
            'candidate_source': config.get('candidate_source', 'history'),
            'recent_sessions_n': config.get('recent_sessions_n', 5)}
    return result


def _atomic_observations(metrics: dict, identity: dict, scope: dict) -> list[dict]:
    result = []
    for family, metric in metrics.items():
        for arm, classes in metric['arms'].items():
            for evidence, values in classes.items():
                if evidence not in ('replayed_L1', 'live_shadow'):
                    continue
                baseline = arm.startswith('heuristic:')
                strategy = values.get('strategy_id', 'history-1')
                result.append({'family': family, 'arm': arm, 'strategy': strategy,
                    'correct': values['correct'], 'n': values['covered_n'], 'total': values['total'],
                    'identity': {'kind': 'rules', 'model': 'prior-transition-v1', 'quant': 'none',
                        'engine': 'python-stdlib', 'backend': 'cpu', 'device': 'groot'} if baseline else identity,
                    'projection': values.get('strategy_config', {}), 'scope': scope,
                    'scope_note': 'Exact OMP session/call labels only; wrapper flag is not task success; no cross-model/device/projection generalization',
                    'evidence_class': evidence, 'prospective': evidence == 'live_shadow'})
    return result


def tool_observations(db, identity: dict, scope: dict) -> tuple[list[dict], dict]:
    """Accuracy is conditional on coverage; compare on identical joined calls."""
    from .learning import predict, STRATEGIES, wilson
    groups = {}
    matched = {arm: Counter() for arm in ('goal_binary', 'history_bigram')}
    for row in db.execute('''SELECT l.*,r.body FROM labels l JOIN receipts r USING(id)
                           WHERE l.family='omp.tool' ORDER BY l.ts,l.id'''):
        receipt = json.loads(row['body'])
        shadows = [s for s in receipt.get('shadow', [])
                   if s.get('model') == identity['model'] and
                   (s.get('arm') == 'goal-binary' or str(s.get('arm','')).startswith('slm-method:'))]
        if not shadows:
            continue
        strategy = receipt.get('projection_strategy', 'history-1')
        projection = receipt.get('projection_config', {})
        previous = db.execute('''SELECT action,features,ts FROM examples WHERE family='omp.tool'
            AND ts<? ORDER BY ts DESC LIMIT 512''', (row['ts'],)).fetchall()
        examples = [{'action': e['action'], 'history': json.loads(e['features']), 'ts': e['ts']} for e in previous]
        action, confidence = predict(json.loads(row['features']), examples, STRATEGIES[4])
        decisions = [('history_bigram', action if confidence >= .8 else None, {
            'kind': 'knn', 'model': 'history-bigram-v1', 'quant': 'none',
            'engine': 'python-stdlib', 'backend': 'cpu', 'device': 'groot'})]
        decisions += [('goal_binary' if s.get('arm') == 'goal-binary' else s['arm'],
                       s.get('selected_action') if not s.get('error') else None, identity) for s in shadows]
        for arm, selected, arm_identity in decisions:
            key = strategy, arm, json.dumps(projection, sort_keys=True)
            value = groups.setdefault(key, {'family': 'omp.tool', 'arm': arm + '@' +
                hashlib.sha256(key[2].encode()).hexdigest()[:12], 'strategy': strategy,
                'correct': 0, 'n': 0, 'total': 0, 'identity': arm_identity, 'projection': projection,
                'scope': scope, 'scope_note': 'Matched exact native OMP tool-call joins; accuracy conditional on coverage; no execution authority or cross-model/device/projection generalization',
                'evidence_class': 'live_shadow', 'prospective': True})
            covered = selected is not None
            correct = int(covered and selected == row['action'])
            value['n'] += int(covered)
            value['total'] += 1
            value['correct'] += correct
            matched.setdefault(arm, Counter()).update(total=1, covered=int(covered), correct=correct)
    comparison = {}
    for arm, counts in matched.items():
        n, total, correct = counts['covered'], counts['total'], counts['correct']
        comparison[arm] = {'total': total, 'n': n, 'correct': correct,
            'accuracy': correct/n if n else None, 'coverage': n/total if total else 0,
            'wilson95_lower': wilson(correct, n)}
    return list(groups.values()), comparison


def census_queue(home: Path, config: dict) -> list[dict]:
    if config.get('census_queue') is not None:
        return config['census_queue']
    path = Path(config.get('census_path') or home/'loop'/'shadow-census.json')
    if not path.exists():
        return []
    census = json.loads(path.read_text())
    names = {'compaction_keep_drop': 'hermes.compaction_flag',
             'model_choice': 'hermes.model_argument'}
    return [{'family': names[job['job']],
        'exact_labels': job.get('full_corpus_extraction', {}).get('eligible_within_shard_unique_sum', 0),
        'label_source': job['label_source']['exact_trace_join'], 'source_path': str(path.resolve()),
        'scope_note': job['label_source']['quality_caveat'], 'census_frozen': True}
        for job in census.get('jobs', []) if job.get('job') in names and
        job.get('label_source', {}).get('observed_literal_or_field') is True]


def runtime_tick(db, home: Path, *, now: float, deadline: float, config: dict) -> dict:
    """Derive exact labels, execute bounded trials, evolve, and publish every measured arm."""
    from .learning import atomic_json
    derived = {'calls_scanned': 0, 'jobs_added': 0, 'labels_added': 0}
    while time.monotonic() < deadline:
        part = derive_jobs(db, now=now, max_calls=10000)
        for key in derived:
            derived[key] += part[key]
        if part['calls_scanned'] < 10000:
            break
    endpoints = load_serving(home/'config'/'serving.json')
    model_id = config.get('model_id', 'qwen3_06b')
    endpoint = endpoints.get(model_id)
    result = {'derived': derived, 'synthetic': False}
    if endpoint is None:
        result.update(error='configured model is not served: ' + model_id,
                      families=family_metrics(db, now=now))
        return result
    identity, scope = serving_identity(endpoint)
    current = _current(db)
    from .slm_candidates import candidate_catalogue
    catalogue = candidate_catalogue(home, DEFAULT_STRATEGIES)
    strategies = _configs(current, catalogue)
    metrics = run_jobs(db, choice_predictor(db, endpoint, identity, scope, home=home), now=now,
        max_jobs=int(config.get('jobs_per_tick', 48)), deadline=deadline,
        strategy_id='history-1', history_width=1, strategies=strategies,
        slm_arm='slm:' + endpoint.served_model)
    result.update(metrics, identity=identity, serving_scope=scope)
    observations = _atomic_observations(metrics['families'], identity, scope)
    tool_rows, matched = tool_observations(db, identity, scope)
    observations.extend(tool_rows)
    result['matched_tool_comparison'] = matched
    tick_id = str(now)
    # Scheduling receives only the active strategy, while publication retains all
    # historical strategy scopes. Never rename accumulated scores after a switch.
    active = [r for r in observations if r['strategy'] == current.get(r['family'], 'history-1')]
    # Live and replay evidence are independently published. Scheduling combines
    # their disjoint observations without conflating their evidence classes.
    combined = {}
    for row in active:
        key = row['family'], row['strategy'], row['arm']
        value = combined.setdefault(key, {**row, 'n': 0, 'correct': 0, 'total': 0})
        for field in ('n', 'correct', 'total'):
            value[field] += row[field]
    search_config = SearchConfig(plateau_ticks=int(config.get('plateau_ticks', 3)),
        min_improvement=float(config.get('min_improvement', .001)),
        credit_bar=float(config.get('credit_bar', .8)),
        min_observations=int(config.get('min_observations', 5)))
    census = census_queue(home, config)
    result['census_queue'] = census
    result['search'] = search_tick(db, tick_id=tick_id, now=now,
        observations=combined.values(), census_queue=census, config=search_config,
        strategies=catalogue)
    artifact = home/'loop'/'findings-sources'/('shadow-' + hashlib.sha256(tick_id.encode()).hexdigest() + '.json')
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(artifact, {'ts': now, 'identity': identity, 'scope': scope,
                          'observations': observations, 'synthetic': False})
    result['findings'] = emit_findings(db, tick_id=tick_id, now=now,
        observations=observations, source_path=artifact, run_id='shadow-' + tick_id,
        home=home, config=search_config)
    result['findings_source'] = str(artifact)
    rpc = [json.loads(r[0]) for r in db.execute('SELECT body FROM learning_rpc WHERE ts>=?', (now,))]
    errors = sum(bool(r.get('error')) for r in rpc)
    result['transport_tick'] = {'attempts': len(rpc), 'errors': errors,
        'error_rate': errors/len(rpc) if rpc else None,
        'parse_errors': sum(bool(r.get('parse_error')) for r in rpc),
        'answered': sum(bool(r.get('answered')) for r in rpc),
        'failure_classes': dict(Counter(r.get('failure_class') for r in rpc if r.get('failure_class')))}
    return result
