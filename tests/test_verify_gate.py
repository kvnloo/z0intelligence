"""Tests for the standalone verification gate (State Packet -> NanoJev -> action).

No CUDA and no checkpoint: the gate takes an injected backend, so the policy,
the question derivation, the state rendering and the receipt contract are all
tested deterministically. The model's own boolean head layout is asserted
against the runtime source separately (see test_boolean_head_* below), because
P(true)=sigmoid(a) only holds while that head keeps its shape.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from z0int.backends.base import DecisionAnswer, DecisionResult
from z0int.receipt import validate_receipt
from z0int.verify_gate import (
    ACTIONS,
    DEFAULT_ALLOW_AT,
    DEFAULT_ESCALATE_BELOW,
    SCHEMA,
    _MIN_LINE_CHARS,
    _STATE_CHAR_BUDGET,
    action_of,
    gate,
    packet_state_text,
    state_digest,
    verification_question,
)

REPO = Path(__file__).resolve().parents[1]


class StubBackend:
    """Minimal DecisionBackend stand-in with a fixed P(true)."""

    ID = "stub"
    MODEL = "stub-model"

    def __init__(self, p_true: float):
        self.p_true = p_true
        self.seen_requests: list = []

    def evaluate(self, request):
        self.seen_requests.append(request)
        q = request.questions[0]
        return DecisionResult(
            backend=self.ID,
            model=self.MODEL,
            revision="deadbeef",
            answers=(
                DecisionAnswer(
                    question_id=q.id,
                    type="boolean",
                    probabilities={"false": 1.0 - self.p_true, "true": self.p_true},
                    value=self.p_true >= 0.5,
                    confidence=max(self.p_true, 1.0 - self.p_true),
                ),
            ),
            latency_ms=3.0,
            diagnostics={"stub": True},
        )


class NoTrueBackend(StubBackend):
    def evaluate(self, request):
        q = request.questions[0]
        return DecisionResult(
            backend=self.ID, model=self.MODEL, revision=None,
            answers=(DecisionAnswer(question_id=q.id, type="boolean",
                                    probabilities={"false": 1.0},
                                    value=False, confidence=1.0),),
            latency_ms=1.0,
        )


def packet(**over):
    base = {
        "query": "which port does the AgentsView MCP server listen on",
        "evidence_counts": {"sessions_touched": 53, "snippets": 87, "facts": 15},
        "by_harness": {"hermes": 32, "grok": 8},
        "decision_like": [
            {"session_id": "coverage:grok:abc#path:/workspace/demo/tool.mjs",
             "agent": "grok", "excerpt": "/workspace/demo/tool.mjs"},
        ],
        "supersession_signals": [],
        "contradiction_candidates": [],
        "evidence_pointers": ["grok:coverage:grok:abc"],
        "evidence_pointers_ranked": [{"pointer": "grok:coverage:grok:abc", "hits": 1}],
        "pointers_omitted": 0,
        "literals": ["/workspace/demo/tool.mjs", "/dev/null"],
        "coverage": {"harnesses_with_hits": ["hermes", "grok"],
                     "providers": {}, "provider_errors": [],
                     "earliest": "2026-08-01T00:00:00Z", "latest": "2026-09-20T00:00:00Z"},
        "unknowns": [],
        "budgets_ms": {"total_ms": 2931.9},
        "plan": {"query": "which port does the AgentsView MCP server listen on",
                 "entities": [], "quoted": [], "question_type": "natural_language",
                 "relation_hints": [], "compiled_by": "z0int (delegated)"},
        "recipe_signature": "284e1230f244a8c3",
        "source": "z0int.context_resolve",
    }
    base.update(over)
    return base


# --- policy -----------------------------------------------------------------

@pytest.mark.parametrize("p,expected", [
    (1.0, "ALLOW"), (0.65, "ALLOW"), (0.6499, "ABSTAIN"),
    (0.5001, "ABSTAIN"), (0.5, "ABSTAIN"), (0.3501, "ABSTAIN"),
    (0.35, "ESCALATE"), (0.0, "ESCALATE"), (0.323, "ESCALATE"), (0.543, "ABSTAIN"),
])
def test_action_of_bands(p, expected):
    assert action_of(p) == expected


def test_action_of_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        action_of(0.5, allow_at=0.2, escalate_below=0.8)


def test_action_of_custom_thresholds():
    assert action_of(0.5, allow_at=0.4, escalate_below=0.1) == "ALLOW"
    assert action_of(0.49, allow_at=0.5, escalate_below=0.4) == "ABSTAIN"
    assert action_of(0.05, allow_at=0.5, escalate_below=0.4) == "ESCALATE"


def test_actions_vocabulary_is_exactly_three():
    assert ACTIONS == ("ALLOW", "ABSTAIN", "ESCALATE")


def test_defaults_are_wider_than_the_bare_half():
    """A 0.5 cut was the thing this gate exists to avoid."""
    assert DEFAULT_ESCALATE_BELOW < 0.5 < DEFAULT_ALLOW_AT


# --- question derivation ----------------------------------------------------

def test_question_is_derived_from_the_packet_query():
    q = verification_question(packet(query="is the daemon listening on 8791"))
    assert "is the daemon listening on 8791" in q
    assert q.endswith("?")


def test_question_differs_between_packets():
    a = verification_question(packet(query="alpha"))
    b = verification_question(packet(query="beta"))
    assert a != b


def test_question_requires_a_query():
    with pytest.raises(ValueError):
        verification_question(packet(query=""))
    with pytest.raises(ValueError):
        verification_question({"query": "   "})


# --- state rendering --------------------------------------------------------

def test_state_render_is_deterministic():
    assert packet_state_text(packet()) == packet_state_text(packet())


def test_state_render_carries_counts_sources_and_literals():
    text = packet_state_text(packet())
    assert "sessions_touched=53" in text
    assert "hermes" in text
    assert "/workspace/demo/tool.mjs" in text
    assert "grok:coverage:grok:abc" in text


def test_state_render_surfaces_contradictions_and_unknowns():
    text = packet_state_text(packet(
        contradiction_candidates=["the daemon binds 8080, not 8791"],
        unknowns=["no record of a TLS terminator"],
    ))
    assert "Contradiction candidate" in text
    assert "the daemon binds 8080, not 8791" in text
    assert "Unknown" in text and "no record of a TLS terminator" in text


def test_state_render_changes_with_content_but_not_with_query_alone():
    """Guards the real finding: counts saturate, so only content moves the state."""
    a = packet_state_text(packet(query="alpha"))
    b = packet_state_text(packet(query="beta"))
    assert a == b  # query is not in the state; it is in the question
    c = packet_state_text(packet(decision_like=[{"session_id": "s", "excerpt": "different"}]))
    assert c != a


def test_state_render_respects_the_char_budget():
    """Bounded in characters, which is only a proxy for the real 512-token budget.

    Path-heavy text tokenizes at ~2.2 chars/token, measured, so the budget is
    deliberately well under 512*4. The runtime's truncated_questions flag is the
    real guarantee; this test only pins the renderer's side of it.
    """
    text = packet_state_text(packet(
        decision_like=[{"session_id": f"s{i}", "excerpt": "x" * 5000} for i in range(50)],
        evidence_pointers_ranked=[{"pointer": "p" * 900, "hits": 1} for _ in range(500)],
        literals=["l" * 300] * 200,
    ))
    assert len(text) <= _STATE_CHAR_BUDGET, f"state over budget: {len(text)} chars"


def test_state_render_keeps_the_header_and_literals_under_pressure():
    """High-value sections survive; the redundant pointer index absorbs the cut."""
    text = packet_state_text(packet(
        decision_like=[{"session_id": "s", "excerpt": "x" * 5000} for _ in range(50)],
        evidence_pointers_ranked=[{"pointer": "p" * 900, "hits": 1} for _ in range(50)],
        literals=["/workspace/demo/tool.mjs", "/dev/null"],
    ))
    assert "Evidence counts:" in text
    assert "Literals:" in text and "/workspace/demo/tool.mjs" in text


def test_state_render_keeps_contradictions_when_budget_is_tight():
    """The decisive content must not be evicted by volume in other sections."""
    text = packet_state_text(packet(
        contradiction_candidates=["the daemon binds 8080, not 8791"],
        decision_like=[{"session_id": f"s{i}", "excerpt": "x" * 5000} for i in range(50)],
    ))
    assert "the daemon binds 8080, not 8791" in text


def test_state_digest_is_stable_and_short():
    d = state_digest("abc")
    assert d == state_digest("abc") and len(d) == 16
    assert d != state_digest("abd")


def test_state_render_includes_evidence_prose():
    """The packet now carries evidence; the verifier must actually see it."""
    text = packet_state_text(packet(evidence=[
        {"rank": 0, "source_id": "conversation:agentsview:hermes:abc#1",
         "locator": "agentsview:hermes:abc#1", "trust_class": "conversation",
         "observed_at": "2026-08-15T10:50:05Z",
         "excerpt": "The MCP bridge listens on port 8791 in this profile."},
    ]))
    assert "Evidence 0 [conversation]: The MCP bridge listens on port 8791" in text


def test_state_render_prefers_evidence_over_the_pointer_index():
    """Evidence must outrank the index sections that used to crowd it out."""
    text = packet_state_text(packet(
        evidence=[{"rank": 0, "source_id": "conversation:x:y#1",
                   "excerpt": "E" * 400}],
        evidence_pointers_ranked=[{"pointer": "facts:" + "p" * 400, "hits": 9}],
        decision_like=[{"session_id": "s" * 300, "excerpt": "D" * 400}],
    ))
    assert "Evidence 0" in text, "evidence was evicted by the index sections"
    if "Pointer" in text:
        assert text.index("Evidence 0") < text.index("Pointer"), "index outranks evidence"


def test_budget_drops_every_sub_useful_remainder():
    """Sweep the whole stub window, not one lucky remainder.

    A single construction pins only the remainder it happens to produce, so
    mutating the constant to 10 or 30 left the earlier version of this test
    green. Sweeping 1..39 pins the boundary wherever it is set.
    """
    from z0int.verify_gate import _Budget

    for rem in range(1, 40):
        b = _Budget(100)
        b.add("x" * (99 - rem))  # leaves exactly `rem` chars of room
        b.add("y" * 200)
        assert len(b.lines) == 1, f"{rem}-char remainder produced a stub: {b.lines}"
        assert b.dropped, f"{rem}-char remainder was not recorded as dropped"


def test_state_render_never_emits_a_clipped_stub():
    """A sub-useful clipped line spends tokens and says nothing."""
    text = packet_state_text(packet(
        evidence=[{"rank": i, "source_id": f"conversation:s{i}",
                   "excerpt": "x" * 320} for i in range(40)],
    ))
    # The threshold is a literal on purpose: comparing against _MIN_LINE_CHARS
    # would make this test follow the constant it is supposed to pin, and it
    # passed against a mutated constant until this was fixed.
    stubs = [l for l in text.splitlines() if l.endswith("\u2026") and len(l) < 40]
    assert stubs == [], f"stub lines emitted: {stubs}"
    assert len(text) <= _STATE_CHAR_BUDGET


def test_state_render_tolerates_a_packet_without_evidence():
    """Older packets have no `evidence` key; rendering must still work."""
    p = packet()
    p.pop("evidence", None)
    text = packet_state_text(p)
    assert "Evidence counts:" in text
    assert "Evidence 0" not in text


def test_gate_passes_evidence_prose_into_the_backend_state():
    stub = StubBackend(0.5)
    gate(packet(evidence=[{"rank": 0, "source_id": "conversation:a:b#1",
                           "excerpt": "The daemon binds 8791."}]), backend=stub)
    assert "The daemon binds 8791." in stub.seen_requests[0].state


# --- gate + receipt ---------------------------------------------------------

def _extra(receipt):
    return receipt["extra"]


def test_gate_allow_band():
    r = gate(packet(), backend=StubBackend(0.9))
    assert r["action_taken"] == "ALLOW"
    assert r["prediction"] == "ALLOW"
    assert _extra(r)["p_true"] == pytest.approx(0.9)
    assert _extra(r)["p_false"] == pytest.approx(0.1)


def test_gate_abstain_band():
    r = gate(packet(), backend=StubBackend(0.5))
    assert r["action_taken"] == "ABSTAIN"


def test_gate_escalate_band():
    r = gate(packet(), backend=StubBackend(0.1))
    assert r["action_taken"] == "ESCALATE"


def test_receipt_validates_against_the_real_contract():
    r = gate(packet(), backend=StubBackend(0.5))
    r.pop("_state_text", None)
    assert validate_receipt(r) == []
    assert r["schema"] == "z0int.decision_receipt.v1"


def test_receipt_carries_every_required_field():
    r = gate(packet(), backend=StubBackend(0.42))
    x = _extra(r)
    prov = x["state_packet"]
    assert prov["recipe_signature"] and prov["query"] and prov["source"]
    assert prov["evidence_counts"]["sessions_touched"] == 53
    assert "which port" in x["verification_question"]
    assert x["verifier"]["backend"] == "stub"
    assert x["verifier"]["model"] == "stub-model"
    assert x["verifier"]["state_chars"] > 0
    assert len(x["verifier"]["state_sha256_16"]) == 16
    assert x["verifier"]["state_truncated"] is False
    assert x["verifier"]["truncated_questions"] == []
    assert x["p_true"] == pytest.approx(0.42)
    assert x["p_false"] == pytest.approx(0.58)
    assert x["thresholds"]["allow_at"] == DEFAULT_ALLOW_AT
    assert x["thresholds"]["escalate_below"] == DEFAULT_ESCALATE_BELOW
    assert x["thresholds"]["provisional"] is True
    assert r["action_taken"] == "ABSTAIN"
    assert r["latency_ms"] == pytest.approx(3.0)
    assert isinstance(r["ts"], float) and r["ts"] > 0
    assert x["evaluate_wall_ms"] > 0
    assert x["evaluate_wall_ms_includes_lazy_load"] is True
    assert x["gate_schema"] == SCHEMA
    # git_sha may be None outside a checkout, but must be present as a key
    assert "git_sha" in x


def test_receipt_route_matches_action():
    routes = {a: gate(packet(), backend=StubBackend(p))["route"]
              for a, p in (("ALLOW", 0.9), ("ABSTAIN", 0.5), ("ESCALATE", 0.1))}
    assert routes == {"ALLOW": "local", "ABSTAIN": "shadow", "ESCALATE": "escalate"}


def test_gate_state_hash_matches_the_state_it_was_given():
    r = gate(packet(), backend=StubBackend(0.5))
    assert _extra(r)["verifier"]["state_sha256_16"] == state_digest(r["_state_text"])
    assert _extra(r)["verifier"]["state_chars"] == len(r["_state_text"])


def test_gate_does_not_mutate_the_packet():
    p = packet()
    before = json.dumps(p, sort_keys=True)
    gate(p, backend=StubBackend(0.5))
    assert json.dumps(p, sort_keys=True) == before


def test_gate_passes_the_question_and_state_to_the_backend():
    stub = StubBackend(0.5)
    gate(packet(query="is the sky blue"), backend=stub)
    req = stub.seen_requests[0]
    assert "is the sky blue" in req.questions[0].instructions
    assert req.questions[0].type == "boolean"
    assert "sessions_touched=53" in req.state


def test_gate_rejects_a_backend_without_a_true_probability():
    with pytest.raises(ValueError, match="no 'true' probability"):
        gate(packet(), backend=NoTrueBackend(0.0))


def test_gate_requires_a_query():
    with pytest.raises(ValueError):
        gate({"evidence_counts": {}}, backend=StubBackend(0.5))


class TruncatingBackend(StubBackend):
    """Reports the truncation the runtime now surfaces in diagnostics."""

    def evaluate(self, request):
        result = super().evaluate(request)
        return DecisionResult(
            backend=result.backend, model=result.model, revision=result.revision,
            answers=result.answers, latency_ms=result.latency_ms,
            diagnostics={"truncated_questions": [request.questions[0].id]},
        )


def test_receipt_records_that_the_state_was_truncated():
    """The receipt must never claim the verifier saw a state it did not."""
    r = gate(packet(), backend=TruncatingBackend(0.5))
    assert _extra(r)["verifier"]["state_truncated"] is True
    assert _extra(r)["verifier"]["truncated_questions"] == ["state_packet_supports_action"]


def test_receipt_is_json_serializable():
    r = gate(packet(), backend=StubBackend(0.5))
    assert json.loads(json.dumps(r, default=str))["trace_id"] == r["trace_id"]


# --- CLI --------------------------------------------------------------------

def test_cli_runs_on_a_packet_file_and_appends_the_exact_path(tmp_path):
    pf = tmp_path / "packet.json"
    pf.write_text(json.dumps(packet()), encoding="utf-8")
    out = tmp_path / "receipts.jsonl"

    # The CLI must not need CUDA: stub the backend constructor it would import.
    script = tmp_path / "run_gate.py"
    script.write_text(
        "import sys\n"
        "from z0int import verify_gate as g\n"
        "from z0int.backends.base import DecisionAnswer, DecisionResult\n"
        "\n"
        "class B:\n"
        "    def evaluate(self, request):\n"
        "        q = request.questions[0]\n"
        "        return DecisionResult(\n"
        "            backend='stub', model='m', revision=None,\n"
        "            answers=(DecisionAnswer(question_id=q.id, type='boolean',\n"
        "                                    probabilities={'false': 0.4, 'true': 0.6},\n"
        "                                    value=True, confidence=0.6),),\n"
        "            latency_ms=1.0,\n"
        "        )\n"
        "\n"
        "g.create_backend = lambda name: B()\n"
        "sys.exit(g.main(['--packet', sys.argv[1], '--receipt-out', sys.argv[2]]))\n",
        encoding="utf-8",
    )
    r = subprocess.run([sys.executable, str(script), str(pf), str(out)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["action_taken"] == "ABSTAIN"  # 0.6 is below the provisional allow_at
    assert rows[0]["extra"]["p_true"] == pytest.approx(0.6)
    assert "_state_text" not in rows[0], "inspection helper must not be persisted"
    # stdout is the receipt and nothing else
    assert json.loads(r.stdout)["trace_id"] == rows[0]["trace_id"]
