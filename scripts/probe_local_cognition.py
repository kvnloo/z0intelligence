#!/usr/bin/env python3
"""Measure the local cognition portfolio on this machine, for real.

Starts one ``llama-server`` per model (never co-resident on a 12GB card), probes
it, records a ``z0int.serving_receipt.v1`` row, and shuts it down. Optionally
folds the measurements back into ``manifests/local_cognition.v1.json``.

    python scripts/probe_local_cognition.py --json
    python scripts/probe_local_cognition.py --model qwen3.5_9b --context 8192
    python scripts/probe_local_cognition.py --all --write-manifest

Nothing here is estimated: if ``nvidia-smi`` is unavailable the VRAM fields are
``null`` rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from z0int.cognition.manifest import default_manifest_path, load_local_cognition  # noqa: E402
from z0int.cognition.probe import (  # noqa: E402
    ServingReceipt,
    probe_endpoint,
    receipts_to_measurements,
    write_receipts,
)
from z0int.cognition.registry import ServingEndpoint  # noqa: E402
from z0int.cognition.serving import (  # noqa: E402
    DEFAULT_GGUF_DIR,
    DEFAULT_LLAMA_SERVER,
    GGUF_LAYOUT,
    LlamaServer,
    free_port,
    llama_build,
    quant_from_name,
)



def probe_model(
    model_id: str,
    *,
    binary: str,
    gguf_dir: Path,
    context: int,
    repeats: int,
) -> ServingReceipt:
    rel = GGUF_LAYOUT[model_id]
    gguf = gguf_dir / rel
    if not gguf.is_file():
        raise FileNotFoundError(f"missing GGUF for {model_id}: {gguf}")
    port = free_port()
    with LlamaServer(binary=binary, gguf=gguf, context=context, port=port) as server:
        endpoint = ServingEndpoint(
            model_id=model_id,
            base_url=server.base_url,
            served_model=model_id,
            runtime=f"llama.cpp-{llama_build(binary)}",
            quantization=quant_from_name(gguf.name),
            backend_id=model_id,
        )
        return probe_endpoint(endpoint, context=context, repeats=repeats)


def _table(receipts: list[ServingReceipt]) -> str:
    head = (
        "| model | runtime | quant | ctx | VRAM idle MiB | VRAM peak MiB | cold load ms "
        "| TTFT ms | decode tok/s | short decision ms | long decision ms |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|"
    rows = [head, sep]
    for r in receipts:
        def fmt(v: float | None, nd: int = 1) -> str:
            return "-" if v is None else f"{v:.{nd}f}"

        rows.append(
            f"| {r.model_id} | {r.runtime} | {r.quantization or '-'} | {r.context} "
            f"| {fmt(r.vram_idle_mib, 0)} | {fmt(r.vram_peak_mib, 0)} "
            f"| {fmt(r.cold_start_ms, 0)} | {fmt(r.warm_ttft_ms)} "
            f"| {fmt(r.decode_tok_s)} | {fmt(r.short_decision_ms, 0)} "
            f"| {fmt(r.long_decision_ms, 0)} |"
        )
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", action="append", default=[],
                    help="model_id from manifests/local_cognition.v1.json (repeatable)")
    ap.add_argument("--all", action="store_true", help="probe every model with a known GGUF")
    ap.add_argument("--served", action="store_true",
                    help="probe through the PRODUCTION serving map (~/.z0int/config/serving.json) "
                         "instead of spawning llama-server, so labels match the deployed stack")
    ap.add_argument("--binary", default=os.environ.get("LLAMA_SERVER", DEFAULT_LLAMA_SERVER))
    ap.add_argument("--gguf-dir", default=os.environ.get("Z0INT_GGUF_DIR", DEFAULT_GGUF_DIR))
    ap.add_argument("--context", type=int, default=4096)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=None, help="receipt directory")
    ap.add_argument("--json", action="store_true", help="print receipts as JSON")
    ap.add_argument("--write-manifest", action="store_true",
                    help="fold measurements into manifests/local_cognition.v1.json")
    args = ap.parse_args()

    manifest = load_local_cognition()
    if args.served:
        from z0int.cognition.registry import load_serving

        endpoints = load_serving()
        served = [m for m in endpoints if m in manifest.models]
        wanted = args.model or served
        if not args.model and not args.all:
            wanted = served
    elif args.all:
        wanted = [m for m in GGUF_LAYOUT if m in manifest.models]
    elif args.model:
        wanted = args.model
    else:
        wanted = ["functiongemma_270m"]
    unknown = [m for m in wanted if m not in manifest.models]
    if unknown:
        print(f"unknown model ids: {unknown}", file=sys.stderr)
        return 2

    binary = args.binary
    if not args.served and not shutil.which(binary) and not Path(binary).is_file():
        print(
            f"llama-server not found at {binary}. Build it with:\n"
            "  cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER=/opt/cuda/bin/nvcc "
            "-DCMAKE_BUILD_TYPE=Release && cmake --build build -j --target llama-server",
            file=sys.stderr,
        )
        return 3

    receipts: list[ServingReceipt] = []
    for model_id in wanted:
        print(f"# probing {model_id} ...", file=sys.stderr)
        try:
            if args.served:
                from z0int.cognition.registry import ServingEndpoint, load_serving

                endpoint = load_serving()[model_id]
                receipts.append(probe_endpoint(endpoint, context=args.context, repeats=args.repeats))
                continue
            receipts.append(
                probe_model(
                    model_id,
                    binary=binary,
                    gguf_dir=Path(args.gguf_dir),
                    context=args.context,
                    repeats=args.repeats,
                )
            )
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"# FAILED {model_id}: {type(exc).__name__}: {exc}", file=sys.stderr)

    if not receipts:
        print("no receipts produced", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else None
    target = write_receipts(receipts, out)
    print(f"# receipts -> {target}", file=sys.stderr)

    if args.write_manifest:
        raw = json.loads(default_manifest_path().read_text(encoding="utf-8"))
        for receipt in receipts:
            row = raw["models"].setdefault(receipt.model_id, {})
            row["measurements"] = [receipts_to_measurements(receipt)]
            row["tested_context"] = receipt.context
            row["tested_quantization"] = receipt.quantization
            row["promotion_state"] = (
                row.get("promotion_state")
                if row.get("promotion_state") not in (None, "unavailable")
                else "candidate"
            )
        default_manifest_path().write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"# manifest updated -> {default_manifest_path()}", file=sys.stderr)

    if args.json:
        print(json.dumps([r.to_dict() for r in receipts], indent=2))
    else:
        print(_table(receipts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
