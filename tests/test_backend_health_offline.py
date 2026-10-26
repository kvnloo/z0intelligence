"""Ordinary health checks must not fetch weights, even on an online host."""
import pytest
from unittest.mock import patch

from z0int.backends.decider import DeciderBackend
from z0int.backends.julia import JuliaBackend
from z0int.backends.laya import LayaBackend


@pytest.mark.parametrize('backend,module,seam', [
    (LayaBackend, 'laya', '_laya_import_error'),
    (DeciderBackend, 'decider', '_runtime_import_error'),
    (JuliaBackend, 'julia', None),
])
def test_uncached_health_cannot_download(backend, module, seam, tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    monkeypatch.delenv('Z0INT_JULIA_MODEL_DIR', raising=False)
    calls = []

    def offline_snapshot(**kwargs):
        calls.append(kwargs)
        assert kwargs.get('local_files_only') is True, 'health attempted network access'
        raise FileNotFoundError('uncached checkpoint')

    instance = backend(model_id='missing', hf='test/missing', revision='0' * 40)
    with patch('huggingface_hub.snapshot_download', side_effect=offline_snapshot):
        if seam:
            with patch(f'z0int.backends.{module}.{seam}', return_value=None):
                health = instance.health(load=False)
        else:
            health = instance.health(load=False)
    assert calls
    assert not health.ready
    assert not health.loaded


def test_doctor_does_not_import_torch(tmp_path):
    """`z0int doctor` (and so the contributor smoke) must not import torch.

    Importing torch loads several GB of CUDA libraries; in the contributor
    clean-container run a cold import took minutes and the agent gave up."""
    import os
    import subprocess
    import sys

    env = dict(os.environ, Z0INT_HOME=str(tmp_path), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               SKIP_EVOLUTION_LAB='1')
    p = subprocess.run([sys.executable, '-X', 'importtime', '-m', 'z0int', 'doctor', '--json'],
                       env=env, capture_output=True, text=True, timeout=600)
    imported = {line.rsplit('|', 1)[-1].strip() for line in p.stderr.splitlines() if line.startswith('import time:')}
    assert 'torch' not in imported, sorted(m for m in imported if m.startswith(('torch', 'transformers')))[:10]
