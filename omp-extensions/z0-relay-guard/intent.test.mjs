import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { registerIntentGate } from "./intent.js";
import { createDocument, ingestUser, updateIntent, contractHash } from "./checker.js";

const sha = text => createHash("sha256").update(text, "utf8").digest("hex");
const user = (id, text, timestamp = new Date(Date.now() - 1000).toISOString()) => ({ type: "message", id, timestamp, message: { role: "user", content: text, timestamp } });
const schema = { optional() { return this; } };
function fakePi() {
  const hooks = new Map(), lifecycle = new Map(), tools = new Map(), messages = [], flags = new Map(), warnings = [];
  return { hooks, lifecycle, tools, messages, flags, warnings,
    logger: { warn(message, details) { warnings.push({ message, details }); } },
    zod: { object: () => schema, enum: () => schema, string: () => schema },
    registerFlag(name, value) { flags.set(name, value.default); },
    registerTool(tool) { tools.set(tool.name, tool); },
    on(name, handler) { hooks.set(name, handler); },
    events: { on(name, handler) { lifecycle.set(name, handler); } },
    sendMessage(message, options) { messages.push({ message, options }); },
    getFlag(name) { return flags.get(name); },
  };
}

async function setup(t, { text = "Check the requested artifact.", acceptance, count = 1, stamped = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "native-intent-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const sourceStamp = join(root, ".content-sha256"), stamp = "a".repeat(64);
  await writeFile(sourceStamp, `${stamp}\n`);
  const entries = [user("ask", text)];
  if (stamped) entries.unshift({ type: "custom", customType: "z0-intent-input", id: "stamped", timestamp: entries[0].timestamp, data: { kind: "input", ts: entries[0].timestamp, rawText: text, sha256: sha(text), extension_content_sha: stamp } });
  const session = "session:native", dir = join(root, "intent", encodeURIComponent(session)), contractId = "contract.native";
  const notifications = [], ctx = { cwd: root, sessionManager: { getSessionId: () => session }, ui: { notify: (...args) => notifications.push(args) } };
  const helpers = {
    directives: () => ({ session, entries, users: new Map(entries.filter(e => e.type === "message" && e.message?.role === "user" && e.message.attribution !== "agent").map(e => [e.id, typeof e.message.content === "string" ? [e.message.content] : e.message.content.filter(p => p.type === "text").map(p => p.text)])) }),
    payloads: (name, input) => name === "task" ? [input.context, ...input.tasks.map(task => task.task)] : name === "write" && input.path?.startsWith("agent://") ? [input.content] : name === "bash" && input.relayPayload ? [input.relayPayload] : null,
    acknowledgement: text => /^ACK-ONLY: ok$/.test(text),
  };
  const options = { home: root, root, sourceStamp };
  let pi = fakePi(); registerIntentGate(pi, helpers, options);
  const sourceId = stamped ? "stamped" : "ask";
  const doc = createDocument(session);
  const source = ingestUser(doc, { ...entries[0], id: sourceId, message: { role: "user", content: text } });
  for (let i = 0; i < source.sentences.length; i++) {
    if (i < count) {
      const target = acceptance?.target ?? `artifact:${i + 1}`;
      updateIntent(doc, { op: "add", taskId: `task.${i + 1}`, entryId: sourceId, sentenceId: source.sentences[i].id, target, acceptance: acceptance ?? { type: "command_exit_zero", cmd: "exit 0", target } });
    } else updateIntent(doc, { op: "not-an-ask", entryId: sourceId, sentenceId: source.sentences[i].id, reason: "Context for the requested artifact, no separate action" });
  }
  const path = join(dir, `${contractId}.json`), runtimePath = join(dir, "runtime", `${contractId}.json`);
  return { root, entries, sourceStamp, stamp, session, dir, contractId, doc, ctx, helpers, notifications, options, path, runtimePath,
    get pi() { return pi; },
    restart() { pi = fakePi(); registerIntentGate(pi, helpers, options); return pi; },
    async publish(mutate) {
      doc.plan.bindings.approval = { status: "approved", approved_by: "kevin", approved_at: new Date().toISOString(), sourceHash: doc.provenance.sourceHash, contractHash: contractHash(doc), contract_id: contractId, session_id: session };
      mutate?.(doc);
      await mkdir(dir, { recursive: true }); await writeFile(path, JSON.stringify(doc));
    },
    async triage(tier, source = entries.at(-1), overrides = {}) {
      await mkdir(dir, { recursive: true });
      const raw = source.data?.rawText ?? source.message.content;
      await writeFile(join(dir, "triage.jsonl"), `${JSON.stringify({ entry_id: source.id, sourceHash: sha(raw), tier, ...overrides })}\n`);
    },
    call(name, input, toolCallId = "call:1") { return pi.hooks.get("tool_call")({ toolName: name, input, toolCallId }, ctx); },
    result(name, toolCallId = "call:1", rest = {}) { return pi.hooks.get("tool_result")({ toolName: name, toolCallId, isError: false, details: { exitCode: 0 }, ...rest }, ctx); },
    invoke(input, toolCallId = "intent:1") { return pi.tools.get("intent").execute(toolCallId, input, new AbortController().signal, () => {}, ctx); },
    stop(text) { return pi.hooks.get("session_stop")({ last_assistant_message: { role: "assistant", content: text } }, ctx); },
    async runtime() { return JSON.parse(await readFile(runtimePath, "utf8")); },
  };
}

const blocked = value => assert.equal(value?.block, true);
const allowed = value => assert.equal(value?.block, undefined);

test("I0 missing receipt defaults CONTRACT, read-only remains allowed, view cannot compile", async t => {
  const env = await setup(t);
  blocked(env.call("bash", { command: "touch artifact" }));
  allowed(env.call("read", { path: "artifact" }, "read:1"));
  allowed(env.call("intent", { op: "view" }, "view:1"));
  const view = await env.invoke({ op: "view" }); assert.deepEqual(JSON.parse(view.content[0].text), []);
  assert.deepEqual(env.pi.tools.get("intent").parameters, schema);
  await assert.rejects(env.invoke({ op: "add", taskId: "task.1" }), /approved|Unknown/);
});

test("I0 exact FAST and CHECK receipts allow mutation; CONTRACT and stale receipts do not", async t => {
  for (const tier of ["FAST", "CHECK"]) {
    const env = await setup(t); await env.triage(tier);
    allowed(env.call("write", { path: join(env.root, "artifact"), content: "text" }));
  }
  for (const overrides of [{ tier: "CONTRACT" }, { sourceHash: "0".repeat(64) }, { entry_id: "old-source" }]) {
    const env = await setup(t); await env.triage("FAST", env.entries[0], overrides);
    blocked(env.call("write", { path: join(env.root, "artifact"), content: "text" }));
  }
});

test("I0 approves exact stamped raw input, rejects modified source stamp and tampered approval", async t => {
  const approved = await setup(t, { stamped: true }); await approved.publish();
  allowed(approved.call("bash", { command: "true" }));
  approved.entries[0].data.extension_content_sha = "b".repeat(64);
  blocked(approved.call("bash", { command: "true" }, "call:2"));
  for (const mutate of [doc => { doc.plan.bindings.approval.sourceHash = "0".repeat(64); }, doc => { doc.plan.bindings.approval.contractHash = "0".repeat(64); }, doc => { doc.plan.bindings.approval.session_id = "other-session"; }, doc => { doc.plan.bindings["task.1.acceptance"].cmd = "exit 1"; }]) {
    const env = await setup(t); await env.publish(mutate); blocked(env.call("bash", { command: "true" }));
  }
});

test("changed root source bytes and new CONTRACT source invalidate old authority", async t => {
  const changed = await setup(t); await changed.publish(); changed.entries[0].message.content = "Different source.";
  blocked(changed.call("bash", { command: "true" }));
  const newer = await setup(t); await newer.publish(); newer.entries.push(user("new-ask", "Change a different artifact."));
  blocked(newer.call("bash", { command: "true" }));
});

test("approved verifier executes acceptance and saves genuine verified stateUpdate", async t => {
  const env = await setup(t); await env.publish();
  allowed(env.call("intent", { op: "verify", taskId: "task.1" }, "intent:1"));
  const result = await env.invoke({ op: "verify", taskId: "task.1" });
  assert.equal(result.details.verified, true);
  env.result("intent", "intent:1");
  const runtime = await env.runtime();
  assert.equal(runtime.plan.bindings["task.1"].status, "verified");
  const verifier = runtime.eventLog.find(e => e.payload.kind === "verified");
  const execution = runtime.eventLog.find(e => e.payload.kind === "tool_result" && e.payload.toolCallId === verifier.payload.toolCallId);
  assert.equal(execution.payload.toolName, "intent_verifier:command_exit_zero");
  assert.ok(verifier.causalParents.includes(execution.eventId));
  assert.equal(env.stop("DONE: task.1"), undefined);
});

test("ordinary successful tool cannot complete approved task; receipts bind pending call id and name", async t => {
  const env = await setup(t); await env.publish();
  allowed(env.call("bash", { command: "exit 0" }, "bash:known"));
  env.result("bash", "bash:unmatched");
  await assert.rejects(env.runtime(), error => error.code === "ENOENT");
  env.result("read", "bash:known");
  assert.deepEqual(env.pi.warnings, [{
    message: "AODL result tool name mismatch", details: { toolCallId: "bash:known" },
  }]);
  let persisted;
  try { persisted = await env.runtime(); } catch (error) { if (error.code !== "ENOENT") throw error; }
  assert.equal(Boolean(persisted?.eventLog.some(e => e.payload.toolCallId === "bash:known")), false);
  allowed(env.call("bash", { command: "exit 0" }, "bash:actual")); env.result("bash", "bash:actual");
  const runtime = await env.runtime();
  assert.equal(runtime.plan.bindings["task.1"].status, "open");
  assert.equal(runtime.eventLog.some(e => e.payload.kind === "verified"), false);
  const receipt = runtime.eventLog.find(e => e.payload.toolCallId === "bash:actual");
  assert.equal(receipt.payload.toolName, "bash"); assert.equal(receipt.payload.exitCode, 0);
  assert.equal(env.stop("DONE: task.1").decision, "block");
});

test("every request relay carries all open TASKS identities, no unknown or duplicate IDs", async t => {
  const env = await setup(t, { text: "Check the first artifact. Check the second artifact.", count: 2 }); await env.publish();
  const taskInput = payload => ({ context: payload, tasks: [{ task: payload }] });
  for (const payload of ["Do the work", "TASKS: task.1\nDo the work", "TASKS: task.1 task.2 invented\nDo the work", "TASKS: task.1 task.1 task.2\nDo the work", "TASKS: task.1 task.2\nTASKS: task.1 task.2\nDo the work"]) blocked(env.call("task", taskInput(payload)));
  allowed(env.call("task", taskInput("TASKS: task.1, task.2\nDo the work"), "task:good"));
  blocked(env.call("task", { context: "TASKS: task.1 task.2\nDo work", tasks: [{ task: "No identities here" }] }));
  blocked(env.call("write", { path: "agent://worker", content: "TASKS: task.1\nDo work" }));
  allowed(env.call("write", { path: "agent://worker", content: "TASKS: task.1 task.2\nDo work" }, "relay:good"));
  allowed(env.call("write", { path: "agent://worker", content: "ACK-ONLY: ok" }, "relay:ack"));
});

test("relay receipt display binds successful/failing actual tool result", async t => {
  const env = await setup(t); await env.publish(); env.pi.flags.set("show", true);
  allowed(env.call("write", { path: "agent://worker", content: "TASKS: task.1\nCheck result" }, "relay:1"));
  env.result("write", "relay:unknown");
  assert.equal(env.pi.messages.some(item => item.message.content.includes("Relay receipt relay:unknown")), false);
  env.result("write", "relay:1", { isError: true, details: { exitCode: 2 } });
  const receipt = env.pi.messages.find(item => item.message.content.includes("Relay receipt relay:1"));
  assert.match(receipt.message.content, /TASKS: task\.1.*failed/s);
  assert.equal((await env.runtime()).plan.bindings["task.1"].status, "open");
});

test("human review requires later authenticated user outcome approval, not approved contract", async t => {
  const env = await setup(t, { acceptance: { type: "human_review", target: "artifact:reviewed" } }); await env.publish();
  assert.equal((await env.invoke({ op: "verify", taskId: "task.1" })).details.verified, false);
  env.entries.push(user("agent-approval", "APPROVE artifact:reviewed")); env.entries.at(-1).message.attribution = "agent";
  assert.equal((await env.invoke({ op: "verify", taskId: "task.1" })).details.verified, false);
  const approval = user("real-approval", "APPROVE artifact:reviewed", new Date().toISOString()); env.entries.push(approval); await env.triage("FAST", approval);
  assert.equal((await env.invoke({ op: "verify", taskId: "task.1" })).details.verified, true);
  assert.equal((await env.runtime()).plan.bindings["task.1"].status, "verified");
});

test("runtime verification survives a fresh gate instance without altering approved source", async t => {
  const env = await setup(t); await env.publish();
  const original = await readFile(env.path, "utf8");
  assert.equal((await env.invoke({ op: "verify", taskId: "task.1" })).details.verified, true);
  env.pi.hooks.get("session_shutdown")(); env.restart();
  const result = await env.invoke({ op: "view" });
  assert.equal(JSON.parse(result.content[0].text)[0].plan.bindings["task.1"].status, "verified");
  assert.equal(env.stop("COVERED: task.1"), undefined);
  assert.equal(await readFile(env.path, "utf8"), original);
});

test("approved amendment preserves runtime receipts and supersedes only its exact parent", async t => {
  const env = await setup(t); await env.publish();
  assert.equal((await env.invoke({ op: "verify", taskId: "task.1" })).details.verified, true);
  const child = await env.runtime();
  child.plan.bindings.compiler = { parent_contract_id: env.contractId, parent_contractHash: env.doc.plan.bindings.approval.contractHash };
  child.plan.bindings.approval = { ...child.plan.bindings.approval, contract_id: "contract.child" };
  child.plan.bindings.approval.contractHash = contractHash(child);
  await writeFile(join(env.dir, "contract.child.json"), JSON.stringify(child));
  env.restart();
  const docs = JSON.parse((await env.invoke({ op: "view" })).content[0].text);
  assert.equal(docs.length, 1);
  assert.equal(docs[0].plan.bindings.approval.contract_id, "contract.child");
  assert.equal(docs[0].plan.bindings["task.1"].status, "verified");
  assert.equal(env.stop("DONE: task.1"), undefined);
  child.plan.bindings.compiler.parent_contractHash = "0".repeat(64);
  child.plan.bindings.approval.contractHash = contractHash(child);
  await writeFile(join(env.dir, "contract.child.json"), JSON.stringify(child));
  env.restart();
  blocked(env.call("bash", { command: "touch artifact" }));
});

test("unresolved stop reinjects three times then surfaces once without recording completion", async t => {
  const env = await setup(t); await env.publish();
  for (let attempt = 1; attempt <= 3; attempt++) {
    const result = env.stop("DONE: task.1"); assert.equal(result.decision, "block"); assert.match(result.reason, new RegExp(`Continuation ${attempt}/3`));
  }
  assert.equal(env.stop("DONE: task.1"), undefined); assert.equal(env.notifications.length, 1);
  assert.equal(env.stop("DONE: task.1"), undefined); assert.equal(env.notifications.length, 1);
  assert.equal(env.pi.messages.filter(item => /automatic continuation stopped after 3/i.test(item.message.content)).length, 1);
  const result = await env.invoke({ op: "view" });
  assert.equal(JSON.parse(result.content[0].text)[0].plan.bindings["task.1"].status, "open");
});

test("native delegation survives tool-result removal and requires actual final child result", async t => {
  const env = await setup(t); await env.publish();
  const payload = "TASKS: task.1\nCheck artifact";
  allowed(env.call("task", { context: payload, tasks: [{ task: payload }] }, "spawn:1"));
  const lifecycle = env.pi.lifecycle.get("task:subagent:lifecycle");
  lifecycle({ id: "worker:one", parentToolCallId: "spawn:1", status: "started" });
  assert.equal(env.stop("Delegated and waiting."), undefined);
  env.result("task", "spawn:1");
  lifecycle({ id: "worker:one", parentToolCallId: "spawn:1", status: "completed" });
  assert.equal((await env.runtime()).plan.bindings["task.1"].delegate.resultReceived, false);
  const childFile = join(env.root, "child.jsonl");
  await writeFile(childFile, `${JSON.stringify({ type: "message", message: { role: "assistant", content: "Actual completed artifact result" } })}\n`);
  lifecycle({ id: "worker:one", parentToolCallId: "spawn:1", status: "completed", sessionFile: childFile });
  assert.equal((await env.runtime()).plan.bindings["task.1"].delegate.resultReceived, true);
  assert.equal((await env.invoke({ op: "verify", taskId: "task.1" })).details.verified, true);
});

test("drop requires reason, delegate tool cannot invent native lifecycle authority", async t => {
  const env = await setup(t); await env.publish();
  await assert.rejects(env.invoke({ op: "drop", taskId: "task.1", reason: " " }), /reason/);
  await assert.rejects(env.invoke({ op: "delegate", taskId: "task.1", delegateId: "invented" }), /live native lifecycle/);
  const dropped = await env.invoke({ op: "drop", taskId: "task.1", reason: "Kevin replaced this artifact request" });
  assert.equal(dropped.details.status, "dropped");
  assert.equal(env.stop("The task was dropped with the recorded reason."), undefined);
  assert.equal(env.stop("DONE: task.1").decision, "block");
});

test("R6 failed Kevin-visible publication preserves approved ask across restart", async t => {
  const env = await setup(t); await env.publish();
  await env.invoke({ op: "view" });
  env.pi.sendMessage = async () => { throw new Error("publication unavailable"); };
  await assert.rejects(env.invoke({ op: "drop", taskId: "task.1", reason: "Kevin replaced this artifact request" }), /publication unavailable/);
  assert.equal((await env.runtime()).plan.bindings["task.1"].status, "open");
  env.restart();
  const view = JSON.parse((await env.invoke({ op: "view" })).content[0].text);
  assert.equal(view[0].plan.bindings["task.1"].status, "open");
  assert.equal(env.stop("No action remains.").decision, "block");
});

test("stamped custom input requires matching raw digest and following authenticated user bytes", async t => {
  const digest = await setup(t, { stamped: true }); await digest.publish();
  digest.entries[0].data.sha256 = "0".repeat(64);
  blocked(digest.call("bash", { command: "true" }));
  const source = await setup(t, { stamped: true }); await source.publish();
  source.entries[1].message.content = "A different following source.";
  blocked(source.call("bash", { command: "true" }));
});
