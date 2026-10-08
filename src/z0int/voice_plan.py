"""Hardware-aware realtime voice planning.

This module intentionally owns selection/admission only. The realtime process
lifecycle lives in z0live.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

VOICE_PLAN_SCHEMA = "z0int.voice_plan.v1"
DEFAULT_ACTOR_ID = "personaplex-7b-nf4"
DEFAULT_MODEL = "brianmatzelle/personaplex-7b-v1-bnb-4bit"
DEFAULT_MODEL_REVISION = "bde165223cb92cc30fd8279878978838c12d25c5"
DEFAULT_RESERVED_VRAM_MB = 10_240
DEFAULT_MIN_TOTAL_VRAM_MB = 12_000
DEFAULT_IDLE_UNLOAD_SECONDS = 120


@dataclass(frozen=True)
class GpuSnapshot:
    index: int
    name: str
    total_vram_mb: int
    free_vram_mb: int

    @property
    def used_vram_mb(self) -> int:
        return max(0, self.total_vram_mb - self.free_vram_mb)


def probe_nvidia_gpus() -> list[GpuSnapshot]:
    """Return NVIDIA GPU memory snapshots using nvidia-smi."""
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return []

    rows: list[GpuSnapshot] = []
    for raw in out.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(",", 3)]
        if len(parts) != 4:
            continue
        try:
            rows.append(
                GpuSnapshot(
                    index=int(parts[0]),
                    name=parts[1],
                    total_vram_mb=int(float(parts[2])),
                    free_vram_mb=int(float(parts[3])),
                )
            )
        except ValueError:
            continue
    return rows


def _choose_gpu(
    gpus: Iterable[GpuSnapshot],
    *,
    min_total_vram_mb: int,
) -> GpuSnapshot | None:
    eligible = [g for g in gpus if g.total_vram_mb >= min_total_vram_mb]
    if not eligible:
        return None
    return max(eligible, key=lambda g: (g.free_vram_mb, g.total_vram_mb, -g.index))


def _plan_id(payload: dict) -> str:
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "vp_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def build_brainstorm_plan(
    *,
    gpus: Iterable[GpuSnapshot] | None = None,
    reserved_vram_mb: int = DEFAULT_RESERVED_VRAM_MB,
    min_total_vram_mb: int = DEFAULT_MIN_TOTAL_VRAM_MB,
    idle_unload_seconds: int = DEFAULT_IDLE_UNLOAD_SECONDS,
    harness: str = "omp",
) -> dict:
    """Build the first hardware-aware Brainstorm VoicePlan.

    The initial candidate is PersonaPlex NF4. z0live consumes this plan and owns
    process lifecycle; this function never launches, kills, or reranks processes.
    """
    gpu_rows = list(probe_nvidia_gpus() if gpus is None else gpus)
    gpu = _choose_gpu(gpu_rows, min_total_vram_mb=min_total_vram_mb)

    if not gpu:
        max_total = max((g.total_vram_mb for g in gpu_rows), default=0)
        admission = {
            "status": "ineligible",
            "admitted": False,
            "reason": "no_cuda_gpu_with_required_total_vram",
            "required_total_vram_mb": min_total_vram_mb,
            "reserved_vram_mb": reserved_vram_mb,
            "max_detected_total_vram_mb": max_total,
            "reclaim_needed_mb": None,
        }
        device = None
    else:
        reclaim_needed = max(0, reserved_vram_mb - gpu.free_vram_mb)
        admitted = reclaim_needed == 0
        admission = {
            "status": "admitted" if admitted else "blocked_busy",
            "admitted": admitted,
            "reason": "enough_free_vram" if admitted else "gpu_busy_reclaim_required",
            "required_total_vram_mb": min_total_vram_mb,
            "reserved_vram_mb": reserved_vram_mb,
            "free_vram_mb": gpu.free_vram_mb,
            "used_vram_mb": gpu.used_vram_mb,
            "reclaim_needed_mb": reclaim_needed,
        }
        device = {
            "backend": "cuda",
            "index": gpu.index,
            "name": gpu.name,
            "total_vram_mb": gpu.total_vram_mb,
            "free_vram_mb": gpu.free_vram_mb,
        }

    stable_identity = {
        "schema": VOICE_PLAN_SCHEMA,
        "profile": "brainstorm",
        "harness": harness,
        "actor_id": DEFAULT_ACTOR_ID,
        "model": DEFAULT_MODEL,
        "model_revision": DEFAULT_MODEL_REVISION,
        "quantization": "nf4",
        "device": device,
        "resource": {
            "residency": "session",
            "reserved_vram_mb": reserved_vram_mb,
            "idle_unload_seconds": idle_unload_seconds,
            "restore_previous_gpu_state": True,
            "priority": "interactive",
        },
        "admission": admission,
    }

    return {
        **stable_identity,
        "plan_id": _plan_id(stable_identity),
        "provider": "local",
        "adapter": "personaplex",
        "endpoint": {"host": "127.0.0.1", "port": 8998, "scheme": "https"},
        "fallbacks": [],
        "selection_reason": (
            "brainstorm profile requests native full-duplex voice; PersonaPlex NF4 is "
            "session-scoped and admitted only when the current CUDA device has enough free VRAM"
        ),
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Emit a hardware-aware z0int VoicePlan")
    p.add_argument("profile", nargs="?", default="brainstorm", choices=["brainstorm"])
    p.add_argument("--harness", default="omp")
    p.add_argument("--reserved-vram-mb", type=int, default=DEFAULT_RESERVED_VRAM_MB)
    p.add_argument("--min-total-vram-mb", type=int, default=DEFAULT_MIN_TOTAL_VRAM_MB)
    p.add_argument("--idle-unload-seconds", type=int, default=DEFAULT_IDLE_UNLOAD_SECONDS)
    p.add_argument("--output", type=Path)
    p.add_argument("--pretty", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = build_brainstorm_plan(
        reserved_vram_mb=max(0, args.reserved_vram_mb),
        min_total_vram_mb=max(0, args.min_total_vram_mb),
        idle_unload_seconds=max(0, args.idle_unload_seconds),
        harness=args.harness,
    )
    payload = json.dumps(plan, indent=2 if args.pretty else None, sort_keys=args.pretty)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if plan["admission"]["admitted"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
