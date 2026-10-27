"""Hermetic regression tests for local serving probes and receipts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess

import pytest

from z0int.cognition import probe
from z0int.cognition.adapters.transport import ChatOutcome, TransportError
from z0int.cognition.registry import ServingEndpoint


def _endpoint():
    return ServingEndpoint(
        model_id="m",
        base_url="http://x.invalid",
        served_model="srv",
        runtime="llama.cpp",
        quantization="q4",
    )


def _receipt(**overrides):
    fields = {
        "model_id": "m",
        "runtime": "r",
        "quantization": None,
        "context": 1,
        "base_url": "u",
        "served_model": "s",
    }
    fields.update(overrides)
    return probe.ServingReceipt(**fields)


def _outcome(*, usage=None, timings=None, latency_ms=10.0, ttft_ms=None):
    return ChatOutcome(
        content="",
        tool_call=None,
        usage={} if usage is None else usage,
        timings={} if timings is None else timings,
        raw={},
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
    )


@pytest.fixture
def fake_transport(monkeypatch):
    prepared = []
    instances = []

    class FakeTransport:
        def __init__(self, config):
            self.config = config
            self.calls = []
            self.outcomes = list(prepared)
            instances.append(self)

        def chat(self, messages, **kw):
            self.calls.append((messages, kw))
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(probe, "OpenAICompatTransport", FakeTransport)
    return prepared, instances


@pytest.fixture
def stub_telemetry(monkeypatch):
    class StubSampler:
        peak_vram = 900.0
        peak_util = 55.0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(probe, "_VramSampler", StubSampler)
    monkeypatch.setattr(probe, "vram_used_mib", lambda: 500.0)
    monkeypatch.setattr(probe, "gpu_name", lambda: "GPU-X")


def test_nvidia_smi_helpers(monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda _name: None)
    assert probe._nvidia_smi("x") == []

    monkeypatch.setattr(probe.shutil, "which", lambda _name: "/x/nvidia-smi")
    calls = []

    def check_output(command, *, text, timeout):
        calls.append((command, text, timeout))
        return "  a \n\n b\n"

    monkeypatch.setattr(probe.subprocess, "check_output", check_output)
    assert probe._nvidia_smi("x") == ["a", "b"]
    assert calls == [(["/x/nvidia-smi", "x"], True, 8)]

    for exc in (subprocess.SubprocessError("bad"), OSError("gone")):
        def fail(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr(probe.subprocess, "check_output", fail)
        assert probe._nvidia_smi("x") == []


def test_gpu_value_parsers(monkeypatch):
    for rows, expected in ((["1234.5"], 1234.5), ([], None), (["N/A"], None)):
        monkeypatch.setattr(probe, "_nvidia_smi", lambda *_args: rows)
        assert probe.vram_used_mib() == expected
        assert probe.gpu_util_pct() == expected

    monkeypatch.setattr(probe, "_nvidia_smi", lambda *_args: ["GPU-X", "GPU-Y"])
    assert probe.gpu_name() == "GPU-X"
    monkeypatch.setattr(probe, "_nvidia_smi", lambda *_args: [])
    assert probe.gpu_name() is None


def test_vram_sampler_peaks_and_thread_join(monkeypatch):
    sampler = probe._VramSampler()
    assert sampler.peak_vram is None
    assert sampler.peak_util is None
    sampler.samples = [1.0, 7.0, 3.0]
    sampler.util = [2.0, 9.0]
    assert sampler.peak_vram == 7.0
    assert sampler.peak_util == 9.0

    monkeypatch.setattr(probe, "vram_used_mib", lambda: 5.0)
    monkeypatch.setattr(probe, "gpu_util_pct", lambda: 6.0)
    with probe._VramSampler(interval_s=0.01) as running:
        pass
    assert running._thread is not None
    assert running._thread.is_alive() is False


def test_serving_receipt_to_dict():
    receipt = _receipt(errors=("e",))
    value = receipt.to_dict()
    assert "quantization" not in value
    assert "machine" not in value
    assert value["schema"] == "z0int.serving_receipt.v1"
    assert value["errors"] == ["e"]
    assert value["measured_at"].endswith("Z")
    assert not _receipt().to_dict().get("errors")

    with pytest.raises(FrozenInstanceError):
        receipt.model_id = "changed"


def test_receipts_to_measurements():
    receipt = _receipt(vram_peak_mib=900.0, gpu_util_peak_pct=55.0)
    value = probe.receipts_to_measurements(receipt)
    assert set(value) == {
        "machine",
        "runtime",
        "quantization",
        "context",
        "measured_vram_mib",
        "vram_idle_mib",
        "vram_peak_mib",
        "cold_start_ms",
        "warm_ttft_ms",
        "decode_tok_s",
        "short_decision_ms",
        "long_decision_ms",
        "gpu_util_pct",
        "measured_at",
    }
    assert value["machine"] == "unknown"
    assert value["quantization"] == "unknown"
    assert value["measured_vram_mib"] == value["vram_peak_mib"] == 900.0
    assert value["gpu_util_pct"] == 55.0
    assert value["context"] == 1
    assert value["measured_at"] == receipt.measured_at


def test_write_receipts_explicit_dir(tmp_path: Path):
    root = tmp_path / "a" / "b"
    path = probe.write_receipts([_receipt(), _receipt(errors=("e",))], root)
    assert root.is_dir()
    assert path == root / "serving-receipts.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    values = [json.loads(line) for line in lines]
    assert all(value["schema"] == probe.SCHEMA for value in values)
    assert all(list(value) == sorted(value) for value in values)
    assert values[1]["errors"] == ["e"]

    empty = probe.write_receipts([], tmp_path / "empty")
    assert empty.is_file()
    assert empty.read_text(encoding="utf-8") == ""


def test_write_receipts_default_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setattr(probe, "_stamp", lambda: "STAMP")
    actual = probe.write_receipts([_receipt()])
    expected = tmp_path / "benchmarks" / "local_cognition" / "STAMP" / "serving-receipts.jsonl"
    assert actual.resolve() == expected.resolve()
    assert len(actual.read_text(encoding="utf-8").splitlines()) == 1


def test_probe_endpoint_metrics(fake_transport, stub_telemetry):
    prepared, instances = fake_transport
    usage = {"prompt_tokens": 5, "completion_tokens": 8}
    prepared.extend(
        [
            _outcome(
                usage=usage,
                timings={
                    "load_ms": 1500,
                    "prompt_ms": 20,
                    "predicted_ms": 500,
                    "predicted_n": 25,
                },
                latency_ms=1600.0,
            ),
            _outcome(
                usage=usage,
                timings={"prompt_ms": 10, "predicted_ms": 400, "predicted_n": 40},
                latency_ms=100.0,
            ),
            _outcome(),
            _outcome(),
        ]
    )
    endpoint = _endpoint()
    receipt = probe.probe_endpoint(endpoint, repeats=2)
    assert receipt.cold_start_ms == 1500.0
    assert receipt.warm_ttft_ms == 10.0
    assert receipt.decode_tok_s == 100.0
    assert receipt.prompt_tokens == 5
    assert receipt.completion_tokens == 8
    assert receipt.vram_idle_mib == 500.0
    assert receipt.vram_peak_mib == 900.0
    assert receipt.gpu_util_peak_pct == 55.0
    assert receipt.machine == "GPU-X"
    assert receipt.context == 4096
    assert receipt.errors == ()
    assert receipt.short_decision_ms is not None and receipt.short_decision_ms >= 0
    assert receipt.long_decision_ms is not None and receipt.long_decision_ms >= 0

    assert len(instances) == 1
    transport = instances[0]
    assert transport.config.model == "srv"
    assert transport.config.base_url == endpoint.base_url
    assert len(transport.calls) == 4
    for messages, kw in transport.calls[:2]:
        assert messages == [{"role": "user", "content": "Reply with the single word: ready."}]
        assert kw == {"max_tokens": 8, "temperature": 0.0}
    for messages, kw in transport.calls[2:]:
        assert [message["role"] for message in messages] == ["system", "user"]
        assert "tool.a" in messages[1]["content"]
        assert kw == {"max_tokens": 64, "temperature": 0.0}


def test_probe_endpoint_fallback_metrics(fake_transport, stub_telemetry):
    prepared, instances = fake_transport
    prepared.extend(
        [
            _outcome(
                usage={"completion_tokens": 10},
                timings={},
                latency_ms=2000.0,
                ttft_ms=30.0,
            ),
            _outcome(),
            _outcome(),
        ]
    )
    receipt = probe.probe_endpoint(_endpoint(), repeats=1)
    assert receipt.cold_start_ms == 2000.0
    assert receipt.warm_ttft_ms == 30.0
    assert receipt.decode_tok_s == 5.0
    assert receipt.errors == ()
    assert len(instances[0].calls) == 3


def test_probe_endpoint_transport_errors_never_raise(fake_transport, stub_telemetry):
    prepared, instances = fake_transport
    prepared.extend(TransportError("boom") for _ in range(4))
    receipt = probe.probe_endpoint(_endpoint(), repeats=2)
    assert receipt.errors == (
        "chat[0]: boom",
        "chat[1]: boom",
        "decision[short]: boom",
        "decision[long]: boom",
    )
    assert receipt.cold_start_ms is None
    assert receipt.short_decision_ms is None
    assert receipt.long_decision_ms is None
    assert len(instances[0].calls) == 4

    prepared[:] = [TransportError("boom") for _ in range(3)]
    minimum = probe.probe_endpoint(_endpoint(), repeats=0)
    assert minimum.errors == (
        "chat[0]: boom",
        "decision[short]: boom",
        "decision[long]: boom",
    )
    assert minimum.cold_start_ms is None
    assert len(instances[1].calls) == 3


def test_check_uses_given_backend(monkeypatch):
    sentinel = object()
    lookups = []

    class FakeManifest:
        def get(self, model_id):
            lookups.append(model_id)
            return sentinel

    monkeypatch.setattr(
        "z0int.cognition.manifest.load_local_cognition", lambda: FakeManifest()
    )

    class FakeDecision:
        def to_dict(self):
            return {"choice": "tool.a"}

    class FakeBackend:
        def __init__(self):
            self.requests = []

        def health(self):
            return {"ok": True}

        def decide(self, request):
            self.requests.append(request)
            return FakeDecision()

    backend = FakeBackend()
    endpoint = _endpoint()
    expected = {"health": {"ok": True}, "decision": {"choice": "tool.a"}}
    assert probe.check(endpoint, tool_backend=backend) == expected
    generated = backend.requests[0]
    assert generated.state == probe.SHORT_DECISION_STATE
    assert [action.action_id for action in generated.legal.legal] == [
        "tool.a",
        "tool.b",
        "tool.c",
        "tool.d",
        "tool.e",
    ]
    assert [action.action_id for action in probe._legal_action_set(state="s").legal] == [
        "tool.a",
        "tool.b",
        "tool.c",
        "tool.d",
        "tool.e",
    ]

    explicit = probe.ToolDecisionRequest(
        state="explicit", legal=probe._legal_action_set(state="explicit")
    )
    assert probe.check(endpoint, request=explicit, tool_backend=backend) == expected
    assert backend.requests[1] is explicit
    assert lookups == ["m", "m"]
