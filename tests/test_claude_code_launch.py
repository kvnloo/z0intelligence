import subprocess

from z0int import claude_code_launch as L


def repo(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    subprocess.run(['git', 'init', '-q', str(d)], check=True)
    (d / 'f').write_text('x')
    subprocess.run(['git', '-C', str(d), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(d), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'i'], check=True)
    return d


def test_profiles_and_argv():
    assert L.PROFILES['stock'] == []
    argv = L.build_argv('lean', ['-p', 'hi'], plugin=False)
    assert argv[0] == 'claude' and '--disable-slash-commands' in argv and argv[-2:] == ['-p', 'hi']


def test_warm_ranking_tracks_ttl_profile_and_snapshot(tmp_path):
    root = tmp_path / 'home'
    a, b, c = repo(tmp_path, 'a'), repo(tmp_path, 'b'), repo(tmp_path, 'c')
    L.record(a, 'lean', root=root)
    L.record(b, 'lean', root=root)
    (b / 'f').write_text('changed')           # git status snapshot changes -> cold
    rows = {r['dir'].rsplit('/', 1)[-1]: r for r in L.rank([a, b, c], 'lean', 3600, root=root)}
    assert rows['a']['warm'] and not rows['b']['warm'] and not rows['c']['warm']
    assert 'git snapshot changed since last launch' in rows['b']['reasons']
    assert rows['c']['reasons'] == ['never launched']
    assert not L.rank([a], 'stock', 3600, root=root)[0]['warm']
    later = L.rank([a], 'lean', 60, root=root, now=__import__('time').time() + 120)[0]
    assert not later['warm'] and 'expired' in later['reasons'][0]


def test_cli_dry_run(capsys):
    from z0int.cli import main
    assert main(['claude-code', 'launch', '--profile', 'lean', '--no-plugin', '--dry-run', '--', '-p', 'x']) == 0
    assert '--strict-mcp-config' in capsys.readouterr().out
