"""CW4 start-here files (spec §1.5, owner decisions O-3, O-5, O-7)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_agents_md_starts_with_a_short_start_here_section():
    text = _read("AGENTS.md")
    sections = re.split(r"^## ", text, flags=re.M)
    assert sections[1].startswith("Start here (any agent)")
    body = [ln for ln in sections[1].splitlines()[1:] if ln.strip()]
    assert len(body) <= 12
    joined = " ".join(body)
    for must in ("z0int contribute --json", "next_action", "ask_human", "explicit yes", "z0int contribute start",
                 "z0int contribute evidence", ".z0/priorities.yaml", "master"):
        assert must in joined, must
    assert "preview" not in joined.replace("`dev`/`preview`/`nightly`", "")  # contributors never target preview


def test_claude_md_is_one_source_of_truth():
    lines = _read("CLAUDE.md").strip().splitlines()
    assert lines[0] == "@AGENTS.md"
    assert "skills/z0int-contribute/SKILL.md" in _read("CLAUDE.md")


def test_skill_is_a_thin_cli_wrapper_and_plugin_copy_is_in_sync():
    skill = _read("skills/z0int-contribute/SKILL.md")
    assert skill.startswith("---\nname: z0int-contribute\n")
    assert "z0int contribute --json" in skill
    assert _read("harness-adapters/z0int-contribute/skills/z0int-contribute/SKILL.md") == skill
    for mp, key in ((".claude-plugin/marketplace.json", "source"), (".agents/plugins/marketplace.json", "source")):
        names = [p["name"] for p in json.loads(_read(mp))["plugins"]]
        assert "z0int-contribute" in names, mp


def test_contributing_states_cla_credit_and_default_branch():
    text = _read("CONTRIBUTING.md")
    assert "Contributor License Agreement" in text and "CLA bot" in text
    assert ".z0/credits.yaml" in text and "Credit: @" in text
    assert "target the default branch **`master`**" in text
    assert "z0evals" in text and "grok" in text


def test_pr_template_carries_task_receipts_credit_and_receipt_yaml():
    text = _read(".github/PULL_REQUEST_TEMPLATE.md")
    for must in ("Task id:", "Receipts", "Credit: @", "Contributor License Agreement", "head_revision:",
                 "base_revision:", "red:", "green:"):
        assert must in text, must


def test_issue_template_is_valid_issue_form():
    form = yaml.safe_load(_read(".github/ISSUE_TEMPLATE/task.yml"))
    assert form["name"] and form["body"]
    ids = {b.get("id") for b in form["body"] if b.get("type") != "markdown"}
    assert {"problem", "verifier", "exit_criterion", "kind"} <= ids


def test_onboarding_doc_fixes():
    text = _read("docs/ONBOARDING.md")
    assert not re.search(r"^cd z0int\s*$", text, re.M)
    assert "https://github.com/kvnloo/z0evals" in text
    assert "z0int contribute" in text
