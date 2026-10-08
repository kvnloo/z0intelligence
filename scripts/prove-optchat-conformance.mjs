// Real resident + installed provider SDK. Responses are scripted on loopback, never inference.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = fileURLToPath(new URL("../", import.meta.url));
const scenario = process.argv[2] || "all";
const output = process.argv[3] || mkdtempSync(join(tmpdir(), "optchat-conformance-results-"));
const scenarios = ["fresh", "large", "rounds", "cache", "off", "cancel-wait", "cancel-model", "rpc-error", "reasoning"];
mkdirSync(output, { recursive: true });

if (scenario === "all") {
  const reports = [];
  for (const name of scenarios) {
    const proc = Bun.spawn([process.execPath, fileURLToPath(import.meta.url), name, output], {
      cwd: repo,
      env: { PATH: process.env.PATH, LANG: "C.UTF-8", Z0INT_OMP_ROOT: process.env.Z0INT_OMP_ROOT, Z0INT_PYTHON: process.env.Z0INT_PYTHON },
      stdout: "pipe", stderr: "pipe",
    });
    const [code, stdout, stderr] = await Promise.all([proc.exited, new Response(proc.stdout).text(), new Response(proc.stderr).text()]);
    process.stdout.write(stdout);
    if (stderr) process.stderr.write(stderr);
    assert.equal(code, 0, `${name} conformance failed`);
    reports.push(JSON.parse(readFileSync(join(output, `${name}.json`), "utf8")));
  }
  writeFileSync(join(output, "omp-summary.json"), JSON.stringify({ scenarios: reports, real_provider_model_calls: 0 }, null, 2));
  console.log(JSON.stringify({ scenarios: reports.length, failed: 0, receipt: join(output, "omp-summary.json") }));
  process.exit(0);
}
assert(scenarios.includes(scenario), `Unknown scenario ${scenario}`);
const home = mkdtempSync(join(tmpdir(), "optchat-conformance-home-"));
const fixture = join(home, "fixture");
mkdirSync(join(home, "optchat"), { recursive: true });
mkdirSync(fixture);
writeFileSync(join(home, "optchat/enabled"), "on\n");
writeFileSync(join(fixture, "AGENTS.md"), "Synthetic instructions. Do not access files outside this fixture.");
writeFileSync(join(fixture, "large.txt"), "HEAD" + "a".repeat(25_000) + "IMPORTANT_MIDDLE_FACT" + "b".repeat(25_000) + "TAIL");
if (scenario === "cache") {
  mkdirSync(join(home, "optchat/chat/main"), { recursive: true });
  const rows = Array.from({ length: 400 }, (_, i) => ({ i, kind: "user", text: `fixture fact ${i}: ` + "c".repeat(285), date: "2026-10-07T00:00:00+0000" }));
  writeFileSync(join(home, "optchat/chat/main/2026-10-07.jsonl"), rows.map(row => JSON.stringify(row)).join("\n") + "\n");
}
if (scenario === "cancel-wait") {
  mkdirSync(join(home, "optchat/chat/main"), { recursive: true });
  writeFileSync(join(home, "optchat/chat/main/2026-10-07.jsonl"), JSON.stringify({ i: 0, kind: "user", text: "old large message " + "x".repeat(1000), date: "2026-10-07T00:00:00+0000" }) + "\n");
}

function object(value) {
  assert(value && typeof value === "object" && !Array.isArray(value), "Expected a JSON object");
  return value;
}
const requests = [], operations = [], notices = [], notifications = [], shown = [], terminalHandlers = new Set();
const reached = Promise.withResolvers();
const release = Promise.withResolvers();
const waiting = Promise.withResolvers();
const pending = new Map();
const listeners = new Set();
let child, buffer = "", stderr = "", serial = 0;

function anthropicSse(round, tool, text = "FINAL_SYNTHETIC_ANSWER") {
  const blocks = text ? [{ type: "text", text }] : [];
  if (tool) blocks.push({ type: "tool_use", id: `tool_${round}`, name: tool.name, input: tool.args });
  const events = [{ type: "message_start", message: { id: `msg_${round}`, type: "message", role: "assistant", model: "audit-local-model", content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 100, output_tokens: 0 } } }];
  for (const [index, block] of blocks.entries()) {
    if (block.type === "text") {
      events.push({ type: "content_block_start", index, content_block: { type: "text", text: "" } });
      events.push({ type: "content_block_delta", index, delta: { type: "text_delta", text: block.text } });
    } else {
      events.push({ type: "content_block_start", index, content_block: { type: "tool_use", id: block.id, name: block.name, input: {} } });
      events.push({ type: "content_block_delta", index, delta: { type: "input_json_delta", partial_json: JSON.stringify(block.input) } });
    }
    events.push({ type: "content_block_stop", index });
  }
  events.push({ type: "message_delta", delta: { stop_reason: tool ? "tool_use" : "end_turn", stop_sequence: null }, usage: { output_tokens: 20 } });
  events.push({ type: "message_stop" });
  return events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join("");
}
function responsesSse(round) {
  const base = { id: `resp_${round}`, object: "response", created_at: 1, model: "gpt-5.6", status: "in_progress", output: [] };
  const events = [{ type: "response.created", response: base }];
  const items = [];
  if (round === 1) {
    const thought = { id: "rs_fixture", type: "reasoning", summary: [{ type: "summary_text", text: "PRIVATE_THOUGHT_FIXTURE" }], encrypted_content: "opaque-fixture-encrypted-reasoning" };
    events.push({ type: "response.output_item.added", output_index: 0, item: thought });
    events.push({ type: "response.output_item.done", output_index: 0, item: thought });
    items.push(thought);
    const tool = { id: "fc_fixture", type: "function_call", call_id: "call_fixture", name: "zoom", arguments: '{"id":0,"n":1}', status: "completed" };
    events.push({ type: "response.output_item.added", output_index: 1, item: { ...tool, arguments: "", status: "in_progress" } });
    events.push({ type: "response.function_call_arguments.delta", item_id: tool.id, output_index: 1, delta: tool.arguments });
    events.push({ type: "response.output_item.done", output_index: 1, item: tool });
    items.push(tool);
  } else {
    const message = { id: `message_${round}`, type: "message", role: "assistant", status: "completed", content: [{ type: "output_text", text: "FINAL_SYNTHETIC_ANSWER", annotations: [] }] };
    events.push({ type: "response.output_item.added", output_index: 0, item: { ...message, status: "in_progress", content: [] } });
    events.push({ type: "response.content_part.added", item_id: message.id, output_index: 0, content_index: 0, part: { type: "output_text", text: "", annotations: [] } });
    events.push({ type: "response.output_text.delta", item_id: message.id, output_index: 0, content_index: 0, delta: "FINAL_SYNTHETIC_ANSWER" });
    events.push({ type: "response.output_item.done", output_index: 0, item: message });
    items.push(message);
  }
  events.push({ type: "response.completed", response: { ...base, status: "completed", output: items, usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } } } });
  return events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join("");
}

const local = Bun.serve({ hostname: "127.0.0.1", port: 0, async fetch(req) {
  const body = object(await req.json());
  const route = new URL(req.url).pathname;
  if (route.endsWith("/chat/completions")) {
    if (scenario === "cancel-wait") await release.promise;
    return Response.json({ choices: [{ message: { role: "assistant", content: "user: synthetic summary" } }] });
  }
  assert(route.endsWith("/messages") || route.endsWith("/responses"), `Unexpected local route ${route}`);
  requests.push(body);
  const round = requests.length;
  if ((scenario === "off" || scenario === "cancel-model") && round === 1) { reached.resolve(); await release.promise; }
  if (scenario === "reasoning") {
    if (round === 1) await call("submit", { text: "MID_RUN_CORRECTION" });
    return new Response(responsesSse(round), { headers: { "Content-Type": "text/event-stream" } });
  }
  let tool;
  let text = "FINAL_SYNTHETIC_ANSWER";
  if (scenario === "large" && round === 1) tool = { name: "read", args: { path: "large.txt" } };
  if (scenario === "rounds" && round <= 8) { tool = { name: "zoom", args: { id: 0, n: 1 } }; text = "working"; }
  if ((scenario === "off" || scenario === "cancel-model" || scenario === "rpc-error") && round === 1) {
    tool = { name: "write", args: { path: "after-stop.txt", content: "This action must not occur." } };
    text = "working";
  }
  return new Response(anthropicSse(round, tool, text), { headers: { "Content-Type": "text/event-stream" } });
} });
const origin = `http://127.0.0.1:${local.port}`;
const nativeFetch = globalThis.fetch;
const externalAttempts = [];
globalThis.fetch = (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
  if (url.origin !== origin) { externalAttempts.push(url.origin); throw new Error("External network blocked by conformance fixture."); }
  return nativeFetch(input, { ...init, redirect: "error" });
};

const roots = process.env.Z0INT_OMP_ROOT ? [process.env.Z0INT_OMP_ROOT] : [
  "/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@oh-my-pi/pi-coding-agent",
  "/home/kvn/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent",
];
const ompRoot = roots.find(root => existsSync(join(root, "package.json")));
assert(ompRoot, "Set Z0INT_OMP_ROOT to the installed OMP package.");
const { buildModel } = await import(pathToFileURL(join(dirname(ompRoot), "pi-catalog/src/build.ts")).href);
const { runTurn, cancelTurn } = await import(pathToFileURL(join(repo, "omp-extensions/z0-optchat/turn.ts")).href);
child = spawn(process.env.Z0INT_PYTHON || "python3", ["-u", "-m", "z0int.optchat"], {
  cwd: repo,
  env: { PATH: process.env.PATH, HOME: home, LANG: "C.UTF-8", PYTHONPATH: join(repo, "src"), Z0INT_HOME: home, Z0INT_OPTCHAT_HOST: origin, Z0INT_OPTCHAT_MODEL: "synthetic-compactor" },
  stdio: ["pipe", "pipe", "pipe"],
});
child.stderr.on("data", chunk => { stderr += String(chunk); });
child.stdout.on("data", chunk => {
  buffer += String(chunk);
  while (buffer.includes("\n")) {
    const newline = buffer.indexOf("\n");
    const row = object(JSON.parse(buffer.slice(0, newline)));
    buffer = buffer.slice(newline + 1);
    if (typeof row.id !== "string") { notices.push(row); for (const handler of listeners) handler(row); continue; }
    const entry = pending.get(row.id);
    if (entry) { clearTimeout(entry.timer); pending.delete(row.id); entry.resolve(row); }
  }
});
child.on("exit", () => { for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(new Error(`Resident exited: ${stderr}`)); } pending.clear(); });
async function call(op, extra = {}, timeoutMs = 1500) {
  const operation = { op, ...extra };
  operations.push(operation);
  if (op === "settle") waiting.resolve();
  const { promise, resolve, reject } = Promise.withResolvers();
  const id = String(++serial);
  const timer = timeoutMs > 0 ? setTimeout(() => { pending.delete(id); reject(new Error(`${op} RPC timed out`)); }, timeoutMs) : undefined;
  pending.set(id, { resolve, reject, timer });
  child.stdin.write(JSON.stringify({ id, op, ...extra }) + "\n");
  const row = await promise;
  operation.reply = row;
  if (scenario === "rpc-error" && op === "record" && extra.kind === "talk") return { ok: false, error: "synthetic durable-log rejection" };
  return row;
}
call.onNotice = handler => { listeners.add(handler); return () => listeners.delete(handler); };
const model = buildModel({
  id: scenario === "reasoning" ? "gpt-5.6" : "audit-local-model", name: "Audit Local", provider: scenario === "reasoning" ? "openai" : "audit-local",
  api: scenario === "reasoning" ? "openai-responses" : "anthropic-messages", baseUrl: origin + "/v1",
  reasoning: scenario === "reasoning", input: ["text"], contextWindow: 200000, maxTokens: 1024,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
});
const ctx = { cwd: fixture, model, modelRegistry: { getApiKey: async () => "dummy-local-not-a-secret" }, ui: {
  notify: (message, level) => notifications.push({ message, level }),
  onTerminalInput: handler => { terminalHandlers.add(handler); return () => terminalHandlers.delete(handler); },
} };
const pi = { sendMessage: message => shown.push(message) };
const checks = [];
function check(name, verify) { verify(); checks.push(name); }
function logRows() {
  const folder = join(home, "optchat/chat/main");
  return [...new Bun.Glob("*.jsonl").scanSync(folder)].flatMap(file => readFileSync(join(folder, file), "utf8").split("\n").filter(Boolean).map(line => object(JSON.parse(line))));
}
async function bounded(promise, name, timeoutMs = 5000) {
  const timeout = Promise.withResolvers();
  const timer = setTimeout(() => timeout.reject(new Error(`${name} did not complete`)), timeoutMs);
  try { return await Promise.race([promise, timeout.promise]); } finally { clearTimeout(timer); }
}

let passed = false;
try {
  const status = await call("status");
  assert.equal(status.ok, true);
  if (scenario === "off" || scenario === "cancel-model") {
    const running = runTurn(call, ctx, "validate stop cancellation", pi);
    await bounded(reached.promise, "first model request");
    if (scenario === "off") { await call("enabled", { on: false }); release.resolve(); }
    else { const cancelled = cancelTurn(call); release.resolve(); await bounded(cancelled, "model cancellation"); }
    await bounded(running, "stopped turn");
  } else if (scenario === "cancel-wait") {
    const running = runTurn(call, ctx, "CANCELLED_UNANSWERED_INPUT", pi);
    await bounded(waiting.promise, "readiness wait");
    const started = performance.now();
    for (const handler of terminalHandlers) handler("\u001b");
    await bounded(running, "Escape cancellation", 1000);
    check("Escape cancels readiness immediately", () => assert(performance.now() - started < 500));
    const rows = logRows();
    check("cancelled pending input is durably logged once", () => assert.equal(rows.filter(row => row.text === "CANCELLED_UNANSWERED_INPUT").length, 1));
    check("cancelled readiness starts no master request", () => assert.equal(requests.length, 0));
    release.resolve();
  } else if (scenario === "rpc-error") {
    await assert.rejects(bounded(runTurn(call, ctx, "FIRST_SYNTHETIC_REQUEST", pi), "rejected turn"), /synthetic durable-log rejection/);
    checks.push("durable-log failure rejects instead of claiming success");
  } else {
    await bounded(runTurn(call, ctx, "FIRST_SYNTHETIC_REQUEST", pi), "first turn", 15000);
    if (scenario === "fresh") await bounded(runTurn(call, ctx, "SECOND_SYNTHETIC_REQUEST", pi), "second turn");
  }
  if (scenario !== "cancel-wait") assert(requests.length, `No actual provider request: ${JSON.stringify(notifications)}`);
  if (scenario === "fresh") {
    const second = requests[1];
    check("separate turns use fresh user requests", () => { assert.equal(requests.length, 2); assert.equal(second.messages.length, 1); assert.equal(second.messages[0].role, "user"); });
    check("system and tools stay stable", () => { assert.deepEqual(requests[0].system, second.system); assert.deepEqual(requests[0].tools, second.tools); });
    check("second request includes prior summary without assistant replay", () => assert(JSON.stringify(second.messages[0]).includes("FIRST_SYNTHETIC_REQUEST")));
    check("idle completed turn performs no readiness wait", () => assert(!operations.some(row => row.op === "settle")));
  }
  if (scenario === "large") {
    const toolBlocks = requests[1].messages.flatMap(message => Array.isArray(message.content) ? message.content : []).filter(block => block.type === "tool_result");
    const wireText = toolBlocks.map(block => typeof block.content === "string" ? block.content : block.content.map(part => part.text || "").join("")).join("");
    const stored = logRows().find(row => row.kind === "echo");
    check("wire echo equals canonical capped storage", () => { assert.equal(wireText, stored.text); assert([...wireText].length <= 30000); assert(wireText.startsWith("HEAD") && wireText.endsWith("TAIL")); });
    check("no false queued-turn warning follows completion", () => assert(!notifications.some(row => row.message.includes("left the turn queued"))));
  }
  if (scenario === "rounds") check("ninth request reaches final answer", () => { assert.equal(requests.length, 9); assert(shown.some(message => message.content === "FINAL_SYNTHETIC_ANSWER")); });
  if (scenario === "cache") {
    const view = operations.find(row => row.op === "begin").reply.view;
    const parts = requests[0].messages.find(message => message.role === "user").content;
    const marked = parts.filter(block => block.cache_control);
    check("three internal view cuts survive actual serialization", () => {
      assert.equal(marked.length, 3);
      let length = 0;
      for (const [i, part] of marked.entries()) { length += [...part.text].length; assert(length <= [50000, 80000, 100000][i]); assert(part.text.endsWith("\n")); assert.deepEqual(part.cache_control, { type: "ephemeral" }); }
      assert(parts.map(block => block.text || "").join("").startsWith(view));
    });
    check("request-end cache is automatic and short", () => assert.deepEqual(requests[0].cache_control, { type: "ephemeral" }));
  }
  if (scenario === "off" || scenario === "cancel-model" || scenario === "rpc-error") {
    check("stopped or unlogged tool does not execute", () => assert(!existsSync(join(fixture, "after-stop.txt"))));
    if (scenario !== "rpc-error") check("stopped response starts no later request", () => assert.equal(requests.length, 1));
  }
  if (scenario === "reasoning") {
    check("native encrypted reasoning survives injected user input", () => {
      assert.equal(requests.length, 2);
      const input = requests[1].input;
      const thought = input.find(item => item.type === "reasoning");
      assert.equal(thought.encrypted_content, "opaque-fixture-encrypted-reasoning");
      assert(JSON.stringify(input).includes("MID_RUN_CORRECTION"));
      assert(input.findIndex(item => item.type === "reasoning") < input.findIndex(item => item.type === "function_call_output"));
    });
    check("Responses stores no server history or turn chain", () => { for (const request of requests) { assert.equal(request.store, false); assert.equal(request.previous_response_id, undefined); } });
    check("thoughts do not enter permanent memory", () => assert(!logRows().some(row => row.text.includes("PRIVATE_THOUGHT_FIXTURE"))));
  }
  check("all model traffic stays on loopback", () => assert.equal(externalAttempts.length, 0));
  check("turn releases terminal-input ownership", () => assert.equal(terminalHandlers.size, 0));
  const report = { scenario, checks, passed: checks.length, failed: 0, notifications, operations, notices, provider_payloads: requests, real_provider_model_calls: 0, sdk_version: JSON.parse(readFileSync(join(dirname(ompRoot), "pi-ai/package.json"), "utf8")).version };
  writeFileSync(join(output, `${scenario}.json`), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ scenario, passed: checks.length, failed: 0 }));
  passed = true;
} catch (error) {
  writeFileSync(join(output, `${scenario}.json`), JSON.stringify({ scenario, error: String(error), notifications, operations, requests, stderr }, null, 2));
  console.error(error);
} finally {
  release.resolve();
  await cancelTurn(call).catch(() => {});
  child.stdin.end();
  const ended = Promise.withResolvers();
  if (child.exitCode !== null) ended.resolve(); else child.once("exit", ended.resolve);
  await bounded(ended.promise, "resident shutdown", 5000).catch(() => child.kill("SIGKILL"));
  for (const entry of pending.values()) clearTimeout(entry.timer);
  local.stop(true);
  globalThis.fetch = nativeFetch;
  rmSync(home, { recursive: true, force: true });
}
process.exit(passed ? 0 : 1);
