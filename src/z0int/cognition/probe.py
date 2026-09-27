"""Local serving probe: record real machine measurements, never estimates.

Produces ``z0int.serving_receipt.v1`` rows under
``~/.z0int/benchmarks/local_cognition/<UTC>/`` and can splice them back into the
capability manifest as ``measurements`` (kerdoios#50 consumes the same rows).

Everything here is best-effort about telemetry: a missing ``nvidia-smi`` yields
``None`` fields, it never fabricates a number and it never blocks the measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
import json
import shutil
import subprocess
import threading
import time

from .adapters.local_slm import ToolDecisionRequest
from .adapters.transport import OpenAICompatTransport, ServerConfig, TransportError
from .actions import ActionCandidate, ActionGraph, compile_actions
from .registry import ServingEndpoint

SCHEMA = "z0int.serving_receipt.v1"

SHORT_DECISION_STATE = "Read the file /etc/hostname and report its contents."
LONG_DECISION_STATE = (
    "Investigate why the nightly benchmark regressed. You may read several files, "
    "run the test suite, inspect the last five commits, and compare two candidate "
    "hypotheses before choosing the next single action. " * 12
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _nvidia_smi(*args: str) -> list[str]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.check_output([exe, *args], text=True, timeout=8)
    except (subprocess.SubprocessError, OSError):
        return []
    return [line.strip() for line in out.strip().splitlines() if line.strip()]


def vram_used_mib() -> float | None:
    rows = _nvidia_smi("--query-gpu=memory.used", "--format=csv,noheader,nounits")
    if not rows:
        return None
    try:
        return float(rows[0])
    except ValueError:
        return None


def gpu_util_pct() -> float | None:
    rows = _nvidia_smi("--query-gpu=utilization.gpu", "--format=csv,noheader,nounits")
    if not rows:
        return None
    try:
        return float(rows[0])
    except ValueError:
        return None


def gpu_name() -> str | None:
    rows = _nvidia_smi("--query-gpu=name", "--format=csv,noheader")
    return rows[0] if rows else None


class _VramSampler:
    """Poll VRAM/GPU utilisation on a background thread for the duration of a probe."""

    def __init__(self, interval_s: float = 0.25) -> None:
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.samples: list[float] = []
        self.util: list[float] = []

    def __enter__(self) -> _VramSampler:
        def loop() -> None:
            while not self._stop.is_set():
                v = vram_used_mib()
                if v is not None:
                    self.samples.append(v)
                g = gpu_util_pct()
                if g is not None:
                    self.util.append(g)
                self._stop.wait(self._interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def peak_vram(self) -> float | None:
        return max(self.samples) if self.samples else None

    @property
    def peak_util(self) -> float | None:
        return max(self.util) if self.util else None


def _fixture_graph() -> ActionGraph:
    actions = tuple(
        ActionCandidate(
            action_id=aid,
            kind="tool",
            description=desc,
            tool=aid.split(".")[0],
            family=aid.split(".")[0],
            arguments_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": (["path"] if aid != "tool.b" else []),
                "additionalProperties": False,
            },
        )
        for aid, desc in (
            ("tool.a", "Read a file from disk"),
            ("tool.b", "List the working directory"),
            ("tool.c", "Run the unit test suite"),
            ("tool.d", "Search the repository for a symbol"),
            ("tool.e", "Compare two revisions"),
        )
    )
    return ActionGraph(actions=actions)


def _legal_action_set(*, state: str):
    return compile_actions(
        graph=_fixture_graph(),
        granted_capabilities=("fs.read", "shell"),
        authority=("read",),
        budget_units=8,
        facts={"state": state},
    )


@dataclass(frozen=True)
class ServingReceipt:
    model_id: str
    runtime: str
    quantization: str | None
    context: int
    base_url: str
    served_model: str
    machine: str | None = None
    vram_idle_mib: float | None = None
    vram_peak_mib: float | None = None
    gpu_util_peak_pct: float | None = None
    cold_start_ms: float | None = None
    warm_ttft_ms: float | None = None
    decode_tok_s: float | None = None
    short_decision_ms: float | None = None
    long_decision_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    errors: tuple[str, ...] = ()
    measured_at: str = field(default_factory=_now)
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v is not None}
        out["schema"] = SCHEMA
        if self.errors:
            out["errors"] = list(self.errors)
        return out


def probe_endpoint(
    endpoint: ServingEndpoint,
    *,
    context: int = 4096,
    repeats: int = 3,
    timeout_s: float = 180.0,
) -> ServingReceipt:
    """Measure one served endpoint end to end."""
    config = ServerConfig(
        base_url=endpoint.base_url,
        model=endpoint.served_model,
        api_key=endpoint.api_key,
        timeout_s=timeout_s,
        runtime=endpoint.runtime,
        quantization=endpoint.quantization,
    )
    transport = OpenAICompatTransport(config)
    errors: list[str] = []
    idle = vram_used_mib()

    prompt = "Reply with the single word: ready."
    cold_start_ms: float | None = None
    warm_ttft: float | None = None
    decode_tok_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    with _VramSampler() as sampler:
        for attempt in range(max(1, repeats)):
            try:
                outcome = transport.chat(
                    [{"role": "user", "content": prompt}], max_tokens=8, temperature=0.0
                )
            except TransportError as exc:
                errors.append(f"chat[{attempt}]: {exc}")
                continue
            timings = outcome.timings or {}
            usage = outcome.usage or {}
            if attempt == 0:
                cold_start_ms = float(timings.get("load_ms") or outcome.latency_ms)
            if timings.get("prompt_ms") is not None:
                warm_ttft = float(timings["prompt_ms"])
            elif outcome.ttft_ms is not None:
                warm_ttft = outcome.ttft_ms
            if timings.get("predicted_ms") and timings.get("predicted_n"):
                decode_tok_s = float(timings["predicted_n"]) / (
                    float(timings["predicted_ms"]) / 1000.0
                )
            elif usage.get("completion_tokens") and outcome.latency_ms > 0:
                decode_tok_s = float(usage["completion_tokens"]) / (outcome.latency_ms / 1000.0)
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")

        short_ms = _time_decision(transport, SHORT_DECISION_STATE, errors, "short")
        long_ms = _time_decision(transport, LONG_DECISION_STATE, errors, "long")

    return ServingReceipt(
        model_id=endpoint.model_id,
        runtime=endpoint.runtime,
        quantization=endpoint.quantization,
        context=context,
        base_url=endpoint.base_url,
        served_model=endpoint.served_model,
        machine=gpu_name(),
        vram_idle_mib=idle,
        vram_peak_mib=sampler.peak_vram,
        gpu_util_peak_pct=sampler.peak_util,
        cold_start_ms=cold_start_ms,
        warm_ttft_ms=warm_ttft,
        decode_tok_s=decode_tok_s,
        short_decision_ms=short_ms,
        long_decision_ms=long_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        errors=tuple(errors),
    )


def _time_decision(
    transport: OpenAICompatTransport, state: str, errors: list[str], label: str
) -> float | None:
    """Time a realistic bounded decision (prompt build + inference), not a bare ping."""
    from .adapters.dialects import render_choice_prompt

    legal = _legal_action_set(state=state)
    prompt = render_choice_prompt(state=state, actions=legal.legal, objective="pick one")
    started = time.perf_counter()
    try:
        transport.chat(
            [
                {"role": "system", "content": "Choose exactly one action id."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=64,
            temperature=0.0,
        )
    except TransportError as exc:
        errors.append(f"decision[{label}]: {exc}")
        return None
    return (time.perf_counter() - started) * 1000.0


def write_receipts(receipts: Sequence[ServingReceipt], out_dir: Path | None = None) -> Path:
    from .. import paths

    root = out_dir or (paths.home() / "benchmarks" / "local_cognition" / _stamp())
    root.mkdir(parents=True, exist_ok=True)
    target = root / "serving-receipts.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for r in receipts:
            fh.write(json.dumps(r.to_dict(), sort_keys=True) + "\n")
    return target


def receipts_to_measurements(receipt: ServingReceipt) -> dict[str, Any]:
    """Shape a receipt as a manifest ``measurements`` entry."""
    return {
        "machine": receipt.machine or "unknown",
        "runtime": receipt.runtime,
        "quantization": receipt.quantization or "unknown",
        "context": receipt.context,
        "measured_vram_mib": receipt.vram_peak_mib,
        "vram_idle_mib": receipt.vram_idle_mib,
        "vram_peak_mib": receipt.vram_peak_mib,
        "cold_start_ms": receipt.cold_start_ms,
        "warm_ttft_ms": receipt.warm_ttft_ms,
        "decode_tok_s": receipt.decode_tok_s,
        "short_decision_ms": receipt.short_decision_ms,
        "long_decision_ms": receipt.long_decision_ms,
        "gpu_util_pct": receipt.gpu_util_peak_pct,
        "measured_at": receipt.measured_at,
    }


def check(
    endpoint: ServingEndpoint,
    *,
    request: ToolDecisionRequest | None = None,
    tool_backend: Any = None,
) -> Mapping[str, Any]:
    """End-to-end liveness check: health, then one real bounded decision.

    Returns an inspectable dict — never raises for a model-side failure, so this
    is safe to call from a readiness gate.
    """
    from .manifest import load_local_cognition

    manifest = load_local_cognition()
    capability = manifest.get(endpoint.model_id)
    backend = tool_backend
    if backend is None:
        from .registry import build_backend

        backend = build_backend(capability, endpoint)
    report: dict[str, Any] = {"health": backend.health()}
    if request is None:
        legal = _legal_action_set(state=SHORT_DECISION_STATE)
        request = ToolDecisionRequest(state=SHORT_DECISION_STATE, legal=legal)
    decision = backend.decide(request)
    report["decision"] = decision.to_dict()
    return report


__all__ = [
    "SCHEMA",
    "ServingReceipt",
    "check",
    "gpu_name",
    "gpu_util_pct",
    "probe_endpoint",
    "receipts_to_measurements",
    "vram_used_mib",
    "write_receipts",
]
