"""Content-free command classes shared by every harness, extracted from the outcome verifier (z0int#54)."""
import json

from z0int import check_class as cc
from z0int import outcome_verifier as ov

# (command, shell exit code or None, output tail, expected class, expected effective exit)
CORPUS = [
    ('pytest -q', 0, '', 'test', 0),
    ('python -m pytest tests/ 2>&1 | tail -5', 0, '=== 2 failed, 10 passed in 1s ===', 'test', 1),
    ('pytest | grep passed', 1, '', 'test', None),
    ('uv pip install -q pytest', 0, '', 'other', 0),
    ('which pytest && pytest --version', 0, '', 'other', 0),
    ('npm test', None, '', 'test', None),
    ('ruff check . | tail -3', 0, 'Found 3 errors.', 'check', 1),
    ('mypy src', 1, '', 'check', 1),
    ('cargo build --release', 0, '', 'build', 0),
    ('make -j8', 2, '', 'build', 2),
    ('make test', 0, '', 'test', 0),
    ('gh run watch 123', 0, '', 'ci', 0),
    ('gh pr checks 5', 1, '', 'ci', 1),
    ('git commit -m "wip"', 0, '', 'git-commit', 0),
    ('git -C repo revert --no-edit abc123', 0, '', 'git-revert', 0),
    ('git cherry-pick abc123', 0, '', 'git-commit', 0),
    ('gh pr merge 12 --squash', 0, '', 'pr-merge', 0),
    ('ls -la /home/someone/private', 0, '', 'other', 0),
]


def test_classes_are_closed_and_cover_the_corpus():
    assert set(cc.CLASSES) == {'test', 'check', 'ci', 'git-commit', 'git-revert', 'pr-merge', 'build', 'other'}
    for command, code, tail, klass, effective in CORPUS:
        got = cc.classify(command, code, tail)
        assert (got['check_class'], got['exit']) == (klass, effective), command


def test_the_verifier_uses_the_extracted_classifier_not_a_copy():
    for name in ('TEST_CMD', 'CHECK_EXTRA', 'CHECK_ANY', 'CI_CMD', 'COMMIT_CMD', 'PR_MERGE', 'PIPE_FILTER',
                 'effective_exit'):
        assert getattr(ov, name) is getattr(cc, name), name


def _transcript(path):
    rows = [{'type': 'user', 'promptId': 'p1', 'sessionId': 's', 'timestamp': '2026-10-03T00:00:00Z', 'cwd': '/w',
             'message': {'content': 'run the checks'}}]
    content = [{'type': 'tool_use', 'id': f'tu{i}', 'name': 'Bash', 'input': {'command': c}}
               for i, (c, *_rest) in enumerate(CORPUS)]
    rows.append({'type': 'assistant', 'timestamp': '2026-10-03T00:00:01Z',
                 'message': {'id': 'm1', 'model': 'x', 'content': content}})
    for i, (c, code, tail, *_rest) in enumerate(CORPUS):
        if code is None:
            block = {'type': 'tool_result', 'tool_use_id': f'tu{i}', 'content': 'Command running in background'}
        elif code:
            block = {'type': 'tool_result', 'tool_use_id': f'tu{i}', 'is_error': True,
                     'content': f'Exit code {code}\n{tail}'}
        else:
            block = {'type': 'tool_result', 'tool_use_id': f'tu{i}', 'content': tail}
        rows.append({'type': 'user', 'timestamp': '2026-10-03T00:00:02Z', 'message': {'content': [block]}})
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return path


def test_outcome_verifier_and_check_class_classify_identically(tmp_path):
    (turn,) = ov.turns_from_transcript(_transcript(tmp_path / 't.jsonl'))
    assert len(turn['bash']) == len(CORPUS)
    for call, (command, code, tail, klass, effective) in zip(turn['bash'], CORPUS):
        direct = cc.classify(command, code, f'Exit code {code}\n{tail}' if code else tail)
        assert call['check_class'] == direct['check_class'] == klass, command
        assert call['exit'] == direct['exit'], command


def test_the_command_string_is_never_part_of_a_classification():
    for command, code, tail, *_ in CORPUS:
        got = cc.classify(command, code, tail)
        assert set(got) == {'check_class', 'exit', 'piped'}
        blob = json.dumps(got)
        assert command not in blob and '/home/someone' not in blob
