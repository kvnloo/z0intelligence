"""Integration merge guards for integrate/wiring-20261003.

C3 (OMP/OMO bridge) and C4a (Hermes plugin) both changed the capture-time cohort in
``harness_capture.begin_turn``: C3 derives ``agent`` from a bridge subagent payload, C4a lets a shim pass
its own cohort. The merge keeps both; these tests fail on either side alone. Synthetic payloads only.
"""
import pytest

from z0int import harness_capture as hc


@pytest.fixture
def home(monkeypatch, tmp_path):
    h = tmp_path / 'z0'
    monkeypatch.setenv('Z0INT_HOME', str(h))
    for name in ('Z0INT_CAPTURE', 'Z0INT_CAPTURE_PRIVACY', 'GROK_HOOK_EVENT', 'GROK_SESSION_ID'):
        monkeypatch.delenv(name, raising=False)
    return h


def turn(h, session, n=1, **extra):
    return {'session_id': session, 'turn_id': f't{n}', 'model': 'm-1', 'prompt': 'summarise the notes',
            'cwd': None, **extra}


def test_bridge_subagent_without_shim_cohort_is_agent(home):
    assert hc.begin_turn('omp', turn('omp', 'sub', parent_id='Main'))['cohort'] == 'agent'
    assert hc.begin_turn('omp', turn('omp', 'sub2', agent_kind='sub'))['cohort'] == 'agent'
    assert hc.begin_turn('omp', turn('omp', 'main', agent_kind='main'))['cohort'] == 'interactive'


def test_shim_cohort_is_kept_and_wins_over_the_subagent_rule(home):
    assert hc.begin_turn('hermes', turn('hermes', 'cron'), cohort='automated')['cohort'] == 'automated'
    assert hc.begin_turn('hermes', turn('hermes', 'cron-sub', parent_id='x'),
                         cohort='automated')['cohort'] == 'automated'
    assert hc.begin_turn('hermes', turn('hermes', 'plain'))['cohort'] == 'interactive'


def test_harness_cohort_from_shim_marks_the_injected_flag(home):
    ctx = hc.begin_turn('hermes', turn('hermes', 'inj'), cohort='harness')
    assert ctx['cohort'] == 'harness'
