"""Prompt-only adapter over the unchanged five-case driver. Default is offline."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'verified_context_cohort'))
import run_cohort as base

PROTOCOL = base.read(HERE / 'protocol.json')
PROMPT = (HERE / 'prompt.txt').read_text()


def prompt_template(template: dict, old_prompt: str) -> dict:
    result = copy.deepcopy(template)
    if (len(result.get('messages', [])) != 2
            or result['messages'][1] != {'role': 'user', 'content': old_prompt + '\n\n' + base.SENTINEL}):
        raise ValueError('predecessor template is not the exact original prompt contract')
    result['messages'][1]['content'] = PROMPT + '\n\n' + base.SENTINEL
    return result


def prepare(out: Path, prior: Path) -> dict:
    previous = base.validate_freeze(prior)
    result = base.replay(prior)
    if (result['status'] != 'COMPLETE' or result['physical_posts_including_failures'] != 5
            or any(row['outcome']['verified_success'] for row in result['case_outcomes'].values())):
        raise ValueError('expected the preserved complete five-failure predecessor')
    if tuple(PROTOCOL['case_order']) != base.CASES:
        raise ValueError('v2 cases differ from the original cohort')
    if Path(previous['staging_path']).exists():
        raise ValueError('predecessor staging resource is occupied; do not alter it')
    old_prompt = (prior / 'prepared/full-complete/prompt.txt').read_text()
    template = prompt_template(base.read(prior / 'wire-template.json'), old_prompt)
    # Only the in-process prompt constant changes during existing preparation.
    # Original source files, ContextPacket policy and checkers stay untouched.
    args = SimpleNamespace(out=out, hermes_repo=Path(previous['hermes_repo']), route_file=prior / 'route.json')
    with patch.object(base.discriminator, 'PROMPT', PROMPT):
        frozen = base.prepare(args)
    for case_id in base.CASES:
        for name in ('selection.json', 'context.txt'):
            if (out / 'prepared' / case_id / name).read_bytes() != (prior / 'prepared' / case_id / name).read_bytes():
                raise ValueError('context/selection changed across prompt-only studies')
    base.save(out / 'wire-template.json', template)
    wire_sizes = {}
    for case_id in base.CASES:
        expected = copy.deepcopy(template)
        expected['messages'][1]['content'] = PROMPT + '\n\n' + (out / 'prepared' / case_id / 'context.txt').read_text()
        wire_sizes[case_id] = len(base.driver.serialize_request(expected))
    if max(wire_sizes.values()) > base.PROTOCOL['max_serialized_request_bytes']:
        raise ValueError('prompt-only expected wire exceeds unchanged byte cap')
    prior_artifacts = {str(path.resolve()): base.digest(path.read_bytes())
                       for path in sorted(prior.rglob('*')) if path.is_file()}
    study = {'schema': PROTOCOL['schema'], 'study_id': PROTOCOL['study_id'], 'protocol': PROTOCOL,
        'provider_calls_at_freeze': 0, 'prior_out': str(prior.resolve()),
        'prior_cohort_freeze_sha256': base.digest((prior / 'cohort-freeze.json').read_bytes()),
        'prior_artifacts': prior_artifacts, 'prior_source_files': previous['source_files'],
        'prior_semantic_failures': 5, 'prior_physical_posts': result['physical_posts_including_failures'],
        'prior_consumed_work': {key: result[key] for key in ('known_work_prompt_tokens', 'known_work_completion_tokens', 'known_work_reported_cost_usd')},
        'old_prompt_sha256': base.digest(old_prompt.encode()), 'new_prompt_sha256': base.digest(PROMPT.encode()),
        'prompt_is_only_native_wire_difference': True,
        'prior_template_sha256': base.digest((prior / 'wire-template.json').read_bytes()),
        'seeded_template_sha256': base.digest((out / 'wire-template.json').read_bytes()),
        'expected_wire_bytes_from_prior_native_template': wire_sizes,
        'wire_size_scope': 'Exact prior request body with only prompt replaced; actual transport still guarded before forwarding',
        'root_execute_cwd': previous['working_directory'], 'shared_staging_path': previous['staging_path'],
        'new_output_directory': str(out.resolve()), 'prior_failures_not_regraded_or_pooled': True}
    base.save(out / 'prompt-study-freeze.json', study)
    frozen['working_directory'] = previous['working_directory']
    frozen['staging_path'] = previous['staging_path']
    # Pin all original bindings, all predecessor artifacts, and new adapter inputs.
    # The old output is read-only except the declared fresh transient staging path.
    frozen['source_files'].update(previous['source_files'])
    frozen['source_files'].update(prior_artifacts)
    frozen['source_files'].update({str(path): base.digest(path.read_bytes())
                                  for path in (Path(__file__).resolve(), HERE / 'protocol.json', HERE / 'prompt.txt')})
    frozen['prepared_files'].update({name: base.digest((out / name).read_bytes())
                                    for name in ('wire-template.json', 'prompt-study-freeze.json')})
    base.save(out / 'cohort-freeze.json', frozen)
    validate(out)
    return study


def validate(out: Path) -> dict:
    frozen = base.validate_freeze(out)
    study = base.read(out / 'prompt-study-freeze.json')
    if study['protocol'] != PROTOCOL or study['new_prompt_sha256'] != base.digest(PROMPT.encode()):
        raise ValueError('v2 prompt or protocol changed')
    prior = Path(study['prior_out'])
    old_prompt = (prior / 'prepared/full-complete/prompt.txt').read_text()
    if base.read(out / 'wire-template.json') != prompt_template(base.read(prior / 'wire-template.json'), old_prompt):
        raise ValueError('fixed template contains a change beyond the prompt')
    if frozen['working_directory'] != study['root_execute_cwd'] or frozen['staging_path'] != study['shared_staging_path']:
        raise ValueError('physical system-boilerplate resources changed')
    return study


def replay(out: Path) -> dict:
    study = validate(out)
    result = base.replay(out)
    result.update(study_id=study['study_id'], semantic_target=PROTOCOL['scope'],
        predecessor_cohort_not_pooled=True, prior_cohort_freeze_sha256=study['prior_cohort_freeze_sha256'],
        prompt_study_freeze_sha256=base.digest((out / 'prompt-study-freeze.json').read_bytes()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prior-cohort', type=Path)
    parser.add_argument('--nous-auth-home', type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--execute', action='store_true')
    modes.add_argument('--replay', action='store_true')
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.execute:
        validate(args.out)  # The seeded pre-inference template is mandatory.
        base.execute(args)
        result = replay(args.out)
        base.save(args.out / 'prompt-study-result.json', result)
    elif args.replay:
        result = replay(args.out)
    else:
        if args.prior_cohort is None:
            parser.error('--prior-cohort required for offline preparation')
        prepare(args.out, args.prior_cohort.resolve())
        result = {'status': 'PREPARED_NOT_EXECUTED', 'study_id': PROTOCOL['study_id'], 'provider_calls': 0}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
