"""Frozen synthetic JSON extraction falsification; no models, network or private data."""

from __future__ import annotations
import argparse
import hashlib
import json
import math
import time
from z0int.cognition.adapters.dialects import extract_json_object

VALUES = (
    "plain",
    "{",
    "}",
    "{{",
    "}}",
    "{left",
    "right}",
    '"{',
    '"}',
    "line\n{",
    "backslash\\{",
    "{}",
    "{nested}",
    '\\"{',
    "[}",
    "]{",
    "λ{",
    "emoji🙂}",
    "literal\\n{",
    "a}b{c}",
    "\t{",
    "'{'",
    "\r}",
    "x{y{z",
)
WRAPPERS = (
    ("", ""),
    ("I choose ", " now."),
    ("<tool_call>", "</tool_call>"),
    ("\x60\x60\x60json\n", "\n\x60\x60\x60"),
    ("Answer:\n", "\nDone"),
    ("  Explanation: ", "\n"),
)


def corpus():
    rows = []
    for index, value in enumerate(VALUES):
        expected = {
            "action_id": "inspect",
            "arguments": {"pattern": value, "nested": [{"value": value}]},
        }
        encoded = json.dumps(expected, ensure_ascii=False)
        for wrapper, (before, after) in enumerate(WRAPPERS):
            rows.append(
                {
                    "id": f"{index}-{wrapper}",
                    "split": "development" if index < 12 else "holdout",
                    "text": before + encoded + after,
                    "expected": expected,
                }
            )
    return rows


def strict(text):
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def decoder_first(text):
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def wilson(k, n):
    z = 1.96
    denominator = 1 + z * z / n
    center = (k / n + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(k / n * (1 - k / n) / n + z * z / (4 * n * n)) / denominator
    return [round(center - radius, 6), round(center + radius, 6)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=10)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    rows = corpus()
    digest = hashlib.sha256(
        json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    output = {
        "schema": "z0int.synthetic_json_extraction.v1",
        "corpus_sha256": digest,
        "rows": len(rows),
        "scope": "synthetic parser correctness, not model accuracy or promotion",
        "arms": {},
    }
    for name, parse in (
        ("strict_json", strict),
        ("first_object_decoder", decoder_first),
        ("production", extract_json_object),
    ):
        splits = {}
        for split in ("development", "holdout"):
            subset = [r for r in rows if r["split"] == split]
            passed = sum(parse(r["text"]) == r["expected"] for r in subset)
            splits[split] = {
                "n": len(subset),
                "correct": passed,
                "wilson95_descriptive": wilson(passed, len(subset)),
            }
        started = time.perf_counter()
        for _ in range(args.repeat):
            for row in rows:
                parse(row["text"])
        output["arms"][name] = {
            "splits": splits,
            "seconds": time.perf_counter() - started,
            "calls": len(rows) * args.repeat,
        }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
