"""Bounded development cohort. Default prepares; only root may request execution.

Reuses the existing driver, ContextPacket renderer and independent task checker.
No provider client, runtime service, model selector, or retry loop is added.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PHASE_C = HERE.parent
sys.path[:0] = [str(HERE), str(PHASE_C), str(PHASE_C / 'causal_context')]
import discriminator
import live_baseline as driver
import selection_boundary as boundary
from cohort_check import grade

digest, save = discriminator.digest, discriminator.save
PROTOCOL = json.loads((HERE / 'protocol.json').read_text())
CASES = tuple(PROTOCOL['case_order'])
SENTINEL = '<EXACT_FROZEN_EVIDENCE_BLOCK>'
REFERENCE_WIRE = PHASE_C / 'results/provider-requalification-20261002/runs/nous-cheap-solar-json-object/native-request.bin'


def read(path: Path):
    return json.loads(path.read_bytes())


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def render_selection(pool: dict, size: str) -> dict:
    packet = discriminator.packet_from(pool)
    legal = [ref.source_id for ref in packet.evidence if ref.source_id != 'limits']
    chosen = legal if size == 'full' else [key for key in legal if key in PROTOCOL['minimal_optional_ids']]
    return boundary.materialize(packet, chosen, pinned_ids=['limits'], legal_optional_ids=legal,
        expected_pool_digest=boundary.freeze_digest(packet), budget=1000000,
        count_tokens=lambda text: len(text.encode()),
        tokenizer_identity='fixture_utf8_bytes_not_provider_tokens')


def normalized_wire(raw: bytes, context: str, prompt: str) -> dict:
    """Normalize ONLY the exact supplied evidence; no path/date/system masking."""
    body = json.loads(raw)
    messages = body.get('messages')
    if (not isinstance(messages, list) or len(messages) != 2
            or messages[0].get('role') != 'system' or messages[1].get('role') != 'user'
            or not isinstance(messages[0].get('content'), str) or not messages[0]['content']
            or messages[1].get('content') != prompt + '\n\n' + context
            or boundary.request_context_occurrences(context, body) != 1):
        raise driver.BudgetRejected('full native system/user contract changed')
    result = copy.deepcopy(body)
    result['messages'][1]['content'] = prompt + '\n\n' + SENTINEL
    return result


def invariant_policy(original_policy, *, template_path: Path, prompt: str):
    """Add a scoped admission assertion to the existing one-POST policy."""
    class FixedWirePolicy(original_policy):
        def admit(self, raw: bytes) -> dict:
            normalized = normalized_wire(raw, self.expected_context, prompt)
            if template_path.exists() and read(template_path) != normalized:
                raise driver.BudgetRejected('non-evidence native wire changed')
            forwarded = super().admit(raw)
            if not template_path.exists():
                with template_path.open('x') as stream:
                    json.dump(normalized, stream, indent=2, sort_keys=True)
                    stream.write('\n')
            return forwarded
    return FixedWirePolicy


def prepare(args) -> dict:
    started = time.perf_counter_ns()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    # Reuse and retain unchanged native preparation for all three original controls.
    discriminator.prepare(PHASE_C, args.hermes_repo, out / 'controls')
    pools = discriminator.build_cases(read(PHASE_C / 'causal_context/fixtures/development_pool.json'))
    prepared = out / 'prepared'
    prepared.mkdir()
    for case_id in CASES:
        size, control = case_id.split('-', 1)
        case_dir = prepared / case_id
        case_dir.mkdir()
        selection = render_selection(pools[control], size)
        admitted, admission = discriminator.native_hook_admission(selection['context'], args.hermes_repo,
            driver.study_spill_config(selection['context'])['max_chars'])
        if admitted != selection['context']:
            raise ValueError('native collection did not preserve exact context')
        if size == 'full' and selection != read(out / 'controls' / control / 'selection.json'):
            raise ValueError('full arm differs from unchanged prepared control')
        save(case_dir / 'selection.json', selection)
        save(case_dir / 'native-admission.json', admission)
        (case_dir / 'context.txt').write_text(selection['context'])
        (case_dir / 'prompt.txt').write_text(discriminator.PROMPT)
    reference = read(REFERENCE_WIRE)
    estimates = {}
    for control, pool in pools.items():
        for size in ('full', 'minimal'):
            selection = render_selection(pool, size)
            request = copy.deepcopy(reference)
            request['messages'][1]['content'] = discriminator.PROMPT + '\n\n' + selection['context']
            estimate = len(driver.serialize_request(request))
            estimates[size + '-' + control] = {'context_bytes': len(selection['context'].encode()),
                'estimated_serialized_request_bytes': estimate,
                'within_unchanged_byte_cap': estimate <= PROTOCOL['max_serialized_request_bytes']}
    if estimates['full-contradictory_source_identity']['within_unchanged_byte_cap']:
        raise ValueError('offline exclusion rationale changed; review a new protocol')
    save(out / 'admission-estimates.json', {'scope': 'Prior observed native boilerplate plus candidate prompt/context; NOT actual cohort transport',
        'reference_wire_sha256': digest(REFERENCE_WIRE.read_bytes()), 'cases': estimates,
        'excluded_before_inference': ['full-contradictory_source_identity']})
    route = driver.validate_route(read(args.route_file), PROTOCOL['max_output_tokens'])
    if route['model'] != PROTOCOL['model'] or route['endpoint'] != PROTOCOL['provider_endpoint']:
        raise ValueError('only the frozen qualified Solar route is permitted')
    save(out / 'route.json', route)
    import z0int.context_resolve as resolver
    sources = [Path(__file__), HERE / 'protocol.json', HERE / 'cohort_check.py',
        PHASE_C / 'causal_context/discriminator.py', PHASE_C / 'causal_context/frozen_check.py',
        PHASE_C / 'causal_context/fixtures/development_pool.json', PHASE_C / 'causal_context/fixtures/sources.json',
        PHASE_C / 'live_baseline.py', PHASE_C / 'selection_boundary.py', PHASE_C / 'replay_bridge.py',
        PHASE_C / 'sources.json', REFERENCE_WIRE, Path(resolver.__file__), args.hermes_repo / 'evals/factory_state/driver.py',
        args.hermes_repo / 'agent/turn_context.py', args.hermes_repo / 'tools/hook_output_spill.py']
    files = [p for directory in (out / 'controls', prepared) for p in directory.rglob('*') if p.is_file()]
    files.append(out / 'route.json')
    files.append(out / 'admission-estimates.json')
    freeze = {'schema': 'hermes.verified_context_cohort.freeze.v1', 'status': 'FROZEN_BEFORE_INFERENCE',
        'provider_calls_at_freeze': 0, 'protocol': PROTOCOL, 'out': str(out),
        'working_directory': str(Path.cwd().resolve()), 'staging_path': str(out / '_staging'),
        'hermes_repo': str(args.hermes_repo.resolve()),
        'hermes_revision': read(out / 'controls/freeze.json')['hermes_revision'],
        'source_files': {str(p.resolve()): digest(p.read_bytes()) for p in sources},
        'prepared_files': {str(p.relative_to(out)): digest(p.read_bytes()) for p in files},
        'route_sha256': driver.route_digest(route),
        'estimated_cost_at_all_token_caps_usd': len(CASES) * driver.estimated_cost(route, PROTOCOL['max_output_tokens']),
        'estimate_is_billing_cap': False,
        'original_checker_unchanged': True, 'visibility_wrapper_frozen_before_inference': True,
        'model_visible_case_labels': False}
    save(out / 'cohort-freeze.json', freeze)
    save(out / 'preparation-timing.json', {'wall_ns': time.perf_counter_ns() - started,
        'provider_calls': 0, 'scope': 'offline preparation only'})
    return freeze


def validate_freeze(out: Path) -> dict:
    freeze = read(out / 'cohort-freeze.json')
    if freeze['protocol'] != PROTOCOL or freeze['out'] != str(out.resolve()):
        raise ValueError('protocol or physical output path differs from freeze')
    for path, expected in freeze['source_files'].items():
        if digest(Path(path).read_bytes()) != expected:
            raise ValueError('frozen source changed: ' + path)
    for relative, expected in freeze['prepared_files'].items():
        if digest((out / relative).read_bytes()) != expected:
            raise ValueError('frozen preparation changed: ' + relative)
    revision = subprocess.check_output(['git', '-C', freeze['hermes_repo'], 'rev-parse', 'HEAD'], text=True).strip()
    if revision != freeze['hermes_revision']:
        raise ValueError('frozen Hermes revision changed')
    pools = discriminator.build_cases(read(PHASE_C / 'causal_context/fixtures/development_pool.json'))
    for case_id in CASES:
        size, control = case_id.split('-', 1)
        selection = read(out / 'prepared' / case_id / 'selection.json')
        if selection != render_selection(pools[control], size):
            raise ValueError('prepared selection no longer matches legal exact-ref policy')
    return freeze


def finite_nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def replay_case(out: Path, case_id: str, freeze: dict) -> dict:
    run = out / 'runs' / case_id
    calls_saved = (run / 'physical-calls.json').exists()
    calls = read(run / 'physical-calls.json') if calls_saved else []
    physical = [call for call in calls if call.get('upstream_sent') is True]
    row = {'case_id': case_id, 'physical_posts': len(physical) if calls_saved else None, 'completed': False,
           'eligible': False, 'outcome': None, 'usage': None, 'issues': []}
    if not (run / 'receipt.json').exists():
        row['issues'].append('incomplete_driver_artifacts')
        return row
    receipt, case_freeze = read(run / 'receipt.json'), read(run / 'freeze.json')
    if receipt['provider_calls'] != len(physical) or len(physical) > 1:
        raise ValueError('physical attempt accounting mismatch')
    if digest((run / 'freeze.json').read_bytes()) != receipt['freeze_sha256']:
        raise ValueError('per-case freeze changed')
    context = (out / 'prepared' / case_id / 'context.txt').read_text()
    prompt = (out / 'prepared' / case_id / 'prompt.txt').read_text()
    selection = read(out / 'prepared' / case_id / 'selection.json')
    if ((run / 'prompt.txt').read_text() != prompt or read(run / 'prepared/selection.json') != selection
            or case_freeze['context_sha256'] != digest(context.encode())
            or case_freeze['prompt_sha256'] != digest(prompt.encode())
            or case_freeze['driver_sha256'] != digest((PHASE_C / 'live_baseline.py').read_bytes())
            or case_freeze['checker_sha256'] != digest((HERE / 'cohort_check.py').read_bytes())
            or case_freeze['max_output_tokens'] != PROTOCOL['max_output_tokens']
            or case_freeze['max_serialized_request_bytes'] != PROTOCOL['max_serialized_request_bytes']
            or case_freeze['max_physical_inference_attempts'] != 1
            or case_freeze['response_format_requested'] != PROTOCOL['response_format']
            or case_freeze['route_sha256'] != freeze['route_sha256']
            or read(run / 'route.json') != read(out / 'route.json')):
        raise ValueError('case preparation or policy differs from frozen cohort')
    answer = read(run / 'answer.json')
    worker_path = run / 'worker-result.json'
    try:
        worker_answer = json.loads(read(worker_path)['result']['final_response']) if worker_path.exists() else None
    except (KeyError, TypeError, ValueError):
        worker_answer = None
    if worker_answer != answer or grade(case_id, answer) != receipt['outcome']:
        raise ValueError('saved answer/outcome differs from independent replay')
    row.update(completed=True, outcome=grade(case_id, answer), total_wall_ns=receipt['total_wall_ns'],
        preparation_wall_ns=receipt['preparation_wall_ns'], execution_wall_ns=receipt['execution_window_wall_ns'])
    if len(physical) != 1:
        row['issues'].append('expected_one_physical_post')
        return row
    call = physical[0]
    if call.get('kind') != 'inference' or call.get('physical_attempt') != 1:
        raise ValueError('unexpected physical call kind')
    for name, key in [('native-request.bin', 'native'), ('forwarded-request.bin', 'forwarded')]:
        raw = (run / name).read_bytes()
        if (digest(raw) != call[key + '_wire_sha256'] or len(raw) != call[key + '_wire_bytes']
                or json.loads(raw) != call[key + '_request']):
            raise ValueError('raw wire does not match physical-call record')
        if normalized_wire(raw, context, prompt) != read(out / 'wire-template.json'):
            raise ValueError('actual full system or non-evidence wire changed')
        if len(raw) > PROTOCOL['max_serialized_request_bytes']:
            raise ValueError('actual wire exceeds byte cap')
    native = call['native_request']
    admitted = driver.OneRequestPolicy(expected_context=context, max_output_tokens=4096,
        route=read(out / 'route.json'), require_json_object=True).admit((run / 'native-request.bin').read_bytes())
    if admitted != call['forwarded_request'] or call.get('endpoint') != PROTOCOL['provider_endpoint']:
        raise ValueError('actual forwarding differs from reused policy')
    response = call.get('response') or {}
    for key in ('served_model', 'served_provider', 'usage'):
        response_key = {'served_model': 'model', 'served_provider': 'provider'}.get(key, key)
        if receipt.get(key) != call.get(key) or call.get(key) != response.get(response_key):
            raise ValueError('response identity/usage differs from records')
    if call.get('status_code') == 200:
        try:
            response_answer = json.loads(response['choices'][0]['message']['content'])
        except (ValueError, KeyError, TypeError, IndexError):
            response_answer = None
        if response_answer != answer:
            raise ValueError('answer differs from actual provider response')
    usage = call.get('usage')
    prompt_tokens = usage.get('prompt_tokens') if isinstance(usage, dict) else None
    completion_tokens = usage.get('completion_tokens') if isinstance(usage, dict) else None
    cost = usage.get('cost') if isinstance(usage, dict) else None
    resources = (type(prompt_tokens) is int and 0 <= prompt_tokens <= driver.MAX_INPUT_TOKENS
        and type(completion_tokens) is int and 0 <= completion_tokens <= PROTOCOL['max_output_tokens']
        and finite_nonnegative(cost) and cost <= read(out / 'route.json')['max_estimated_cost_usd'])
    if receipt['resource_comparison_eligible'] is not resources or receipt['reported_cost'] != cost:
        raise ValueError('resource eligibility/cost differs from independent calculation')
    checks = {'response_not_success': call.get('status_code') == 200,
        'worker_execution_error': receipt.get('returncode') == 0 and receipt.get('error_type') is None,
        'unknown_or_changed_model': call.get('served_model') == PROTOCOL['model'],
        'resources_unknown_or_out_of_bounds': resources,
        'repeated_local_inference': not any(c.get('kind') == 'blocked_inference' for c in calls)}
    row['issues'].extend(key for key, passed in checks.items() if not passed)
    row.update(eligible=not row['issues'], usage=usage, served_model=call.get('served_model'),
        served_provider=call.get('served_provider'), provider_wall_ns=call.get('wall_ns'),
        system_sha256=digest(native['messages'][0]['content'].encode()),
        normalized_wire_sha256=digest(canonical(normalized_wire((run / 'native-request.bin').read_bytes(), context, prompt))),
        native_wire_bytes=call['native_wire_bytes'], forwarded_wire_bytes=call['forwarded_wire_bytes'])
    return row


def replay(out: Path) -> dict:
    freeze = validate_freeze(out)
    rows = {case_id: replay_case(out, case_id, freeze) for case_id in CASES
            if (out / 'runs' / case_id).exists() or (out / 'attempts' / (case_id + '.json')).exists()}
    known_posts = sum(row['physical_posts'] or 0 for row in rows.values())
    accounting_complete = all(row['physical_posts'] is not None for row in rows.values())
    posts = known_posts if accounting_complete else None
    if known_posts > PROTOCOL['max_physical_posts_total']:
        raise ValueError('cohort physical attempt budget exceeded')
    pairs = {}
    for control in ('complete', 'missing_source_identity'):
        full, minimal = (rows.get(size + '-' + control) for size in ('full', 'minimal'))
        eligible = bool(full and minimal and full['eligible'] and minimal['eligible'])
        correct = eligible and full['outcome']['verified_success'] and minimal['outcome']['verified_success']
        pairs[control] = {'eligible': eligible, 'both_verified_success': bool(correct),
            'prompt_token_reduction': full['usage']['prompt_tokens'] - minimal['usage']['prompt_tokens'] if eligible else None,
            'reported_cost_reduction_usd': full['usage']['cost'] - minimal['usage']['cost'] if eligible else None,
            'observed_total_wall_reduction_ns': full['total_wall_ns'] - minimal['total_wall_ns'] if eligible else None,
            'verified_prompt_token_reduction': bool(correct and full['usage']['prompt_tokens'] > minimal['usage']['prompt_tokens'])}
    def total(field):
        if not accounting_complete:
            return None
        values = [(row['usage'] or {}).get(field) for row in rows.values() if row['physical_posts']]
        return sum(values) if all(finite_nonnegative(v) for v in values) else None
    eligible_all = len(rows) == len(CASES) and all(row['eligible'] for row in rows.values())
    correct_all = eligible_all and all(row['outcome']['verified_success'] for row in rows.values())
    return {'schema': 'hermes.verified_context_cohort.result.v1',
        'status': 'NOT_RUN' if not rows else 'COMPLETE' if len(rows) == len(CASES) else 'INCOMPLETE',
        'case_outcomes': rows, 'pairs': pairs, 'physical_posts_including_failures': posts,
        'known_recorded_physical_posts': known_posts, 'physical_attempt_accounting_complete': accounting_complete,
        'known_work_prompt_tokens': total('prompt_tokens'), 'known_work_completion_tokens': total('completion_tokens'),
        'known_work_reported_cost_usd': total('cost'),
        'usage_complete_for_every_physical_post': accounting_complete and all(row['usage'] is not None for row in rows.values() if row['physical_posts']),
        'sum_driver_total_wall_ns': sum(row.get('total_wall_ns', 0) for row in rows.values()),
        'offline_preparation_wall_ns': read(out / 'preparation-timing.json')['wall_ns'],
        'all_cases_verified': bool(correct_all),
        'all_pairs_verified_prompt_token_reduction': all(pair['verified_prompt_token_reduction'] for pair in pairs.values()),
        'full_wire_comparison_eligible': eligible_all,
        'backend_provider_causal_attribution': False,
        'backend_provider_limit': 'Nous response provider may be null; configured endpoint is not backend identity',
        'speedup_or_population_claim': False, 'holdout': False, 'automatic_retry_authorized': False,
        'scope': PROTOCOL['scope'], 'replay_provider_calls': 0,
        'cohort_freeze_sha256': digest((out / 'cohort-freeze.json').read_bytes())}


def execute(args) -> dict:
    out = args.out.resolve()
    freeze = validate_freeze(out)
    if str(Path.cwd().resolve()) != freeze['working_directory']:
        raise ValueError('execution cwd differs from frozen system-boilerplate path')
    if args.nous_auth_home is None or not args.nous_auth_home.is_dir():
        raise ValueError('explicit external Nous auth profile required for root execution')
    driver.external_secret_path(args.nous_auth_home)  # Location check only; no credentials read here.
    with (out / 'execution-started.json').open('x') as stream:
        json.dump({'max_physical_posts': len(CASES), 'automatic_retries': False}, stream)
    started = time.perf_counter_ns()
    (out / 'runs').mkdir()
    (out / 'attempts').mkdir()
    staging = Path(freeze['staging_path'])
    if staging.exists():
        raise ValueError('staging directory already exists; preserve it and stop')
    stop = None
    for case_id in CASES:
        validate_freeze(out)
        with (out / 'attempts' / (case_id + '.json')).open('x') as stream:
            json.dump({'case_id': case_id, 'max_physical_posts': 1, 'automatic_retries': False}, stream)
        prepared = out / 'prepared' / case_id
        run_args = SimpleNamespace(out=staging, hermes_repo=Path(freeze['hermes_repo']), execute=True,
            nous_auth_home=args.nous_auth_home, credential_file=None, route_file=out / 'route.json',
            max_output_tokens=PROTOCOL['max_output_tokens'], require_json_object=True)
        exception = None
        try:
            policy = invariant_policy(driver.OneRequestPolicy, template_path=out / 'wire-template.json',
                                      prompt=(prepared / 'prompt.txt').read_text())
            with patch.object(driver, 'OneRequestPolicy', policy):
                driver.run(run_args, prepared_case={
                    'selection': read(prepared / 'selection.json'), 'prompt': (prepared / 'prompt.txt').read_text(),
                    'checker': lambda answer, case_id=case_id: grade(case_id, answer),
                    'checker_source': HERE / 'cohort_check.py',
                    'metadata': {'case_id': case_id, 'cohort_freeze_sha256': digest((out / 'cohort-freeze.json').read_bytes())}})
        except Exception as error:
            exception = type(error).__name__
        finally:
            if staging.exists():
                staging.rename(out / 'runs' / case_id)
        if exception:
            save(out / 'execution-error.json', {'case_id': case_id, 'error_type': exception,
                'automatic_retry_authorized': False, 'remaining_cases_not_attempted': True})
            stop = 'driver_exception'
            break
        row = replay_case(out, case_id, freeze)
        if not row['eligible']:
            stop = 'case_ineligible:' + case_id
            break
        # A semantic failure remains an observation, never a reason to repair or
        # replace the case. Continue all remaining predeclared controls.
    save(out / 'execution-timing.json', {'wall_ns': time.perf_counter_ns() - started, 'stop_reason': stop})
    result = replay(out)
    save(out / 'cohort-result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--hermes-repo', type=Path)
    parser.add_argument('--route-file', type=Path)
    parser.add_argument('--nous-auth-home', type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--execute', action='store_true')
    modes.add_argument('--replay', action='store_true')
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.replay:
        result = replay(args.out)
    elif args.execute:
        if not (args.out / 'cohort-freeze.json').exists():
            parser.error('prepare and independently review a frozen cohort before root execution')
        result = execute(args)
    else:
        if args.hermes_repo is None or args.route_file is None:
            parser.error('--hermes-repo and --route-file required for preparation')
        prepare(args)
        validate_freeze(args.out)
        result = {'status': 'PREPARED_NOT_EXECUTED', 'physical_posts': 0, 'cases': list(CASES)}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
