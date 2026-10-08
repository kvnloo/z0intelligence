"""One frozen real-Hermes evidence-dependence pair; default prepares only.

Reuses the existing native worker, one-request proxy and exact-context admission
policy. No network code or alternative provider policy is implemented here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import subprocess
import time

from discriminator import HERE, digest, module_at, prepare, save
from frozen_check import grade

CASES = ('complete', 'missing_source_identity')


def assess_pair(arms: dict) -> dict:
    """Conservative attribution from fixed-case independent outcomes and receipts."""
    problems = []
    if set(arms) != set(CASES):
        problems.append('incomplete_or_unexpected_arms')
    models, providers = [], []
    for case in CASES:
        row = arms.get(case)
        if not isinstance(row, dict):
            continue
        for field, expected in (('provider_calls', 1), ('returncode', 0),
                                ('exact_selected_context_in_forwarded_request', True),
                                ('resource_comparison_eligible', True)):
            if type(row.get(field)) is not type(expected) or row.get(field) != expected:
                problems.append(case + ':' + field)
        if row.get('error_type') is not None:
            problems.append(case + ':execution_error')
        if row.get('physical_response_ok') is False:
            problems.append(case + ':physical_response_not_success')
        models.append(row.get('served_model'))
        providers.append(row.get('served_provider'))
    for field, values in (('served_model', models), ('served_provider', providers)):
        if len(values) != 2 or not all(isinstance(v, str) and v for v in values) or values[0] != values[1]:
            problems.append('unknown_or_changed:' + field)
    if problems:
        decision = 'UNIDENTIFIABLE'
    elif all(arms[case].get('outcome', {}).get('verified_success') is True for case in CASES):
        decision = 'CAUSAL_DISCRIMINATOR_PASSED'
    else:
        decision = 'CAUSAL_DISCRIMINATOR_FAILED'

    def consumed(field):
        values = []
        for case in CASES:
            usage = arms.get(case, {}).get('usage')
            value = usage.get(field) if isinstance(usage, dict) else None
            if type(value) is not int or value < 0:
                return None
            values.append(value)
        return sum(values)

    return {'schema': 'hermes.context_causal_pair.v1', 'decision': decision,
            'attribution_problems': problems,
            'case_outcomes': {case: arms.get(case, {}).get('outcome') for case in CASES},
            'served_models': models, 'served_providers': providers,
            'actual_consumed_prompt_tokens': consumed('prompt_tokens'),
            'actual_consumed_completion_tokens': consumed('completion_tokens'),
            'speedup_or_noninferiority_claim': False, 'thermal_selection_claim': False,
            'model_necessity_established': False, 'retry_authorized': False,
            'scope': 'one inspected development pair; semantic evidence-dependence discriminator only',
            'decision_boundary': 'Retain the deterministic same-ref context implementation. No additional RLM architecture is justified by byte-identical rendering.'}


def shared_driver(phase_c: Path):
    # Existing driver imports its sibling modules by their original names.
    sys.path.insert(0, str(phase_c))
    return module_at('qualified_native_live_baseline', phase_c / 'live_baseline.py')


def prepare_pair(args) -> dict:
    args.out.mkdir(parents=True, exist_ok=False)
    study = args.out / 'study'
    prepare(args.phase_c, args.hermes_repo, study)
    driver = shared_driver(args.phase_c)
    if driver.MODEL != 'openrouter/free' or driver.ENDPOINT != 'https://openrouter.ai/api/v1/chat/completions':
        raise ValueError('only the predeclared free route is permitted')
    source_files = {
        'pair_runner': Path(__file__), 'checker': HERE / 'frozen_check.py',
        'discriminator': HERE / 'discriminator.py',
        'shared_driver': args.phase_c / 'live_baseline.py',
        'shared_boundary': args.phase_c / 'selection_boundary.py',
        'native_recorder': args.hermes_repo / 'evals/factory_state/driver.py',
        'native_composition': args.hermes_repo / 'agent/turn_context.py',
        'native_spill': args.hermes_repo / 'tools/hook_output_spill.py',
        'context_resolver': Path(sys.modules['z0int.context_resolve'].__file__),
    }
    freeze = {'schema': 'hermes.context_pair_preregistration.v1',
              'status': 'FROZEN_BEFORE_PROVIDER_CALLS', 'provider_calls_at_freeze': 0,
              'source_files': {name: {'path': str(path.resolve()), 'sha256': digest(path.read_bytes())}
                               for name, path in source_files.items()},
              'study_freeze_sha256': digest((study / 'freeze.json').read_bytes()),
              'requested_model': driver.MODEL, 'endpoint': driver.ENDPOINT,
              'max_physical_posts_per_arm': 1, 'max_physical_posts_total': 2,
              'max_serialized_request_bytes': 20000, 'max_output_tokens': 1024,
              'max_wall_seconds_per_arm': 120, 'max_paid_cost_usd': 0,
              'allow_fallbacks': False, 'automatic_retries': False,
              'case_order': list(CASES), 'fresh_profile_per_arm': True,
              'same_actual_model_and_provider_required': True,
              'checker_outside_worker_context': True,
              'fixed_pairing': 'full evidence versus source-identity withheld; same prompt and renderer',
              'stop': 'Stop after one pair. Do not spend the second attempt after an ineligible first run. Never retry to repair a failed result.',
              'no_claims': ['population noninferiority', 'speedup', 'thermal advantage', 'model necessity']}
    save(args.out / 'pair-freeze.json', freeze)
    return freeze


def validate_freeze(out: Path) -> dict:
    freeze = json.loads((out / 'pair-freeze.json').read_text())
    for source in freeze['source_files'].values():
        if digest(Path(source['path']).read_bytes()) != source['sha256']:
            raise ValueError('frozen implementation changed: ' + source['path'])
    study = out / 'study'
    if digest((study / 'freeze.json').read_bytes()) != freeze['study_freeze_sha256']:
        raise ValueError('study freeze changed')
    study_freeze = json.loads((study / 'freeze.json').read_text())
    hermes_root = Path(freeze['source_files']['native_composition']['path']).parent.parent
    revision = subprocess.check_output(['git', '-C', str(hermes_root), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != study_freeze['hermes_revision']:
        raise ValueError('frozen Hermes revision changed')
    for case in CASES:
        if digest((study / case / 'context.txt').read_bytes()) != study_freeze['cases'][case]['context_sha256']:
            raise ValueError('prepared context changed')
        if digest((study / case / 'prompt.txt').read_bytes()) != study_freeze['prompt_sha256']:
            raise ValueError('prepared prompt changed')
        selection = json.loads((study / case / 'selection.json').read_text())
        if selection['context'] != (study / case / 'context.txt').read_text():
            raise ValueError('selection and frozen context differ')
    return freeze


def replay_pair(out: Path) -> dict:
    """Regrade saved answers and validate actual physical observations; no calls."""
    freeze = validate_freeze(out)
    boundary = module_at('pair_replay_boundary', Path(freeze['source_files']['shared_boundary']['path']))
    arms, evidence_hashes = {}, {}
    for case in CASES:
        arm = out / case
        if not (arm / 'receipt.json').exists():
            continue
        receipt = json.loads((arm / 'receipt.json').read_text())
        answer = json.loads((arm / 'answer.json').read_text())
        independently_replayed = grade(case, answer)
        if receipt['outcome'] != independently_replayed:
            raise ValueError('saved outcome differs from frozen independent checker')
        calls = json.loads((arm / 'physical-calls.json').read_text())
        physical = [row for row in calls if row.get('kind') == 'inference' and row.get('upstream_sent') is True]
        if len(physical) != receipt['provider_calls']:
            raise ValueError('physical attempt accounting mismatch')
        context = (out / 'study' / case / 'context.txt').read_text()
        exact = len(physical) == 1 and boundary.request_context_occurrences(context, physical[0]['forwarded_request']) == 1
        if exact != receipt['exact_selected_context_in_forwarded_request']:
            raise ValueError('observed context witness mismatch')
        receipt['physical_response_ok'] = len(physical) == 1 and physical[0].get('status_code') == 200
        if physical:
            observed = physical[0]
            if any(receipt.get(key) != observed.get(key) for key in ('served_model', 'served_provider', 'usage')):
                raise ValueError('model identity or usage differs from physical receipt')
            forwarded = observed['forwarded_request']
            if (forwarded.get('model') != 'openrouter/free' or forwarded.get('tools')
                    or forwarded.get('stream') or type(forwarded.get('max_tokens')) is not int
                    or not 1 <= forwarded['max_tokens'] <= 1024
                    or forwarded.get('provider') != {'allow_fallbacks': False,
                                                     'max_price': {'prompt': 0, 'completion': 0}}):
                raise ValueError('observed route, authority, or budget differs from frozen policy')
        arms[case] = receipt
        evidence_hashes[case] = {name: digest((arm / name).read_bytes())
                                for name in ('receipt.json', 'answer.json', 'physical-calls.json', 'freeze.json')}
    result = assess_pair(arms)
    result['pair_freeze_sha256'] = digest((out / 'pair-freeze.json').read_bytes())
    result['case_evidence_sha256'] = evidence_hashes
    result['replay_is_new_provider_work'] = False
    result['provider_calls'] = sum(arm.get('provider_calls', 0) for arm in arms.values())
    if not arms and (out / 'provider-block.json').exists():
        result['status'] = 'BLOCKED_PROVIDER_RATE_LIMIT'
        result['decision'] = 'NOT_RUN'
        result['attribution_problems'] = ['provider_rate_limit_prevented_execution']
        result['provider_block_sha256'] = digest((out / 'provider-block.json').read_bytes())
    return result


def execute_pair(args) -> dict:
    if (args.out / 'provider-block.json').exists():
        raise ValueError('provider rate-limit block is active; no attempts are permitted until root confirms free quota is available and clears the block')
    freeze = validate_freeze(args.out)
    if (args.phase_c / 'live_baseline.py').resolve() != Path(freeze['source_files']['shared_driver']['path']):
        raise ValueError('execution driver path differs from frozen source')
    if (args.hermes_repo / 'agent/turn_context.py').resolve() != Path(freeze['source_files']['native_composition']['path']):
        raise ValueError('execution Hermes tree differs from frozen source')
    if args.credential_file is None or not args.credential_file.is_file():
        raise ValueError('authorized credential-file is required for explicit execution')
    # Exclusive marker survives a crash; replay cannot accidentally spend again.
    with (args.out / 'execution-started.json').open('x') as stream:
        json.dump({'max_total_physical_posts': 2, 'automatic_retry_authorized': False}, stream)
    started = time.perf_counter_ns()
    driver = shared_driver(args.phase_c)
    first_stop = None
    for case in CASES:
        prepared = args.out / 'study' / case
        selection = json.loads((prepared / 'selection.json').read_text())
        run_args = SimpleNamespace(out=args.out / case, hermes_repo=args.hermes_repo,
                                  execute=True, credential_file=args.credential_file,
                                  max_output_tokens=1024)
        try:
            receipt = driver.run(run_args, prepared_case={
                'selection': selection, 'prompt': (prepared / 'prompt.txt').read_text(),
                'checker': lambda answer, case_id=case: grade(case_id, answer),
                'checker_source': HERE / 'frozen_check.py',
                'metadata': {'case_id': case, 'new_task': True,
                             'pair_freeze_sha256': digest((args.out / 'pair-freeze.json').read_bytes())}})
        except Exception as error:
            save(args.out / 'execution-error.json', {'case': case, 'error_type': type(error).__name__,
                                                    'automatic_retry_authorized': False})
            first_stop = 'driver exception; subsequent arm not run'
            break
        if case == CASES[0] and not (receipt.get('provider_calls') == 1 and receipt.get('returncode') == 0
                and receipt.get('exact_selected_context_in_forwarded_request') is True
                and receipt.get('resource_comparison_eligible') is True
                and isinstance(receipt.get('served_model'), str) and receipt['served_model']
                and isinstance(receipt.get('served_provider'), str) and receipt['served_provider']):
            first_stop = 'first arm ineligible; second arm not run'
            break
    result = replay_pair(args.out)
    save(args.out / 'pair-result.json', result)
    save(args.out / 'pair-execution-timing.json', {'wall_ns': time.perf_counter_ns() - started,
                                                  'stop_reason': first_stop})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase-c', type=Path)
    parser.add_argument('--hermes-repo', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--credential-file', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--replay', action='store_true')
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(replay_pair(args.out), sort_keys=True))
    else:
        if args.phase_c is None or args.hermes_repo is None:
            parser.error('--phase-c and --hermes-repo are required for preparation/execution')
        if not (args.out / 'pair-freeze.json').exists():
            prepare_pair(args)
        if args.execute:
            print(json.dumps(execute_pair(args), sort_keys=True))
        else:
            validate_freeze(args.out)
            print(json.dumps({'status': 'PREPARED_NOT_EXECUTED', 'provider_calls': 0}, sort_keys=True))
