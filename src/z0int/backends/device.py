"""Default compute device for torch-backed DecisionBackends, without importing torch.

An explicit env var always wins. Otherwise pick CUDA only when an NVIDIA driver is
visibly present, else CPU — so every backend works on any machine (a CUDA-less laptop
must not raise just because nobody exported ``Z0INT_*_DEVICE``). Detection is a
filesystem/PATH probe, never a torch import, so ``doctor``/``list`` stay cheap.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_NVIDIA_PROC = Path("/proc/driver/nvidia/version")


def nvidia_present() -> bool:
    if os.environ.get("CUDA_VISIBLE_DEVICES", None) in ("", "-1"):
        return False
    return _NVIDIA_PROC.exists() or shutil.which("nvidia-smi") is not None


def default_device(env_var: str, *, cuda: str = "cuda") -> str:
    """``$env_var`` if set, else ``cuda`` (spelled as the backend wants) when NVIDIA is present, else ``cpu``."""
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit
    return cuda if nvidia_present() else "cpu"
