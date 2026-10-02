"""Portable frozen-judge replay of two saved Hermes studies. Standard library only.

Never imports a runtime driver, reads auth, calls a provider, or rewrites an answer.
Absolute paths in historical freezes are mapped to explicit input archives.
"""
from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sys
import types

ORIGINAL_CHECKER = '7c646ba5040a0a5c3090c8f9132aefcf4b7a9f93d243271b35cbc45428fcfdb5'
VISIBILITY_CHECKER = '5cd620aee008c42f2691c63f97a4e129533899509fe0de063885f940772fa255'
CASES = ('full-complete', 'minimal-complete', 'full-missing_source_identity',
         'minimal-missing_source_identity', 'minimal-contradictory_source_identity')
SENTINEL = '<EXACT_FROZEN_EVIDENCE_BLOCK>'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique)


def snapshot_index(directory):
    return {digest(path.read_bytes()): path for path in directory.iterdir() if path.is_file()}


def frozen_judge(snapshots):
    """Execute only the unchanged two checker sources, pinned independently here."""
    for expected in (ORIGINAL_CHECKER, VISIBILITY_CHECKER):
        require(expected in snapshots, 'missing exact frozen checker snapshot: ' + expected)
    original = types.ModuleType('frozen_check')
    exec(compile(snapshots[ORIGINAL_CHECKER].read_bytes(), 'frozen_check.py', 'exec'), original.__dict__)
    wrapper = types.ModuleType('saved_visibility_checker')
    wrapper.__file__ = str(snapshots[VISIBILITY_CHECKER])
    previous, old_path = sys.modules.get('frozen_check'), list(sys.path)
    try:
        sys.modules['frozen_check'] = original
        exec(compile(snapshots[VISIBILITY_CHECKER].read_bytes(), 'cohort_check.py', 'exec'), wrapper.__dict__)
    finally:
        sys.path[:] = old_path
        if previous is None:
            sys.modules.pop('frozen_check', None)
        else:
            sys.modules['frozen_check'] = previous
    return wrapper.grade


def normalize(body, prompt, context):
    messages = body.get('messages', [])
    require(len(messages) == 2 and messages[0].get('role') == 'system'
            and isinstance(messages[0].get('content'), str)
            and messages[1] == {'role': 'user', 'content': prompt + '\n\n' + context},
            'exact complete system/user contract mismatch')
    result = copy.deepcopy(body)
    result['messages'][1]['content'] = prompt + '\n\n' + SENTINEL
    return result


def local_runtime(relative):
    return '/profile/' in relative or relative.endswith('/worker.log') or relative == 'execution.log'


def audit_study(root, snapshots, judge):
    frozen = read(root / 'cohort-freeze.json')
    require(tuple(frozen['protocol']['case_order']) == CASES, 'case order changed')
    for name, expected in frozen['prepared_files'].items():
        require(digest((root / name).read_bytes()) == expected, 'prepared artifact changed: ' + name)
    template, route = read(root / 'wire-template.json'), read(root / 'route.json')
    require(route['model'] == 'upstage/solar-mini4' and route['provider'] == 'nous'
            and route['endpoint'] == 'https://inference-api.nousresearch.com/v1/chat/completions', 'fixed route changed')
    rows, totals = {}, {'prompt_tokens': 0, 'completion_tokens': 0, 'cost': Decimal('0')}
    for case in CASES:
        run = root / 'runs' / case
        receipt, case_freeze = read(run / 'receipt.json'), read(run / 'freeze.json')
        require(digest((run / 'freeze.json').read_bytes()) == receipt['freeze_sha256'], 'case freeze hash changed')
        require(case_freeze['checker_sha256'] == VISIBILITY_CHECKER, 'checker differs from the frozen independent judge')
        calls = read(run / 'physical-calls.json')
        physical = [call for call in calls if call.get('upstream_sent') is True]
        require(len(physical) == receipt['provider_calls'] == 1, 'physical call budget/accounting mismatch')
        require(not any(call.get('kind') == 'blocked_inference' for call in calls), 'repeated local inference')
        call = physical[0]
        require(call['kind'] == 'inference' and call['physical_attempt'] == 1 and call['status_code'] == 200,
                'physical execution is not the declared successful single POST')
        require(call['method'] == 'POST' and call['endpoint'] == route['endpoint'], 'endpoint mismatch')
        selection = read(root / 'prepared' / case / 'selection.json')
        context, prompt = ((root / 'prepared' / case / name).read_text() for name in ('context.txt', 'prompt.txt'))
        require(selection == read(run / 'prepared/selection.json') and selection['context'] == context,
                'selected evidence changed')
        require((run / 'prompt.txt').read_text() == prompt, 'saved run prompt changed')
        require(digest(context.encode()) == selection['context_sha256'] == case_freeze['context_sha256']
                and digest(prompt.encode()) == case_freeze['prompt_sha256'], 'prompt/context freeze mismatch')
        wires = {}
        for name, key in (('native-request.bin', 'native'), ('forwarded-request.bin', 'forwarded')):
            raw = (run / name).read_bytes()
            body = read(run / name)
            require(digest(raw) == call[key + '_wire_sha256'] and len(raw) == call[key + '_wire_bytes']
                    and body == call[key + '_request'], 'physical raw wire binding mismatch')
            require(len(raw) <= 20000 and normalize(body, prompt, context) == template, 'full-wire invariant mismatch')
            require(body.get('model') == route['model'] and body.get('max_tokens') == 4096
                    and body.get('response_format') == {'type': 'json_object'}
                    and not any(body.get(field) for field in ('tools', 'stream', 'models', 'route', 'provider', 'usage')),
                    'route, output format, authority, or cap changed')
            wires[key] = body
        require(wires['native'] == wires['forwarded'], 'Nous forwarding changed the native body')
        require(case_freeze['max_output_tokens'] == 4096 and case_freeze['max_serialized_request_bytes'] == 20000
                and case_freeze['max_physical_inference_attempts'] == 1 and case_freeze['max_wall_seconds'] == 120,
                'case policy changed')
        response = call['response']
        provider_answer = json.loads(response['choices'][0]['message']['content'], object_pairs_hook=unique)
        worker_answer = json.loads(read(run / 'worker-result.json')['result']['final_response'], object_pairs_hook=unique)
        answer = read(run / 'answer.json')
        verdict = judge(case, answer)
        require(answer == provider_answer == worker_answer and verdict == receipt['outcome'], 'answer or frozen verdict mismatch')
        usage = response['usage']
        require(usage == call['usage'] == receipt['usage'], 'usage differs from actual provider response')
        require(response['model'] == call['served_model'] == receipt['served_model'] == route['model'], 'served model drift')
        require(response.get('provider') == call.get('served_provider') == receipt.get('served_provider'), 'provider identity mismatch')
        require(type(usage['prompt_tokens']) is int and 0 <= usage['prompt_tokens'] <= 20000
                and type(usage['completion_tokens']) is int and 0 <= usage['completion_tokens'] <= 4096,
                'token usage exceeds cap')
        require(usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'], 'usage sum mismatch')
        require(type(usage['cost']) in (float, int) and math.isfinite(usage['cost'])
                and 0 <= usage['cost'] <= route['max_estimated_cost_usd']
                and receipt['reported_cost'] == usage['cost'] and receipt['resource_comparison_eligible'] is True,
                'reported cost/resource eligibility mismatch')
        require(receipt['returncode'] == 0 and receipt['error_type'] is None, 'worker failure')
        for key in ('prompt_tokens', 'completion_tokens'):
            totals[key] += usage[key]
        totals['cost'] += Decimal(str(usage['cost']))
        rows[case] = {'verified_success': verdict['verified_success'], 'problems': verdict['problems'],
            'answer': answer, 'usage': usage, 'served_provider': response.get('provider'),
            'system_sha256': digest(wires['native']['messages'][0]['content'].encode()),
            'native_wire_sha256': call['native_wire_sha256'], 'context_sha256': selection['context_sha256']}
    saved = read(root / 'cohort-result.json')
    require(saved['physical_posts_including_failures'] == 5
            and saved['known_work_prompt_tokens'] == totals['prompt_tokens']
            and saved['known_work_completion_tokens'] == totals['completion_tokens']
            and math.isclose(saved['known_work_reported_cost_usd'], float(totals['cost']), abs_tol=1e-15),
            'saved aggregate accounting mismatch')
    return {'rows': rows, 'physical_posts': 5, 'verified_successes': sum(row['verified_success'] for row in rows.values()),
            'reported_prompt_tokens': totals['prompt_tokens'], 'reported_completion_tokens': totals['completion_tokens'],
            'reported_cost_usd_decimal': str(totals['cost']), 'unchanged_frozen_judge_replay': True}


def audit(v1, v2, snapshots_dir, *, public_artifacts=False):
    snapshots = snapshot_index(snapshots_dir)
    judge = frozen_judge(snapshots)
    freeze1, freeze2, study = read(v1 / 'cohort-freeze.json'), read(v2 / 'cohort-freeze.json'), read(v2 / 'prompt-study-freeze.json')
    expected_sources = set(freeze1['source_files'].values()) | {
        value for path, value in freeze2['source_files'].items() if '/claim_contract_v2/' in path}
    require(expected_sources <= snapshots.keys(), 'required source snapshots missing or changed')
    require(digest((v1 / 'cohort-freeze.json').read_bytes()) == study['prior_cohort_freeze_sha256'], 'predecessor freeze changed')
    checked, omitted = 0, []
    for absolute, expected in study['prior_artifacts'].items():
        relative = str(Path(absolute).relative_to(study['prior_out']))
        path = v1 / relative
        if not path.exists() and public_artifacts and local_runtime(relative):
            omitted.append(relative)
            continue
        require(path.exists() and digest(path.read_bytes()) == expected, 'predecessor artifact changed/missing: ' + relative)
        checked += 1
    result1, result2 = audit_study(v1, snapshots, judge), audit_study(v2, snapshots, judge)
    cross = {}
    old_prompt = (v1 / 'prepared/full-complete/prompt.txt').read_text()
    new_prompt = (v2 / 'prepared/full-complete/prompt.txt').read_text()
    transformed = read(v1 / 'wire-template.json')
    transformed['messages'][1]['content'] = new_prompt + '\n\n' + SENTINEL
    require(transformed == read(v2 / 'wire-template.json'), 'seeded first-forward template changed beyond prompt')
    require(freeze1['working_directory'] == freeze2['working_directory']
            and freeze1['staging_path'] == freeze2['staging_path'], 'physical boilerplate resources changed')
    for case in CASES:
        require((v1 / 'prepared' / case / 'context.txt').read_bytes() == (v2 / 'prepared' / case / 'context.txt').read_bytes()
                and read(v1 / 'prepared' / case / 'selection.json') == read(v2 / 'prepared' / case / 'selection.json'),
                'context/selection intervention changed across studies')
        for wire in ('native-request.bin', 'forwarded-request.bin'):
            old_raw, new_raw = ((root / 'runs' / case / wire).read_bytes() for root in (v1, v2))
            # Replace the exact JSON-string-encoded prompt prefix in the RAW bytes;
            # every other byte, including complete system and serialization, stays.
            before = json.dumps(old_prompt, ensure_ascii=False)[1:-1].encode()
            after = json.dumps(new_prompt, ensure_ascii=False)[1:-1].encode()
            require(old_raw.count(before) == 1 and old_raw.replace(before, after, 1) == new_raw,
                    'actual raw cross-study wire changed beyond exact prompt bytes: ' + case + ':' + wire)
        cross[case] = {'raw_native_prompt_only_difference': True, 'raw_forwarded_prompt_only_difference': True,
                       'contexts_and_selections_byte_identical': True, 'full_system_byte_identical': True}
    return {'schema': 'hermes.dual_study_saved_audit.v1', 'status': 'PASS',
        'v1': result1, 'v2': result2, 'cross_study': cross,
        'predecessor_immutability': {'expected_files': len(study['prior_artifacts']), 'checked_files': checked,
            'all_available_bound_files_unchanged': True, 'all_198_locally_rechecked': not omitted,
            'omitted_local_runtime_files': omitted, 'omissions_are_not_verified_by_this_replay': bool(omitted)},
        'unique_source_snapshots_checked': len(expected_sources),
        'no_new_inference': True, 'no_answer_repair_or_changed_judge': True, 'studies_not_pooled': True,
        'backend_causal_attribution': False,
        'limits': ['One inspected task with controls and a changed prompt contract; no population or holdout inference',
                   'Response provider is null; exact request control does not identify backend or remove temporal/service variation',
                   'Public mode can replay scientific artifacts while explicitly reporting omitted profiles/logs'],
        'auditor_sha256': digest(Path(__file__).read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--v1', type=Path, required=True)
    parser.add_argument('--v2', type=Path, required=True)
    parser.add_argument('--source-snapshots', type=Path, default=Path(__file__).parent / 'source-snapshots')
    parser.add_argument('--public-artifacts', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.v1, args.v2, args.source_snapshots, public_artifacts=args.public_artifacts)
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'v1_verified': result['v1']['verified_successes'],
        'v2_verified': result['v2']['verified_successes'], 'prior_files_checked': result['predecessor_immutability']['checked_files'],
        'raw_prompt_only_cases': len(result['cross_study']), 'output': str(args.out)}))


if __name__ == '__main__':
    main()
