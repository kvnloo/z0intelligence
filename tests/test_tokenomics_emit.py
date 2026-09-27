from __future__ import annotations

import json
import tempfile
from pathlib import Path

from z0int.tokenomics_emit import emit_provider_usage


def test_provider_usage_preserves_explicit_measurement_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = emit_provider_usage(
            harness="omp",
            trace_id="1" * 32,
            session_id="s1",
            provider="anthropic",
            model="claude",
            usage={"input_tokens": 10, "output_tokens": 2},
            measurement_state="complete",
            state_reason="provider_response_usage",
            logical_source_id="account-a",
            identity_basis="provider",
            root=root,
        )
        assert path is not None
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(rows) == 1
        row = rows[0]
        source = row.get("measurement_source") or row
        assert source["measurement_state"] == "complete"
        assert source["state_reason"] == "provider_response_usage"
        assert source["logical_source_id"] == "account-a"
        assert source["identity_basis"] == "provider"


def test_provider_usage_does_not_invent_measurement_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = emit_provider_usage(
            harness="hermes",
            trace_id="2" * 32,
            provider="openai",
            model="gpt",
            usage={"input_tokens": 1, "output_tokens": 1},
            root=root,
        )
        assert path is not None
        row = json.loads(path.read_text(encoding="utf-8").strip())
        source = row.get("measurement_source")
        assert source is None or source.get("measurement_state") is None
        assert source is None or source.get("state_reason") is None
