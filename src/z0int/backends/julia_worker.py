#!/usr/bin/env python3
"""Resident Julia-1 decision worker — JSONL over stdin/stdout.

WHY A SEPARATE PROCESS
    Julia-1 pins ``transformers>=5.0,<5.1``. The z0int runtime runs transformers
    5.17, on which Julia fails outright::

        AttributeError: 'ModernBertModel' object has no attribute '_update_attention_mask'

    So Julia cannot be imported in-process the way ``laya``/``nanojev`` are. This
    worker runs under an interpreter where the pin holds, holds the engine
    resident, and speaks the same named-question shape the in-process adapters
    use. ``backends/julia.py`` owns the z0int-facing translation.

WHY IT IMPORTS NO Z0INT
    Keeping this file stdlib + ``julia`` only means it runs under a foreign venv
    with no z0int install, and cannot drag the z0int import graph into the
    pinned environment.

PROTOCOL
    one JSON object per line in, one JSON object per line out.

    {"op":"status"}                      -> {"op":"status","ok":true,...}
    {"op":"warm"}                        -> {"op":"warm","ok":true,"load_ms":...}
    {"op":"shutdown"}                    -> exits
    {"id":..,"state":..,"questions":{}}  -> {"id":..,"ok":true,"answers":{...}}

    Any failure is reported on the request's own line and never kills the worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

# Library chatter (transformers/torch warnings, tokenizer notices) must never
# reach the protocol stream. Keep a private handle on the real stdout and point
# sys.stdout at stderr for everything else.
_PROTOCOL_OUT = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
sys.stdout = sys.stderr

# Running this file as a script puts its own directory on sys.path[0]. That
# directory is `z0int/backends/`, which contains the *adapter* `julia.py` — so
# `import julia` would resolve to the adapter instead of the SupersonicLabs model
# package and fail with "attempted relative import with no known parent package".
# This worker is deliberately self-contained (stdlib + `julia` only), so its own
# directory is never wanted on the path.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]


def emit(payload: dict) -> None:
    _PROTOCOL_OUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _PROTOCOL_OUT.flush()


class _NoAutocast:
    """Disable torch.autocast for the duration of a call.

    Julia's CUDA path wraps inference in bfloat16 autocast. Measured on this host
    that is worth ~7.5e-2 on a 2-option ``noul`` decision — enough to flip a
    borderline boolean — while the residual true device difference is ~2.7e-3.
    ``--autocast off`` therefore makes CUDA numerics match CPU.
    """

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


class Engine:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.engine = None
        self.load_ms: float | None = None
        self.loaded_device: str | None = None
        self.load_error: str | None = None

    def warm(self) -> None:
        if self.engine is not None:
            return
        from julia import load_model

        if self.args.autocast == "off":
            import torch

            torch.autocast = _NoAutocast  # type: ignore[assignment]

        t0 = time.perf_counter()
        self.engine = load_model(
            self.args.model_dir,
            device=self.args.device,
            strict_encoding=self.args.strict,
            max_length=self.args.max_length,
            head_length=self.args.head_length,
        )
        self.load_ms = (time.perf_counter() - t0) * 1000.0
        self.loaded_device = self.args.device

    def status(self) -> dict:
        return {
            "op": "status",
            "ok": True,
            "loaded": self.engine is not None,
            "load_ms": self.load_ms,
            "load_error": self.load_error,
            "device": self.loaded_device or self.args.device,
            "model_dir": self.args.model_dir,
            "strict_encoding": self.args.strict,
            "max_length": self.args.max_length,
            "head_length": self.args.head_length,
            "autocast": self.args.autocast,
            "pid": os.getpid(),
        }

    def predict(self, req: dict) -> dict:
        self.warm()
        assert self.engine is not None
        t0 = time.perf_counter()
        raw = self.engine.predict(state=req["state"], questions=req["questions"])
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "id": req.get("id"),
            "ok": True,
            "answers": raw.get("answers") or {},
            "latency_ms": latency_ms,
            "load_ms": self.load_ms,
            "device": self.loaded_device,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--max-length", type=int, default=8192)
    ap.add_argument("--head-length", type=int, default=512)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--autocast", choices=("auto", "off"), default="auto")
    ap.add_argument("--warm-on-start", action="store_true",
                    help="load weights before serving; otherwise load on first request")
    args = ap.parse_args()

    engine = Engine(args)
    if args.warm_on_start:
        # A load failure is recorded, not fatal: `status` must still answer so the
        # adapter can report *why* it is unhealthy instead of seeing a dead pipe.
        try:
            engine.warm()
        except Exception as exc:  # noqa: BLE001
            engine.load_error = f"{type(exc).__name__}: {exc}"

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            emit({"id": None, "ok": False, "error": f"invalid JSON: {exc}"})
            continue

        op = req.get("op")
        if op == "shutdown":
            return 0
        try:
            if op == "status":
                emit(engine.status())
            elif op == "warm":
                engine.warm()
                emit({"op": "warm", "ok": True, "load_ms": engine.load_ms,
                      "device": engine.loaded_device})
            else:
                emit(engine.predict(req))
        except Exception as exc:  # noqa: BLE001
            emit({"id": req.get("id"), "ok": False,
                  "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()[-800:]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
