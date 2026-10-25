"""Law H synthetic proc snapshots; no signals or cgroup writes."""
from pathlib import Path
import pytest
from z0int.fsm.cgroup_placement import audit, decide

DESKTOP = '/user.slice/user-1000.slice/user@1000.service/session.slice/wayland-wm@hyprland.desktop.service'
SCOPED = '/user.slice/user-1000.slice/user@1000.service/agent.slice/agent-omp.scope'


@pytest.fixture
def proc(tmp_path):
    def add(pid, parent, exe, group, argv=None):
        root = tmp_path / str(pid)
        root.mkdir()
        (root / 'stat').write_text(f'{pid} (worker) S {parent} ' + ' '.join(['0'] * 17 + [str(pid * 100)]))
        (root / 'comm').write_text(Path(exe).name)
        (root / 'cmdline').write_bytes(b'\0'.join(s.encode() for s in (argv or [exe])) + b'\0')
        (root / 'cgroup').write_text('0::' + group + '\n')
        (root / 'exe').symlink_to(exe)
    add(1, 0, '/usr/lib/systemd/systemd', '/init.scope')
    return tmp_path, add


def test_tonights_desktop_child(proc):
    root, add = proc
    add(10, 1, '/usr/bin/bun', DESKTOP, ['bun', '/opt/omp/omp.js'])
    add(11, 10, '/usr/bin/node', DESKTOP, ['node', '/tmp/worker.js'])
    report = audit(root)
    assert {r['pid'] for r in report['violations']} == {10, 11}
    child = next(r for r in report['violations'] if r['pid'] == 11)
    assert [r['pid'] for r in child['ancestry']] == [11, 10, 1]
    assert child['exe'] == '/usr/bin/node' and child['cgroup'] == DESKTOP
    assert report['mode'] == 'report-only' and report['enforcement_enabled'] is False


def test_non_interpreter_descendant(proc):
    root, add = proc
    add(10, 1, '/usr/bin/omp', SCOPED)
    add(11, 10, '/usr/bin/bash', DESKTOP)
    add(12, 11, '/usr/bin/curl', DESKTOP)
    report = audit(root)
    assert {row['pid'] for row in report['violations']} == {11, 12}
    assert [row['pid'] for row in report['violations'][1]['ancestry']] == [12, 11, 10, 1]


def test_correct_scope(proc):
    root, add = proc
    add(10, 1, '/usr/bin/omp', SCOPED)
    assert audit(root)['status'] == 'pass'


def test_orphan_reparented_to_systemd(proc):
    root, add = proc
    add(11, 1, '/usr/bin/python3', DESKTOP, ['python3', '/tmp/worker.py'])
    report = audit(root)
    assert report['status'] == 'unknown'
    assert report['unknown'][0]['reason'] == 'orphan-workload-identity-unknown'


def test_unreadable_proc(proc, monkeypatch):
    root, add = proc
    add(10, 1, '/usr/bin/omp', DESKTOP)
    original = Path.read_bytes
    def denied(path):
        if path == root / '10/cmdline':
            raise PermissionError('fixture denied')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', denied)
    report = audit(root)
    assert report['status'] == 'unknown'
    assert report['unknown'][0]['pid'] == 10


@pytest.mark.parametrize('name', ['omp', 'hermes', 'claude', 'codex', 'opencode', 'pi', 'unii', 'llama-server'])
def test_all_agent_executables(proc, name):
    root, add = proc
    add(10, 1, '/usr/bin/' + name, '/session.slice/other-desktop.service')
    assert audit(root)['violations'][0]['pid'] == 10


def test_farm_and_component_boundaries(proc):
    root, add = proc
    add(10, 1, '/usr/bin/llama-server', '/system.slice/z0-farm-llama.service')
    add(11, 1, '/usr/bin/omp', '/not-agent.slice/agent.slice.fake')
    report = audit(root)
    assert [r['pid'] for r in report['violations']] == [11]
    assert [r['pid'] for r in report['compliant']] == [10]


def test_prompt_is_not_agent_identity(proc):
    root, add = proc
    add(10, 1, '/usr/bin/node', DESKTOP, ['node', '/tmp/editor.js', 'omp'])
    assert not audit(root)['violations']


@pytest.mark.parametrize('available,agent,scoped,expected', [
    (False, True, False, 'unknown'), (True, True, False, 'violation'),
    (True, True, True, 'pass'), (True, False, False, 'pass'),
    (True, None, True, 'unknown')])
def test_decision(available, agent, scoped, expected):
    assert decide(available, agent, scoped) == expected
