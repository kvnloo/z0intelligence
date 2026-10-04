from __future__ import annotations

from unittest import mock

from z0int import intelligence_mcp
from z0int.capabilities.ctx_history import CtxEventHydration, CtxSearchEvidence, CtxSearchHit
from z0int.context_resolve import EvidenceRef
from z0int.memory import surface as ms
from z0int.memory_contract import EventIdentity, MemoryScope


def _hit(*, event="evt-1", session="ses-1", provider="claude", cwd="/work/z0", text="unified memory decision"):
    ref = EvidenceRef(
        source_id=f"ctx:event:{event}",
        source_version="ctx-core:gen-7",
        locator=f"ctx:event:{event}",
        trust_class="conversation",
        observed_at="2026-10-04T01:00:00Z",
        excerpt=text,
    )
    return CtxSearchHit(
        evidence=ref,
        event_id=event,
        session_id=session,
        provider=provider,
        timestamp="2026-10-04T01:00:00Z",
        cwd=cwd,
        rank=1,
        retrieval_score=7.0,
    )


class _Cap:
    available = True

    def __init__(self, hit=None, generation="gen-7"):
        self.hit = hit or _hit()
        self.generation = generation
        self.search_kwargs = None
        self.show_kwargs = None

    def search(self, query, **kwargs):
        self.search_kwargs = kwargs
        return CtxSearchEvidence(
            query=query,
            evidence=(self.hit.evidence,),
            hits=(self.hit,),
            generation_id=self.generation,
            requested_mode="lexical",
            effective_mode="lexical",
            returned=1,
            more_available=False,
            latency_ms=12.0,
        )

    def show_event(self, event_id, **kwargs):
        self.show_kwargs = kwargs
        identity = EventIdentity.from_source(
            source_system="ctx:claude",
            source_session="ses-1",
            source_event_id=event_id,
            payload_hash="sha256:test",
        )
        return CtxEventHydration(
            event={
                "ctx_event_id": event_id,
                "ctx_session_id": "ses-1",
                "provider": "claude",
                "event_type": "message",
                "role": "assistant",
                "text": "verified ctx hydration",
            },
            identity=identity,
            window_events=(),
            latency_ms=4.0,
        )


def test_ctx_pull_is_caller_neutral_keyword_shaped_scoped_and_generation_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    cap = _Cap()
    monkeypatch.setattr(ms, "_ctx_capability", lambda: cap)
    policy = ms.ScopePolicy(
        scope=MemoryScope(user=ms.DEFAULT_USER, project="z0"),
        requester="dsh",
        cross_harness=True,
    )
    out = ms.search(
        "what was the last unified memory decision",
        policy,
        layers=("ctx",),
        required=(),
        config={"ctx": {"timeout_ms": 100}},
    )
    assert out["ok"] and out["layers"]["ctx"]["status"] == "ok"
    assert [e["locator"] for e in out["evidence"]] == ["ctx:event:evt-1"]
    assert out["layers"]["ctx"]["revision"] == "gen-7"
    assert cap.search_kwargs["include_current_session"] is True
    assert {"unified", "memory", "decision"} <= set(cap.search_kwargs["terms"])
    assert out["memory_snapshot_id"].startswith("mem_")


def test_ctx_scope_is_filtered_before_ranking(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    cap = _Cap(hit=_hit(cwd="/work/sibling"))
    monkeypatch.setattr(ms, "_ctx_capability", lambda: cap)
    policy = ms.ScopePolicy(
        scope=MemoryScope(user=ms.DEFAULT_USER, project="z0"),
        requester="dsh",
        cross_harness=True,
    )
    out = ms.search("unified memory", policy, layers=("ctx",), required=(), config={})
    assert out["evidence"] == []
    assert out["out_of_scope"] == 1


def test_ctx_unknown_scope_fails_closed_for_project_request(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    cap = _Cap(hit=_hit(cwd=None))
    monkeypatch.setattr(ms, "_ctx_capability", lambda: cap)
    policy = ms.ScopePolicy(scope=MemoryScope(user=ms.DEFAULT_USER, project="z0"))
    out = ms.search("unified memory", policy, layers=("ctx",), required=(), config={})
    assert out["evidence"] == []
    assert out["out_of_scope"] == 1


def test_ctx_generation_changes_snapshot_and_pull_bypasses_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    policy = ms.ScopePolicy(scope=MemoryScope(user=ms.DEFAULT_USER, project="z0"))
    cap = _Cap(generation="gen-7")
    monkeypatch.setattr(ms, "_ctx_capability", lambda: cap)
    first = ms.memory_brief("unified memory", policy, layers=("ctx",), required=(), config={})
    cap.generation = "gen-8"
    second = ms.memory_brief("unified memory", policy, layers=("ctx",), required=(), config={})
    assert first["cache"] == second["cache"] == "bypass:ctx_generation"
    assert first["memory_snapshot_id"] != second["memory_snapshot_id"]
    assert "ctx" in first["receipt"]["capability_ids"]


def test_ctx_exact_hydration_is_bounded_read_only_inspect(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    cap = _Cap()
    monkeypatch.setattr(ms, "_ctx_capability", lambda: cap)
    out = ms.inspect("ctx:event:evt-1", chars=64, context=3)
    assert out["ok"] is True
    assert out["identity"]["source_system"] == "ctx:claude"
    assert out["event"]["text"] == "verified ctx hydration"
    assert cap.show_kwargs["window"] == 3


def test_missing_ctx_is_explicit_and_does_not_break_other_layers(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    missing = mock.Mock(available=False)
    monkeypatch.setattr(ms, "_ctx_capability", lambda: missing)
    out = ms.search("unified memory", layers=("ctx",), required=(), config={})
    assert out["layers"]["ctx"]["status"] == "unavailable"
    assert out["layers"]["ctx"]["reason"] == "not_installed"
    assert out["evidence"] == []


def test_memory_mcp_uses_pull_layers_including_ctx(monkeypatch):
    seen = {}

    def fake_search(query, policy, **kwargs):
        seen["layers"] = kwargs["layers"]
        return {"ok": True, "evidence": [], "layers": {}, "memory_snapshot_id": "mem_x"}

    monkeypatch.setattr(ms, "search", fake_search)
    handler = intelligence_mcp._memory_handlers()["memory_search"]
    handler({"query": "unified memory"})
    assert tuple(seen["layers"]) == ms.PULL_LAYERS
    assert "ctx" in seen["layers"]
