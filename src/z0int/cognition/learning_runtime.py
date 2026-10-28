"""Effectful glue for private atomic experiments and the canonical policy ledger."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import socket
import ipaddress
import time
from urllib.parse import quote, urlsplit

from .adapters.transport import OpenAICompatTransport, ServerConfig
from .learning_families import derive_jobs, run_jobs, family_metrics
from .learning_search import DEFAULT_STRATEGIES, SearchConfig, search_tick, emit_findings
from .registry import load_serving


def serving_identity(endpoint) -> tuple[dict, dict]:
    transport = OpenAICompatTransport(ServerConfig(endpoint.base_url, endpoint.served_model,
        timeout_s=max(10, endpoint.timeout_s), runtime=endpoint.runtime,
        quantization=endpoint.quantization, api_key=getattr(endpoint, 'api_key', None)))
    return measured_serving_identity(transport, endpoint.served_model)


def measured_serving_identity(transport, served_model) -> tuple[dict, dict]:
    """Measure the server and model addressed by this actual transport."""
    root = transport._get('/props')
    props = transport._get('/props?model=' + quote(served_model))
    slots = transport._get('/slots?model=' + quote(served_model))
    models = transport._get('/models')
    model = next((m for m in models.get('data', []) if m.get('id') == served_model), {})
    args = model.get('status', {}).get('args', [])
    device = args[args.index('--device')+1] if '--device' in args else 'unreported'
    backend = 'cuda' if device.startswith('CUDA') else 'cpu' if device in ('none', 'CPU') else device
    build = root.get('build_info') or props.get('build_info')
    if not build or backend == 'unreported' or not props.get('model_ftype'):
        raise ValueError('serving endpoint lacks measured build/backend/quant identity')
    n_ctx = min([s['n_ctx'] for s in slots] or [props['default_generation_settings']['n_ctx']])
    host = urlsplit(transport.config.base_url).hostname
    try:
        local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        local = False
    identity = {'kind': 'slm', 'model': props['model_alias'], 'quant': props['model_ftype'],
                'engine': 'llama.cpp@' + build, 'backend': backend,
                'device': socket.gethostname() if local else 'unverified-endpoint:' + str(host)}
    scope = {'base_url': transport.config.base_url, 'served_model': served_model,
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
            if strategy.get('decision_method')=='slm_pipeline_v1':
                from types import SimpleNamespace
                from .slm_scoped import run_scoped
                # The caller pins this set from the decision's graph. A frozen
                # training vocabulary is not evidence that an action is legal.
                legal=SimpleNamespace(ids=lambda:tuple(choices))
                backend=SimpleNamespace(_transport=SimpleNamespace(chat=chat),
                    config=SimpleNamespace(model=endpoint.served_model),
                    backend_id=identity.get('backend'))
                projected = json.loads(prompt)
                context = {
                    'task': projected.get('task'),
                    'previous_tools': [{'tool': value} for value in projected.get('previous_tools', [])
                                       if isinstance(value, str)],
                    'examples': [{'previous_tools': item.get('previous_tools', []),
                                  'next_tool': item.get('label')}
                                 for item in projected.get('examples', []) if isinstance(item, dict)],
                }
                return run_scoped({'goal_context':context},legal,backend,strategy['method_config'],
                    n_ctx=scope['n_ctx'],policy_identity=identity,policy_home=home)
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
    from .learning_search import _validate_strategy
    catalogue = {s['id']: _validate_strategy(s)['config'] for s in catalogue_rows}
    result = {}
    for family, strategy in current.items():
        config = catalogue.get(strategy, catalogue['history-1'])
        result[family] = {
            'strategy_id': strategy, 'history_width': min(8, config['history_width']),
            'candidate_k': min(3, config['candidate_k']), 'prompt_bytes': min(3000, config['prompt_bytes']),
            'retrieval_k': config.get('retrieval_k', 64),
            'candidate_source': config.get('candidate_source', 'history'),
            'recent_sessions_n': config.get('recent_sessions_n', 5),
            'decision_method': config.get('decision_method', 'per_candidate_binary_logit'),
            'method_config': dict(config),
        }
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
    """Build non-promotable research curves and paired, unattested diagnostics.

    Neither the currently served identity nor a receipt's configured request
    scope attests the bytes that produced a shadow response.
    """
    from .learning import predict, STRATEGIES, wilson
    groups = {}
    for row in db.execute('''SELECT l.*,r.body FROM labels l JOIN receipts r USING(id)
                           WHERE l.family='omp.tool' ORDER BY l.ts,l.id'''):
        receipt = json.loads(row['body'])
        shadows = [s for s in receipt.get('shadow', [])
                   if s.get('arm') == 'goal-binary' or str(s.get('arm', '')).startswith('slm-method:')]
        if not shadows:
            continue
        previous = db.execute('''SELECT action,features,ts FROM examples WHERE family='omp.tool'
            AND ts<? ORDER BY ts DESC,id DESC LIMIT 512''', (row['ts'],)).fetchall()
        examples = [{'action': e['action'], 'history': json.loads(e['features']), 'ts': e['ts']} for e in previous]
        action, confidence = predict(json.loads(row['features']), examples, STRATEGIES[4])
        baseline = action if confidence >= .8 else None
        for shadow in shadows:
            provenance = {
                'request_scope': shadow.get('request_scope'),
                'model': shadow.get('model'),
                'requested_model': shadow.get('requested_model'),
                'method': shadow.get('arm'),
                'method_config': shadow.get('method_config'),
                'strategy': receipt.get('projection_strategy') or 'history-1',
                'projection': receipt.get('projection_config') or {},
            }
            key = json.dumps(provenance, sort_keys=True, separators=(',', ':'))
            group = groups.setdefault(key, {'provenance': provenance, 'model': Counter(), 'baseline': Counter()})
            failed = (shadow.get('error') or shadow.get('parse_error') or shadow.get('timed_out')
                      or shadow.get('abstained') or
                      ('rpc_count' in shadow and not shadow['rpc_count']) or
                      (str(shadow.get('arm', '')).startswith('slm-method:') and not shadow.get('rpc_count')))
            selected = None if failed else shadow.get('selected_action')
            for name, decision in (('model', selected), ('baseline', baseline)):
                covered = decision is not None
                group[name].update(total=1, covered=int(covered),
                                   correct=int(covered and decision == row['action']))

    def summary(counts):
        n, total, correct = counts['covered'], counts['total'], counts['correct']
        return {'total': total, 'n': n, 'correct': correct,
                'accuracy': correct/n if n else None, 'coverage': n/total if total else 0,
                'wilson95_lower': wilson(correct, n)}

    observations, arms = [], {}
    for key, group in sorted(groups.items()):
        digest = hashlib.sha256(key.encode()).hexdigest()
        provenance = group['provenance']
        arms[digest] = {'provenance': provenance, 'classification': 'unattested_diagnostic_only',
                        'model': summary(group['model']), 'baseline': summary(group['baseline'])}
        for name in ('model', 'baseline'):
            counts = group[name]
            observations.append({'family': 'omp.tool', 'strategy': provenance['strategy'],
                'arm': 'unattested:' + name + ':' + digest,
                'correct': counts['correct'], 'n': counts['covered'], 'total': counts['total'],
                'evidence_kind': 'proxy', 'promotion_eligible': False,
                'binding': 'unattested_diagnostic_only', 'request_provenance': provenance})
    comparison = {'arms': arms, 'binding': {'classification': 'unattested_diagnostic_only',
        'attempts': sum(group['model']['total'] for group in groups.values()),
        'attested_attempts': 0, 'model_credit': False}}
    return observations, comparison


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
        db.commit()
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
    db.commit()
    result.update(metrics, identity=identity, serving_scope=scope)
    observations = _atomic_observations(metrics['families'], identity, scope)
    tool_rows, matched = tool_observations(db, identity, scope)
    research_observations = observations + tool_rows
    result['matched_tool_comparison'] = matched
    tick_id = str(now)
    # Scheduling receives only the active strategy, while publication retains all
    # historical strategy scopes. Never rename accumulated scores after a switch.
    active = [r for r in research_observations if r['strategy'] == current.get(r['family'], 'history-1')]
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
    db.commit()
    artifact = home/'loop'/'findings-sources'/('shadow-' + hashlib.sha256(tick_id.encode()).hexdigest() + '.json')
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(artifact, {'ts': now, 'identity': identity, 'scope': scope,
                          'observations': observations, 'synthetic': False})
    result['findings'] = emit_findings(db, tick_id=tick_id, now=now,
        observations=observations, source_path=artifact, run_id='shadow-' + tick_id,
        home=home, config=search_config)
    db.commit()
    result['findings_source'] = str(artifact)
    rpc = [json.loads(r[0]) for r in db.execute('SELECT body FROM learning_rpc WHERE ts>=?', (now,))]
    errors = sum(bool(r.get('error')) for r in rpc)
    result['transport_tick'] = {'attempts': len(rpc), 'errors': errors,
        'error_rate': errors/len(rpc) if rpc else None,
        'parse_errors': sum(bool(r.get('parse_error')) for r in rpc),
        'answered': sum(bool(r.get('answered')) for r in rpc),
        'failure_classes': dict(Counter(r.get('failure_class') for r in rpc if r.get('failure_class')))}
    return result
