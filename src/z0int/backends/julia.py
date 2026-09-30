"""Julia-1 (~144.3M) DecisionBackend — SupersonicLabs/Julia-1 via a resident worker.

Julia is a finite-choice decision model, not a chat model: it scores 2–20 supplied
options for a typed question about a state and returns full softmax probabilities.

WHY THIS ONE USES A WORKER
    ``laya`` and ``nanojev`` import their runtimes in-process. Julia cannot:
    it pins ``transformers>=5.0,<5.1`` and fails on the 5.17 that z0int runs with::

        AttributeError: 'ModernBertModel' object has no attribute '_update_attention_mask'

    Rather than move the whole z0int stack onto a pinned transformers, the engine
    lives in ``julia_worker.py`` under an interpreter where the pin holds, and this
    adapter owns the z0int-facing translation. The wire shape is the same
    named-question mapping the in-process adapters use, so nothing about the
    routing layer is Julia-specific.

PLACEMENT
    Defaults to CPU. Julia on CUDA costs ~825 MB VRAM to buy ~1.5-3.7x latency, and
    it runs bfloat16 autocast there, which measured ~7.5e-2 on a 2-option ``noul``
    decision — enough to flip a borderline boolean. ``Z0INT_JULIA_AUTOCAST=off``
    makes CUDA match FP32. See ``docs/julia-decision-model.md``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import (
    BackendCapabilities,
    BackendHealth,
    DecisionRequest,
    DecisionResult,
)
from .named_questions import answer_from_named, question_to_named

ID = "julia_1"
ALIASES = ("julia", "julia-1")

#: Julia's own hard contract (julia/data.py validate_row): 2..20 rendered options.
JULIA_MAX_OPTIONS = 20
#: z0int's DecisionQuestion caps score levels at 10, which binds before Julia's 20.
Z0INT_MAX_SCORE_LEVELS = 10

WORKER = Path(__file__).with_name("julia_worker.py")
DEFAULT_TIMEOUT_S = 180.0


def _default_device() -> str:
    return os.environ.get("Z0INT_JULIA_DEVICE", "cpu")


def _default_autocast() -> str:
    return os.environ.get("Z0INT_JULIA_AUTOCAST", "auto")


def _candidate_pythons() -> list[str]:
    """Interpreters that might satisfy Julia's transformers pin, best first."""
    out: list[str] = []
    for env in ("Z0INT_JULIA_PYTHON",):
        v = os.environ.get(env)
        if v:
            out.append(v)
    out += [
        "/home/kvn/tmp/julia-venv/bin/python",
        str(Path.home() / ".z0int" / "julia-venv" / "bin" / "python"),
    ]
    return out


def _probe(python: str) -> tuple[bool, str]:
    """Can this interpreter import julia and load the model directory?"""
    if not python or not Path(python).exists():
        return False, f"interpreter not found: {python}"
    code = (
        "import importlib.metadata as m, transformers, torch;"
        "print(transformers.__version__)"
    )
    try:
        p = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"probe failed: {exc}"
    if p.returncode != 0:
        return False, f"probe failed: {p.stderr.strip()[-200:]}"
    tv = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "?"
    try:
        major_minor = tuple(int(x) for x in tv.split(".")[:2])
    except ValueError:
        return True, f"transformers {tv}"
    if major_minor >= (5, 1) or major_minor < (5, 0):
        return False, f"transformers {tv} violates Julia's >=5.0,<5.1 pin"
    return True, f"transformers {tv}"


def _resolve_model_dir(*, hf: str, revision: str, model_dir: Path | None = None) -> Path:
    """Locate the Julia checkout.

    ``Z0INT_JULIA_MODEL_DIR`` lets an operator pin the complete repository
    anywhere on disk; otherwise fall back to the Hugging Face cache. The complete
    repository is required, not just ``model.safetensors`` — the runtime code ships
    with the weights and this is not a Transformers AutoModel.
    """
    if model_dir is not None:
        return model_dir.expanduser()
    override = os.environ.get("Z0INT_JULIA_MODEL_DIR")
    if override:
        return Path(override).expanduser()
    # scripts/setup-julia.sh's default target: use it when it is a complete checkout.
    from z0int import paths

    managed = paths.home() / "models" / "julia_1"
    if managed.is_dir() and not missing_entries(managed):
        return managed
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo_id=hf, revision=revision))


def _required_entries() -> tuple[str, ...]:
    """Julia is not a Transformers AutoModel: the runtime code must ship with the weights."""
    return (
        "model.safetensors",
        "julia_config.json",
        "encoder/config.json",
        "tokenizer/tokenizer.json",
        "julia/__init__.py",
    )


def missing_entries(path: Path) -> list[str]:
    return [n for n in _required_entries() if not (path / n).exists()]


@dataclass
class _Worker:
    process: subprocess.Popen
    python: str
    started_at: float
    lock: threading.Lock
    requests: int = 0


class JuliaBackend:
    ID = ID

    def __init__(
        self,
        *,
        model_id: str = ID,
        hf: str = "",
        revision: str = "",
        device: str | None = None,
        autocast: str | None = None,
        max_length: int = 8192,
        head_length: int = 512,
        strict_encoding: bool = True,
        model_dir: Path | None = None,
        python: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        self.model_id = model_id
        self.hf = hf
        self.revision = revision
        self.device = device or _default_device()
        self.autocast = autocast or _default_autocast()
        self.max_length = max_length
        self.head_length = head_length
        self.strict_encoding = strict_encoding
        self._model_dir = model_dir
        self._python = python
        self.timeout_s = timeout_s
        self._worker: _Worker | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- construction
    @classmethod
    def for_manifest_id(cls, manifest_id: str = ID) -> "JuliaBackend":
        from z0int.models_mgmt import load_manifest

        meta = (load_manifest().get("models") or {}).get(manifest_id) or {}
        hf = str(meta.get("hf") or "")
        rev = str(meta.get("revision") or "")
        if not hf or not rev:
            raise ValueError(f"manifest {manifest_id!r} missing hf/revision")
        return cls(model_id=manifest_id, hf=hf, revision=rev)

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            id=self.ID,
            kind="finite_choice_decision",
            description=(
                "SupersonicLabs Julia-1 144.3M finite-choice decision model; "
                "choice/score/noul over 2-20 supplied options"
            ),
            local=True,
            supports_boolean=True,
            supports_choice=True,
            supports_score=True,
            # Julia's own wire limit binds for choice; z0int's DecisionQuestion
            # caps score levels at 10 before Julia's 20 is reachable.
            max_choice_options=JULIA_MAX_OPTIONS,
            max_score_levels=Z0INT_MAX_SCORE_LEVELS,
            trainable=False,
            returns_distribution=True,
            autoregressive_decode=False,
            supports_batch_questions=True,
            supports_observed_outcome_training=False,
            supports_soft_distribution_training=False,
        )

    # -------------------------------------------------------------------- health
    def _model_path(self) -> Path:
        return _resolve_model_dir(hf=self.hf, revision=self.revision, model_dir=self._model_dir)

    def _select_python(self) -> tuple[str, str]:
        if self._python:
            ok, detail = _probe(self._python)
            return self._python, detail
        details = []
        for cand in _candidate_pythons():
            ok, detail = _probe(cand)
            if ok:
                return cand, detail
            details.append(f"{cand}: {detail}")
        which = shutil.which("python3")
        if which:
            ok, detail = _probe(which)
            if ok:
                return which, detail
            details.append(f"{which}: {detail}")
        return "", "; ".join(details) or "no candidate interpreter could import julia"

    def health(self, *, load: bool = False) -> BackendHealth:
        diags: dict[str, Any] = {
            "hf": self.hf,
            "manifest_id": self.model_id,
            "revision": self.revision,
            "device": self.device,
            "autocast": self.autocast,
            "runtime": "julia-worker",
            "strict_encoding": self.strict_encoding,
            "max_length": self.max_length,
            "head_length": self.head_length,
            "license": "apache-2.0",
            "commercial_use": True,
        }
        python, detail = self._select_python()
        diags["python"] = python
        diags["python_detail"] = detail
        if not python:
            return BackendHealth(
                id=self.ID, configured=True, ready=False, loaded=False,
                model=self.model_id, detail=f"no usable interpreter: {detail}", diagnostics=diags,
            )
        try:
            path = self._model_path()
        except Exception as exc:  # noqa: BLE001
            return BackendHealth(
                id=self.ID, configured=True, ready=False, loaded=False, model=self.model_id,
                detail=f"checkpoint not resolvable: {exc}", diagnostics=diags,
            )
        missing = missing_entries(path)
        diags["model_dir"] = str(path)
        diags["missing_entries"] = missing
        if missing:
            return BackendHealth(
                id=self.ID, configured=True, ready=False, loaded=False, model=self.model_id,
                detail=f"incomplete Julia checkout, missing {missing}", diagnostics=diags,
            )
        if load:
            try:
                self._ensure_worker()
                st = self._request({"op": "warm"})
                diags["load_ms"] = st.get("load_ms")
                diags["worker_pid"] = st.get("pid")
                return BackendHealth(
                    id=self.ID, configured=True, ready=True, loaded=True, model=self.model_id,
                    detail=f"loaded on {self.device} ({self.autocast} autocast)",
                    checkpoint=str(path), diagnostics=diags,
                )
            except Exception as exc:  # noqa: BLE001
                return BackendHealth(
                    id=self.ID, configured=True, ready=True, loaded=False, model=self.model_id,
                    detail=f"load failed: {exc}", diagnostics=diags,
                )
        return BackendHealth(
            id=self.ID, configured=True, ready=True, loaded=False, model=self.model_id,
            detail="checkout present", checkpoint=str(path), diagnostics=diags,
        )

    # ------------------------------------------------------------------- worker
    def _worker_cmd(self, python: str) -> list[str]:
        cmd = [
            python, str(WORKER),
            "--model-dir", str(self._model_path()),
            "--device", self.device,
            "--max-length", str(self.max_length),
            "--head-length", str(self.head_length),
            "--autocast", self.autocast,
        ]
        if self.strict_encoding:
            cmd.append("--strict")
        return cmd

    def _ensure_worker(self) -> _Worker:
        with self._lock:
            if self._worker is not None and self._worker.process.poll() is None:
                return self._worker
            python, detail = self._select_python()
            if not python:
                raise RuntimeError(f"Julia interpreter unavailable: {detail}")
            proc = subprocess.Popen(
                self._worker_cmd(python),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
            self._worker = _Worker(process=proc, python=python,
                                   started_at=time.time(), lock=threading.Lock())
            return self._worker

    def _request(self, payload: dict) -> dict:
        worker = self._ensure_worker()
        with worker.lock:
            proc = worker.process
            if proc.poll() is not None:
                worker = self._ensure_worker()
                proc = worker.process
            try:
                proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                detail = ""
                try:
                    detail = (proc.stderr.read() or "")[-400:] if proc.stderr else ""
                except Exception:  # noqa: BLE001
                    pass
                self._worker = None
                raise RuntimeError(f"julia worker pipe failed: {exc} {detail}") from exc

            deadline = time.monotonic() + self.timeout_s
            while True:
                if time.monotonic() > deadline:
                    self._worker = None
                    proc.kill()
                    raise TimeoutError(f"julia worker exceeded {self.timeout_s}s")
                line = proc.stdout.readline()
                if not line:
                    code = proc.poll()
                    detail = ""
                    try:
                        detail = (proc.stderr.read() or "")[-400:] if proc.stderr else ""
                    except Exception:  # noqa: BLE001
                        pass
                    self._worker = None
                    raise RuntimeError(f"julia worker exited (code={code}) {detail}")
                line = line.strip()
                if not line:
                    continue
                try:
                    out = json.loads(line)
                except json.JSONDecodeError:
                    # Never let library chatter desync the protocol.
                    continue
                worker.requests += 1
                if not out.get("ok", False) and out.get("error"):
                    raise ValueError(f"julia: {out['error']}")
                return out

    def resident_pids(self) -> list[int]:
        """Out-of-process memory the bench should attribute to this backend."""
        w = self._worker
        return [w.process.pid] if w is not None and w.process.poll() is None else []

    def close(self) -> None:
        with self._lock:
            w = self._worker
            self._worker = None
        if w is None:
            return
        try:
            if w.process.poll() is None:
                w.process.stdin.write('{"op":"shutdown"}\n')
                w.process.stdin.flush()
                w.process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                w.process.kill()
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------------------------------------------- evaluate
    def evaluate(self, request: DecisionRequest) -> DecisionResult:
        questions = {q.id: question_to_named(q) for q in request.questions}
        state = request.state
        if not isinstance(state, str):
            # Render the state EXACTLY as Julia's own runtime does. julia/data.py
            # `sequence` uses ``json.dumps(state, ensure_ascii=False)`` -- default
            # separators, insertion order. An earlier version of this adapter used
            # ``sort_keys=True, separators=(",", ":")``, which changes the token
            # stream and measured 1300/2000 against the published 1451/2000 on
            # typed-decisions: a 151-question fidelity loss from serialization
            # alone. Validate first (finiteness), then render identically.
            json.dumps(state, ensure_ascii=False, allow_nan=False)
            state = json.dumps(state, ensure_ascii=False)
        worker = self._ensure_worker()
        t0 = time.perf_counter()
        out = self._request({
            "id": request.request_id,
            "state": state,
            "questions": questions,
        })
        wall_ms = (time.perf_counter() - t0) * 1000.0
        raw_answers = out.get("answers") or {}
        answers = []
        for q in request.questions:
            raw = raw_answers.get(q.id)
            if not isinstance(raw, dict):
                raise ValueError(f"julia returned no answer for question {q.id!r}")
            answers.append(answer_from_named(q, raw, source="julia"))
        return DecisionResult(
            backend=self.ID,
            model=self.model_id,
            revision=self.revision,
            answers=tuple(answers),
            latency_ms=wall_ms,
            diagnostics={
                "device": out.get("device") or self.device,
                "placement": "cuda" if str(out.get("device") or self.device).startswith("cuda") else "cpu",
                "autocast": self.autocast,
                "strict_encoding": self.strict_encoding,
                "max_length": self.max_length,
                "head_length": self.head_length,
                "load_ms": out.get("load_ms"),
                "worker_ms": out.get("latency_ms"),
                "worker_pid": worker.process.pid,
                "worker_requests": worker.requests,
                "question_count": len(request.questions),
                "runtime": "julia-worker",
                "model_sha256": "df853bf7fe424420011f3d0c47a05d7341aa9eefa7fb9f203ea4aada4ad95b72",
                "probability_status": "full softmax over supplied options; not guaranteed certainty",
                "option_limit": JULIA_MAX_OPTIONS,
            },
        )
