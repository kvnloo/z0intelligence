"""Evidence-receipt gate: .github/scripts/check-receipt.py.

Runs the script the way .github/workflows/receipt.yml does (PR body written to a
file, `--file <body> --head <sha>`) against synthetic bodies only.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "check-receipt.py"
SPEC = importlib.util.spec_from_file_location("check_receipt", SCRIPT)
R = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(R)

HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
OTHER = "f" * 40


def body(head: str = HEAD, base: str = BASE, issue: str = "42", red: str = "pytest -q (1 failed)") -> str:
    return (
        "## Summary\n\nSynthetic PR.\n\n## Evidence\n\n```yaml\n"
        f"issue: {issue}\n"
        f"base_revision: {base}\n"
        f"head_revision: {head}\n"
        "tests:\n"
        f"  red: {red}\n"
        "  green: pytest -q (1 passed)\n"
        "```\n"
    )


def run_gate(tmp_path: Path, text: str, head: str = HEAD) -> subprocess.CompletedProcess[str]:
    pr_body = tmp_path / "pr-body.md"
    pr_body.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(pr_body), "--head", head],
        capture_output=True,
        text=True,
        check=False,
    )


ACCEPTED_HEADS = {
    "unquoted full": HEAD,
    "unquoted short": HEAD[:7],
    "unquoted uppercase": HEAD.upper(),
    "unquoted trailing whitespace": HEAD + "   ",
    "double-quoted full": f'"{HEAD}"',
    "single-quoted full": f"'{HEAD}'",
    "double-quoted short": f'"{HEAD[:7]}"',
    "single-quoted short": f"'{HEAD[:12]}'",
    "unquoted trailing comment": f"{HEAD}  # PR head",
    "double-quoted trailing comment": f'"{HEAD}"  # PR head',
    "single-quoted trailing comment": f"'{HEAD}' # PR head",
}


@pytest.mark.parametrize("head", ACCEPTED_HEADS.values(), ids=ACCEPTED_HEADS.keys())
def test_gate_accepts_valid_yaml_spellings_of_the_exact_head(tmp_path: Path, head: str) -> None:
    proc = run_gate(tmp_path, body(head=head))
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout.strip() == "ok: evidence receipt is complete"


@pytest.mark.parametrize("base", [f'"{BASE}"', f"'{BASE}'", f"{BASE} # merge base"])
def test_gate_accepts_quoted_or_commented_base_revision(tmp_path: Path, base: str) -> None:
    proc = run_gate(tmp_path, body(base=base))
    assert proc.returncode == 0, proc.stdout


@pytest.mark.parametrize("head", [OTHER, f'"{OTHER}"', f"'{OTHER}'", f'"{OTHER[:7]}"'])
def test_gate_rejects_a_wrong_sha_quoted_or_not(tmp_path: Path, head: str) -> None:
    proc = run_gate(tmp_path, body(head=head))
    assert proc.returncode == 1
    assert "does not match PR head" in proc.stdout
    assert "tests from another SHA are not evidence" in proc.stdout


REJECTED_NON_SHA = {
    "non-hex": "not-a-sha-zzzz",
    "non-hex quoted": '"zzzzzzzzzzzz"',
    "too short": '"12345"',
    "too long": f'"{HEAD}0"',
    "mismatched quotes": f"\"{HEAD}'",
    "unterminated quote": f'"{HEAD}',
    "backticks": f"`{HEAD}`",
    "sha then junk": f"{HEAD} junk",
    "quoted sha then junk": f'"{HEAD}" junk',
    "two shas": f"{HEAD} {HEAD}",
    "sha glued to hash": f"{HEAD}#x",
}


@pytest.mark.parametrize("head", REJECTED_NON_SHA.values(), ids=REJECTED_NON_SHA.keys())
def test_gate_rejects_values_that_are_not_a_git_sha(tmp_path: Path, head: str) -> None:
    proc = run_gate(tmp_path, body(head=head))
    assert proc.returncode == 1
    assert "head_revision is not a git SHA" in proc.stdout


@pytest.mark.parametrize("head", ['""', "''", '"" # todo'])
def test_gate_rejects_an_empty_quoted_head(tmp_path: Path, head: str) -> None:
    proc = run_gate(tmp_path, body(head=head))
    assert proc.returncode == 1
    assert "head_revision is empty" in proc.stdout
    assert "cannot bind to exact head" in proc.stdout


def test_gate_rejects_empty_quoted_test_evidence(tmp_path: Path) -> None:
    proc = run_gate(tmp_path, body(red='""'))
    assert proc.returncode == 1
    assert "tests.red is empty" in proc.stdout


@pytest.mark.parametrize(
    "text",
    ["", "Just a description, no receipt.\n", "```yaml\nnotes: nothing relevant\n```\n"],
    ids=["empty body", "prose only", "unrelated yaml"],
)
def test_gate_rejects_a_missing_receipt(tmp_path: Path, text: str) -> None:
    proc = run_gate(tmp_path, text)
    assert proc.returncode == 1
    assert "receipt check failed:" in proc.stdout


def test_gate_rejects_a_receipt_without_head_revision(tmp_path: Path) -> None:
    text = body().replace(f"head_revision: {HEAD}\n", "")
    proc = run_gate(tmp_path, text)
    assert proc.returncode == 1
    assert "missing head_revision" in proc.stdout
    assert "cannot bind to exact head" in proc.stdout


def test_gate_rejects_a_receipt_without_tests(tmp_path: Path) -> None:
    text = body().split("tests:\n")[0] + "```\n"
    proc = run_gate(tmp_path, text)
    assert proc.returncode == 1
    assert "missing tests mapping with red/green" in proc.stdout


def test_hash_prefixed_issue_reference_is_still_a_value() -> None:
    # `issue: #42` is how people write it; it must not be eaten as a comment.
    assert R.check(body(issue="#42"), HEAD) == []
    assert R.parse_simple_yaml("issue: #42\n")["issue"] == "#42"


def test_parser_unquotes_scalars_and_keeps_nested_mapping() -> None:
    parsed = R.parse_simple_yaml(
        f'issue: "42"\nhead_revision: \'{HEAD}\'  # head\ntests:\n  red: "cmd a"\n  green: cmd b # passed\n'
    )
    assert parsed == {"issue": "42", "head_revision": HEAD, "tests": {"red": "cmd a", "green": "cmd b"}}


def test_quoted_empty_tests_value_does_not_open_a_mapping() -> None:
    text = body().replace("tests:\n", 'tests: ""\n')
    assert "missing tests mapping with red/green" in R.check(text, HEAD)
