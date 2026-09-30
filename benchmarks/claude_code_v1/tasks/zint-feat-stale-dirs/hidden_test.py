import json

from z0int import claude_code_launch as ccl


def test_stale_dirs_empty(tmp_path):
    assert ccl.stale_dirs(root=tmp_path, ttl=100, now=1000.0) == []


def test_stale_dirs_filters_and_sorts(tmp_path):
    path = ccl.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        '/w/zeta': {'snapshot': 'a', 'profile': 'lean', 'ts': 100.0},    # age 900 > 300: stale
        '/w/alpha': {'snapshot': 'b', 'profile': 'lean', 'ts': 200.0},   # age 800: stale
        '/w/fresh': {'snapshot': 'c', 'profile': 'lean', 'ts': 900.0},   # age 100: fresh
        '/w/edge': {'snapshot': 'd', 'profile': 'stock', 'ts': 700.0},   # age exactly 300: not strictly older
    }
    path.write_text(json.dumps(data))
    assert ccl.stale_dirs(root=tmp_path, ttl=300, now=1000.0) == ['/w/alpha', '/w/zeta']


def test_stale_dirs_uses_record(tmp_path):
    d = tmp_path / 'repo'
    d.mkdir()
    ccl.record(d, 'lean', root=tmp_path)
    recorded = next(iter(json.loads(ccl.state_path(tmp_path).read_text())))
    assert ccl.stale_dirs(root=tmp_path, ttl=10**9) == []
    assert ccl.stale_dirs(root=tmp_path, ttl=5, now=10**12) == [recorded]
