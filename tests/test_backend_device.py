from z0int.backends import device


def test_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("Z0INT_DECIDER_DEVICE", "cuda:1")
    monkeypatch.setattr(device, "nvidia_present", lambda: False)
    assert device.default_device("Z0INT_DECIDER_DEVICE") == "cuda:1"


def test_no_gpu_defaults_to_cpu(monkeypatch):
    for var in ("Z0INT_DECIDER_DEVICE", "Z0INT_OPENJEV_DEVICE", "Z0INT_NANOJEV_DEVICE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(device, "nvidia_present", lambda: False)
    from z0int.backends.decider import _default_device
    from z0int.backends.nanojev import NanoJevBackend
    from z0int.backends.openjev_direct import OpenJevDirectBackend

    assert _default_device() == "cpu"
    assert OpenJevDirectBackend(model_id="openjev_06b", hf="Qwen/Qwen3-0.6B", revision="c" * 40).device == "cpu"
    nj = NanoJevBackend.from_config()
    assert nj.device == "cpu" and nj.precision == "fp32"


def test_nvidia_present_selects_cuda_spelling(monkeypatch):
    monkeypatch.delenv("Z0INT_NANOJEV_DEVICE", raising=False)
    monkeypatch.setattr(device, "nvidia_present", lambda: True)
    assert device.default_device("Z0INT_NANOJEV_DEVICE", cuda="cuda:0") == "cuda:0"
    assert device.default_device("Z0INT_OPENJEV_DEVICE") == "cuda"


def test_hidden_gpus_mean_cpu(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert device.nvidia_present() is False


def test_detection_never_imports_torch():
    import subprocess
    import sys

    code = ("import sys; from z0int.backends.device import default_device; "
            "default_device('Z0INT_OPENJEV_DEVICE'); "
            "from z0int.backends.registry import backend_status; "
            "print('torch' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "False"
