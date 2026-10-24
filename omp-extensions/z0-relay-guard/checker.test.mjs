import { randomUUID } from "node:crypto";
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import { createDocument, ingestUser, updateIntent, appendEvent, verifyTask, check, contractHash, registerDelegate, registerDelegateResult, registerHumanApproval } from "./checker.js";
import { runVerifier, createRunner, validateVerifier, normalizeVerifier, VERIFIER_TYPES } from "./verifiers.js";

const entry = (id, content, timestamp = new Date(Date.now() - 1000).toISOString(), role = "user") => ({ id, timestamp, message: { role, content, timestamp } });
function fixture(acceptance = { type: "command_exit_zero", cmd: "exit 0", target: "fixture:result" }) {
  const doc = createDocument("session:test");
  const source = ingestUser(doc, entry("user:1", "Check the requested result."));
  updateIntent(doc, { op: "add", taskId: "task.one", entryId: "user:1", sentenceId: source.sentences[0].id, target: acceptance.target, acceptance });
  return doc;
}
const pass = (target, extras = {}) => ({ executed: true, passed: true, exitCode: 0, toolCallId: `test:${randomUUID()}`, target, startedAt: new Date().toISOString(), finishedAt: new Date().toISOString(), ...extras });
const laws = (doc, options) => check(doc, options).violations.map(value => value.law);
function approve(doc) {
  doc.plan.bindings.approval = { status: "approved", approved_by: "kevin", approved_at: new Date().toISOString(), sourceHash: doc.provenance.sourceHash, contractHash: contractHash(doc), contract_id: "contract:test", session_id: "session:test" };
}

test("exact sentences retain punctuation, whitespace, duplicate asks and separate blocks", () => {
  const doc = createDocument("sources");
  const texts = ["  Do it!\nDo it!", "A URL https://example.test/x.\nContext only."];
  const source = ingestUser(doc, entry("user:1", texts.map(text => ({ type: "text", text }))));
  for (const sentence of source.sentences) assert.equal(sentence.text, texts[sentence.block].slice(sentence.start, sentence.end));
  assert.equal(source.sentences.filter(s => s.text.trim() === "Do it!").length, 2);
  assert.ok(laws(doc).includes("I1"));
  for (const sentence of source.sentences) updateIntent(doc, { op: "not-an-ask", entryId: "user:1", sentenceId: sentence.id, reason: "Test source is quoted context, not a live instruction" });
  assert.equal(check(doc).ok, true);
  source.sentences[0].text = source.sentences[0].text.trim();
  assert.ok(laws(doc).includes("I1"));
});

test("compiler rejects missing reasons, fake targets, duplicate classification and source edits", () => {
  const doc = createDocument("invalid");
  const source = ingestUser(doc, entry("u", "Build it."));
  const input = { entryId: "u", sentenceId: source.sentences[0].id };
  assert.throws(() => updateIntent(doc, { op: "drop", ...input }), /reason/);
  assert.throws(() => updateIntent(doc, { op: "not-an-ask", ...input, reason: " " }), /reason/);
  assert.throws(() => updateIntent(doc, { op: "add", ...input, taskId: "t", target: "unknown", acceptance: { type: "human_review", target: "unknown" } }), /real target/);
  updateIntent(doc, { op: "not-an-ask", ...input, reason: "Quoted example" });
  assert.throws(() => updateIntent(doc, { op: "not-an-ask", ...input, reason: "Again" }), /already/);
  assert.throws(() => ingestUser(doc, entry("u", "Different.")), /changed/);
  assert.throws(() => ingestUser(doc, entry("agent", "APPROVE result", undefined, "assistant")), /authenticated/);
});

test("I0 requires exact approved contract and session only on contracted mutations", () => {
  const doc = fixture();
  assert.ok(laws(doc, { mutating: true }).includes("I0"));
  assert.equal(check(doc, { mutating: true, requiresContract: false }).ok, true);
  approve(doc);
  assert.equal(check(doc, { mutating: true, sessionId: "session:test" }).ok, true);
  assert.ok(laws(doc, { mutating: true, sessionId: "other" }).includes("I0"));
  doc.plan.bindings["task.one.acceptance"].cmd = "exit 1";
  assert.ok(laws(doc, { mutating: true }).includes("I0"));
});

test("approval hash freezes acceptance, compiler metadata, sources, graph and target, not runtime", () => {
  const doc = fixture();
  doc.plan.bindings.compiler = { model_id: "compiler", memory: ["citation"] };
  const original = contractHash(doc);
  registerDelegate(doc, "task.one", "worker:1");
  assert.equal(contractHash(doc), original);
  registerDelegateResult(doc, "task.one", "worker:1", { output: "actual worker result" });
  assert.equal(contractHash(doc), original);
  registerHumanApproval(doc, entry("review", "APPROVE fixture:result"));
  assert.equal(contractHash(doc), original);
  for (const change of [copy => { copy.plan.bindings.compiler.memory[0] = "changed"; }, copy => { copy.plan.bindings["task.one"].target = "different"; }, copy => { copy.intentGraph.edges[0].relation = "data"; }]) {
    const copy = structuredClone(doc); change(copy); assert.notEqual(contractHash(copy), original);
  }
});

test("I1 does not permit arbitrary successful tools, dead delegates, or reasonless drops", () => {
  const doc = fixture();
  appendEvent(doc, "stateUpdate", { kind: "tool_result", toolName: "bash", toolCallId: "arbitrary", exitCode: 0, passed: true });
  assert.ok(laws(doc, { turnEnd: true }).includes("I1"));
  updateIntent(doc, { op: "delegate", taskId: "task.one", delegateId: "worker:1" });
  assert.ok(laws(doc, { turnEnd: true }).includes("I1"));
  registerDelegate(doc, "task.one", "worker:1", { live: true });
  assert.equal(check(doc, { turnEnd: true }).ok, true);
  doc.plan.bindings["task.one"].delegate.live = false;
  assert.ok(laws(doc, { turnEnd: true }).includes("I1"));
  updateIntent(doc, { op: "drop", taskId: "task.one", reason: "User replaced this target" });
  assert.equal(check(doc, { turnEnd: true }).ok, true);
  doc.plan.bindings["task.one"].reason = "";
  assert.ok(laws(doc, { turnEnd: true }).includes("I1"));
});

test("real command execution alone produces causal verified receipt and covered claim", async () => {
  const doc = fixture();
  const result = await verifyTask(doc, "task.one");
  assert.equal(result.verified, true);
  assert.equal(check(doc, { claims: ["task.one"], turnEnd: true }).ok, true);
  const execution = doc.eventLog.find(e => e.payload.kind === "tool_result");
  assert.ok(result.receipt.causalParents.includes(execution.eventId));
  assert.equal(execution.payload.toolName, "intent_verifier:command_exit_zero");
});

test("failed execution keeps task open and blocks done claims", async () => {
  const doc = fixture({ type: "command_exit_zero", cmd: "exit 7", target: "fixture:result" });
  const result = await verifyTask(doc, "task.one");
  assert.equal(result.verified, false);
  assert.equal(result.receipt.payload.exitCode, 7);
  assert.equal(doc.plan.bindings["task.one"].status, "open");
  assert.ok(laws(doc, { claims: ["task.one"] }).includes("I2"));
  assert.equal(doc.eventLog.some(e => e.payload.kind === "verified"), false);
});

test("rejects stale, missing-execution, mismatched-target and reused tool IDs", async () => {
  for (const modify of [r => ({ ...r, startedAt: new Date(0).toISOString() }), r => ({ ...r, executed: false }), r => ({ ...r, target: "different" }), r => ({ ...r, toolCallId: "" })]) {
    const doc = fixture();
    await assert.rejects(verifyTask(doc, "task.one", v => modify(pass(v.target))));
    assert.equal(doc.plan.bindings["task.one"].status, "open");
  }
  const doc = fixture();
  appendEvent(doc, "stateUpdate", { kind: "tool_result", toolCallId: "used" });
  await assert.rejects(verifyTask(doc, "task.one", v => pass(v.target, { toolCallId: "used" })), /already used/);
});

test("checker rejects tampered pass, timestamps, causal parents, target, revision and tool identity", async () => {
  const doc = fixture(); await verifyTask(doc, "task.one");
  for (const mutate of [
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").payload.passed = false; },
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").payload.startedAt = new Date(0).toISOString(); },
    copy => { copy.eventLog.find(e => e.payload.kind === "verified").causalParents = []; },
    copy => { copy.eventLog.find(e => e.payload.kind === "verified").payload.target = "different"; },
    copy => { copy.eventLog.find(e => e.payload.kind === "verified").sourceHash = "0".repeat(64); },
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").payload.toolName = "bash"; },
    copy => { copy.eventLog.find(e => e.payload.kind === "tool_result").revision = 999; },
    copy => { const execution = structuredClone(copy.eventLog.find(e => e.payload.kind === "tool_result")); execution.eventId = "duplicate-execution"; copy.eventLog.push(execution); },
  ]) { const copy = structuredClone(doc); mutate(copy); assert.equal(check(copy, { claims: ["task.one"] }).ok, false); }
});

test("immutable target and acceptance cannot change after add", () => {
  const doc = fixture();
  assert.throws(() => updateIntent(doc, { op: "drop", taskId: "task.one", reason: "drop", target: "other" }), /immutable/);
  doc.plan.bindings["task.one.acceptance"].cmd = "true";
  assert.ok(laws(doc).includes("I3"));
});

test("delegation requires a matching actual result before verifier execution", async () => {
  const doc = fixture(); registerDelegate(doc, "task.one", "worker:1");
  await assert.rejects(verifyTask(doc, "task.one"), /Await delegated result/);
  assert.throws(() => registerDelegateResult(doc, "task.one", "wrong-worker", "output"), /match/);
  registerDelegateResult(doc, "task.one", "worker:1", { output: "Completed requested work" });
  assert.ok(laws(doc, { turnEnd: true }).includes("I1"));
  assert.equal((await verifyTask(doc, "task.one")).verified, true);
  assert.equal(check(doc, { turnEnd: true }).ok, true);
  doc.eventLog.find(e => e.payload.kind === "delegate_result").payload.delegateId = "wrong-worker";
  assert.ok(laws(doc, { claims: ["task.one"] }).includes("I2"));
});

test("relay IDs cover forwarded identities, not vice versa", () => {
  const doc = fixture();
  assert.equal(check(doc, { forwardedTaskIds: ["task.one"], relayTaskIds: ["task.one"] }).ok, true);
  assert.ok(laws(doc, { forwardedTaskIds: ["task.one"], relayTaskIds: [] }).includes("I4"));
  assert.ok(laws(doc, { forwardedTaskIds: [], relayTaskIds: ["invented"] }).includes("I4"));
});

test("every verifier family validates its required fields and exact identity", () => {
  const valid = [
    { type: "command_exit_zero", cmd: "true", target: "artifact:one" },
    { type: "file_contains", path: "/tmp/one", pattern: "exact bytes", target: "/tmp/one" },
    { type: "url_status", url: "https://example.test/", code: 200, target: "https://example.test/" },
    { type: "pr_state", repo: "owner/repo", number: 1, head_sha: "a".repeat(40), state: "merged", target: `owner/repo#1@${"a".repeat(40)}` },
    { type: "service_active", unit: "owned.service", target: "owned.service" },
    { type: "metric_threshold", query: "latency", op: "<=", value: 10, target: "owned:service" },
    { type: "human_review", target: "artifact:one" },
  ];
  assert.deepEqual(valid.map(v => v.type), VERIFIER_TYPES);
  for (const v of valid) { assert.equal(validateVerifier(v), v); assert.throws(() => normalizeVerifier(v, "different"), /differs/); }
  for (const v of [{ type: "command_exit_zero", target: "x" }, { type: "file_contains", target: "x", path: "x" }, { type: "url_status", url: "file:///etc/passwd", code: 200, target: "file:///etc/passwd" }, { ...valid[3], head_sha: "short" }, { type: "service_active", unit: "-bad;echo", target: "-bad;echo" }, { type: "metric_threshold", query: "q", op: "exec", value: 1, target: "x" }, { type: "human_review", target: "unknown" }]) assert.throws(() => validateVerifier(v));
});

test("file_contains reads literal bytes, URL checks actual response and no redirect substitution", async () => {
  const directory = await mkdtemp(join(tmpdir(), "intent-verifiers-"));
  const path = join(directory, "literal;not-a-command.txt");
  const server = createServer((req, res) => { res.statusCode = req.url === "/ok" ? 201 : 302; res.setHeader("Location", "/ok"); res.end("response"); });
  try {
    await writeFile(path, "literal .* bytes");
    assert.equal((await runVerifier({ type: "file_contains", path, pattern: ".*", target: path })).passed, true);
    assert.equal((await runVerifier({ type: "file_contains", path, pattern: "missing", target: path })).passed, false);
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    assert.equal((await runVerifier({ type: "url_status", url: `${base}/ok`, code: 201, target: `${base}/ok` })).passed, true);
    assert.equal((await runVerifier({ type: "url_status", url: `${base}/redirect`, code: 201, target: `${base}/redirect` })).passed, false);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(directory, { recursive: true, force: true }); }
});

test("PR and service families execute fixed argv without shell expansion", { concurrency: false }, async () => {
  const directory = await mkdtemp(join(tmpdir(), "intent-argv-"));
  const previous = process.env.PATH;
  try {
    const sha = "a".repeat(40);
    await writeFile(join(directory, "gh"), `#!/bin/sh\n[ "$1" = api ] && [ "$2" = repos/owner/repo/pulls/1 ] || exit 9\nprintf '%s' '{"number":1,"head":{"sha":"${sha}"},"state":"closed","merged_at":"2026-01-01","base":{"repo":{"full_name":"owner/repo"}}}'\n`);
    await writeFile(join(directory, "systemctl"), "#!/bin/sh\n[ \"$1\" = is-active ] && [ \"$2\" = -- ] && [ \"$3\" = owned.service ] || exit 9\nprintf 'active\\n'\n");
    await chmod(join(directory, "gh"), 0o700); await chmod(join(directory, "systemctl"), 0o700);
    process.env.PATH = `${directory}:${previous}`;
    const pr = { type: "pr_state", repo: "owner/repo", number: 1, head_sha: sha, state: "merged", target: `owner/repo#1@${sha}` };
    assert.equal((await runVerifier(pr)).passed, true);
    assert.equal((await runVerifier({ ...pr, state: "open" })).passed, false);
    assert.equal((await runVerifier({ type: "service_active", unit: "owned.service", target: "owned.service" })).passed, true);
    assert.equal((await runVerifier({ type: "service_active", unit: "missing.service", target: "missing.service" })).passed, false);
  } finally { process.env.PATH = previous; await rm(directory, { recursive: true, force: true }); }
});

test("metric adapter requires exact sample target and real finite measurement", async () => {
  const v = { type: "metric_threshold", query: "p95(latency)", op: "<=", value: 10, target: "service:owned" };
  assert.equal((await runVerifier(v)).passed, false);
  assert.equal((await runVerifier(v, { metricQuery: async (query, target) => ({ target, value: 9 }) })).passed, true);
  assert.equal((await runVerifier(v, { metricQuery: async () => ({ target: "other", value: 9 }) })).passed, false);
  assert.equal((await runVerifier(v, { metricQuery: async (query, target) => ({ target, value: 11 }) })).passed, false);
  assert.equal((await runVerifier(v, { metricQuery: async (query, target) => ({ target, value: NaN }) })).passed, false);
});

test("human_review requires authenticated exact outcome approval, not contract or agent approval", async () => {
  const doc = fixture({ type: "human_review", target: "artifact:one" }); approve(doc);
  assert.equal((await verifyTask(doc, "task.one")).verified, false);
  const runner = createRunner({ humanReview: async target => ({ approved: true, actor: "user", target, entryId: "review", sentenceId: "nonexistent" }) });
  assert.equal((await verifyTask(doc, "task.one", runner)).verified, false);
  assert.throws(() => registerHumanApproval(doc, entry("agent", "APPROVE artifact:one", undefined, "assistant")), /authenticated/);
  const registered = registerHumanApproval(doc, entry("review", "APPROVE artifact:one", new Date().toISOString()));
  const authenticated = createRunner({ humanReview: async target => ({ approved: true, actor: "user", target, entryId: "review", sentenceId: registered.payload.sentences[0].id }) });
  assert.equal((await verifyTask(doc, "task.one", authenticated)).verified, true);
  assert.equal(check(doc, { mutating: true, claims: ["task.one"], turnEnd: true }).ok, true);
  const rejected = await runVerifier({ type: "human_review", target: "artifact:one" }, { humanReview: async target => ({ approved: true, actor: "agent", target, entryId: "review", sentenceId: "s" }) });
  assert.equal(rejected.passed, false);
});
