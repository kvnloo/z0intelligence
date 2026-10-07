import json

from z0int.receipt import Outcome, append_receipt, close_turn, find_receipt, find_receipt_by_extra, join_outcome


def write_stream(root, name, rows):
    path = root / "stream" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_canonical_receipt_survives_stale_stream_and_close(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    original = {"trace_id": "reuse-trace", "ts": 1, "extra": {"stage": "opened"}}
    append_receipt(original, root=tmp_path)
    write_stream(tmp_path, "bridge.jsonl", [{"trace_id": "reuse-trace", "receipt": original}])
    enriched = {"trace_id": "reuse-trace", "ts": 2, "extra": {"stage": "checked", "mutation_event_id": 7}}
    append_receipt(enriched, root=tmp_path)

    assert find_receipt("reuse-trace", root=tmp_path)["extra"] == enriched["extra"]
    result = close_turn(
        "reuse-trace", root=tmp_path,
        outcome=Outcome(execution_completed=True, verified_success=True, verification_source="owning-tests:result"),
    )
    assert result["receipt"]["extra"] == enriched["extra"]
    assert result["outcome_join"]["receipt"]["extra"] == enriched["extra"]
    current = find_receipt("reuse-trace", root=tmp_path)
    assert current["outcome"]["verified_success"] is True
    assert current["outcome_tier"] == "gold"


def test_extra_lookup_prefers_canonical_append_order(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    for ts, stage in ((20, "prepared"), (10, "checked")):
        append_receipt({"trace_id": "reuse-trace", "ts": ts, "extra": {"packet_id": "packet", "stage": stage}}, root=tmp_path)
    write_stream(tmp_path, "raw.jsonl", [{"trace_id": "reuse-trace", "ts": 999, "extra": {"packet_id": "packet", "stage": "stale"}}])
    assert find_receipt_by_extra("packet_id", "packet", root=tmp_path)["extra"]["stage"] == "checked"


def test_explicit_root_does_not_read_ambient_streams(tmp_path, monkeypatch):
    ambient, requested = tmp_path / "ambient", tmp_path / "requested"
    monkeypatch.setenv("Z0INT_HOME", str(ambient))
    write_stream(ambient, "bridge.jsonl", [{"trace_id": "reuse-trace", "extra": {"packet_id": "packet"}}])
    assert find_receipt("reuse-trace", root=requested) is None
    assert find_receipt_by_extra("packet_id", "packet", root=requested) is None


def test_legacy_nested_receipt_remains_available_in_requested_root(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "ambient"))
    requested = tmp_path / "requested"
    nested = {"trace_id": "legacy", "extra": {"packet_id": "packet"}, "provider": "legacy-provider"}
    write_stream(requested, "bridge.jsonl", [{"trace_id": "legacy", "receipt": nested}])
    assert find_receipt("legacy", root=requested)["provider"] == "legacy-provider"
    assert find_receipt_by_extra("packet_id", "packet", root=requested) == nested


def test_independent_verification_after_ambient_close_keeps_reuse_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    extra = {"reuse": {"mutation_check_event_ids": [7], "verifier_binding": {"verifier_id": "owning-tests"}}}
    append_receipt({"trace_id": "reuse-trace", "extra": extra}, root=tmp_path)
    close_turn("reuse-trace", root=tmp_path, outcome=Outcome(execution_completed=True, source="bridge_turn_end"))
    assert find_receipt("reuse-trace", root=tmp_path)["outcome_tier"] == "execution"

    result = join_outcome(
        "reuse-trace", Outcome(execution_completed=True, verified_success=True,
                               verification_source="owning-tests:independent-result", source="independent-verifier"),
        root=tmp_path,
    )
    assert result["outcome_tier"] == "gold"
    current = find_receipt("reuse-trace", root=tmp_path)
    assert current["extra"] == extra
    assert current["outcome"]["verification_source"] == "owning-tests:independent-result"
