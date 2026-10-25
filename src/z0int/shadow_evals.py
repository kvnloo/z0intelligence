"""Private, observe-only evaluation worker. No candidate can invoke tools.

The OMP transcript/IntentService receipt is the input hook: a separate process
observes it, so no evaluation work is awaited by the real turn.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import time

DEFAULT_ROOT = Path('/mnt/zer0models/workspace/zer0/data/operations/shadow-evals')
DEFAULT_SESSION = Path('/home/kvn/.omp/agent/sessions/--mnt-zer0models--/2026-10-24T18-59-09-096Z_01a16f47-f4a8-7753-9976-a51a85b8b697.jsonl')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def save(path, value):
    """Atomic private replacement, including report files containing citations."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.new')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            if isinstance(value, str):
                stream.write(value)
            else:
                json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
                stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_config(path):
    config = json.loads(Path(path).expanduser().read_text())
    if not isinstance(config, dict):
        raise ValueError('Configuration must be an object')
    root = Path(config.get('root', DEFAULT_ROOT)).expanduser().resolve()
    if not root.is_absolute():
        raise ValueError('Private experiment root must be absolute')
    config['root'] = str(root)
    return config


def cohort_counts(config):
    counts = (config.get('calibration_positive', 103), config.get('calibration_negative', 50))
    if counts not in ((103, 50), (27, 13)):
        raise ValueError('Only the full audit or explicitly authorized stratified bootstrap cohort is admitted')
    return counts


def calibration_state(config):
    path = Path(config['root']) / 'calibration-summary.json'
    if not path.exists():
        return {'status': 'not_run', 'scores_enabled': False}
    state = json.loads(path.read_text())
    # Bind score use to the same judge, rubric and complete selected gold set.
    from .shadow_evals_judge import RUBRIC
    matching = state.get('judge') == config.get('judge') and state.get('rubric_sha256') == digest(RUBRIC)
    positive, negative = cohort_counts(config)
    state['scores_enabled'] = bool(matching and state.get('scores_enabled') and state.get('complete') and state.get('evaluated') == positive + negative and state.get('failures') == positive and state.get('nonfailures') == negative)
    return state


def judge_candidate(job, bundle, candidate, config):
    from .shadow_evals_fanout import complete
    from .shadow_evals_judge import judge_prompt, parse_judgment
    judge = config.get('judge')
    if not isinstance(judge, dict) or not judge.get('provider') or not judge.get('model'):
        return {'error': 'frontier_subscription_judge_not_configured'}
    if candidate.get('error'):
        return {'error': 'candidate_failed_not_scored'}
    result = complete(judge, [{'role': 'user', 'content': judge_prompt(job, bundle, candidate)}], {**config, 'call_role': 'judge'})
    if result.get('error'):
        return {'error': result['error'], 'metrics': result.get('metrics')}
    try:
        return {**parse_judgment(result['content']), 'judge_metrics': result.get('metrics'), 'judge': judge}
    except (ValueError, TypeError, KeyError):
        return {'error': 'invalid_judge_response', 'metrics': result.get('metrics')}


def process_job(job, config):
    from .shadow_evals_fanout import build_bundle, run_candidates, select_offers
    root = Path(config['root'])
    identity = job.get('job_id') or digest({k: job[k] for k in ('session_id', 'entry_id', 'sha256')})
    bundle = build_bundle(job, config)
    save(root / 'bundles' / (identity + '.json'), bundle)
    selection = select_offers(config, bundle)
    result = {'schema': 'z0.shadow.evals.result.v1', 'job_id': identity, 'session_id': job['session_id'], 'entry_id': job['entry_id'], 'sha256': job['sha256'], 'tier': job['tier'], 'ts': now(), 'synthetic': False, 'bundle_sha256': digest(bundle), 'fact_catalog': bundle.get('fact_catalog', {}), 'selection': selection, 'outcome': job.get('outcome'), 'candidates': []}
    minimum = 2 if config.get('phase') == 'bootstrap' else 3
    if len(selection.get('offers', [])) < minimum:
        result.update(status='blocked', reason='insufficient_private_budget_safe_offers', phase=config.get('phase', 'scored'))
    else:
        candidates = run_candidates(job, bundle, config)
        calibrated = calibration_state(config)
        for candidate in candidates:
            candidate['job_id'] = identity
            candidate['bundle_sha256'] = result['bundle_sha256']
            candidate['judgment'] = judge_candidate(job, bundle, candidate, config) if calibrated['scores_enabled'] else {'error': 'judge_calibration_incomplete_scores_disabled'}
        result.update(status='completed' if all(not row.get('error') for row in candidates) else 'error', candidates=candidates, calibration=calibrated)
    save(root / 'results' / (identity + '.json'), result)
    return result


def calibrate(config, audit_root):
    from .shadow_evals_judge import RUBRIC, calibration_cases, calibration_metrics
    root = Path(config['root'])
    inventory = calibration_cases(Path(audit_root))
    save(root / 'calibration-inventory.json', inventory)
    positive, negative = cohort_counts(config)
    if (positive, negative) == (103, 50):
        cases = inventory
    else:
        cases = ([row for row in inventory if row.get('gold_miss') is True and row.get('status') == 'ready'][:positive]
                 + [row for row in inventory if row.get('gold_miss') is False and row.get('status') == 'ready'][:negative])
    save(root / 'calibration-selection.json', cases)
    rows = []
    # Persist after each case; an interruption never fabricates a completed run.
    for case in cases:
        job = case.get('job', case)
        bundle = case.get('bundle', {'historical_context': case.get('context', []), 'condition': 'historical_calibration_no_future_labels'})
        candidate = case.get('candidate', {'content': case.get('assistant_answer', case.get('answer', ''))})
        judgment = ({'error': 'missing_gold', 'reasons': case.get('missing_gold', [])}
                    if case.get('status') == 'missing_gold' else judge_candidate(job, bundle, candidate, config))
        rows.append({**case, 'judgment': judgment})
        save(root / 'calibration-rows.json', rows)
        # Provider admission/transport errors are shared prerequisites. Malformed
        # judge answers instead count as unknowns and do not erase the cohort.
        if isinstance(judgment.get('error'), dict):
            break
    metrics = calibration_metrics(rows, expected_positive=positive, expected_negative=negative)
    evaluated = sum(not row['judgment'].get('error') for row in rows)
    failures = sum(row.get('gold_miss') is True for row in rows if not row['judgment'].get('error'))
    negatives = sum(row.get('gold_miss') is False for row in rows if not row['judgment'].get('error'))
    state = {**metrics, 'ts': now(), 'selected': len(cases), 'inventory_size': len(inventory), 'selection_scope': 'first deterministic ready cases in each reviewed-label stratum; missing gold excluded explicitly', 'evaluated': evaluated, 'scored_failures': failures, 'scored_nonfailures': negatives, 'complete': bool(metrics.get('scores_enabled')) and evaluated == positive + negative and failures == positive and negatives == negative, 'judge': config.get('judge'), 'rubric_sha256': digest(RUBRIC)}
    state['scores_enabled'] = state['complete']
    save(root / 'calibration-summary.json', state)
    return state


def replay(config, session, count=10):
    from .shadow_evals_queue import ShadowQueue, discover_jobs
    if type(count) is not int or not 1 <= count <= 10:
        raise ValueError('Replay is bounded to one through ten real messages')
    queue = ShadowQueue(config['root'])
    jobs = discover_jobs(Path(session), Path(config.get('intent_home', '~/.z0int')).expanduser())[-count:]
    previous = {row['job']['job_id']: row['result'] for row in queue.records()}
    receipt = {'ts': now(), 'session_path': str(session), 'requested': count, 'selected': len(jobs), 'jobs': []}
    for job in jobs:
        queue.enqueue(job)
        prior = previous.get(job['job_id'])
        result = (prior if prior and prior.get('status') != 'blocked'
                  else process_job(job, config))
        identity = result['job_id']
        queue.finish(identity, result)
        receipt['jobs'].append({'job_id': identity, 'entry_id': job['entry_id'], 'sha256': job['sha256'], 'tier': job['tier'], 'status': result['status'], 'reason': result.get('reason')})
    save(Path(config['root']) / 'replay.json', receipt)
    return receipt


def worker(config, *, once=False):
    from .shadow_evals_queue import ShadowQueue
    root = Path(config['root'])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(root / 'worker.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        raise RuntimeError('Another shadow worker owns this experiment') from None
    stopping = False
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, stop)
    queue = ShadowQueue(root)
    try:
        while not stopping:
            observed = queue.ingest_sessions(Path(config.get('session_root', '~/.omp/agent/sessions')).expanduser(), Path(config.get('intent_home', '~/.z0int')).expanduser())
            for job in queue.pending():
                if stopping:
                    break
                try:
                    result = process_job(job, config)
                except Exception as error:
                    # Never log upstream text, credentials, or arbitrary exceptions.
                    result = {'status': 'error', 'error_type': type(error).__name__, 'ts': now()}
                queue.finish(job['job_id'], result)
            save(root / 'worker-status.json', {'ts': now(), 'observation': observed, 'mode': 'shadow_only', 'live_enabled': config.get('live_enabled', False)})
            if once:
                break
            time.sleep(config.get('poll_seconds', 3))
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        os.close(lock)


def report(config):
    from .shadow_evals_judge import RUBRIC, findings, law_candidates, reference_comparison
    from .shadow_evals_queue import ShadowQueue
    root = Path(config['root'])
    results = [json.loads(path.read_text()) for path in sorted((root / 'results').glob('*.json'))]
    outcomes = {row['job']['job_id']: row['outcome'] for row in ShadowQueue(root).records()}
    rows = []
    for result in results:
        for candidate in result.get('candidates', []):
            compared = config.get('bootstrap_offers') if config.get('phase') == 'bootstrap' else [f"{offer['provider']}:{offer['model']}" for offer in result['selection'].get('offers', [])]
            facts = {identity: {'text': fact['text'], 'source': f"{fact['source']}:{fact.get('source_id', '')}:{fact.get('line', '')}"} for identity, fact in result.get('fact_catalog', {}).items()}
            rows.append({**candidate, 'job_id': result['job_id'], 'context_hash': result['bundle_sha256'], 'synthetic': False, 'real_job': True, 'calibrated': result.get('calibration', {}).get('scores_enabled', False), 'category': candidate.get('judgment', {}).get('task_category', 'other'), 'task': 'intent.shadow', 'fact_catalog': facts, 'compared_models': compared, 'outcome': outcomes.get(result['job_id']) or result.get('outcome')})
    state = calibration_state(config)
    save(root / 'LAW-CANDIDATES.md', law_candidates(rows))
    save(root / 'candidate-rows.jsonl', ''.join(json.dumps(row, sort_keys=True, allow_nan=False) + '\n' for row in rows))
    scope = {'source_path': str(root / 'candidate-rows.jsonl'), 'ts': now(), 'calibration': state, 'judge': config.get('judge')}
    measured = findings(rows, scope) if state['scores_enabled'] else []
    if measured:
        from .policy.findings import append_findings
        append_findings(measured, path=root / 'findings.jsonl')
    save(root / 'findings.json', measured)
    comparison = reference_comparison(rows, config.get('reference_model', 'openai-codex:gpt-6.1-sol'))
    save(root / 'reference-comparison.json', comparison)
    lines = ['# Shadow evaluations', '', 'Shadow only. Candidate drafts never execute tools or authorize actions.', '', '## Judge calibration', '', '| Selected | Evaluated | Confirmed failures | Non-failures | Precision | Recall | Scores enabled |', '|---:|---:|---:|---:|---:|---:|---|', f"| {state.get('selected', 0)} | {state.get('evaluated', 0)} | {state.get('failures', 0)} | {state.get('nonfailures', 0)} | {state.get('precision', 'unmeasured')} | {state.get('recall', 'unmeasured')} | {state['scores_enabled']} |", '', 'No unmeasured score is used for model credit.', '', '## Last-ten replay', '', '| Entry | Tier | Status | Reason |', '|---|---|---|---|']
    replay_path = root / 'replay.json'
    replay_receipt = json.loads(replay_path.read_text()) if replay_path.exists() else {}
    for job in replay_receipt.get('jobs', []):
        lines.append(f"| {job['entry_id']} | {job['tier']} | {job['status']} | {job.get('reason') or ''} |")
    lines += ['', '## Model × rubric dimension', '', '| Provider/model | ' + ' | '.join(RUBRIC) + ' |', '|---|' + '|'.join('---:' for _ in RUBRIC) + '|']
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.get('provider'), row.get('model'))].append(row)
    for (provider, model), candidates in sorted(grouped.items()):
        values = []
        for dimension in RUBRIC:
            scores = [r['judgment']['dimensions'][dimension]['score'] for r in candidates if type(r.get('judgment', {}).get('dimensions', {}).get(dimension, {}).get('score')) is int]
            values.append(f'{sum(scores)/len(scores):.2f} (n={len(scores)})' if scores else 'unmeasured')
        lines.append(f"| {provider}/{model} | " + ' | '.join(values) + ' |')
    if not grouped:
        lines.append('| No eligible evaluated model | ' + ' | '.join('unmeasured' for _ in RUBRIC) + ' |')
    lines += ['', '## Cheaper arms versus Sol by task class', '', '| Task class | Candidate | Dimension | Paired n | Unknown n | Candidate mean | Sol mean | Delta |', '|---|---|---|---:|---:|---:|---:|---:|']
    for item in comparison['summary']:
        lines.append(f"| {item['task_class']} | {item['candidate_model']} | {item['dimension']} | {item['paired_n']} | {item['unknown_n']} | {item['candidate_mean_score']} | {item['reference_mean_score']} | {item['mean_delta']} |")
    matches = defaultdict(lambda: {'paired': 0, 'matched': 0, 'unknown': 0})
    for pair in comparison['comparisons']:
        tally = matches[(pair['task_class'], pair['candidate_model'])]
        if pair['status'] != 'paired':
            tally['unknown'] += 1
            continue
        tally['paired'] += 1
        tally['matched'] += int(all(item['delta'] >= 0 for item in pair['dimensions'].values()))
    lines += ['', '### Relative offload matches', '', '| Task class | Candidate | Not worse than Sol in every dimension | Paired | Unknown |', '|---|---|---:|---:|---:|']
    for (task_class, candidate), tally in sorted(matches.items()):
        lines.append(f"| {task_class} | {candidate} | {tally['matched']} | {tally['paired']} | {tally['unknown']} |")
    lines += ['', 'A relative match is not an absolute quality gate or permission to offload. Sol can also fail the rubric.']
    lines += ['', 'These are paired rubric comparisons, not execution success. Context-failed arms remain unknown.', '', 'Calibration cohort and precision/recall Wilson intervals, including interval widths:', '', '```json', json.dumps({key: value for key, value in state.items() if key not in ('judge', 'rubric_sha256')}, indent=2), '```', '']
    lines += ['', '## Enablement', '', 'Not enabled unless calibration, remote tests, ten real-message replay and eligible offers are proven.', '', 'Unit: `systemd/z0-shadow-evals.service`.', '', 'Enable command (only after those gates pass): `systemctl --user enable --now z0-shadow-evals.service`.', '', 'Read `delivery.json` for exact Draft PR, commit and remote job evidence; absent fields are unverified.', '', 'Privacy exclusions and missing routing measurements are retained per job in `results/`.', 'No laws are auto-applied. Review `LAW-CANDIDATES.md` with Kevin.', '']
    delivery = root / 'delivery.json'
    if delivery.exists():
        lines += ['## Delivery evidence', '', '```json', delivery.read_text().strip(), '```', '']
    save(root / 'REPORT.md', '\n'.join(lines))
    return {'results': len(results), 'candidate_rows': len(rows), 'scores_enabled': state['scores_enabled'], 'report': str(root / 'REPORT.md')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('worker', 'replay', 'calibrate', 'report'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--session', default=str(DEFAULT_SESSION))
    parser.add_argument('--audit-root', default='/mnt/zer0models/workspace/zer0/data/operations/ask-audit')
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == 'worker':
        worker(config, once=args.once)
        result = {'status': 'worker_stopped'}
    elif args.command == 'replay':
        result = replay(config, args.session, args.count)
    elif args.command == 'calibrate':
        result = calibrate(config, args.audit_root)
    else:
        result = report(config)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
