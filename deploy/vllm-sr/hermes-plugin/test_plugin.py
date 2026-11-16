"""Behaviour tests for the sr Hermes plugin (no network, no real stores)."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sr_plugin", HERE / "__init__.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)


def test_tiers_are_cumulative_and_all_covers_every_keyed_backend():
    t = sr.tiers()
    assert set(t["minimal"]) < set(t["poweruser"]) < set(t["all"])
    keyed = {c[2] for c in sr.free_pool().CANDIDATES if c[2]}
    assert keyed <= set(t["all"])                       # a new provider in free_pool lands in `all`
    assert set(t["all"]) <= set(sr.PROVIDERS)           # ...and must be given a signup URL here


def test_onboard_prompts_only_for_missing_and_never_returns_values(monkeypatch):
    monkeypatch.setattr(sr, "present", lambda keys, store, project=None: {k: k == "GROQ_API_KEY" for k in keys})
    saved, asked = {}, []
    monkeypatch.setattr(sr, "store_key", lambda k, v, s, p=None: saved.setdefault(k, v) and "saved")

    def prompt(msg):
        asked.append(msg)
        return "sk-secret-value" if "OpenRouter" in msg else ""
    rows = sr.onboard("minimal", "hermes", prompt=prompt)
    assert len(asked) == 1 and "OpenRouter" in asked[0]  # GROQ is already set: not asked
    assert saved == {"OPENROUTER_API_KEY": "sk-secret-value"}
    assert "sk-secret-value" not in json.dumps(rows)
    by = {r["key"]: r["status"] for r in rows}
    assert by["GROQ_API_KEY"] == "set" and by["OPENROUTER_API_KEY"] == "saved"


def test_check_only_never_prompts(monkeypatch):
    monkeypatch.setattr(sr, "present", lambda keys, store, project=None: {k: False for k in keys})
    rows = sr.onboard("all", "hermes", prompt=lambda m: (_ for _ in ()).throw(AssertionError("prompted")),
                      check_only=True)
    assert {r["status"] for r in rows if r["key"] != "(none)"} == {"missing"}


def test_bws_store_needs_project_and_token(monkeypatch):
    monkeypatch.delenv("BWS_PROJECT_ID", raising=False)
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    try:
        sr.present(["GROQ_API_KEY"], "bws")
    except SystemExit as e:
        assert "project id" in str(e)
    else:
        raise AssertionError("bws without config must refuse")


def test_ledger_counts_only_scored_free_trials():
    base = {"suite": "skillsbench", "clean": True, "smoke": False, "error_class": None, "finished_at": "2026-11-16T10:00"}
    rows = [
        {**base, "combo": "free/a", "model": "vllm-sr/m/a", "reward": 1.0},
        {**base, "combo": "free/a", "model": "vllm-sr/m/a", "reward": 0.0},
        {**base, "combo": "free/a", "model": "vllm-sr/m/a", "reward": 0.0, "error_class": "infra:docker"},  # not the model's fault
        {**base, "combo": "free/a", "model": "vllm-sr/m/a", "reward": 1.0, "smoke": True},
        {**base, "combo": "free/a", "model": "vllm-sr/m/a", "reward": 1.0, "clean": False},
        {**base, "combo": "opus/x", "model": "claude", "reward": 1.0},                               # not free
    ]
    assert sr.ledger_rows(rows) == [{"model": "a", "source": "skillsbench", "score0to1": 0.5, "n": 2, "date": "2026-11-16"}]
    assert sr.ledger_rows([rows[2]]) == []                                                          # infra-only: no score


def test_status_reports_router_down_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(sr, "BASE", "http://127.0.0.1:9/v1")
    s = sr.status()
    assert s["up"] is False and "error" in s and "models" not in s
