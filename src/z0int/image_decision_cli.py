"""CLI for reproducible z0int image decisions and JSONL benchmark runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from z0int.backends.image_decision import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    ImageDecisionOption,
    QwenImageDecisionBackend,
)


def _load_request(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("request JSON must be an object")
    return raw


def _parse_options(raw: Any) -> tuple[ImageDecisionOption, ...]:
    if not isinstance(raw, list):
        raise ValueError("options must be an array")
    options: list[ImageDecisionOption] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each option must be an object")
        options.append(
            ImageDecisionOption(
                id=str(item.get("id") or ""),
                description=str(item.get("description") or ""),
            )
        )
    return tuple(options)


def _resolve_image(request_path: Path, image: str) -> Path:
    path = Path(image).expanduser()
    if path.is_absolute():
        return path
    return (request_path.parent / path).resolve()


def _backend(args: argparse.Namespace) -> QwenImageDecisionBackend:
    return QwenImageDecisionBackend(
        model_id=args.model,
        revision=args.revision,
        device=args.device,
        dtype=args.dtype,
    )


def _run_one(
    backend: QwenImageDecisionBackend,
    raw: dict[str, Any],
    source_path: Path,
) -> dict[str, Any]:
    image_raw = str(raw.get("image") or "")
    question = str(raw.get("question") or "")
    if not image_raw:
        raise ValueError("request requires image")
    result = backend.decide(
        image=_resolve_image(source_path, image_raw),
        question=question,
        options=_parse_options(raw.get("options")),
    )
    out = result.to_dict()
    if raw.get("id") is not None:
        out["id"] = str(raw["id"])
    return out


def cmd_run(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    result = _run_one(_backend(args), _load_request(source), source)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    backend = _backend(args)
    rows: list[str] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"line {line_number}: row must be an object")
        try:
            result = _run_one(backend, raw, source)
        except Exception as exc:
            result = {
                "schema": "z0int.image_decision_error.v1",
                "id": raw.get("id"),
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(json.dumps(result, sort_keys=True))

    payload = "\n".join(rows) + ("\n" if rows else "")
    if args.output:
        destination = Path(args.output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL_ID)
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--device", default=None, help="default: cuda:0 when available")
    parser.add_argument(
        "--dtype",
        default="bfloat16",
        choices=("bfloat16", "bf16", "float16", "fp16", "float32", "fp32"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="z0int-image-decide",
        description="Pinned local image + dynamic-options decision runner",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one request JSON")
    run.add_argument("--input", required=True)
    _common(run)
    run.set_defaults(func=cmd_run)

    batch = sub.add_parser("batch", help="run JSONL requests with one resident model")
    batch.add_argument("--input", required=True)
    batch.add_argument("--output", default=None)
    _common(batch)
    batch.set_defaults(func=cmd_batch)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
