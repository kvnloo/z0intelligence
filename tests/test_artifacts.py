"""Candidate import must preserve the bytes and identity the producer submitted."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from z0int.artifacts import get_artifact, import_manifest, inspect_manifest


def bundle(root: Path, files: dict[str, bytes], **changes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        entries.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest()})
    identity = hashlib.sha256('|'.join(sorted(e['sha256'] for e in entries)).encode()).hexdigest()
    manifest = {
        'schema': 'z0int.candidate_artifact.v1', 'artifact_id': 'sha256:' + identity,
        'runtime': 'z0int.backend.mushroom.v1', 'capability_id': 'fixture.quality',
        'producer': {'repo': 'synthetic/producer', 'revision': 'fixture'},
        'files': entries, 'promotion': {'status': 'candidate'}, **changes,
    }
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest))
    return path


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))


def test_import_rejects_id_that_does_not_bind_file_hashes(tmp_path):
    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'}, artifact_id='sha256:' + '0' * 64)
    assert not inspect_manifest(path)['ok']
    assert not import_manifest(path)['ok']
    assert not (tmp_path / 'z0' / 'specialists').exists()


@pytest.mark.parametrize('files', [
    {'a/weights.bin': b'first', 'b/weights.bin': b'second'},
    {'metadata/manifest.json': b'candidate payload'},
])
def test_import_rejects_flat_destination_collisions(tmp_path, files):
    path = bundle(tmp_path / 'bundle', files)
    assert not import_manifest(path)['ok']
    assert not (tmp_path / 'z0' / 'specialists').exists()


@pytest.mark.parametrize('escape', ['parent', 'absolute', 'symlink'])
def test_import_never_reads_files_outside_bundle(tmp_path, escape):
    outside = tmp_path / 'outside.bin'
    outside.write_bytes(b'private outside bundle')
    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'})
    manifest = json.loads(path.read_text())
    if escape == 'symlink':
        (path.parent / 'linked.bin').symlink_to(outside)
        name = 'linked.bin'
    else:
        name = '../outside.bin' if escape == 'parent' else str(outside)
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest['files'] = [{'path': name, 'sha256': digest}]
    manifest['artifact_id'] = 'sha256:' + hashlib.sha256(digest.encode()).hexdigest()
    path.write_text(json.dumps(manifest))
    assert not import_manifest(path)['ok']
    assert not (tmp_path / 'z0' / 'specialists').exists()


def test_error_filename_cannot_bypass_hash_validation(tmp_path):
    path = bundle(tmp_path / 'bundle', {'promoted-weights.bin': b'original'})
    (path.parent / 'promoted-weights.bin').write_bytes(b'changed')
    assert not import_manifest(path)['ok']


def test_same_bytes_cannot_rebind_installed_capability(tmp_path):
    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'})
    first = import_manifest(path)
    manifest = json.loads(path.read_text())
    manifest['capability_id'] = 'different.capability'
    path.write_text(json.dumps(manifest))
    second = import_manifest(path)
    assert not second['ok']
    assert get_artifact(first['artifact_id'])['capability_id'] == 'fixture.quality'


def test_reimport_does_not_silently_accept_corrupted_installed_bytes(tmp_path):
    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'})
    first = import_manifest(path)
    installed = Path(first['path']) / 'weights.bin'
    installed.write_bytes(b'corrupted')
    second = import_manifest(path)
    assert not second['ok']
    assert installed.read_bytes() == b'corrupted'  # no implicit overwrite or repair


@pytest.mark.parametrize('stored_identity', [None, ''])
def test_reimport_rejects_installed_manifest_without_consumer_identity(tmp_path, stored_identity):
    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'})
    first = import_manifest(path)
    installed = Path(first['path']) / 'manifest.json'
    manifest = json.loads(installed.read_text())
    if stored_identity is None:
        manifest.pop('artifact_id')
    else:
        manifest['artifact_id'] = stored_identity
    installed.write_text(json.dumps(manifest))
    before = installed.read_bytes()
    assert get_artifact(first['artifact_id']) is None
    assert not import_manifest(path)['ok']
    assert installed.read_bytes() == before


def test_exact_reimport_preserves_existing_artifact(tmp_path):
    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'})
    first = import_manifest(path)
    installed = Path(first['path']) / 'manifest.json'
    before = installed.read_bytes()
    second = import_manifest(path)
    assert first['ok'] and second['ok'] and second['already_installed']
    assert installed.read_bytes() == before
    assert (Path(first['path']) / 'weights.bin').read_bytes() == b'candidate'


def test_partial_copy_never_publishes_and_retry_is_safe(tmp_path, monkeypatch):
    from z0int import artifacts

    path = bundle(tmp_path / 'bundle', {'weights.bin': b'complete candidate bytes'})
    def interrupted(source, destination, length):
        destination.write(source.read(4))
        raise OSError('synthetic interrupted copy')
    with monkeypatch.context() as patch:
        patch.setattr(artifacts.shutil, 'copyfileobj', interrupted)
        assert not import_manifest(path)['ok']
    assert artifacts.list_artifacts() == []
    assert not list((tmp_path / 'z0' / 'specialists').glob('.import-*'))
    completed = import_manifest(path)
    assert completed['ok']
    assert (Path(completed['path']) / 'weights.bin').read_bytes() == b'complete candidate bytes'


def test_completed_staging_manifest_is_not_discoverable_before_publish(tmp_path, monkeypatch):
    from z0int import artifacts

    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'})
    rename = artifacts.os.rename
    observed = []
    def observe_then_publish(source, destination):
        observed.append(artifacts.list_artifacts())
        rename(source, destination)
    monkeypatch.setattr(artifacts.os, 'rename', observe_then_publish)
    assert import_manifest(path)['ok']
    assert observed == [[]]
    assert [item['capability_id'] for item in artifacts.list_artifacts()] == ['fixture.quality']


def test_stale_index_cannot_advertise_absent_promoted_artifact(tmp_path):
    from z0int.artifacts import list_artifacts, specialists_root

    identity = 'sha256:' + '0' * 64
    (specialists_root() / 'index.json').write_text(json.dumps({
        'schema': 'z0int.specialists_index.v1',
        'artifacts': {identity: {'artifact_id': identity, 'promotion': {'status': 'promoted'}}},
    }))
    assert list_artifacts() == []


def test_exact_reimport_preserves_independent_local_promotion(tmp_path):
    path = bundle(tmp_path / 'bundle', {'weights.bin': b'candidate'})
    first = import_manifest(path)
    installed = Path(first['path']) / 'manifest.json'
    manifest = json.loads(installed.read_text())
    manifest['promotion'] = {'status': 'promoted', 'verifier': 'synthetic independent owner'}
    installed.write_text(json.dumps(manifest))
    before = installed.read_bytes()
    result = import_manifest(path)
    assert result['ok'] and result['already_installed']
    assert result['promotion'] == manifest['promotion']
    assert installed.read_bytes() == before
