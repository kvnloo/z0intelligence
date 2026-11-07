"""Write local model labels to a JSONL file without acting on them."""

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import string
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


TASKS = {
    "effort": (
        "How much reasoning does the assistant need for its next step? "
        "low = mechanical (run a command, read a file, small edit, status reply); "
        "medium = ordinary coding or writing; high = design, debugging an unknown "
        "cause, multi-step planning. Answer one word.",
        {"low", "medium", "high"}, "high", 2000,
    ),
    "route": (
        "Which model tier can finish this whole task at the same quality? "
        "haiku = mechanical, checklist, status polling; sonnet = ordinary "
        "implementation; opus = hard reasoning, ambiguity, high stakes. Answer one word.",
        {"haiku", "sonnet", "opus"}, "opus", 4000,
    ),
}


class Labeler:
    def __init__(self, base_url, model, max_calls, timeout=30):
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.model = model
        self.max_calls = max_calls
        self.timeout = timeout
        self.calls = 0
        self.failures = 0
        self.latencies = []

    def label(self, task, record):
        prompt, allowed, fallback, limit = TASKS[task]
        row = {"id": record["id"], "task": task, "label": None,
               "reason": "cap", "latency_ms": 0, "model": self.model}
        if self.calls >= self.max_calls:
            return row
        text = record["text"][:limit]
        if task == "effort":
            text += (f"\nn_tool_calls_so_far: {record['n_tool_calls_so_far']}"
                     f"\ncontext_chars: {record['context_chars']}")
        body = json.dumps({"model": self.model, "temperature": 0, "max_tokens": 8,
                           "messages": [{"role": "system", "content": prompt},
                                        {"role": "user", "content": text}]}).encode()
        request = Request(self.url, data=body, headers={"Content-Type": "application/json"})
        self.calls += 1
        start = time.monotonic()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                reply = json.load(response)["choices"][0]["message"]["content"]
            words = reply.split()
            word = words[0].strip(string.punctuation).lower() if words else ""
            row.update(label=word if word in allowed else fallback,
                       reason="ok" if word in allowed else "unparsed")
        except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            self.failures += 1
            row.update(label=fallback, reason="error")
        finally:
            row["latency_ms"] = (time.monotonic() - start) * 1000
            self.latencies.append(row["latency_ms"])
        return row


def summarize(out_rows, weights):
    result = {}
    for row in out_rows:
        group = result.setdefault(row["label"], {"count": 0, "weight": 0, "share": 0})
        group["count"] += 1
        group["weight"] += weights.get(row["id"], 0)
    total = sum(group["weight"] for group in result.values())
    for group in result.values():
        group["share"] = 100 * group["weight"] / total if total else 0
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("--in", dest="input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    args = parser.parse_args()
    output = Path(args.out)
    done = set()
    if output.exists():
        with output.open(encoding="utf-8") as stream:
            done = {json.loads(line)["id"] for line in stream if line.strip()}
    labeler = Labeler(args.base_url, args.model, args.max_calls)
    counts = Counter()
    cap_hits = 0
    with open(args.input, encoding="utf-8") as source, output.open("a", encoding="utf-8") as dest:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["id"] in done:
                continue
            row = labeler.label(args.task, record)
            dest.write(json.dumps(row) + "\n")
            dest.flush()
            done.add(record["id"])
            counts[str(row["label"]) if row["label"] is not None else "null"] += 1
            cap_hits += row["reason"] == "cap"
    print(json.dumps({"labels": dict(counts), "calls": labeler.calls,
                      "failures": labeler.failures, "cap_hits": cap_hits,
                      "p50_latency_ms": statistics.median(labeler.latencies) if labeler.latencies else 0}))


if __name__ == "__main__":
    main()
