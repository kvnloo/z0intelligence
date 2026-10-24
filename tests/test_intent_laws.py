"""Synthetic I0-I4 boundaries: independent Python model versus the JS checker.

Fixtures contain no private transcripts. Execution receipts are synthetic test data,
not claims about a real command. The Python model checks the controlled fixture
vocabulary, not the full AODL schema. Run only in the parent's MBP verification job.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "omp-extensions/z0-relay-guard/checker.js"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _contract_hash(doc):
    value = deepcopy(doc)
    value.pop("eventLog", None)
    value.pop("revision", None)
    bindings = value["plan"]["bindings"]
    bindings.pop("approval", None)
    for node in value["intentGraph"]["nodes"]:
        node.pop("lifecycle", None)
        if node["kind"] == "task":
            for key in ("status", "reason", "delegate"):
                bindings[node["id"]].pop(key, None)
    return _hash(value)


def _time(value):
    if isinstance(value, (int, float)):
        return value / 1000
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return float("nan")


def python_check(doc, options):
    """Independent law-set oracle for canonical, single-source synthetic fixtures."""
    laws, opened = set(), []
    b, events = doc["plan"]["bindings"], doc["eventLog"]
    tasks = {node["id"]: b[node["id"]] for node in doc["intentGraph"]["nodes"] if node["kind"] == "task"}
    now = datetime.now(timezone.utc).timestamp()
    if options.get("mutating") and options.get("requiresContract", True):
        approval = b.get("approval", {})
        if not (approval.get("status") == "approved" and approval.get("approved_by") == "kevin"
                and _time(approval.get("approved_at")) <= now
                and approval.get("sourceHash") == doc["provenance"]["sourceHash"]
                and approval.get("contractHash") == _contract_hash(doc)
                and approval.get("contract_id")
                and approval.get("session_id") == options.get("sessionId", b["sessionId"])):
            laws.add("I0")
    # Fixture source is immutable. Classification and exact task linkage remain independent.
    for entry_id, source in b["sources"].items():
        for sentence in source["sentences"]:
            linked = [task for task in tasks.values() if task["entryId"] == entry_id and task["sentenceId"] == sentence["id"]]
            if sentence["classification"] == "add":
                if len(linked) != 1:
                    laws.add("I1")
            elif sentence["classification"] not in ("drop", "not-an-ask") or not sentence.get("reason") or linked:
                laws.add("I1")
    seen = set()
    previous_revision = -1
    for event in events:
        if event["eventId"] in seen or not previous_revision < event["revision"] <= doc["revision"] or any(parent not in seen for parent in event["causalParents"]):
            laws.add("I2")
        seen.add(event["eventId"])
        previous_revision = event["revision"]
    for task_id, task in tasks.items():
        verifier = b[task["acceptance"]]
        additions = [event["payload"] for event in events if event["type"] == "addNode" and event["payload"].get("kind") == "intent_add" and event["payload"].get("taskId") == task_id]
        if not additions or additions[0]["target"] != task["target"] or additions[0]["verifierId"] != task["acceptance"] or additions[0]["verifier"] != verifier or verifier["target"] != task["target"]:
            laws.add("I3")
        if task["status"] == "dropped":
            if not task.get("reason", "").strip() or not any(item["type"] == "stateUpdate" and item["payload"].get("kind") == "drop" and item["payload"].get("taskId") == task_id and item["payload"].get("reason") == task.get("reason") for item in events):
                laws.add("I1")
        elif task["status"] != "verified":
            opened.append(task_id)
            delegate = task.get("delegate", {})
            history = [item for item in events if item["type"] == "stateUpdate" and item["payload"].get("kind") in ("delegate", "delegate_result") and item["payload"].get("taskId") == task_id]
            latest = history[-1]["payload"] if history else {}
            live = task["status"] == "delegated" and delegate.get("live") is True and delegate.get("resultReceived") is False and latest.get("kind") == "delegate" and latest.get("delegateId") == delegate.get("id") and latest.get("live") is True
            if options.get("turnEnd") and not live:
                laws.add("I1")
        else:
            verified = [event for event in events if event["payload"].get("kind") == "verified" and event["payload"].get("taskId") == task_id]
            if not verified:
                laws.add("I2")
                continue
            event, p = verified[-1], verified[-1]["payload"]
            if not (event["type"] == "stateUpdate" and p.get("verified") is True and p.get("passed") is True and p.get("verifierId") == task["acceptance"]):
                laws.add("I2")
            if p.get("target") != task["target"] or verifier.get("target") != task["target"]:
                laws.add("I3")
            results = [item for item in events if item["type"] == "stateUpdate" and item["payload"].get("kind") == "tool_result" and item["payload"].get("toolCallId") == p.get("toolCallId")]
            if not p.get("toolCallId") or len(results) != 1:
                laws.add("I2")
                continue
            execution, r = results[0], results[0]["payload"]
            if not (r.get("taskId") == task_id and r.get("verifierId") == task["acceptance"] and r.get("toolName") == "intent_verifier:" + verifier["type"] and r.get("executed") is True and r.get("passed") is True and r.get("exitCode") == 0 and r.get("isError") is False):
                laws.add("I2")
            if r.get("target") != task["target"]:
                laws.add("I3")
            if not (_time(task["askedAt"]) < _time(r.get("startedAt")) <= _time(r.get("finishedAt")) <= now and r.get("startedAt") == p.get("startedAt") and r.get("finishedAt") == p.get("finishedAt")):
                laws.add("I2")
            if events.index(execution) >= events.index(event) or execution["eventId"] not in event["causalParents"]:
                laws.add("I2")
            if execution["sourceHash"] != event["sourceHash"] or event["sourceHash"] != doc["provenance"]["sourceHash"] or execution["revision"] >= event["revision"]:
                laws.add("I2")
            if any(item is not event and item["payload"].get("kind") == "verified" and item["payload"].get("toolCallId") == p.get("toolCallId") for item in events):
                laws.add("I2")
            if "delegate" in task:
                delegate = task["delegate"]
                delegations = [item for item in events if item["type"] == "stateUpdate" and item["payload"].get("kind") == "delegate" and item["payload"].get("taskId") == task_id]
                delegation = delegations[-1] if delegations else None
                received = delegation is not None and any(item["type"] == "stateUpdate" and item["payload"].get("kind") == "delegate_result" and item["payload"].get("taskId") == task_id and item["payload"].get("delegateId") == delegate["id"] and item["payload"].get("result") is not None and _time(delegation["payload"].get("timestamp")) <= _time(item["payload"].get("timestamp")) <= _time(r.get("startedAt")) and events.index(delegation) < events.index(item) < events.index(execution) for item in events)
                if not delegate.get("resultReceived") or not received or delegation["payload"].get("delegateId") != delegate["id"]:
                    laws.add("I2")
    if any(task_id not in tasks or tasks[task_id]["status"] != "verified" for task_id in options.get("claims", [])):
        laws.add("I2")
    carried = options.get("relayTaskIds", [])
    if any(task_id not in tasks for task_id in carried) or any(task_id not in tasks or task_id not in carried for task_id in options.get("forwardedTaskIds", [])):
        laws.add("I4")
    return {"ok": not laws, "laws": sorted(laws), "openTasks": opened}


# One Node process creates all canonical fixtures and checks them. No tools execute.
FIXTURES_JS = r'''
const C = await import(process.argv[1]);
const baseTime = '2026-01-01T00:00:00.000Z';
const askTime = '2025-12-31T23:59:59.000Z';
const later = '2026-01-01T00:00:01.000Z';
const rows = [];
function base() {
  const doc = C.createDocument('synthetic-intent-laws');
  const source = C.ingestUser(doc, {id:'user:1',timestamp:askTime,message:{role:'user',content:'Write the artifact.'}});
  C.updateIntent(doc,{op:'add',taskId:'task:1',entryId:'user:1',sentenceId:source.sentences[0].id,target:'/tmp/intent-laws/artifact',acceptance:{type:'command_exit_zero',cmd:'true',target:'/tmp/intent-laws/artifact'}});
  return doc;
}
function verify(doc, delegated=false) {
  const task = doc.plan.bindings['task:1'];
  if (delegated) {
    C.registerDelegate(doc,'task:1','worker:1');
    doc.eventLog.find(e=>e.payload.kind==='delegate').payload.timestamp=baseTime;
    C.registerDelegateResult(doc,'task:1','worker:1',{artifact:'synthetic'});
    doc.eventLog.find(e=>e.payload.kind==='delegate_result').payload.timestamp=baseTime;
  }
  const p={kind:'tool_result',taskId:'task:1',verifierId:task.acceptance,toolCallId:'synthetic-tool:1',toolName:'intent_verifier:command_exit_zero',executed:true,passed:true,exitCode:0,isError:false,target:task.target,startedAt:baseTime,finishedAt:later};
  const result=C.appendEvent(doc,'stateUpdate',p);
  C.appendEvent(doc,'stateUpdate',{kind:'verified',taskId:'task:1',verifierId:task.acceptance,toolCallId:p.toolCallId,verified:true,passed:true,target:task.target,startedAt:p.startedAt,finishedAt:p.finishedAt},[result.eventId]);
  task.status='verified';
  return doc;
}
const task=d=>d.plan.bindings['task:1'];
const result=d=>d.eventLog.find(e=>e.payload.kind==='tool_result');
const receipt=d=>d.eventLog.find(e=>e.payload.kind==='verified');
function add(name, factory, change, options={}, expected=[]) {
  const doc=factory(); if(change) change(doc);
  rows.push({name,doc,options,expected,result:C.check(doc,options)});
}
add('open work continues',base,null);
add('open work blocks turn',base,null,{turnEnd:true},['I1']);
add('unverified completion claim',base,null,{claims:['task:1']},['I2']);
add('unknown completion claim',base,null,{claims:['task:absent']},['I2']);
add('live delegation permits turn',base,d=>C.registerDelegate(d,'task:1','worker:1'),{turnEnd:true});
add('dead delegation blocks turn',base,d=>C.registerDelegate(d,'task:1','worker:1',{live:false}),{turnEnd:true},['I1']);
add('reasoned drop permits turn',base,d=>C.updateIntent(d,{op:'drop',taskId:'task:1',reason:'Kevin removed this ask'}),{turnEnd:true});
add('empty drop reason blocks turn',base,d=>{task(d).status='dropped';task(d).reason='';},{turnEnd:true},['I1']);
add('passing verified claim',()=>verify(base()),null,{claims:['task:1'],turnEnd:true});
add('verified state without update',base,d=>{task(d).status='verified';},{claims:['task:1']},['I2']);
add('verified mark false',()=>verify(base()),d=>{receipt(d).payload.verified=false;},{claims:['task:1']},['I2']);
add('verifier pass false',()=>verify(base()),d=>{receipt(d).payload.passed=false;},{claims:['task:1']},['I2']);
add('tool did not execute',()=>verify(base()),d=>{result(d).payload.executed=false;},{claims:['task:1']},['I2']);
add('tool failed',()=>verify(base()),d=>{result(d).payload.exitCode=1;},{claims:['task:1']},['I2']);
add('tool error flag',()=>verify(base()),d=>{result(d).payload.isError=true;},{claims:['task:1']},['I2']);
add('receipt before ask',()=>verify(base()),d=>{result(d).payload.startedAt=receipt(d).payload.startedAt='2025-12-31T23:59:58.999Z';},{claims:['task:1']},['I2']);
add('receipt exactly at ask',()=>verify(base()),d=>{result(d).payload.startedAt=receipt(d).payload.startedAt=askTime;},{claims:['task:1']},['I2']);
add('receipt one millisecond after ask',()=>verify(base()),d=>{result(d).payload.startedAt=receipt(d).payload.startedAt='2025-12-31T23:59:59.001Z';},{claims:['task:1']});
add('finish before start',()=>verify(base()),d=>{result(d).payload.finishedAt=receipt(d).payload.finishedAt='2025-12-31T23:59:59.999Z';},{claims:['task:1']},['I2']);
add('equal timestamp boundary',()=>verify(base()),d=>{result(d).payload.finishedAt=receipt(d).payload.finishedAt=baseTime;},{claims:['task:1']});
add('future receipt',()=>verify(base()),d=>{result(d).payload.finishedAt=receipt(d).payload.finishedAt='9999-01-01T00:00:00.000Z';},{claims:['task:1']},['I2']);
add('receipt time differs from execution',()=>verify(base()),d=>{receipt(d).payload.startedAt=later;},{claims:['task:1']},['I2']);
add('missing causal execution parent',()=>verify(base()),d=>{receipt(d).causalParents=[];},{claims:['task:1']},['I2']);
add('execution after verified event',()=>verify(base()),d=>{const e=d.eventLog.pop();d.eventLog.splice(d.eventLog.length-1,0,e);},{claims:['task:1']},['I2']);
add('execution ID reused',()=>verify(base()),d=>C.appendEvent(d,'stateUpdate',{...result(d).payload}),{claims:['task:1']},['I2']);
add('receipt source changed',()=>verify(base()),d=>{receipt(d).sourceHash='f'.repeat(64);},{claims:['task:1']},['I2']);
add('receipt revision not after execution',()=>verify(base()),d=>{receipt(d).revision=result(d).revision;},{claims:['task:1']},['I2']);
add('another result target',()=>verify(base()),d=>{result(d).payload.target+='.other';},{claims:['task:1']},['I3']);
add('another verified target',()=>verify(base()),d=>{receipt(d).payload.target+='.other';},{claims:['task:1']},['I3']);
add('target mutated after add',()=>verify(base()),d=>{task(d).target+='.other';},{claims:['task:1']},['I3']);
add('acceptance mutated after add',()=>verify(base()),d=>{d.plan.bindings[task(d).acceptance].cmd='false';},{claims:['task:1']},['I3']);
add('delegated result precedes execution',()=>verify(base(),true),null,{claims:['task:1']});
add('delegated result missing',()=>verify(base(),true),d=>{task(d).delegate.resultReceived=false;},{claims:['task:1']},['I2']);
add('delegated result late',()=>verify(base(),true),d=>{d.eventLog.find(e=>e.payload.kind==='delegate_result').payload.timestamp=later;},{claims:['task:1']},['I2']);
add('delegated result event after execution',()=>verify(base(),true),d=>{const i=d.eventLog.findIndex(e=>e.payload.kind==='delegate_result');const [e]=d.eventLog.splice(i,1);d.eventLog.push(e);},{claims:['task:1']},['I2']);
add('delegated result predates delegation',()=>verify(base(),true),d=>{d.eventLog.find(e=>e.payload.kind==='delegate_result').payload.timestamp='2025-12-31T23:59:59.999Z';},{claims:['task:1']},['I2']);
add('prior worker result reused after redelegation',()=>verify(base(),true),d=>{const e=d.eventLog.find(e=>e.payload.kind==='delegate');const clone=structuredClone(e);clone.eventId='event:synthetic-redelegation';const i=d.eventLog.findIndex(e=>e.payload.kind==='tool_result');d.eventLog.splice(i,0,clone);d.eventLog.forEach((e,i)=>{e.revision=i+1;});d.revision=d.eventLog.length;},{claims:['task:1']},['I2']);
add('drop reason without runtime update',base,d=>{task(d).status='dropped';task(d).reason='Removed';},{turnEnd:true},['I1']);
add('finished delegate cannot be made live by binding',base,d=>{C.registerDelegate(d,'task:1','worker:1');C.registerDelegateResult(d,'task:1','worker:1',{artifact:'synthetic'});task(d).delegate.live=true;task(d).delegate.resultReceived=false;},{turnEnd:true},['I1']);
add('carried covers forwarded',base,null,{relayTaskIds:['task:1'],forwardedTaskIds:['task:1']});
add('forwarded ID not carried',base,null,{relayTaskIds:[],forwardedTaskIds:['task:1']},['I4']);
add('unknown carried ID',base,null,{relayTaskIds:['task:absent']},['I4']);
add('unknown forwarded ID',base,null,{relayTaskIds:['task:1'],forwardedTaskIds:['task:absent']},['I4']);
add('duplicate carried IDs allowed',base,null,{relayTaskIds:['task:1','task:1'],forwardedTaskIds:['task:1']});
function approve(d) {d.plan.bindings.approval={status:'approved',approved_by:'kevin',approved_at:later,sourceHash:d.provenance.sourceHash,contractHash:C.contractHash(d),contract_id:'synthetic-contract:1',session_id:d.plan.bindings.sessionId};}
add('Q&A without approval',base,null,{mutating:false});
add('mutation without approval',base,null,{mutating:true},['I0']);
add('mutation with exact Kevin approval',base,approve,{mutating:true});
add('approval from another actor',base,d=>{approve(d);d.plan.bindings.approval.approved_by='agent';},{mutating:true},['I0']);
add('approval for another session',base,approve,{mutating:true,sessionId:'other-session'},['I0']);
add('approval for another source',base,d=>{approve(d);d.plan.bindings.approval.sourceHash='f'.repeat(64);},{mutating:true},['I0']);
add('approval for another contract',base,d=>{approve(d);d.plan.bindings.approval.contractHash='f'.repeat(64);},{mutating:true},['I0']);
add('future approval',base,d=>{approve(d);d.plan.bindings.approval.approved_at='9999-01-01T00:00:00.000Z';},{mutating:true},['I0']);
console.log(JSON.stringify(rows));
'''


@pytest.fixture(scope="module")
def parity_rows():
    node = shutil.which("node")
    assert node, "Node is required for Python/JS intent-checker parity on the MBP"
    process = subprocess.run([node, "--input-type=module", "-e", FIXTURES_JS, CHECKER.as_uri()], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(process.stdout)


def test_python_js_checker_parity_boundaries(parity_rows):
    for row in parity_rows:
        python = python_check(row["doc"], row["options"])
        javascript = {"ok": row["result"]["ok"], "laws": sorted({issue["law"] for issue in row["result"]["violations"]}), "openTasks": row["result"]["openTasks"]}
        assert python == javascript, (row["name"], python, javascript)
        assert javascript["laws"] == row["expected"], row["name"]
        assert javascript["ok"] == (not row["expected"]), row["name"]


def test_boundary_corpus_covers_every_law_and_success(parity_rows):
    assert {law for row in parity_rows for law in row["expected"]} == {"I0", "I1", "I2", "I3", "I4"}
    assert len(parity_rows) >= 40
    for law in ("I0", "I1", "I2", "I3", "I4"):
        assert sum(law in row["expected"] for row in parity_rows) >= 2
    assert any(not row["expected"] and row["options"].get("claims") for row in parity_rows)
