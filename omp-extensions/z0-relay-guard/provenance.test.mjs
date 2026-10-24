import test from "node:test";
import assert from "node:assert/strict";
import { createDocument, ingestUser, updateIntent, check, verifyTask, registerDelegate, registerDelegateResult, registerHumanApproval } from "./checker.js";
import { createRunner } from "./verifiers.js";

function setup(type = "command_exit_zero") {
  const doc = createDocument("provenance");
  const timestamp = new Date(Date.now() - 1000).toISOString();
  const source = ingestUser(doc, { id: "ask", timestamp, message: { role: "user", content: "Check the result." } });
  updateIntent(doc, { op: "add", taskId: "task.one", entryId: "ask", sentenceId: source.sentences[0].id, target: "artifact:result", acceptance: { type, target: "artifact:result", ...(type === "command_exit_zero" ? { cmd: "exit 0" } : {}) } });
  return doc;
}

test("old delegate result cannot satisfy a newer delegation with reused worker id", async () => {
  const doc = setup();
  registerDelegate(doc, "task.one", "worker");
  registerDelegateResult(doc, "task.one", "worker", { output: "old result" });
  registerDelegate(doc, "task.one", "worker");
  doc.plan.bindings["task.one"].delegate.resultReceived = true;
  await assert.rejects(verifyTask(doc, "task.one"), /current result/);
  assert.equal(doc.plan.bindings["task.one"].status, "delegated");
});

test("runtime live flag cannot resurrect a finished delegate from old event", () => {
  const doc = setup();
  registerDelegate(doc, "task.one", "worker");
  registerDelegateResult(doc, "task.one", "worker", { output: "finished" });
  doc.plan.bindings["task.one"].delegate.live = true;
  doc.plan.bindings["task.one"].delegate.resultReceived = false;
  assert.ok(check(doc, { turnEnd: true }).violations.some(v => v.law === "I1"));
});

test("human approval before ask or for a different target cannot verify", async () => {
  for (const [text, timestamp] of [["APPROVE artifact:result", new Date(0).toISOString()], ["APPROVE artifact:other", new Date().toISOString()]]) {
    const doc = setup("human_review");
    const event = registerHumanApproval(doc, { id: "review", timestamp, message: { role: "user", content: text } });
    const runner = createRunner({ humanReview: async target => ({ approved: true, actor: "user", target, entryId: "review", sentenceId: event.payload.sentences[0].id }) });
    assert.equal((await verifyTask(doc, "task.one", runner)).verified, false);
    assert.equal(doc.plan.bindings["task.one"].status, "open");
  }
});
