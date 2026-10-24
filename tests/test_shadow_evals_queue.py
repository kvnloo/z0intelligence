import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from z0int.shadow_evals_queue import (
    REPO, ShadowQueue, content_text, deterministic_triage, discover_jobs,
    ingest_sessions, job_identity, join_outcomes, read_session,
    verified_runtime_updates,
)

TS = "2026-10-24T12:00:00Z"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def user(entry_id, text, parent=None, attribution="user"):
    return {"type": "message", "id": entry_id, "parentId": parent, "timestamp": TS,
            "message": {"role": "user", "attribution": attribution,
                        "content": [{"type": "text", "text": text}]}}


def custom(entry_id, text, parent=None, **overrides):
    data = {"rawText": text, "sha256": digest(text), "extension_content_sha": "c" * 64,
            "ts": TS, "kind": "input", "source": "interactive", **overrides}
    return {"type": "custom", "customType": "z0-intent-input", "id": entry_id,
            "parentId": parent, "timestamp": TS, "data": data}


def session(path, entries, session_id="session-1"):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "title", "title": "Real session format"},
            {"type": "session", "version": 3, "id": session_id, "timestamp": TS,
             "cwd": "/fixture"}, *entries]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return path


def receipt(home, entry_id, text, tier="CHECK", **overrides):
    path = home / "intent/session-1/triage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"entry_id": entry_id, "sourceHash": digest(text), "tier": tier,
           "ts": TS, "slm_used": False, **overrides}
    with path.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    return path


def classify(requests):
    return [{"tier": "CHECK", "sourceHash": digest(row["text"]), "slm_used": False}
            for row in requests]


def job(text="Check the real result.", entry_id="u1"):
    sha = digest(text)
    return {"job_id": job_identity("session-1", entry_id, sha), "session_id": "session-1",
            "entry_id": entry_id, "sha256": sha, "tier": "CHECK", "ts": TS,
            "provenance": {"synthetic": False, "harness": "omp"},
            "payload": {"text": text, "recent_user_msgs": [], "recent_messages": []}}


def event(source, success=True, **overrides):
    return {"eventId": "stateUpdate-real", "type": "stateUpdate", "sourceHash": source["sha256"],
            "session_id": source["session_id"], "entry_id": source["entry_id"],
            "payload": {"verified": True, "success": success, "verifier": "command_exit_zero"},
            **overrides}


def test_exact_receipted_custom_input_without_transcript_turn(tmp_path):
    text = "/aodl  Preserve the exact 🛰️ bytes.\n"
    source = custom("input-1", text)
    path = session(tmp_path / "sessions/work/current.jsonl", [source])
    home = tmp_path / "intent-home"
    receipt_path = receipt(home, "input-1", text, "CONTRACT")
    before = path.read_bytes(), receipt_path.read_bytes()

    def forbidden(_):
        pytest.fail("An existing matching receipt must not invoke triage")

    jobs = discover_jobs(path, home, triage_runner=forbidden)
    assert len(jobs) == 1
    observed = jobs[0]
    assert observed["entry_id"] == "input-1"
    assert observed["payload"]["text"] == text
    assert observed["sha256"] == digest(text)
    assert observed["ts"] == TS
    assert observed["provenance"]["triage_source"] == "intent-service-receipt"
    assert observed["provenance"]["input_source"] == "intent-custom-entry"
    assert observed["provenance"]["extension_content_sha"] == "c" * 64
    assert observed["provenance"]["synthetic"] is False
    assert (path.read_bytes(), receipt_path.read_bytes()) == before


def test_dispatch_is_not_a_second_observation_and_recent_context_is_branch_local(tmp_path):
    entries = [user("prior", "Prior exact ask."),
               custom("input-1", "/aodl fix this", "prior"),
               user("dispatched", "fix this\n\n[Intent context — evidence, not approval]\nReviewed.", "input-1"),
               user("next", "Now inspect the result.", "dispatched"),
               user("fork", "Different branch.", "prior")]
    path = session(tmp_path / "current.jsonl", entries)
    jobs = discover_jobs(path, tmp_path / "home", triage_runner=classify)
    assert [row["entry_id"] for row in jobs] == ["prior", "input-1", "next", "fork"]
    assert jobs[2]["payload"]["recent_user_msgs"] == ["Prior exact ask.", "/aodl fix this"]
    assert jobs[3]["payload"]["recent_user_msgs"] == ["Prior exact ask."]


def test_unrelated_input_after_blocked_custom_is_not_discarded(tmp_path):
    path = session(tmp_path / "current.jsonl", [custom("input-1", "First ask."),
                                               user("u2", "A different actual ask.", "input-1")])
    jobs = discover_jobs(path, tmp_path / "home", triage_runner=classify)
    assert [row["entry_id"] for row in jobs] == ["input-1", "u2"]


@pytest.mark.parametrize("change", ["hash", "entry", "stamp", "receipt-tier"])
def test_custom_or_receipt_identity_mismatch_fails_closed(tmp_path, change):
    text = "Read my exact request."
    source = custom("input-1", text)
    home = tmp_path / "home"
    if change == "hash":
        source["data"]["sha256"] = "a" * 64
    if change == "stamp":
        source["data"]["extension_content_sha"] = "not-a-content-stamp"
    receipt(home, "input-1", text, sourceHash="b" * 64 if change == "entry" else digest(text),
            tier="UNKNOWN" if change == "receipt-tier" else "CHECK")
    path = session(tmp_path / "current.jsonl", [source])
    with pytest.raises(ValueError):
        discover_jobs(path, home, triage_runner=classify)


def test_missing_receipt_sidecar_source_is_explicit(tmp_path):
    path = session(tmp_path / "current.jsonl", [user("u1", "Check exact text.")])
    seen = []

    def triage(requests):
        seen.extend(requests)
        return classify(requests)

    jobs = discover_jobs(path, tmp_path / "home", triage_runner=triage)
    assert seen == [{"text": "Check exact text.", "open_contract": False, "has_images": False}]
    assert jobs[0]["provenance"]["triage_source"] == "sidecar-deterministic-js"
    assert jobs[0]["provenance"]["receipt_path"] is None
    assert not (tmp_path / "home").exists()


def test_sidecar_uses_image_and_open_contract_features(tmp_path):
    state = {"type": "custom", "customType": "z0-intent-state", "id": "state", "parentId": None,
             "data": {"contract": {"pending": True}}}
    source = user("u1", "Inspect the attached plan.", "state")
    source["message"]["content"].append({"type": "image", "data": "private-image"})
    path = session(tmp_path / "current.jsonl", [state, source])
    seen = []

    def triage(requests):
        seen.extend(requests)
        return classify(requests)

    discover_jobs(path, tmp_path / "home", triage_runner=triage)
    assert seen[0]["open_contract"] is True
    assert seen[0]["has_images"] is True


def test_fast_is_not_queued_and_bad_fallback_hash_is_rejected(tmp_path):
    path = session(tmp_path / "current.jsonl", [user("u1", "Hello")])
    assert discover_jobs(path, tmp_path / "home", triage_runner=lambda rows: [
        {"tier": "FAST", "sourceHash": digest(rows[0]["text"])}]) == []
    with pytest.raises(ValueError, match="identity"):
        discover_jobs(path, tmp_path / "home", triage_runner=lambda rows: [
            {"tier": "CONTRACT", "sourceHash": "a" * 64}])


def test_attribution_and_automated_notice_filters_retain_only_actual_user(tmp_path):
    entries = [user("agent", "Check this task.", attribution="agent"),
               user("worker", "You are a worker. Check this.", "agent"),
               user("budget", "[budget notice] Wrap up now.", "worker"),
               user("irc", '<irc from="peer">Check this.</irc>', "budget"),
               user("actual", '<system-reminder>Current time</system-reminder>\nCheck the exact ask.', "irc")]
    path = session(tmp_path / "current.jsonl", entries)
    jobs = discover_jobs(path, tmp_path / "home", triage_runner=classify)
    assert [row["entry_id"] for row in jobs] == ["actual"]
    assert jobs[0]["payload"]["text"] == entries[-1]["message"]["content"][0]["text"]


def test_text_and_input_text_blocks_preserve_order():
    assert content_text([{"type": "input_text", "text": "A"}, {"type": "image"},
                         {"type": "text", "text": "B"}]) == "A\nB"


def test_partial_tail_waits_for_complete_record_and_duplicate_ids_fail(tmp_path):
    path = session(tmp_path / "current.jsonl", [user("u1", "Check exact text.")])
    with path.open("a") as stream:
        stream.write('{"type":"message"')
    assert len(read_session(path)[1]) == 1
    path = session(path, [user("u1", "First"), user("u1", "Second")])
    with pytest.raises(ValueError, match="Duplicate"):
        read_session(path)


@pytest.mark.parametrize("parent", ["missing", "u1"])
def test_bad_parent_identity_fails_closed(tmp_path, parent):
    path = session(tmp_path / "current.jsonl", [user("u1", "Check result.", parent)])
    with pytest.raises(ValueError, match="parent"):
        discover_jobs(path, tmp_path / "home", triage_runner=classify)


def test_restart_create_finish_and_explicit_retry_are_idempotent(tmp_path):
    root = tmp_path / "queue"
    queue = ShadowQueue(root)
    source = job()
    assert queue.enqueue(source) is True
    assert ShadowQueue(root).enqueue(source) is False
    assert ShadowQueue(root).pending() == [source]
    blocked = {"job_id": source["job_id"], "status": "blocked", "reason": "no trustworthy offer"}
    queue.finish(source["job_id"], blocked)
    queue.finish(source["job_id"], blocked)
    assert ShadowQueue(root).pending() == []
    assert queue.requeue(source["job_id"]) is True
    assert queue.requeue(source["job_id"]) is False
    queue.finish(source["job_id"], {"status": "completed"})
    with pytest.raises(ValueError, match="blocked or error"):
        queue.requeue(source["job_id"])
    with pytest.raises(ValueError, match="changed"):
        queue.finish(source["job_id"], {"status": "error"})
    assert ShadowQueue(root).records()[0]["result"] == {"status": "completed"}


def test_concurrent_create_has_one_winner(tmp_path):
    root = tmp_path / "queue"
    ShadowQueue(root)

    def enqueue(_):
        return ShadowQueue(root).enqueue(job())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(enqueue, range(8)))
    assert results.count(True) == 1
    assert len(ShadowQueue(root).pending()) == 1


def test_existing_entry_cannot_change_bytes_or_forge_job_hash(tmp_path):
    queue = ShadowQueue(tmp_path / "queue")
    queue.enqueue(job())
    with pytest.raises(ValueError, match="identity changed"):
        queue.enqueue(job("A changed ask."))
    forged = job()
    forged["payload"]["text"] += " changed"
    with pytest.raises(ValueError, match="hash"):
        queue.enqueue(forged)
    forged = job()
    forged["job_id"] = "f" * 64
    with pytest.raises(ValueError, match="identity"):
        queue.enqueue(forged)
    with pytest.raises(ValueError, match="Unknown"):
        queue.finish("f" * 64, {"status": "error"})


def test_spool_private_permissions_and_no_stale_lock_reaping(tmp_path):
    root = tmp_path / "queue"
    root.mkdir(mode=0o777)
    lock = root / "worker.lock"
    lock.mkdir()
    marker = lock / "owner"
    marker.write_text("another process")
    before = marker.read_bytes()
    queue = ShadowQueue(root)
    queue.enqueue(job())
    assert root.stat().st_mode & 0o777 == 0o700
    assert queue.db_path.stat().st_mode & 0o777 == 0o600
    assert marker.read_bytes() == before
    with pytest.raises(ValueError):
        symlink = tmp_path / "linked"
        symlink.symlink_to(root, target_is_directory=True)
        ShadowQueue(symlink)


def test_database_symlink_is_not_followed(tmp_path):
    root = tmp_path / "queue"
    root.mkdir()
    target = tmp_path / "untouched"
    target.write_text("private")
    (root / "queue.sqlite3").symlink_to(target)
    with pytest.raises(OSError):
        ShadowQueue(root)
    assert target.read_text() == "private"


def test_outcome_requires_next_actual_human_and_never_labels_silence_success():
    source = job()
    entries = [user("u1", source["payload"]["text"]),
               user("agent", "I already asked!", "u1", attribution="agent")]
    outcome = join_outcomes(source, entries)
    assert outcome["label"] is None
    assert outcome["status"] == "pending"
    assert outcome["failure_signal"] is False
    entries.append(user("u2", "Thanks!", "agent"))
    outcome = join_outcomes(source, entries)
    assert outcome["label"] is None
    assert outcome["status"] == "unlabelled-next-turn"
    assert outcome["candidate_success"] is None


def test_next_correction_has_failure_signal_without_causal_or_counterfactual_claim():
    source = job()
    entries = [user("u1", source["payload"]["text"]),
               user("u2", "I already asked you to preserve the contract.", "u1"),
               user("u3", "Thanks.", "u2")]
    outcome = join_outcomes(source, entries)
    assert outcome["failure_signal"] is True
    assert outcome["kind"] == "correction_lexicon"
    assert outcome["label"] is None
    assert outcome["lexicon_matches"]
    assert outcome["next_entry_ids"] == ["u2"]
    assert outcome["evidence"][0]["entry_id"] == "u2"
    assert outcome["candidate_success"] is None
    assert outcome["synthetic"] is False


def test_later_or_sibling_correction_does_not_join_to_wrong_ask():
    source = job()
    entries = [user("u1", source["payload"]["text"]), user("u2", "Next unrelated ask.", "u1"),
               user("u3", "I already asked!", "u2"), user("sibling", "I told you before!")]
    outcome = join_outcomes(source, entries)
    assert outcome["failure_signal"] is False
    assert outcome["next_entry_ids"] == ["u2"]


def test_custom_dispatch_and_correction_have_one_next_human_turn():
    source = job("/aodl Check the real result.", "input-1")
    entries = [custom("input-1", source["payload"]["text"]),
               user("dispatch", "Check the real result.", "input-1"),
               custom("input-2", "I already asked you to check the result.", "dispatch"),
               user("dispatch-2", "I already asked you to check the result.", "input-2")]
    outcome = join_outcomes(source, entries)
    assert outcome["failure_signal"] is True
    assert outcome["next_entry_ids"] == ["input-2"]


@pytest.mark.parametrize("alteration", ["hash", "entry", "session", "unverified", "tool", "assistant"])
def test_unbound_unverified_or_noncanonical_success_remains_unlabelled(alteration):
    source = job()
    update = event(source)
    if alteration == "hash":
        update["sourceHash"] = "a" * 64
    elif alteration == "entry":
        update["entry_id"] = "other"
    elif alteration == "session":
        update["session_id"] = "other"
    elif alteration == "unverified":
        update["payload"]["verified"] = False
    elif alteration == "tool":
        update["type"] = "tool_result"
    else:
        update = {"type": "message", "message": {"role": "assistant", "content": "All done!"}}
    assert join_outcomes(source, [user("u1", source["payload"]["text"])], [update])["label"] is None


def test_exact_verified_aodl_success_and_conflict_handling():
    source = job()
    entries = [user("u1", source["payload"]["text"])]
    outcome = join_outcomes(source, entries, [event(source)])
    assert outcome["label"] == "success"
    assert outcome["kind"] == "aodl_verified"
    assert outcome["candidate_success"] is None
    outcome = join_outcomes(source, entries, [event(source), event(source, False, eventId="failed")])
    assert outcome["label"] is None
    assert outcome["status"] == "ambiguous"
    entries.append(user("u2", "I already asked for this.", "u1"))
    outcome = join_outcomes(source, entries, [event(source)])
    assert outcome["failure_signal"] is True
    assert outcome["label"] is None


def test_ingest_restart_refreshes_finished_job_outcomes_and_excludes_nested_agents(tmp_path):
    source = user("u1", "Check the real result.")
    root = tmp_path / "sessions"
    path = session(root / "work/current.jsonl", [source])
    session(root / "work/current/agents/worker.jsonl", [user("worker", "Check worker task.")])
    queue = ShadowQueue(tmp_path / "queue")
    home = tmp_path / "home"
    first = ingest_sessions(root, home, queue=queue, triage_runner=classify)
    assert first["sessions"] == 1
    assert first["enqueued"] == 1
    queue.finish(queue.pending()[0]["job_id"], {"status": "completed"})
    session(path, [source, user("u2", "I already asked you!", "u1")])
    second = ingest_sessions(root, home, queue=queue, triage_runner=classify)
    assert second["existing"] == 1
    assert second["enqueued"] == 1
    assert queue.records()[0]["outcome"]["failure_signal"] is True
    assert queue.records()[0]["result"] == {"status": "completed"}


def test_latest_only_and_discovery_report_do_not_write_inputs(tmp_path):
    root = tmp_path / "sessions"
    older = session(root / "one/older.jsonl", [user("old", "Check old ask.")], "older")
    latest = session(root / "two/latest.jsonl", [user("new", "Check new ask.")], "newer")
    os.utime(older, ns=(1, 1))
    os.utime(latest, ns=(2, 2))
    before = latest.read_bytes()
    report = ingest_sessions(root, tmp_path / "home", latest_only=True, triage_runner=classify)
    assert report["sessions"] == 1
    assert report["jobs"][0]["entry_id"] == "new"
    assert latest.read_bytes() == before
    assert not (tmp_path / "home").exists()


def test_ingest_errors_do_not_disclose_private_text(tmp_path):
    path = session(tmp_path / "current.jsonl", [custom("input-1", "PRIVATE secret ask", sha256="bad")])
    report = ingest_sessions(path, tmp_path / "home", triage_runner=classify)
    assert report["errors"] == [{"path": str(path), "error": "ValueError"}]
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runs existing deterministic triage")
def test_real_js_triage_is_used_without_models_or_receipt_writes(tmp_path):
    packet = tmp_path / "packet.txt"
    packet.write_text("https://github.com/test/project\n")
    rows = deterministic_triage([
        {"text": "What is a closure?", "open_contract": False, "has_images": False},
        {"text": "I already asked you to fix the live service.", "open_contract": False, "has_images": False},
    ], packet_path=packet)
    assert [row["tier"] for row in rows] == ["FAST", "CONTRACT"]
    assert all(row["slm_used"] is False for row in rows)
    assert rows[1]["sourceHash"] == digest("I already asked you to fix the live service.")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runs canonical AODL checker")
def test_runtime_success_requires_actual_causal_verifier_receipt_and_exact_approval(tmp_path):
    script = """import {createDocument, ingestUser, updateIntent, contractHash, verifyTask} from './omp-extensions/z0-relay-guard/checker.js';
const doc = createDocument('session-1');
const timestamp = new Date(Date.now() - 1000).toISOString();
const source = ingestUser(doc, {id:'u1',timestamp,message:{role:'user',content:'Check the real result.'}});
updateIntent(doc,{op:'add',taskId:'task.one',entryId:'u1',sentenceId:source.sentences[0].id,
 target:'fixture:result',acceptance:{type:'command_exit_zero',cmd:'exit 0',target:'fixture:result'}});
doc.plan.bindings.approval = {status:'approved', approved_by:'kevin', approved_at:new Date().toISOString(),
 sourceHash:doc.provenance.sourceHash, contractHash:contractHash(doc), contract_id:'contract:test',session_id:'session-1'};
await verifyTask(doc,'task.one');
console.log(JSON.stringify(doc));"""
    completed = subprocess.run(["node", "--input-type=module", "-e", script], cwd=REPO,
                               capture_output=True, text=True, check=True, timeout=30)
    document = json.loads(completed.stdout)
    source = job()
    updates = verified_runtime_updates(document, [source], tmp_path / "runtime.json")
    assert len(updates) == 1
    outcome = join_outcomes(source, [user("u1", source["payload"]["text"])], [updates[0][1]])
    assert outcome["label"] == "success"
    assert updates[0][1]["original_events"][0]["payload"]["kind"] == "verified"
    corrupted = copy.deepcopy(document)
    corrupted["eventLog"] = [event for event in corrupted["eventLog"] if event["payload"].get("kind") != "tool_result"]
    assert verified_runtime_updates(corrupted, [source], tmp_path / "runtime.json") == []
    pending = copy.deepcopy(document)
    pending["plan"]["bindings"]["approval"]["status"] = "pending"
    assert verified_runtime_updates(pending, [source], tmp_path / "runtime.json") == []
    altered = copy.deepcopy(source)
    altered["entry_id"] = "wrong-entry"
    assert verified_runtime_updates(document, [altered], tmp_path / "runtime.json") == []
