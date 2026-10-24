import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createIntentClient, approvalIdentity, renderContract } from "./index.js";
import { sourceHash } from "./triage.js";

const hash = "a".repeat(64), compiledHash = "b".repeat(64), stamp = "c".repeat(64);
function contract(id = "intent-1", revision = 3, status = "pending") {
  return { specVersion: "0.2", graphId: "intent:session-1", revision,
    intentGraph: { nodes: [{ id: "task.1", kind: "task" }] },
    provenance: { sourceHash: hash },
    plan: { bindings: { sessionId: "session-1", approval: { status, contract_id: id, session_id: "session-1", sourceHash: hash, contractHash: compiledHash, ...(status === "approved" ? { approved_by: "kevin", approved_at: "2026-10-24T00:00:00Z" } : {}) }, "task.1": { text: "Fix scoped service", target: "repo:file", acceptance: "verify.1" }, "verify.1": { kind: "file", target: "repo:file" }, compiler: { memory: ["memory://source"], assumptions: [], conflicts: ["test conflict"], delegation_plan: ["one worker"] } } } };
}
async function fixture(t, responses = [], choices = []) {
  const home = await mkdtemp(join(tmpdir(), "intent-client-"));
  t.after(() => rm(home, { recursive: true, force: true }));
  const packetPath = join(home, "STATE-PACKET.md");
  await writeFile(packetPath, "## Not a project\nhttps://github.com/test/actual-project\n");
  const entries = [], calls = [], prompts = [], notices = [];
  let sessionId = "session-1", leaf = null;
  const pi = { appendEntry(type, data) { leaf = `custom-${entries.length + 1}`; entries.push({ id: leaf, type: "custom", customType: type, data }); } };
  const ctx = { hasUI: true, sessionManager: { getSessionId: () => sessionId, getLeafId: () => leaf, getBranch: () => entries.slice() }, ui: { select: async (title, options) => { prompts.push({ title, options }); return choices.shift(); }, editor: async () => "Fix scoped service and preserve logs", notify: (message, level) => notices.push({ message, level }) } };
  const fetch = async (url, init) => {
    calls.push({ path: new URL(url).pathname, body: JSON.parse(init.body), redirect: init.redirect });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return { ok: true, json: async () => typeof next === "function" ? next() : next };
  };
  const client = createIntentClient(pi, { home, packetPath, fetch, contentSha: stamp });
  const receipts = async () => (await readFile(join(home, "intent/session-1/triage.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
  return { home, entries, calls, prompts, notices, ctx, pi, client, receipts, changeSession: id => { sessionId = id; } };
}

test("FAST never calls the service and records exact input using actual session entry id", async t => {
  const f = await fixture(t);
  assert.equal(await f.client.input({ text: "What is a closure?", source: "interactive" }, f.ctx), undefined);
  assert.equal(f.calls.length, 0);
  assert.equal((await f.receipts())[0].entry_id, f.entries[0].id);
  assert.deepEqual(f.entries[0].data, { rawText: "What is a closure?", sha256: sourceHash("What is a closure?"), ts: f.entries[0].data.ts, extension_content_sha: stamp, kind: "input", source: "interactive" });
  const result = await f.client.input({ text: "quick: deploy live", source: "interactive" }, f.ctx);
  assert.equal(result.text, "deploy live");
  assert.equal(f.calls.length, 0);
  assert.equal((await f.receipts())[1].sourceHash, sourceHash("quick: deploy live"));
});

test("CHECK calls only /check and injects short non-approval context", async t => {
  const f = await fixture(t, [{ context_note: "Prior evidence only; no action authority." }]);
  const result = await f.client.input({ text: "like before", source: "interactive" }, f.ctx);
  assert.deepEqual(f.calls.map(call => call.path), ["/check"]);
  assert.match(result.text, /evidence, not approval/);
  assert.equal(f.prompts.length, 0);
  assert.equal(f.calls[0].body.text, "like before");
});

test("CONTRACT renders real select and approves only the exact explicit selection", async t => {
  const pending = contract();
  const summary = "Proposed intent contract\n• Fix scoped service\n  Target: repo:file · Executor: self\n  Check: file repo:file\nMemory facts:\n• remembered fact [source:one]\nConflicts: test conflict\nDelegation: one worker\nlook good?";
  const f = await fixture(t, [{}, { contract: pending, summary, memory_sources: ["source"] }, { contract: contract("intent-1", 4, "approved"), path: "/private/intent-1.json" }], ["Approve"]);
  const raw = "/aodl deploy scoped service";
  const result = await f.client.input({ text: raw, source: "interactive" }, f.ctx);
  assert.deepEqual(f.calls.map(call => call.path), ["/current", "/reconcile", "/approve"]);
  assert.deepEqual(f.calls[2].body, approvalIdentity(pending, "session-1"));
  assert.equal(f.calls[1].body.text, raw);
  assert.equal(f.calls[1].body.entry_id, f.entries[0].id);
  assert.match(f.prompts[0].title, /look good\?/);
  for (const section of ["Check:", "Memory facts:", "remembered fact", "Conflicts:", "Delegation:"]) assert.ok(f.prompts[0].title.includes(section));
  assert.equal(f.prompts[0].title.split("Fix scoped service").length, 2);
  assert.deepEqual(f.prompts[0].options, ["Approve", "Edit", "Reject"]);
  assert.match(result.text, /^deploy scoped service/);
  assert.equal(f.entries[0].data.rawText, raw);
});

test("UI edits append new source, reconcile against prior id and reask", async t => {
  const f = await fixture(t, [{}, { contract: contract() }, { contract: contract("intent-2", 5) }, { contract: contract("intent-2", 6, "approved"), path: "/private/intent-2.json" }], ["Edit", "Approve"]);
  await f.client.input({ text: "/aodl fix service" }, f.ctx);
  assert.deepEqual(f.calls.map(call => call.path), ["/current", "/reconcile", "/reconcile", "/approve"]);
  assert.equal(f.calls[2].body.open_contract, "intent-1");
  assert.equal(f.calls[2].body.text, "Fix scoped service and preserve logs");
  assert.equal(f.prompts.length, 2);
  const sources = f.entries.filter(entry => entry.customType === "z0-intent-input");
  assert.deepEqual(sources.map(entry => entry.data.rawText), ["/aodl fix service", "Fix scoped service and preserve logs"]);
  assert.equal(sources[1].data.kind, "edit");
  assert.equal((await f.receipts()).length, 2);
});

test("reject or cancel never approves and logs reject input", async t => {
  const f = await fixture(t, [{}, { contract: contract() }, { contract: contract("intent-1", 4, "rejected"), path: "/private/rejected.json" }], [undefined]);
  assert.deepEqual(await f.client.input({ text: "/aodl fix service" }, f.ctx), { handled: true });
  assert.deepEqual(f.calls.map(call => call.path), ["/current", "/reconcile", "/reject"]);
  assert.equal(f.entries.filter(entry => entry.customType === "z0-intent-input").at(-1).data.kind, "reject");
});

test("midtask input restores contract and reasks without /current or automatic approval", async t => {
  const f = await fixture(t, [{ contract: contract("intent-2", 5) }, {}], ["Reject"]);
  f.pi.appendEntry("z0-intent-state", { contract: contract("intent-1", 4, "approved") });
  await f.client.input({ text: "also preserve logs" }, f.ctx);
  assert.deepEqual(f.calls.map(call => call.path), ["/reconcile", "/reject"]);
  assert.equal(f.calls[0].body.open_contract, "intent-1");
  assert.equal((await f.receipts())[0].tier, "CONTRACT");
});

test("service errors, no UI, bad identity and switched session fail closed", async t => {
  const f = await fixture(t, [new Error("offline")]);
  assert.deepEqual(await f.client.input({ text: "again" }, f.ctx), { handled: true });
  assert.match(f.notices[0].message, /blocked/);
  const noUI = await fixture(t, [{}, { contract: contract() }]);
  noUI.ctx.hasUI = false;
  assert.deepEqual(await noUI.client.input({ text: "/aodl fix service" }, noUI.ctx), { handled: true });
  assert.equal(noUI.calls.some(call => call.path === "/approve"), false);
  const malformed = await fixture(t, [{}, { contract: { ...contract(), revision: null } }]);
  assert.deepEqual(await malformed.client.input({ text: "/aodl fix service" }, malformed.ctx), { handled: true });
  const switched = await fixture(t, [{}, { contract: contract() }], ["Approve"]);
  switched.ctx.ui.select = async () => { switched.changeSession("session-2"); return "Approve"; };
  assert.deepEqual(await switched.client.input({ text: "/aodl fix service" }, switched.ctx), { handled: true });
  assert.equal(switched.calls.some(call => call.path === "/approve"), false);
});

test("missing source append or receipt failure prevents any service action", async t => {
  const f = await fixture(t);
  const client = createIntentClient({ appendEntry() {} }, { home: f.home, contentSha: stamp, fetch() { throw new Error("must not call"); } });
  assert.deepEqual(await client.input({ text: "quick: hello" }, f.ctx), { handled: true });
  const badHome = join(f.home, "not-directory");
  await writeFile(badHome, "x");
  const disk = createIntentClient(f.pi, { home: badHome, contentSha: stamp, fetch() { throw new Error("must not call"); } });
  assert.deepEqual(await disk.input({ text: "quick: hello" }, f.ctx), { handled: true });
});

test("service URL cannot redirect or disclose requests beyond literal loopback", () => {
  for (const url of ["https://127.0.0.1:8431", "http://example.com", "http://localhost:8431", "http://127.0.0.1:8431/private", "http://user:pass@127.0.0.1:8431"]) assert.throws(() => createIntentClient({}, { url }));
  assert.match(renderContract(contract()), /test conflict/);
});

test("actual OMP input runner persists a real source id before the user message exists", { skip: process.env.Z0INT_INTENT_REAL_OMP !== "1" }, async t => {
  const { createMockModel, registerMockApi } = await import("@oh-my-pi/pi-ai/providers/mock");
  const { createAgentSession, SessionManager, Settings } = await import("@oh-my-pi/pi-coding-agent");
  const { initializeExtensions } = await import("@oh-my-pi/pi-coding-agent/modes/runtime-init");
  const root = await mkdtemp(join(tmpdir(), "intent-real-omp-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const packetPath = join(root, "STATE-PACKET.md");
  await writeFile(packetPath, "# Orientation only\n");
  const settings = await Settings.loadIsolated({ cwd: root, agentDir: join(root, ".agent"), inMemory: true });
  const manager = SessionManager.inMemory(root);
  registerMockApi("intent-real-omp");
  const model = createMockModel({ id: "intent-no-inference", provider: "ollama", responses: [{ content: ["not invoked"] }] });
  const errors = [];
  const { session } = await createAgentSession({
    cwd: root, agentDir: join(root, ".agent"), settings, sessionManager: manager, model,
    autoApprove: false, hasUI: true, cacheWarming: false, disableExtensionDiscovery: true,
    enableMCP: false, enableLsp: false, skipPythonPreflight: true, skills: [], rules: [], contextFiles: [],
    extensions: [pi => {
      const client = createIntentClient(pi, { home: root, packetPath, contentSha: stamp, fetch() { throw new Error("FAST must not call a service"); } });
      pi.on("input", client.input);
    }],
  });
  await initializeExtensions(session, {
    reportSendError: (_action, error) => errors.push(String(error)),
    reportRuntimeError: error => errors.push(String(error)),
  });
  try {
    const result = await session.extensionRunner.emitInput("quick: hello", undefined, "interactive");
    assert.equal(result.text, "hello");
    const entries = manager.getEntries();
    const source = entries.find(entry => entry.customType === "z0-intent-input");
    assert.ok(source?.id);
    assert.equal(entries.some(entry => entry.type === "message" && entry.message.role === "user"), false);
    const receipts = (await readFile(join(root, "intent", manager.getSessionId(), "triage.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
    assert.equal(receipts[0].entry_id, source.id);
    assert.equal(receipts[0].sourceHash, sourceHash("quick: hello"));
    assert.deepEqual(errors, []);
  } finally { await session.dispose(); await manager.close(); }
});

test("CHECK rejects a returned contract and notification errors cannot fail open", async t => {
  const f = await fixture(t, [{ context_note: "not authority", contract: contract("intent-1", 3, "approved") }]);
  f.ctx.ui.notify = () => { throw new Error("UI unavailable"); };
  assert.deepEqual(await f.client.input({ text: "again" }, f.ctx), { handled: true });
  assert.equal(f.calls.some(call => call.path === "/approve"), false);
});

test("a service approval classification still requires the explicit human dialog choice", async t => {
  const f = await fixture(t, [{}, { classification: "approval", contract: contract() }, {}], ["Reject"]);
  await f.client.input({ text: "/aodl yes" }, f.ctx);
  assert.deepEqual(f.calls.map(call => call.path), ["/current", "/reconcile", "/reject"]);
});

test("a question classified by reconcile may answer without granting new approval", async t => {
  const f = await fixture(t, [{}, { classification: "question", answerable: true, context_note: "Read-only answer context" }]);
  const result = await f.client.input({ text: "/aodl What is a closure?" }, f.ctx);
  assert.match(result.text, /^What is a closure/);
  assert.equal(f.calls.some(call => call.path === "/approve"), false);
  assert.equal(f.prompts.length, 0);
});

test("editor quick override is logged but cannot bypass a pending review", async t => {
  const f = await fixture(t, [{}, { contract: contract() }], ["Edit"]);
  f.ctx.ui.editor = async () => "quick: deploy without review";
  assert.deepEqual(await f.client.input({ text: "/aodl deploy service" }, f.ctx), { handled: true });
  const receipts = await f.receipts();
  assert.equal(receipts.at(-1).override, "quick:");
  assert.equal(receipts.at(-1).tier, "FAST");
  assert.equal(f.calls.some(call => call.path === "/approve"), false);
});

test("explicit rejection classification rejects pending proposal without offering approval", async t => {
  const f = await fixture(t, [{}, { classification: "rejection", contract: contract() }, {}], ["Approve"]);
  assert.deepEqual(await f.client.input({ text: "/aodl no" }, f.ctx), { handled: true });
  assert.equal(f.prompts.length, 0);
  assert.deepEqual(f.calls.map(call => call.path), ["/current", "/reconcile", "/reject"]);
});

test("recent user context uses strings while exact input ids remain in custom records", async t => {
  const f = await fixture(t, [{ context_note: "context" }]);
  f.entries.push({ id: "actual-user", type: "message", message: { role: "user", content: [{ type: "text", text: "prior ask" }] } });
  await f.client.input({ text: "again" }, f.ctx);
  assert.deepEqual(f.calls[0].body.recent_user_msgs, ["prior ask"]);
  assert.equal(f.calls[0].body.entry_id, f.entries.find(entry => entry.customType === "z0-intent-input").id);
});
