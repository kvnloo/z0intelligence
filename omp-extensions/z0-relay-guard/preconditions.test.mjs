import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createDocument, ingestUser, updateIntent, verifyPrecondition, verifyTask, check, contractHash, registerHumanApproval } from "./checker.js";
import { createRunner } from "./verifiers.js";

function fixture(preconditions = [{ type: "command_exit_zero", cmd: "exit 0", target: "service:prerequisite", claim: "Claimed service is present; execution must observe it" }]) {
  const doc = createDocument("prerequisite:session");
  const timestamp = new Date(Date.now() - 1000).toISOString();
  const source = ingestUser(doc, { id: "ask", timestamp, message: { role: "user", content: "Check the artifact after observing its dependencies." } });
  const task = updateIntent(doc, { op: "add", taskId: "task.one", entryId: "ask", sentenceId: source.sentences[0].id, target: "artifact:result", acceptance: { type: "command_exit_zero", cmd: "exit 0", target: "artifact:result" }, preconditions });
  doc.plan.bindings.approval = { status: "approved", approved_by: "kevin", approved_at: new Date().toISOString(), sourceHash: doc.provenance.sourceHash, contractHash: contractHash(doc), contract_id: "prerequisite.contract", session_id: "prerequisite:session" };
  return { doc, task, id: task.preconditions?.[0] };
}
const mutation = { mutating: true, requiresContract: true };
const has = (doc, law, options = mutation) => check(doc, options).violations.some(issue => issue.law === law);
const receipt = (target, extra = {}) => ({ executed: true, passed: true, exitCode: 0, target, toolCallId: `synthetic:${randomUUID()}`, startedAt: new Date().toISOString(), finishedAt: new Date().toISOString(), ...extra });

test("compiler preserves claim metadata and fixes each prerequisite's independent target and verifier node", () => {
  const { doc, task, id } = fixture();
  assert.deepEqual(task.preconditions, ["task.one.pre.0"]);
  assert.equal(doc.plan.bindings[id].target, "service:prerequisite");
  assert.notEqual(doc.plan.bindings[id].target, task.target);
  assert.match(doc.plan.bindings[id].claim, /execution must observe/);
  assert.equal(doc.intentGraph.nodes.find(n => n.id === id).kind, "verifier");
  assert.ok(doc.intentGraph.edges.some(e => e.from === id && e.to === "task.one"));
  assert.equal(check(doc).ok, true);
  assert.throws(() => updateIntent(doc, { op: "drop", taskId: "task.one", reason: "Drop", preconditions: [] }), /immutable/);
});

test("claimed or unknown prerequisite evidence never permits contracted mutation", () => {
  for (const state of ["claimed", "unknown", "observed"]) {
    const { doc } = fixture([{ type: "command_exit_zero", cmd: "exit 0", target: "dependency:claimed", evidence_state: state }]);
    assert.equal(has(doc, "I0"), true);
    assert.equal(has(doc, "I0", { mutating: true, requiresContract: false }), false);
    assert.equal(check(doc).ok, true);
  }
});

test("prerequisite verification exemption preserves contract approval requirements", () => {
  const { doc } = fixture();
  assert.equal(check(doc, { ...mutation, verifyPrecondition: true }).ok, true);
  delete doc.plan.bindings.approval;
  assert.equal(has(doc, "I0", { ...mutation, verifyPrecondition: true }), true);
});

test("actual passing prerequisite creates causal receipt but never closes task or licenses done claim", async () => {
  const { doc, task, id } = fixture();
  const originalHash = contractHash(doc);
  const result = await verifyPrecondition(doc, "task.one", id);
  assert.equal(result.verified, true);
  assert.equal(result.verifierId, id);
  assert.equal(task.status, "open");
  assert.equal(doc.intentGraph.nodes.find(n => n.id === "task.one").lifecycle, "declared");
  assert.equal(result.receipt.payload.kind, "precondition_verified");
  assert.equal(result.receipt.payload.target, "service:prerequisite");
  const execution = doc.eventLog.find(e => e.payload.toolCallId === result.receipt.payload.toolCallId && e.payload.kind === "tool_result");
  assert.equal(execution.payload.verifierId, id);
  assert.equal(execution.payload.prerequisite, true);
  assert.ok(result.receipt.causalParents.includes(execution.eventId));
  assert.equal(check(doc, mutation).ok, true);
  assert.equal(contractHash(doc), originalHash);
  assert.equal(has(doc, "I2", { claims: ["task.one"] }), true);
  assert.equal(has(doc, "I1", { turnEnd: true }), true);
  assert.equal(doc.eventLog.some(e => e.payload.kind === "verified"), false);
  assert.equal((await verifyTask(doc, "task.one")).verified, true);
  assert.equal(check(doc, { ...mutation, claims: ["task.one"], turnEnd: true }).ok, true);
});

test("all prerequisites must pass before mutation or task completion", async () => {
  const { doc, task } = fixture([
    { type: "command_exit_zero", cmd: "exit 0", target: "dependency:first" },
    { type: "command_exit_zero", cmd: "exit 0", target: "dependency:second" },
  ]);
  await assert.rejects(verifyTask(doc, "task.one"), /unverified precondition/);
  await verifyPrecondition(doc, "task.one", task.preconditions[0]);
  assert.equal(has(doc, "I0"), true);
  await assert.rejects(verifyTask(doc, "task.one"), /unverified precondition/);
  await verifyPrecondition(doc, "task.one", task.preconditions[1]);
  assert.equal(check(doc, mutation).ok, true);
  assert.equal((await verifyTask(doc, "task.one")).verified, true);
});

test("failed real prerequisite cannot emit successful observation or close task", async () => {
  const { doc, task, id } = fixture([{ type: "command_exit_zero", cmd: "exit 4", target: "dependency:missing" }]);
  const result = await verifyPrecondition(doc, "task.one", id);
  assert.equal(result.verified, false);
  assert.equal(result.receipt.payload.exitCode, 4);
  assert.equal(task.status, "open");
  assert.equal(doc.eventLog.some(e => e.payload.kind === "precondition_verified"), false);
  assert.equal(has(doc, "I0"), true);
});

test("real file observation changes unknown to proven without trusting source assertion", async t => {
  const directory = await mkdtemp(join(tmpdir(), "precondition-file-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, ".env");
  const { doc, id } = fixture([{ type: "file_contains", path, pattern: "CONFIG_READY=1", target: path, claim: "Unknown whether the actual env file exists" }]);
  assert.equal((await verifyPrecondition(doc, "task.one", id)).verified, false);
  assert.equal(has(doc, "I0"), true);
  await writeFile(path, "CONFIG_READY=1\n");
  assert.equal((await verifyPrecondition(doc, "task.one", id)).verified, true);
  assert.equal(check(doc, mutation).ok, true);
});

test("prerequisite runner enforces strict after-ask time, exact target and actual execution", async () => {
  for (const mutate of [
    (r, task) => ({ ...r, startedAt: task.askedAt }),
    r => ({ ...r, startedAt: new Date(0).toISOString() }),
    r => ({ ...r, target: "dependency:other" }),
    r => ({ ...r, executed: false }),
  ]) {
    const { doc, task, id } = fixture();
    await assert.rejects(verifyPrecondition(doc, "task.one", id, v => mutate(receipt(v.target), task)));
    assert.equal(task.status, "open");
    assert.equal(doc.eventLog.some(e => e.payload.kind === "precondition_verified"), false);
  }
  const { doc } = fixture();
  await assert.rejects(verifyPrecondition(doc, "task.one", "task.one.acceptance"), /fixed precondition/);
  await assert.rejects(verifyPrecondition(doc, "task.one", "invented"), /fixed precondition/);
});

test("tampered prerequisite identity, source, causal receipt and tool pass block mutation", async () => {
  const { doc, id } = fixture(); await verifyPrecondition(doc, "task.one", id);
  for (const mutate of [
    copy => { copy.eventLog.find(e => e.payload.kind === "precondition_verified").payload.target = "other"; },
    copy => { copy.eventLog.find(e => e.payload.kind === "precondition_verified").causalParents = []; },
    copy => { copy.eventLog.find(e => e.payload.kind === "precondition_verified").sourceHash = "0".repeat(64); },
    copy => { copy.eventLog.find(e => e.payload.kind === "precondition_verified").payload.prerequisite = false; },
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").payload.verifierId = "task.one.acceptance"; },
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").payload.target = "artifact:result"; },
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").payload.exitCode = 2; },
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").payload.prerequisite = false; },
  ]) {
    const copy = structuredClone(doc); mutate(copy);
    assert.equal(check(copy, mutation).ok, false);
    assert.equal(has(copy, "I0"), true);
  }
});

test("later failed observation invalidates earlier passing prerequisite until a fresh pass", async () => {
  const { doc, id } = fixture(); await verifyPrecondition(doc, "task.one", id);
  assert.equal(check(doc, mutation).ok, true);
  await verifyPrecondition(doc, "task.one", id, v => receipt(v.target, { passed: false, exitCode: 1 }));
  assert.equal(has(doc, "I0"), true);
  await assert.rejects(verifyTask(doc, "task.one"), /unverified precondition/);
  await verifyPrecondition(doc, "task.one", id);
  assert.equal(check(doc, mutation).ok, true);
});

test("prerequisite verifier binding, claim and task references cannot change after add", () => {
  const { doc, id } = fixture();
  for (const mutate of [
    copy => { copy.plan.bindings[id].target = "other"; },
    copy => { copy.plan.bindings[id].cmd = "echo changed"; },
    copy => { copy.plan.bindings[id].claim = "Replaced original evidence claim"; },
    copy => { copy.plan.bindings["task.one"].preconditions = []; },
    copy => { copy.plan.bindings["task.one"].preconditions.push(id); },
    copy => { copy.intentGraph.nodes.find(n => n.id === id).kind = "tool"; },
  ]) { const copy = structuredClone(doc); mutate(copy); assert.equal(has(copy, "I3"), true); assert.equal(has(copy, "I0"), true); }
});

test("human prerequisite needs explicit user approval of prerequisite target, not task outcome", async () => {
  const { doc, task, id } = fixture([{ type: "human_review", target: "dependency:human-observed" }]);
  const timestamp = new Date().toISOString();
  const source = registerHumanApproval(doc, { id: "human-observation", timestamp, message: { role: "user", content: "APPROVE dependency:human-observed" } });
  const runner = createRunner({ humanReview: async target => ({ approved: true, actor: "user", target, entryId: "human-observation", sentenceId: source.payload.sentences[0].id }) });
  assert.equal((await verifyPrecondition(doc, "task.one", id, runner)).verified, true);
  assert.equal(task.status, "open");
  assert.equal(check(doc, mutation).ok, true);
});

test("one tool execution cannot be reused as prerequisite and task completion", async () => {
  const { doc, id } = fixture(); const result = await verifyPrecondition(doc, "task.one", id);
  await assert.rejects(verifyTask(doc, "task.one", v => receipt(v.target, { toolCallId: result.receipt.payload.toolCallId })), /already used/);
  assert.equal(doc.plan.bindings["task.one"].status, "open");
});
