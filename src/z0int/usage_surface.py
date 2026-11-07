"""Tern /usage snapshot: context savings + tok/s for every model in the network.

tok/s is a receipt field (``decode_tok_s``, else ``completion_tokens / duration_ms``).
It is never read from an OMP assistant-message ``usage`` block. Assistant messages
keep token counts. TTFT is ``ttft_ms`` on the same receipt.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "z0int.usage_surface.v1"
TOKEN_METHOD = "ceil_chars_div_4_v1"


def est_tokens(chars: int) -> int:
    return int(math.ceil(max(0, int(chars)) / 4))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def model_speed(row: dict[str, Any]) -> dict[str, Any]:
    """Speed for one model. Ignores assistant-message ``usage`` counts."""
    model_id = str(row.get("model_id") or row.get("id") or "unknown")
    ttft_ms = _finite(row.get("ttft_ms"))
    decode = _finite(row.get("decode_tok_s"))
    if decode is not None:
        return {
            "id": model_id,
            "tok_s": decode,
            "ttft_ms": ttft_ms,
            "source": "receipt",
        }
    completion = row.get("completion_tokens")
    duration_ms = _finite(row.get("duration_ms"))
    if duration_ms is None:
        duration_ms = _finite(row.get("latency_ms"))
    if isinstance(completion, int) and not isinstance(completion, bool) and duration_ms and duration_ms > 0:
        return {
            "id": model_id,
            "tok_s": float(completion) / (duration_ms / 1000.0),
            "ttft_ms": ttft_ms,
            "source": "derived",
        }
    return {"id": model_id, "tok_s": None, "ttft_ms": ttft_ms, "source": "missing"}


def _offload_saved(receipts: list[dict[str, Any]]) -> int:
    total = 0
    for row in receipts:
        if row.get("measurement_state") != "complete":
            continue
        value = row.get("actual_tokens_saved")
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def build_snapshot(
    *,
    sol_pi_chars_withheld: int = 0,
    rlm_chars_withheld: int = 0,
    receipts: list[dict[str, Any]] | None = None,
    models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "token_method": TOKEN_METHOD,
        "savings": {
            "sol_pi": est_tokens(sol_pi_chars_withheld),
            "rlm": est_tokens(rlm_chars_withheld),
            "z0int_offload": _offload_saved(receipts or []),
        },
        "models": [model_speed(row) for row in (models or [])],
    }


def _tok_s_line(model: dict[str, Any]) -> str:
    tok_s = model.get("tok_s")
    if not isinstance(tok_s, (int, float)) or not math.isfinite(float(tok_s)):
        return f"{model.get('id', 'unknown')} — tok/s"
    return f"{model.get('id', 'unknown')} {int(round(float(tok_s)))} tok/s"


def format_usage_text(snapshot: dict[str, Any]) -> str:
    savings = snapshot.get("savings") or {}
    lines = [
        "/usage",
        f"SoL-Pi {int(savings.get('sol_pi') or 0)} tok saved",
        f"RLM {int(savings.get('rlm') or 0)} tok saved",
        f"z0int {int(savings.get('z0int_offload') or 0)} tok saved",
    ]
    for model in snapshot.get("models") or []:
        lines.append(_tok_s_line(model))
    return "\n".join(lines)


def snapshot_path(root: Path | None = None) -> Path:
    from . import paths

    return paths.ensure_layout(root)["runtime"] / "usage-surface.json"


def write_snapshot(snapshot: dict[str, Any], *, root: Path | None = None) -> Path:
    path = snapshot_path(root)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    del argv
    snap = build_snapshot()
    write_snapshot(snap)
    print(format_usage_text(snap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
