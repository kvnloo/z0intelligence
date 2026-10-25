"""Exercise root-cause commands through the public guard entrypoint."""
import json

from z0int.fsm.guard import main


def test_guard_attribute_dispatches_typed_same_window_evidence(tmp_path, capsys):
    sample = tmp_path / 'sample.json'
    sample.write_text(json.dumps({
        'process': {'pid': 42, 'starttime': 1, 'end_starttime': 1,
                    'units': 'bytes', 'window_start': 10, 'window_end': 20,
                    'before': 0, 'after': 30},
        'system': {'units': 'bytes', 'includes_swap': True,
                   'window_start': 10, 'window_end': 20,
                   'before': 0, 'after': 100},
    }))
    assert main(['attribute', '--input', str(sample)]) == 0
    assert json.loads(capsys.readouterr().out) == {'decision': 'allow', 'mode': 'report'}


def test_guard_attribute_unknown_does_not_promote_claim(tmp_path, capsys):
    sample = tmp_path / 'unknown.json'
    sample.write_text('{}')
    assert main(['attribute', '--input', str(sample)]) == 4
    assert json.loads(capsys.readouterr().out)['decision'] == 'escalate'
