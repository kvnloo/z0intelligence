"""Atomic live projections over an append-only human decision-batch ledger."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

DEFAULT_LIVE_DIR = Path("/mnt/zer0models/workspace/zer0/data/operations/fsm/live")


def atomic_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def publish(report: dict, directory: Path = DEFAULT_LIVE_DIR) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = directory / "needs-kevin.jsonl"
        records = {}
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                event = json.loads(line)
                records[event["id"]] = event
        active = {}
        for item in report["needs_kevin"]["items"]:
            key = "worker-" + hashlib.sha256(item["worker"].encode()).hexdigest()[:20]
            active[key] = {"id": key, "worker": item["worker"], "goal": item["goal"], "why": item["why"],
                "recommended_action": item["recommended"], "options": ["resume", "abandon", "integrated"],
                "evidence_path": item["log"], "phase": item["phase"]}
        with ledger.open("a") as stream:
            for key, item in active.items():
                previous = records.get(key)
                if not previous or previous["event"] == "resolved" or previous.get("item") != item:
                    stream.write(json.dumps({"id": key, "event": "open", "at": report["generated_at"], "item": item}) + "\n")
            for key, previous in records.items():
                if key not in active and previous["event"] != "resolved":
                    stream.write(json.dumps({"id": key, "event": "resolved", "at": report["generated_at"]}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        batch = {"schema": "z0.fsm.needs-kevin/v1", "generated_at": report["generated_at"],
                 "delivery": "pull-only; batch review during owner's deep-work blocks; never a dispatch blocker",
                 "size": len(active), "items": list(active.values()), "ledger_path": str(ledger)}
        # Both projections carry the same generation; consumers can reject a mixed read.
        atomic_json(directory / "state.json", report)
        atomic_json(directory / "needs-kevin.json", batch)
        return batch
