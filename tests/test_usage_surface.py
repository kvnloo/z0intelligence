"""Red-first checks for the Tern /usage network snapshot.

tok/s lives on the receipt (decode_tok_s or completion_tokens/duration_ms).
It is never read from an OMP assistant message usage block.
"""
from __future__ import annotations


def test_est_tokens_ceil_chars_div_4():
    from z0int.usage_surface import est_tokens

    assert est_tokens(0) == 0
    assert est_tokens(5) == 2
    assert est_tokens(4960) == 1240
    assert est_tokens(3520) == 880


def test_model_speed_prefers_receipt_decode_tok_s():
    from z0int.usage_surface import model_speed

    speed = model_speed(
        {
            "model_id": "laya_421m",
            "decode_tok_s": 117.147,
            "ttft_ms": 57.955,
            "latency_ms": 778.5,
            "completion_tokens": 8,
        }
    )
    assert speed["id"] == "laya_421m"
    assert speed["tok_s"] == 117.147
    assert speed["ttft_ms"] == 57.955
    assert speed["source"] == "receipt"


def test_model_speed_derives_from_duration_not_assistant_usage():
    from z0int.usage_surface import model_speed

    derived = model_speed(
        {
            "model_id": "openjev_06b",
            "completion_tokens": 20,
            "duration_ms": 200,
            "ttft_ms": 40,
        }
    )
    assert derived["tok_s"] == 100.0
    assert derived["source"] == "derived"

    ignored = model_speed(
        {
            "model_id": "frontier",
            "usage": {"input": 1200, "output": 80, "total": 1280},
        }
    )
    assert ignored["tok_s"] is None
    assert ignored["source"] == "missing"

def test_offload_savings_include_regressions_and_require_complete_measurement():
    from z0int.usage_surface import build_snapshot

    receipts = [
        {"actual_tokens_saved": 20, "measurement_state": "complete"},
        {"actual_tokens_saved": -60, "measurement_state": "complete"},
    ]
    for state in (None, "unknown", "partial", "unsupported", "failed", "missing", "invalid"):
        receipts.append({"actual_tokens_saved": 1000, "measurement_state": state})
    assert build_snapshot(receipts=receipts)["savings"]["z0int_offload"] == -40


