"""Priority source (.z0/priorities.yaml), vocabulary and credits: load + check.

``python -m z0int.contribute.priorities --check [--live]`` is the CI job
``priorities-check`` (spec §2.1). Offline it checks the schema, the closed
vocabulary, the newcomer laws and that every verifier/smoke path exists. With
``--live`` it also reads issue state and labels through ``gh`` (read-only) and
applies the conflict rule: the file wins for order and eligibility, labels win for
claim state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from . import schemas

FEEDBACK_TASK = "t-onboarding-feedback"
BLOCKING_LABELS = ("blocked", "parked", "hcl:backlog")
CLAIMABLE_LABEL = "claimable"
DEFAULT_REPO = "kvnloo/z0intelligence"
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SECRETISH = re.compile(r"\$|(?i:token|secret|password|api[_-]?key)")
_PATHISH_EXT = (".py", ".sh", ".mjs", ".ts", ".js", ".json", ".yaml", ".yml")


def _yaml():
    try:
        import yaml  # PyYAML: transitive dependency (transformers, huggingface-hub)
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("PyYAML is required: pip install -e '.[test]'") from exc
    return yaml


def load_yaml(path: Path) -> Any:
    text = Path(path).read_text(encoding="utf-8")
    doc = _yaml().safe_load(text)
    return _normalize(doc)


def _normalize(obj: Any) -> Any:
    """YAML dates -> ISO strings so documents are plain JSON values."""
    import datetime as _dt

    if isinstance(obj, dict):
        return {str(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    return obj


def priorities_path(root: Path) -> Path:
    return Path(root) / ".z0" / "priorities.yaml"


def load_priorities(root: Path) -> dict[str, Any]:
    return load_yaml(priorities_path(root))


def load_vocab(root: Path) -> dict[str, Any]:
    return load_yaml(Path(root) / ".z0" / "vocab.yaml")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def all_tags(vocab: dict[str, Any]) -> set[str]:
    return set(vocab.get("languages", [])) | set(vocab.get("domains", [])) | set(vocab.get("toolchains", []))


def claim_limit(claim: str) -> int | None:
    """1 for exclusive, n for parallel(n), None (no limit) for none."""
    if claim == "exclusive":
        return 1
    m = re.fullmatch(r"parallel\((\d+)\)", claim or "")
    if m:
        return int(m.group(1))
    return None


def verifier_paths(command: str | None) -> list[str]:
    """Repo-relative paths a verifier/smoke command refers to (must exist)."""
    if not command:
        return []
    out: list[str] = []
    for tok in shlex.split(command):
        if tok.startswith("-") or "://" in tok:
            continue
        tok = tok.split("::", 1)[0]
        if "/" in tok or tok.endswith(_PATHISH_EXT):
            out.append(tok)
    return out


def check_vocab(vocab: dict[str, Any]) -> list[str]:
    errors = [f"vocab: {e}" for e in schemas.validate(vocab, "vocab")]
    if errors:
        return errors
    langs, doms, tcs = set(vocab["languages"]), set(vocab["domains"]), set(vocab["toolchains"])
    for tc, ls in (vocab.get("toolchain_languages") or {}).items():
        if tc not in tcs:
            errors.append(f"vocab: toolchain_languages key {tc!r} is not a toolchain")
        for lang in ls:
            if lang not in langs:
                errors.append(f"vocab: toolchain_languages[{tc}] has non-language {lang!r}")
    for key, ds in (vocab.get("affinity") or {}).items():
        if key not in langs | tcs:
            errors.append(f"vocab: affinity key {key!r} is not a language or toolchain")
        for d in ds:
            if d not in doms:
                errors.append(f"vocab: affinity[{key}] has non-domain {d!r}")
    for tc in vocab.get("installable_toolchains") or []:
        if tc not in tcs:
            errors.append(f"vocab: installable toolchain {tc!r} is not a toolchain")
    return errors


def check_priorities(doc: dict[str, Any], vocab: dict[str, Any], *, repo_root: Path | None = None) -> list[str]:
    """Offline laws for the priority file. Empty list = valid."""
    errors = [f"schema: {e}" for e in schemas.validate(doc, "priorities")]
    if errors:
        return errors
    errors += check_vocab(vocab)
    tags = all_tags(vocab)
    toolchains = set(vocab.get("toolchains", []))
    weights = doc["alignment"]["weights"]
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        errors.append(f"alignment.weights sum to {sum(weights.values()):.4f}, must be 1.0")
    ids: set[str] = set()
    tier_ranks: set[tuple[str, int]] = set()
    for t in doc["tasks"]:
        tid = t["id"]
        if tid in ids:
            errors.append(f"{tid}: duplicate id")
        ids.add(tid)
        key = (t["tier"], t["rank"])
        if key in tier_ranks:
            errors.append(f"{tid}: rank {t['rank']} is not unique within {t['tier']}")
        tier_ranks.add(key)
        for s in t["skills"]:
            if s not in tags:
                errors.append(f"{tid}: skill {s!r} is not in .z0/vocab.yaml")
        for tc in t.get("toolchains", []):
            if tc not in toolchains:
                errors.append(f"{tid}: toolchain {tc!r} is not in .z0/vocab.yaml")
        if t["issue"] is None and t["claim"] != "none":
            errors.append(f"{tid}: a task without an issue cannot be claimed publicly (claim must be none)")
        if t["newcomer"] and t["verifier"] and _SECRETISH.search(t["verifier"]):
            errors.append(f"{tid}: newcomer verifier must run without env interpolation or owner secrets")
        if repo_root is not None:
            for field in ("verifier", "smoke"):
                for rel in verifier_paths(t.get(field)):
                    if not (Path(repo_root) / rel).exists():
                        errors.append(f"{tid}: {field} refers to missing path {rel}")
    for t in doc["tasks"]:
        gate = t.get("gate") or {}
        if "task" in gate:
            if gate["task"] == t["id"]:
                errors.append(f"{t['id']}: gates on itself")
            elif gate["task"] not in ids:
                errors.append(f"{t['id']}: gate refers to unknown task {gate['task']!r}")
    fb = [t for t in doc["tasks"] if t["id"] == FEEDBACK_TASK]
    if len(fb) != 1:
        errors.append(f"{FEEDBACK_TASK}: the always-on feedback task must exist exactly once")
    else:
        t = fb[0]
        if not (t["newcomer"] and t["status"] == "open" and t["claim"] == "none"
                and t["hardware"]["gpu_vram_gib"] == 0 and t["runtime"] == "repo_only"
                and t["network"] == "none" and t["difficulty"] == "S"):
            errors.append(f"{FEEDBACK_TASK}: must be newcomer, open, claim none, S, no GPU, repo_only, no network")
    return errors


def check_credits(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict) or doc.get("schema") != "z0.credits.v1":
        return ["credits: schema must be z0.credits.v1"]
    seen: set[str] = set()
    for i, c in enumerate(doc.get("contributors") or []):
        login = str(c.get("login", ""))
        if not _LOGIN_RE.match(login):
            errors.append(f"credits[{i}]: login {login!r} is not a GitHub handle")
        if login.lower() in seen:
            errors.append(f"credits[{i}]: duplicate login {login!r}")
        seen.add(login.lower())
        if not all(isinstance(x, str) and re.match(r"^[a-z0-9][a-z0-9-]*$", x) for x in c.get("tasks") or []):
            errors.append(f"credits[{i}]: tasks must be task ids")
        if not all(isinstance(x, int) and x > 0 for x in c.get("prs") or []):
            errors.append(f"credits[{i}]: prs must be PR numbers")
        if c.get("since") is not None and not _DATE_RE.match(str(c.get("since"))):
            errors.append(f"credits[{i}]: since must be YYYY-MM-DD")
        unknown = set(c) - {"login", "tasks", "prs", "since"}
        if unknown:
            errors.append(f"credits[{i}]: unexpected keys {sorted(unknown)}")
    return errors


def check_live(doc: dict[str, Any], issues: dict[int, dict[str, Any]], *, claimable_issues: Iterable[int]) -> list[str]:
    """L1 (labels) vs L2 (file) conflicts. ``issues``: number -> {state, labels[]}."""
    errors: list[str] = []
    listed = {t["issue"] for t in doc["tasks"] if t["issue"]}
    for t in doc["tasks"]:
        n = t["issue"]
        if not n or not (t["newcomer"] and t["status"] == "open"):
            continue
        live = issues.get(n)
        if live is None:
            continue
        if str(live.get("state", "")).upper() == "CLOSED":
            errors.append(f"{t['id']}: open newcomer task but issue #{n} is closed")
        bad = sorted(set(live.get("labels") or []) & set(BLOCKING_LABELS))
        if bad:
            errors.append(f"{t['id']}: open newcomer task but issue #{n} carries {bad}")
    for n in sorted(set(claimable_issues)):
        if n not in listed:
            errors.append(f"issue #{n} has the {CLAIMABLE_LABEL!r} label but no .z0/priorities.yaml entry")
    return errors


def _gh_json(args: list[str]) -> Any:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout or "null")


def fetch_live(repo: str = DEFAULT_REPO) -> tuple[dict[int, dict[str, Any]], list[int]]:
    """Read-only: issue states/labels and the issues labelled ``claimable``."""
    rows = _gh_json(["issue", "list", "-R", repo, "--state", "all", "--limit", "500",
                     "--json", "number,state,labels"])
    issues = {
        int(r["number"]): {"state": r["state"], "labels": [lb["name"] for lb in r.get("labels") or []]}
        for r in rows or []
    }
    claimable = [n for n, r in issues.items() if CLAIMABLE_LABEL in r["labels"] and r["state"].upper() == "OPEN"]
    return issues, claimable


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m z0int.contribute.priorities", description=__doc__)
    ap.add_argument("--check", action="store_true", help="validate .z0/ (default action)")
    ap.add_argument("--live", action="store_true", help="also check issue state + labels via gh (read-only)")
    ap.add_argument("--require-live", action="store_true", help="fail when the live check cannot run")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--root", default=".", help="checkout root (default: cwd)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        doc = load_priorities(root)
        vocab = load_vocab(root)
        errors += check_priorities(doc, vocab, repo_root=root)
        errors += check_credits(load_yaml(root / ".z0" / "credits.yaml"))
    except Exception as exc:  # noqa: BLE001 - report, do not crash CI with a traceback
        errors.append(f"load: {type(exc).__name__}: {exc}")
        doc = None
    if args.live and doc is not None and not errors:
        if shutil.which("gh") is None:
            (errors if args.require_live else warnings).append("live: gh not available; label state unknown")
        else:
            try:
                issues, claimable = fetch_live(args.repo)
                errors += check_live(doc, issues, claimable_issues=claimable)
            except Exception as exc:  # noqa: BLE001
                (errors if args.require_live else warnings).append(f"live: {type(exc).__name__}: {exc}")
    out = {"schema": "z0.priorities-check.v1", "ok": not errors, "errors": errors, "warnings": warnings}
    if args.as_json:
        print(json.dumps(out, indent=2))
    else:
        for w in warnings:
            print(f"warning: {w}")
        for e in errors:
            print(f"error: {e}")
        print("priorities-check: OK" if not errors else f"priorities-check: FAIL ({len(errors)} error(s))")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
