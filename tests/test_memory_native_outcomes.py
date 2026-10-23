"""Native source-backed outcomes. All HTTP sources and content are synthetic."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_memory_mcp import factory, record as base_record, replace_policy
from z0int.memory_contract import MemoryScope


SCOPE = MemoryScope(user="synthetic-user", project="project-A", repo="synthetic-repo", task="synthetic-task")
NATIVE_START = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def record(**kwargs):
    kwargs.setdefault("date", (NATIVE_START + timedelta(seconds=kwargs.get("ordinal", 3))).isoformat())
    return base_record(session_id="omp:synthetic-session", **kwargs)


def offline_replay(*args, **kwargs):
    # Keep collection runnable against the pre-feature source for the RED gate.
    from z0int.memory_outcomes import replay_outcomes
    return replay_outcomes(*args, **kwargs)


def scoped(factory, rows):
    server, path, backend, clock = factory(rows=rows, wall_clock=lambda: NATIVE_START.timestamp())
    server.bind_workstream(SCOPE, native_session="omp:synthetic-session", after_ordinal=0, through_ordinal=10)
    return server, path, backend, clock


def test_retrieval_is_pending_and_scope_replays_without_native_use(factory):
    server, _, _, _ = scoped(factory, [record(role="user")])
    result = server.search("private query")
    server.fetch(result["results"][0]["id"])
    replay = server.replay_outcomes(SCOPE)
    assert replay[0]["receipt_id"] == result["receipt_id"]
    assert replay[0]["outcome"] is None
    assert replay[0]["retrieval"] == "hit"
    assert all(row["outcome"] is None for row in replay)
    with pytest.raises(ValueError):
        server.replay_outcomes(MemoryScope(user="other", project="project-A", repo="synthetic-repo", task="synthetic-task"))


def test_native_assistant_citation_is_used_and_absent_citation_is_ignored(factory):
    evidence = record(role="user", text="The accepted option is blue.", ordinal=1)
    decision = record(role="assistant", text="Use [" + evidence["locator"] + "@" + evidence["source_version"] + "]", ordinal=2)
    server, _, backend, _ = scoped(factory, [evidence])
    found = server.search("decision")
    backend.records = [decision]
    decision_id = server.search("native answer")["results"][0]["id"]
    joined = server.join_native_outcome(found["receipt_id"], decision_id, scope=SCOPE)
    assert joined["outcome"] == "hit_used"
    assert server.replay_outcomes(SCOPE)[0]["outcome"] == "hit_used"
    assert server.join_native_outcome(found["receipt_id"], decision_id, scope=SCOPE) == joined
    backend.records = [record(role="assistant", text="An unrelated complete answer", ordinal=3)]
    ignored = server.search("ignored")
    other = ignored["results"][0]["id"]
    assert server.join_native_outcome(ignored["receipt_id"], other, scope=SCOPE)["outcome"] == "hit_ignored"


def test_miss_stays_distinct_from_denial_and_outage(factory):
    server, _, backend, _ = scoped(factory, [])
    missing = server.search("no match")
    backend.down = True
    server.search("outage")
    assert [(r["retrieval"], r["outcome"]) for r in server.replay_outcomes(SCOPE)] == [("miss", None), ("unavailable", None)]
    backend.down = False
    backend.records = [record(role="assistant")]
    decision = server.search("closed answer")["results"][0]["id"]
    assert server.join_native_outcome(missing["receipt_id"], decision, scope=SCOPE)["outcome"] == "miss"


@pytest.mark.parametrize("role", ["user", None, "tool", "system"])
def test_non_assistant_decision_cannot_claim_use(factory, role):
    server, _, _, _ = scoped(factory, [record(role=role)])
    found = server.search("evidence")
    with pytest.raises(ValueError):
        server.join_native_outcome(found["receipt_id"], found["results"][0]["id"], scope=SCOPE)


def test_user_restated_requires_native_user_pair_and_chronology(factory):
    evidence = record(role="user", text="Use blue", ordinal=1)
    decision = record(role="assistant", text="I selected red", ordinal=2)
    correction = record(role="user", ordinal=3, text="Restatement of [" + decision["locator"] + "@" + decision["source_version"] + "]: Use blue, as requested.")
    server, _, backend, _ = scoped(factory, [evidence])
    found = server.search("option")
    backend.records = [decision]
    decision_id = server.search("answer")["results"][0]["id"]
    server.join_native_outcome(found["receipt_id"], decision_id, scope=SCOPE)
    backend.records = [decision, correction]
    correction_id = server.search("correction")["results"][1]["id"]
    result = server.join_native_outcome(found["receipt_id"], decision_id, scope=SCOPE, restatement_id=correction_id)
    assert result["outcome"] == "user_restated"
    assert server.replay_outcomes(SCOPE)[0]["outcome"] == "user_restated"
    assert result["prior_outcome"] == "hit_ignored"
    for role in ("assistant", "tool"):
        backend.records[1] = record(role=role, ordinal=3, text=correction["text"])
        with pytest.raises(ValueError):
            server.join_native_outcome(found["receipt_id"], decision_id, scope=SCOPE, restatement_id=correction_id)


def test_native_replacement_scope_revocation_and_unbound_workstream_fail_closed(factory):
    server, path, backend, _ = scoped(factory, [record(role="assistant")])
    found = server.search("original")
    decision = found["results"][0]["id"]
    backend.records[0] = record(role="assistant", text="replaced")
    with pytest.raises(ValueError):
        server.join_native_outcome(found["receipt_id"], decision, scope=SCOPE)
    replace_policy(path, lambda p: p["principals"]["operator"].clear())
    with pytest.raises(ValueError):
        server.join_native_outcome(found["receipt_id"], decision, scope=SCOPE)
    unbound, _, _, _ = factory()
    with pytest.raises(ValueError):
        unbound.join_native_outcome(found["receipt_id"], decision, scope=SCOPE)


def test_outcome_audit_has_only_hashes_and_replay_rejects_foreign_joins(factory):
    evidence = record(role="user", text="SYNTHETIC_PRIVATE_DECISION", ordinal=1)
    server, _, backend, _ = scoped(factory, [evidence])
    found = server.search("SYNTHETIC_PRIVATE_QUERY")
    backend.records = [record(role="assistant", ordinal=2)]
    decision = server.search("answer")["results"][0]["id"]
    server.join_native_outcome(found["receipt_id"], decision, scope=SCOPE)
    audit = (server.state_dir / "audit.jsonl").read_text()
    for forbidden in (evidence["text"], evidence["locator"], "SYNTHETIC_PRIVATE_QUERY", decision, SCOPE.repo, SCOPE.user):
        assert forbidden not in audit
    rows = [json.loads(line) for line in audit.splitlines()]
    rows[-1]["scope_hmac"] = "0" * 64
    with pytest.raises(ValueError):
        server.replay_outcomes(SCOPE, rows=rows)


@pytest.mark.parametrize("mutation", ["revision", "session", "role", "missing_read", "citation"])
def test_replay_rejects_native_provenance_tampering(factory, mutation):
    server, _, backend, _ = scoped(factory, [record(role="user")])
    found = server.search("query")
    backend.records = [record(role="assistant", ordinal=5)]
    decision = server.search("answer")["results"][0]["id"]
    joined = server.join_native_outcome(found["receipt_id"], decision, scope=SCOPE)
    rows = server._audit_rows()
    if mutation == "revision":
        rows[-1]["decision"]["revision_hmac"] = "0" * 64
    elif mutation == "session":
        rows[-1]["decision"]["session_hmac"] = "0" * 64
    elif mutation == "role":
        rows[-1]["decision"]["role"] = "user"
    elif mutation == "missing_read":
        rows = [r for r in rows if r.get("receipt_id") != joined["decision_fetch_receipt_id"]]
    else:
        rows[-1]["cited_evidence_hmacs"] = ["0" * 64]
    with pytest.raises(ValueError):
        offline_replay(rows, scope_hmac=joined["scope_hmac"], principal="operator")


def test_real_http_native_adapter_joins_output_and_later_user_source(tmp_path, monkeypatch):
    """Exercise authenticated routes and _original, not a callback role label."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from threading import Thread
    from urllib.parse import parse_qs, urlsplit
    from z0int.memory_mcp import MemoryServer
    import hashlib

    session = "omp:synthetic-http"
    originals = {
        1: {"role": "user", "content": "Select blue."},
        2: {"role": "assistant", "content": ""},
        3: {"role": "user", "content": ""},
    }
    for ordinal, message in originals.items():
        message.update(id=700 + ordinal, session_id=session, ordinal=ordinal,
                       is_system=False, has_thinking=False, has_tool_use=False,
                       thinking_text="", timestamp=f"2026-10-23T12:00:0{ordinal}Z")
    def citation(ordinal):
        message = originals[ordinal]
        digest = hashlib.sha256(message["content"].encode()).hexdigest()
        return f"agentsview:{session}:message:{message['id']}:ordinal:{ordinal}@sha256:{digest}"
    originals[2]["content"] = f"Selected blue, per [{citation(1)}]."
    originals[3]["content"] = f"Restatement of [{citation(2)}]: Keep blue as originally requested."
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer synthetic-local-auth"
            calls.append(self.path)
            parsed = urlsplit(self.path)
            if parsed.path == "/api/v1/search/content":
                query = parse_qs(parsed.query)
                ordinal = {"evidence": 1, "answer": 2, "correction": 3}[query["pattern"][0]]
                body = {"matches": [{"project": "project-A", "session_id": session,
                                     "ordinal": ordinal, "location": "message"}]}
            elif parsed.path.endswith("/messages"):
                ordinal = int(parse_qs(parsed.query)["around"][0])
                body = {"messages": [originals[ordinal]]}
            else:
                body = {"id": session, "project": "project-A", "machine": "synthetic"}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=http.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    config = tmp_path / ".agentsview"
    config.mkdir()
    (config / "config.toml").write_text(f'port = {http.server_port}\nauth_token = "synthetic-local-auth"\n')
    policy = tmp_path / "policy.json"
    grants = [{"source": "agentsview", "collection": "history",
               "scope": "project-A", "privacy": ["private"]}]
    policy.write_text(json.dumps({"version": 2, "principals": {"operator": grants, "chatgpt": []}}))
    try:
        server = MemoryServer(policy, tmp_path / "state", principal="operator")
        server.wall_clock = lambda: NATIVE_START.timestamp()
        server.bind_workstream(SCOPE, native_session=session, after_ordinal=0, through_ordinal=10)
        query = server.search("evidence")
        answer = server.search("answer")["results"][0]["id"]
        assert server.join_native_outcome(query["receipt_id"], answer, scope=SCOPE)["outcome"] == "hit_used"
        correction = server.search("correction")["results"][0]["id"]
        joined = server.join_native_outcome(query["receipt_id"], answer, scope=SCOPE, restatement_id=correction)
        assert joined["outcome"] == "user_restated"
        # Replay has no native source access and does not need the server key.
        before = len(calls)
        assert offline_replay(server._audit_rows(), scope_hmac=joined["scope_hmac"], principal="operator")[0]["outcome"] == "user_restated"
        assert len(calls) == before
        originals[3]["role"] = "assistant"
        with pytest.raises(ValueError):
            server.join_native_outcome(query["receipt_id"], answer, scope=SCOPE, restatement_id=correction)
    finally:
        http.shutdown()
        http.server_close()
        worker.join()


@pytest.mark.parametrize("change", ["truncated", "redacted", "foreign_session", "before_window", "wrong_revision"])
def test_incomplete_or_unrelated_native_answer_never_means_ignored(factory, change):
    evidence = record(role="user", ordinal=1)
    server, _, backend, _ = scoped(factory, [evidence])
    query = server.search("query")
    kwargs = {"role": "assistant", "ordinal": 2}
    if change == "truncated":
        kwargs["text"] = "long answer " * 1000
    elif change == "redacted":
        kwargs["text"] = "Authorization: Bearer synthetic-secret"
    elif change == "before_window":
        kwargs["ordinal"] = 0
    elif change == "wrong_revision":
        kwargs["text"] = f'Use [{evidence["locator"]}@sha256:{"0" * 64}]'
    if change == "foreign_session":
        backend.records = [base_record(session_id="omp:foreign", **kwargs)]
    else:
        backend.records = [record(**kwargs)]
    decision = server.search("answer")["results"][0]["id"]
    with pytest.raises(ValueError):
        server.join_native_outcome(query["receipt_id"], decision, scope=SCOPE)


@pytest.mark.parametrize("change", ["unpaired", "wrong_revision", "earlier", "assistant", "sibling"])
def test_unproven_native_user_restatement_is_rejected(factory, change):
    server, _, backend, _ = scoped(factory, [record(role="user", ordinal=1)])
    query = server.search("query")
    decision = record(role="assistant", ordinal=3)
    backend.records = [decision]
    answer = server.search("answer")["results"][0]["id"]
    marker = f'Restatement of [{decision["locator"]}@{decision["source_version"]}]: repeat blue'
    correction = record(role="user", text=marker, ordinal=4)
    if change == "unpaired":
        correction = record(role="user", text="repeat blue", ordinal=4)
    elif change == "wrong_revision":
        correction = record(role="user", text=marker.replace(decision["source_version"], "sha256:" + "0" * 64), ordinal=4)
    elif change == "earlier":
        correction = record(role="user", text=marker, ordinal=2)
    elif change == "assistant":
        correction = record(role="assistant", text=marker, ordinal=4)
    elif change == "sibling":
        correction = base_record(role="user", text=marker, ordinal=4, session_id="omp:sibling")
    backend.records = [decision, correction]
    token = server.search("correction")["results"][1]["id"]
    with pytest.raises(ValueError):
        server.join_native_outcome(query["receipt_id"], answer, scope=SCOPE, restatement_id=token)


def test_binding_is_immutable_and_expired_query_cannot_join(factory):
    server, _, backend, clock = scoped(factory, [record(role="user")])
    query = server.search("query")
    with pytest.raises(ValueError):
        server.bind_workstream(SCOPE, native_session="omp:sibling", after_ordinal=0, through_ordinal=10)
    backend.records = [record(role="assistant", ordinal=5)]
    decision = server.search("answer")["results"][0]["id"]
    clock.now += 601
    with pytest.raises(ValueError):
        server.join_native_outcome(query["receipt_id"], decision, scope=SCOPE)


def test_bound_workstream_does_not_borrow_a_sibling_project_grant(factory):
    grants = [{"source": "agentsview", "collection": "history",
               "scope": project, "privacy": ["private"]}
              for project in ("project-A", "project-B")]
    server, _, backend, _ = factory(operator=grants, rows=[record(role="user", scope="project-B")])
    server.bind_workstream(SCOPE, native_session="omp:synthetic-session", after_ordinal=0, through_ordinal=10)
    result = server.search("sibling")
    assert result["results"] == []
    assert server.replay_outcomes(SCOPE)[0]["retrieval"] == "denied"
    assert all(g["scope"] == "project-A" for call in backend.search_calls for g in call[2])


@pytest.mark.parametrize("change", ["predates_query", "beyond_task_window", "unknown_time", "naive_time"])
def test_native_causality_and_finite_task_window_are_required(factory, change):
    server, _, backend, _ = scoped(factory, [record(role="user", ordinal=1)])
    # A matching citation in an old answer is not use of this later retrieval.
    server.wall_clock = lambda: (NATIVE_START + timedelta(seconds=5)).timestamp()
    query = server.search("later query")
    kwargs = {"role": "assistant", "ordinal": 2}
    if change == "beyond_task_window":
        kwargs["ordinal"] = 11
    elif change == "unknown_time":
        kwargs["date"] = "unknown"
    elif change == "naive_time":
        kwargs["date"] = "2026-10-23T12:00:06"
    evidence = record(role="user", ordinal=1)
    kwargs["text"] = f'Use [{evidence["locator"]}@{evidence["source_version"]}]'
    backend.records = [record(**kwargs)]
    decision = server.search("native answer")["results"][0]["id"]
    with pytest.raises(ValueError):
        server.join_native_outcome(query["receipt_id"], decision, scope=SCOPE)


@pytest.mark.parametrize("change", ["query_time", "task_window"])
def test_offline_replay_enforces_native_causality_and_task_window(factory, change):
    server, _, backend, _ = scoped(factory, [record(role="user", ordinal=1)])
    query = server.search("query")
    backend.records = [record(role="assistant", ordinal=2)]
    decision = server.search("native answer")["results"][0]["id"]
    joined = server.join_native_outcome(query["receipt_id"], decision, scope=SCOPE)
    rows = server._audit_rows()
    if change == "query_time":
        rows[0]["observed_at"] = joined["decision"]["observed_at"] + 1
    else:
        rows[0]["through_ordinal"] = joined["decision"]["ordinal"] - 1
    with pytest.raises(ValueError):
        offline_replay(rows, scope_hmac=joined["scope_hmac"], principal="operator")
