import json
import os
from pathlib import Path
import sqlite3
import subprocess


SCRIPT = Path(__file__).resolve().parents[1]/'operations'/'shadow-learner'/'deploy-proposed.sh'


def test_deploy_resolves_commit_copies_source_and_backs_up_private_state(tmp_path):
    repo, home = tmp_path/'source', tmp_path/'home'
    repo.mkdir()
    for name in ('local-cognition', 'z0int-bridge'):
        target = repo/'omp-extensions'/name
        target.mkdir(parents=True)
        (target/'index.ts').write_text('export const fixture = true;\n')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-qm', 'fixture'], check=True)
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    extensions = home/'.omp'/'agent'/'extensions'
    extensions.mkdir(parents=True)
    for name in ('local-cognition', 'z0int-bridge'):
        (extensions/name).symlink_to(repo/'omp-extensions'/name, target_is_directory=True)
    loop = home/'.z0int'/'loop'
    loop.mkdir(parents=True)
    (loop/'shadow-learning-status.json').write_text('{"before":true}\n')
    with sqlite3.connect(loop/'shadow-learning.sqlite') as db:
        db.execute('CREATE TABLE example(value TEXT)')
        db.execute("INSERT INTO example VALUES('preserved')")
    census = tmp_path/'census.json'
    census.write_text('{"jobs":[]}\n')
    env = {**os.environ, 'HOME': str(home), 'SHADOW_SOURCE': str(repo), 'SHADOW_CENSUS': str(census)}
    result = subprocess.run(['bash', str(SCRIPT), 'HEAD'], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((loop/'shadow-deployment.json').read_text())
    installed = home/'.local'/'lib'/'z0int-shadow'/sha
    assert receipt['sha'] == sha and receipt['operator_session_restarted'] is False
    assert (installed/'DEPLOYED_SHA').read_text().strip() == sha
    assert (extensions/'local-cognition').resolve() == installed/'omp-extensions'/'local-cognition'
    assert not (installed/'omp-extensions'/'local-cognition'/'index.ts').stat().st_mode & 0o222
    backup = Path(receipt['backup'])
    assert json.loads((backup/'loop'/'shadow-learning-status.json').read_text()) == {'before': True}
    with sqlite3.connect(backup/'shadow-learning.sqlite') as db:
        assert db.execute('SELECT value FROM example').fetchone()[0] == 'preserved'
    assert list((home/'.omp'/'agent'/'extensions-backup').iterdir())
    assert json.loads((loop/'shadow-census.json').read_text()) == {'jobs': []}
