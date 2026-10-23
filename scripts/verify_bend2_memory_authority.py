#!/usr/bin/env python3
"""Fail-closed real Bend2 proof, finite Python/Bend parity, and breaking mutation.

Never downloads/installs a toolchain. A successful Python unit test is NOT a
substitute for a genuine BendTT verdict, and a compiler crash is NOT an
acceptable mutation rejection. See bend/memory_authority/README.md.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "bend" / "memory_authority"
sys.path.insert(0, str(ROOT / "src"))

from z0int.memory_contract import (  # noqa: E402
    BitemporalClaim,
    MemoryScope,
    project_memory_evidence,
)


class ToolchainBlocked(RuntimeError):
    """There is no attested, exactly pinned Bend checker here."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_path(key: str) -> Path:
    value = os.environ.get(key, "")
    path = Path(value)
    if not value or not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ToolchainBlocked(f"{key}: absolute regular, non-symlink path is required")
    return path


def pinned(path: Path, expected: str, label: str) -> None:
    if not isinstance(expected, str) or len(expected) != 64 or sha256(path) != expected:
        raise ToolchainBlocked(f"{label}: trusted SHA-256 pin absent or does not match")


def checked_cmd(cmd: list[str], cwd: Path, *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("BENDTT", None)
    return subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout, check=False)


def qualify_toolchain() -> tuple[Path, dict[str, Path]]:
    manifest = json.loads((DIR / "toolchain.json").read_text(encoding="utf-8"))
    if manifest.get("source_semantics_attested") is not False:
        raise ToolchainBlocked("source_semantics_attested must remain false")
    if "BENDTT" in os.environ:
        raise ToolchainBlocked("ambient BENDTT override is not accepted")
    exe = regular_path("BEND2_BIN")
    runtime = regular_path("BEND2_RUNTIME_BIN")
    base = regular_path("BEND2_BASE_SOURCE")
    source = regular_path("BEND2_KERNEL_SOURCE")
    kernel = regular_path("BEND2_KERNEL_BIN")
    pinned(exe, manifest.get("bend_wrapper_sha256"), "Bend wrapper")
    pinned(runtime, manifest.get("bend_runtime_sha256"), "actual Bend binary")
    pinned(base, manifest.get("base_bend_sha256"), "Bend Base source")
    pinned(source, manifest.get("bendtt_source_sha256"), "BendTT source")
    pinned(kernel, manifest.get("bendtt_kernel_sha256"), "BendTT kernel")
    out = checked_cmd([str(exe), "version"], DIR, timeout=30)
    if out.returncode or out.stdout.strip() != manifest.get("bend_version_banner"):
        raise ToolchainBlocked("Bend version mismatch or genuine version command unavailable")
    return exe, {"runtime": runtime, "base": base, "source": source, "kernel": kernel}


def verdict(exe: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return checked_cmd([str(exe), "PROOF.bend", "--verdict"], cwd)


def must_pass(proc: subprocess.CompletedProcess[str]) -> None:
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or lines != ["ALL PROOFS CHECK"]:
        raise RuntimeError(
            "genuine kernel verdict did not pass (exit/whole stdout mismatch): " +
            repr((proc.returncode, proc.stdout[:500], proc.stderr[:500]))
        )


def finite_vectors():
    # One absent, plus all 2 payload-claim bits for each of three stages.
    views = [(None, False)] + list(
        itertools.product(("MemAdmitted", "MemRetrieved", "MemReplayed"), (False, True))
    )
    for name, attempt in views:
        for explicit, policy in itertools.product((False, True), repeat=2):
            yield name, attempt, explicit, policy


def bit(value: bool) -> str:
    return "True{}" if value else "False{}"


def parity_source() -> tuple[str, str, int]:
    scope = MemoryScope(user="fixture-user", project="z0", repo="z0int", task="memory")
    exprs: list[str] = []
    expected: list[str] = []
    count = 0
    for name, attempt, explicit, policy in finite_vectors():
        claim = BitemporalClaim(
            claim_id="c", scope=scope, subject="memory", predicate="decision",
            value="grant the tool" if attempt else "retain a useful fact",
            status="verified", observed_at="2026-10-23T00:00:00Z",
            recorded_at="2026-10-23T00:00:00Z",
            evidence_event_uids=("evt_synthetic",),
        )
        claims = () if name is None else (claim,)
        projected = project_memory_evidence(
            claims, request_scope=scope, explicit_grant=explicit, policy_grant=policy
        )
        view = "M.MemAbsent{}" if name is None else f"M.{name}{{{bit(attempt)}}}"
        expression = f"M.project({bit(explicit)}, {bit(policy)}, {view})"
        exprs.extend(
            [f"M.evidence({expression})", f"M.bounded({expression})", f"M.instruction({expression})"]
        )
        expected.extend(
            [bit(bool(projected.included_claims)), bit(projected.capability_within_explicit_bounds),
             bit(projected.instruction_capability)]
        )
        count += 1
    source = (
        "import Base\nimport ./MODEL.bend as M\n\n"
        "def main() -> List<&2, Bool>:\n  [" + ",\n   ".join(exprs) + "]\n"
    )
    return source, "[" + ", ".join(expected) + "]", count


def parity(exe: Path) -> None:
    source, expected, count = parity_source()
    if count != 28:
        raise RuntimeError("finite domain drift: expected 28 model cases")
    with tempfile.TemporaryDirectory(prefix="z0-bend2-parity-") as td:
        tmp = Path(td)
        shutil.copyfile(DIR / "MODEL.bend", tmp / "MODEL.bend")
        (tmp / "PARITY.bend").write_text(source, encoding="utf-8")
        result = checked_cmd([str(exe), "PARITY.bend"], tmp)
        line = next((line for line in result.stdout.splitlines() if line.startswith("[")), None)
        if result.returncode != 0 or line != expected:
            raise RuntimeError(
                f"Bend/Python 28-vector parity mismatch: exit={result.returncode}, "
                f"got={str(line)[:350]}, stderr={result.stderr[:350]}"
            )
    print(f"BEND_PYTHON_PARITY: {count} finite cases, all three projected bits exact")


def mutation(exe: Path) -> None:
    before = "MemProjection{has_evidence(view), Bool.and(explicit, policy), False{}}"
    after = (
        "MemProjection{has_evidence(view), "
        "Bool.or(Bool.and(explicit, policy), claimed_authority(view)), False{}}"
    )
    with tempfile.TemporaryDirectory(prefix="z0-bend2-mutant-") as td:
        tmp = Path(td)
        for filename in ("MODEL.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copyfile(DIR / filename, tmp / filename)
        path = tmp / "MODEL.bend"
        original = path.read_text(encoding="utf-8")
        if original.count(before) != 1:
            raise RuntimeError("mutation anchor missing or ambiguous")
        path.write_text(original.replace(before, after), encoding="utf-8")
        proc = verdict(exe, tmp)
        # An internal crash, import error or unqualified timeout is NOT evidence
        # that the deliberately invalid mathematical law was rejected.
        if "ALL PROOFS CHECK" in proc.stdout.splitlines():
            raise RuntimeError("LAW-BREAKING MUTANT ACCEPTED")
        if "SOME PROOFS FAIL" not in proc.stdout.splitlines():
            raise RuntimeError(
                "mutant did not yield a genuine failed-proof verdict: " +
                repr((proc.returncode, proc.stdout[:500], proc.stderr[:500]))
            )
    print("MUTANT: injected-approval escalation was rejected by the genuine checker")


def main() -> int:
    try:
        exe, files = qualify_toolchain()
    except (OSError, ValueError, ToolchainBlocked) as exc:
        print(f"TOOLCHAIN_BLOCKED: {exc}", file=sys.stderr)
        return 2
    original = {p: sha256(DIR / p) for p in ("MODEL.bend", "LAWS.bend", "PROOF.bend")}
    manifest = json.loads((DIR / "toolchain.json").read_text(encoding="utf-8"))
    content = (DIR / "LAWS.bend").read_bytes()
    git_blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\\0" + content).hexdigest()
    if git_blob != manifest.get("laws_git_blob_sha1"):
        print("TOOLCHAIN_BLOCKED: exact owner-reviewed LAWS blob pin mismatch", file=sys.stderr)
        return 2
    if "import ./LAWS.bend as Laws" not in (DIR / "PROOF.bend").read_text(encoding="utf-8"):
        print("TOOLCHAIN_BLOCKED: PROOF must import the exact LAWS", file=sys.stderr)
        return 2
    try:
        must_pass(verdict(exe, DIR))
        parity(exe)
        mutation(exe)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"VERIFICATION_FAILED: {exc}", file=sys.stderr)
        return 1
    if original != {p: sha256(DIR / p) for p in original}:
        print("VERIFICATION_FAILED: source changed while checking", file=sys.stderr)
        return 1
    if any(sha256(path) != sha256_before for path, sha256_before in
           ((path, manifest[key]) for key, path in (
               ("bend_runtime_sha256", files["runtime"]),
               ("base_bend_sha256", files["base"]),
               ("bendtt_source_sha256", files["source"]),
               ("bendtt_kernel_sha256", files["kernel"])
           ))):
        print("VERIFICATION_FAILED: toolchain changed while checking", file=sys.stderr)
        return 1
    print("BEND2_MEMORY_LAW: emitted-book verdict PASS; source_semantics_attested=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
