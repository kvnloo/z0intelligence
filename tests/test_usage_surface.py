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


def test_snapshot_text_names_three_savings_and_every_model():
    from z0int.usage_surface import build_snapshot, format_usage_text

    snap = build_snapshot(
        sol_pi_chars_withheld=4960,
        rlm_chars_withheld=3520,
        receipts=[{"actual_tokens_saved": 1860, "measurement_state": "complete"}],
        models=[
            {"model_id": "laya_421m", "decode_tok_s": 117.147, "ttft_ms": 57.955},
            {"model_id": "openjev_06b", "decode_tok_s": 87.93, "ttft_ms": 49.951},
            {
                "model_id": "frontier",
                "completion_tokens": 42,
                "duration_ms": 1000,
                "ttft_ms": 210,
            },
        ],
    )
    text = format_usage_text(snap)
    assert text.splitlines()[0] == "/usage"
    assert "SoL-Pi 1240 tok saved" in text
    assert "RLM 880 tok saved" in text
    assert "z0int 1860 tok saved" in text
    assert "laya_421m 117 tok/s" in text
    assert "openjev_06b 88 tok/s" in text
    assert "frontier 42 tok/s" in text
    assert "assistant" not in text.lower() or "frontier" in text
