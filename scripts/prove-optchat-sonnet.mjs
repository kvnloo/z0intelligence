#!/usr/bin/env bun
// Actual Python SonnetBridge + installed pi-ai. Scripted loopback SSE is NOT real inference or cache-hit evidence.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { homedir, tmpdir } from "node:os";
import { delimiter, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

// Bun's node:http shim does not emit response/socket close for a cancelled request
// held before headers. Native Node owns the fixture server; the actual SDK worker
// remains Bun. This keeps the disconnect assertion meaningful.
const bunExecutable = process.versions.bun ? process.execPath : process.env.OPTCHAT_PROOF_BUN_EXECUTABLE;
if (process.versions.bun) {
  const supervisor = spawn("node", [fileURLToPath(import.meta.url), ...process.argv.slice(2)], {
    stdio: "inherit",
    env: { ...process.env, OPTCHAT_PROOF_BUN_EXECUTABLE: bunExecutable },
  });
  const code = await new Promise((resolveExit, reject) => {
    supervisor.once("error", reject);
    supervisor.once("exit", code => resolveExit(code ?? 1));
  });
  process.exit(code);
}

const repo = fileURLToPath(new URL("../", import.meta.url));
const model = "claude-sonnet-4-6";
const dummyKey = "sk-optchat-loopback-fixture-NOT-A-REAL-ANTHROPIC-KEY";
const requestedOutput = process.argv[2];
assert(requestedOutput, "Usage: bun scripts/prove-optchat-sonnet.mjs /tmp/proof-dir");
const output = resolve(requestedOutput);
const outputRelative = relative(repo, output);
assert(outputRelative.startsWith(`..${sep}`) || outputRelative === "..", "Keep generated proof output outside the source checkout.");
mkdirSync(output, { recursive: true, mode: 0o700 });

const checks = [];
const requests = [];
const serverErrors = [];
const sockets = new Set();
let home, server, driver, driverResult, nativeInput, origin, fixtureData;
let guardEvents = [];
let failure;
let activeRequests = 0;
let peakRequests = 0;
let concurrentPeak = 0;
const rounds = new Map();
const concurrentResponses = new Map();
const abortClosed = Promise.withResolvers();
const driverExited = Promise.withResolvers();

function check(name, fn) {
  try {
    const detail = fn();
    checks.push({ name, ok: true, ...(detail === undefined ? {} : { detail }) });
  } catch (error) {
    checks.push({ name, ok: false, error: messageOf(error) });
    throw error;
  }
}
function messageOf(error) {
  return (error instanceof Error ? error.message : String(error)).replaceAll(dummyKey, "[dummy-key-redacted]");
}
function digest(text) {
  return createHash("sha256").update(text).digest("hex");
}
function object(value) {
  assert(value !== null && typeof value === "object" && !Array.isArray(value), "Expected a JSON object");
  return value;
}
function textOf(content) {
  if (typeof content === "string") return content;
  assert(Array.isArray(content), "Native content must be a string or blocks");
  return content.filter(block => block.type === "text").map(block => block.text).join("");
}
function stepFor(name) {
  assert(nativeInput, "Python must announce its actual compactor prompt before provider I/O");
  return `For scale, this line is exactly ${nativeInput.node} bytes:\n${nativeInput.scale}\n\nCompress this message into one line, in at most ${nativeInput.node} bytes:\nuser: [OPTCHAT_CASE:${name}]\n${fixtureData.source}`;
}
function sse(name, round, plan) {
  const events = [{
    type: "message_start",
    message: {
      id: `msg_fixture_${name}_${round}`, type: "message", role: "assistant", model, content: [],
      stop_reason: null, stop_sequence: null, usage: { input_tokens: 100, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 },
    },
  }];
  let index = 0;
  if (plan.thinking) {
    events.push({ type: "content_block_start", index, content_block: { type: "thinking", thinking: "" } });
    events.push({ type: "content_block_delta", index, delta: { type: "thinking_delta", thinking: plan.thinking } });
    const split = Math.floor(plan.signature.length / 2);
    events.push({ type: "content_block_delta", index, delta: { type: "signature_delta", signature: plan.signature.slice(0, split) } });
    events.push({ type: "content_block_delta", index, delta: { type: "signature_delta", signature: plan.signature.slice(split) } });
    events.push({ type: "content_block_stop", index });
    index++;
  }
  events.push({ type: "content_block_start", index, content_block: { type: "text", text: "" } });
  const split = Math.floor(plan.text.length / 2);
  events.push({ type: "content_block_delta", index, delta: { type: "text_delta", text: plan.text.slice(0, split) } });
  events.push({ type: "content_block_delta", index, delta: { type: "text_delta", text: plan.text.slice(split) } });
  events.push({ type: "content_block_stop", index });
  events.push({ type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: plan.outputTokens ?? 20 } });
  events.push({ type: "message_stop" });
  return events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join("");
}
function respond(res, name, round) {
  const plan = fixtureData.plans[name]?.[round - 1];
  assert(plan, `Unexpected provider request: ${name}, round ${round}`);
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
  res.end(sse(name, round, plan));
}

// The shim only injects a preload into the real command emitted by SonnetBridge._start.
// All inference serialization, response parsing, authentication resolution and retry state remain native.
const guardSource = String.raw`
import assert from "node:assert/strict";
import { appendFileSync } from "node:fs";
import { mock } from "bun:test";
import net from "node:net";
import dns from "node:dns";

const origin = process.env.OPTCHAT_PROOF_ORIGIN;
const logPath = process.env.OPTCHAT_PROOF_GUARD_LOG;
assert(origin && logPath, "Proof preload requires an explicit owned loopback origin and log");
const target = new URL(origin);
assert.equal(target.hostname, "127.0.0.1");
assert.equal(target.protocol, "http:");
assert(target.port);
let selfTesting = false;
let sequence = 0;
function record(value) { appendFileSync(logPath, JSON.stringify({ pid: process.pid, ...value }) + "\n"); }
function deny(api) {
  record({ event: selfTesting ? "selftest-denied" : "denied", api });
  if (!selfTesting) process.exit(71);
  throw new Error("OptChat proof blocked network/process API: " + api);
}
const nativeFetch = globalThis.fetch.bind(globalThis);
async function guardedFetch(input, init) {
  let url;
  try { url = new URL(input instanceof Request ? input.url : String(input)); }
  catch { return deny("fetch-invalid-url"); }
  if (url.origin !== origin || url.hostname !== "127.0.0.1" || url.protocol !== "http:" || url.username || url.password || url.pathname !== "/v1/messages" || (url.search && url.search !== "?beta=true")) {
    return deny("fetch-nonfixture-target");
  }
  if (init?.proxy || init?.unix || init?.socketPath) return deny("fetch-alternate-transport");
  const method = init?.method ?? (input instanceof Request ? input.method : "GET");
  if (method !== "POST") return deny("fetch-nonfixture-method");
  const request = ++sequence;
  record({ event: "fetch", request, method, path: url.pathname });
  const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
  const aborted = () => record({ event: "fetch-aborted", request });
  signal?.addEventListener("abort", aborted, { once: true });
  try { return await nativeFetch(input, { ...init, redirect: "error" }); }
  finally { signal?.removeEventListener("abort", aborted); }
}
Object.defineProperty(guardedFetch, "preconnect", { value: () => deny("fetch.preconnect") });
globalThis.fetch = guardedFetch;
for (const [label, names] of [
  ["node:http", ["request", "get"]],
  ["node:https", ["request", "get"]],
  ["node:net", ["connect", "createConnection"]],
  ["node:tls", ["connect"]],
  ["node:dgram", ["createSocket"]],
  ["node:http2", ["connect"]],
  ["node:child_process", ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"]],
]) {
  const namespace = await import(label);
  const nativeExports = { ...namespace };
  const module = namespace.default;
  for (const name of names) module[name] = () => deny(label + "." + name);
  mock.module(label, () => ({ ...nativeExports, ...module, default: module }));
}
net.Socket.prototype.connect = () => deny("node:net.Socket.connect");
for (const name of ["lookup", "resolve", "resolve4", "resolve6", "resolveAny", "resolveCaa", "resolveCname", "resolveMx", "resolveNaptr", "resolveNs", "resolvePtr", "resolveSoa", "resolveSrv", "resolveTxt", "reverse"]) {
  if (typeof dns[name] === "function") dns[name] = () => deny("node:dns." + name);
  if (typeof dns.promises[name] === "function") dns.promises[name] = () => deny("node:dns.promises." + name);
  if (typeof dns.Resolver.prototype[name] === "function") dns.Resolver.prototype[name] = () => deny("node:dns.Resolver." + name);
  if (typeof dns.promises.Resolver.prototype[name] === "function") dns.promises.Resolver.prototype[name] = () => deny("node:dns.promises.Resolver." + name);
}
for (const name of ["connect", "udpSocket", "spawn", "spawnSync"]) {
  if (typeof Bun[name] === "function") Bun[name] = () => deny("Bun." + name);
}
for (const name of ["WebSocket", "EventSource"]) {
  if (globalThis[name]) globalThis[name] = class { constructor() { deny(name); } };
}
const liveHttps = await import("node:https");
selfTesting = true;
for (const [api, call] of [
  ["fetch", () => globalThis.fetch("https://203.0.113.1/never-send")],
  ["https", () => liveHttps.request("https://203.0.113.1/never-send")],
  ["net", () => new net.Socket().connect(443, "203.0.113.1")],
  ["Bun.connect", () => Bun.connect({ hostname: "203.0.113.1", port: 443, socket: {} })],
]) {
  let denied = false;
  try { await call(); } catch (error) { denied = String(error).includes("OptChat proof blocked"); }
  assert(denied, "Guard negative self-test failed: " + api);
}
selfTesting = false;
record({ event: "ready", guards: ["fetch-exact-origin", "redirect-error", "no-proxy", "node-http-https", "node-net-tls-dns-udp-http2", "Bun-sockets", "WebSocket-EventSource", "no-subprocess-network-bypass"] });
`;

const pythonSource = String.raw`
import copy
import hashlib
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# This driver needs no networking; only the guarded real Bun worker reaches loopback.
def deny_network(*args, **kwargs):
    raise RuntimeError("Python proof driver networking is disabled")
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
socket.getaddrinfo = deny_network
socket.gethostbyname = deny_network
socket.gethostbyname_ex = deny_network
socket.gethostbyaddr = deny_network
socket.socket.sendto = deny_network

from z0int.optchat.compact import COMPACT, SCALE, SonnetBridge, enforce
from z0int.optchat.log import NODE

fixture = json.loads(Path(sys.argv[1]).read_text())
results = {}
workers = []
bridges = []

def send(value):
    print(json.dumps(value), flush=True)

send({"event": "native-input", "system": COMPACT, "scale": SCALE, "node": NODE})

def messages(case):
    step = (
        f"For scale, this line is exactly {NODE} bytes:\n{SCALE}\n\n"
        f"Compress this message into one line, in at most {NODE} bytes:\n"
        f"user: [OPTCHAT_CASE:{case}]\n{fixture['source']}"
    )
    return [
        {"role": "system", "content": COMPACT},
        {"role": "user", "content": [
            {"type": "text", "text": fixture["context"]},
            {"type": "text", "text": step},
        ]},
    ]

def close_owned(bridge, name):
    process = bridge._process
    assert process is not None, "Expected an actual owned Sonnet worker"
    escalations = []
    for method in ("terminate", "kill"):
        original = getattr(process, method)
        def observed(original=original, method=method):
            escalations.append(method)
            return original()
        setattr(process, method, observed)
    started = time.monotonic()
    bridge.close()
    row = {
        "case": name, "pid": process.pid, "exit_code": process.poll(),
        "seconds": time.monotonic() - started, "termination_escalations": escalations,
        "reader_joined": bridge._reader is not None and not bridge._reader.is_alive(),
        "stdin_closed": process.stdin is not None and process.stdin.closed,
        "stdout_closed": process.stdout is not None and process.stdout.closed,
        "pending_after_close": len(bridge._pending),
    }
    workers.append(row)
    assert row["exit_code"] == 0, row
    assert not escalations, row
    assert row["reader_joined"] and row["stdin_closed"] and row["stdout_closed"], row
    assert row["pending_after_close"] == 0, row
    return row

try:
    bridge = SonnetBridge()
    bridges.append(bridge)
    tries = []
    def retry_complete(thread):
        reply = bridge.complete(thread)
        tries.append({"bytes": len(reply.strip().encode()), "sha256": hashlib.sha256(reply.strip().encode()).hexdigest(), "messages": len(thread)})
        return reply
    reply = enforce(messages("retry"), retry_complete)
    assert reply == fixture["plans"]["retry"][-1]["text"], reply
    assert len(tries) == 3 and [row["messages"] for row in tries] == [2, 4, 6], tries
    assert all(row["bytes"] > NODE for row in tries[:-1]) and tries[-1]["bytes"] <= NODE, tries
    results["retry"] = {"reply": reply, "tries": tries, "implementation": "z0int.optchat.compact.enforce + SonnetBridge.complete"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {name: pool.submit(bridge.complete, messages(name)) for name in ("concurrent-a", "concurrent-b")}
        replies = {name: future.result() for name, future in pending.items()}
    for name, value in replies.items():
        assert value == fixture["plans"][name][0]["text"], (name, value)
    results["concurrent"] = replies

    try:
        enforce(messages("empty"), bridge.complete)
    except RuntimeError as error:
        assert "empty" in str(error).lower(), str(error)
        results["empty"] = {"rejected": True, "error": str(error), "boundary": "actual Python bridge/enforce path"}
    else:
        raise AssertionError("Whitespace-only provider text must be rejected, even when thinking is nonempty")

    try:
        enforce(messages("empty-plain"), bridge.complete)
    except RuntimeError as error:
        assert "empty" in str(error).lower(), str(error)
        results["empty_plain"] = {"rejected": True, "error": str(error)}
    else:
        raise AssertionError("A genuinely empty native completion must fail before any SDK resampling")

    try:
        enforce(messages("stall"), bridge.complete)
    except RuntimeError as error:
        assert "Thinking loop" in str(error), str(error)
        results["stall"] = {"rejected": True, "error": str(error)}
    else:
        raise AssertionError("A native thinking-loop error must fail the node, not resample inside the SDK")

    original = messages("mismatch")
    oversized = bridge.complete(original)
    assert len(oversized.encode()) > NODE
    tampered = copy.deepcopy(original)
    tampered[1]["content"][1]["text"] += "\nTAMPERED_INITIAL_SOURCE"
    tampered.extend([{"role": "assistant", "content": oversized}, {"role": "user", "content": "Please shorten this."}])
    try:
        bridge.complete(tampered)
    except RuntimeError as error:
        assert "original conversation" in str(error).lower(), str(error)
        results["conversation_mismatch"] = {"rejected": True, "error": str(error)}
    else:
        raise AssertionError("Changed initial text must not be silently accepted as a size retry")

    eof_reply = bridge.complete(messages("eof"))
    assert eof_reply == fixture["plans"]["eof"][0]["text"]
    results["eof"] = close_owned(bridge, "eof")

    original_agent_dir = os.environ["Z0INT_OPTCHAT_AGENT_DIR"]
    assert "ANTHROPIC_API_KEY" not in os.environ
    os.environ["Z0INT_OPTCHAT_AGENT_DIR"] = fixture["invalid_agent_dir"]
    # A dummy default credential makes the unsafe stock-endpoint fallback observable.
    os.environ["ANTHROPIC_API_KEY"] = fixture["dummy_key"]
    invalid_bridge = SonnetBridge()
    bridges.append(invalid_bridge)
    try:
        try:
            invalid_bridge.complete(messages("invalid-config"))
        except RuntimeError as error:
            assert "model configuration is invalid" in str(error), str(error)
            results["invalid_config"] = {"rejected": True, "error": str(error)}
        else:
            raise AssertionError("Invalid models.yml must fail before stock catalog credentials or provider I/O")
        results["invalid_config"]["worker"] = close_owned(invalid_bridge, "invalid-config")
    finally:
        os.environ["Z0INT_OPTCHAT_AGENT_DIR"] = original_agent_dir
        del os.environ["ANTHROPIC_API_KEY"]

    abort_bridge = SonnetBridge()
    bridges.append(abort_bridge)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(abort_bridge.complete, messages("abort"))
        send({"event": "awaiting-abort-request"})
        control = json.loads(sys.stdin.readline())
        assert control == {"abort_request_reached": True}, control
        results["abort"] = close_owned(abort_bridge, "abort")
        try:
            pending.result()
        except RuntimeError as error:
            assert "closed" in str(error).lower(), str(error)
            results["abort"]["pending_rejected"] = True
            results["abort"]["error"] = str(error)
        else:
            raise AssertionError("Closing an owned worker must reject its in-flight request")
    send({"event": "result", "ok": True, "results": results, "workers": workers})
except Exception as error:
    send({"event": "result", "ok": False, "error": str(error), "results": results, "workers": workers})
    sys.exit(1)
finally:
    for bridge in bridges:
        bridge.close()
`;

async function handle(req, res) {
  try {
    assert.equal(req.socket.remoteAddress, "127.0.0.1", "Fixture accepts IPv4 loopback only");
    assert.equal(req.method, "POST");
    assert(req.url === "/v1/messages" || req.url === "/v1/messages?beta=true", "Only native Anthropic Messages is permitted");
    const authValues = [req.headers["x-api-key"], req.headers.authorization].filter(value => value !== undefined);
    assert(authValues.length > 0, "Native SDK must resolve its canonical configured dummy credential");
    assert(authValues.every(value => value === dummyKey || value === `Bearer ${dummyKey}`), "Nonfixture credentials are forbidden");
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = object(JSON.parse(Buffer.concat(chunks).toString("utf8")));
    assert(Array.isArray(body.messages));
    const first = body.messages.find(message => message.role === "user");
    assert(first, "Missing initial user input");
    const match = textOf(first.content).match(/\[OPTCHAT_CASE:([a-z-]+)\]/);
    assert(match, "Missing synthetic fixture case marker");
    const name = match[1];
    const round = (rounds.get(name) ?? 0) + 1;
    rounds.set(name, round);
    const captured = { case: name, round, method: req.method, path: req.url, fixture_credential_only: true, body };
    requests.push(captured);
    activeRequests++;
    peakRequests = Math.max(peakRequests, activeRequests);
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      activeRequests--;
      if (name === "abort") {
        captured.disconnected_before_response = !res.writableFinished;
        abortClosed.resolve(captured);
      }
    };
    res.once("finish", release);
    res.once("close", release);
    if (name.startsWith("concurrent-")) {
      assert.equal(round, 1, "Concurrency fixture must not retry provider requests");
      concurrentResponses.set(name, res);
      concurrentPeak = Math.max(concurrentPeak, concurrentResponses.size);
      if (concurrentResponses.size === 2) {
        for (const [caseName, response] of concurrentResponses) respond(response, caseName, 1);
      }
    } else if (name === "abort") {
      assert.equal(round, 1);
      // Hold before response headers: shutdown must abort the native pending fetch, not finish a synthetic response.
      driver.stdin.write(JSON.stringify({ abort_request_reached: true }) + "\n");
    } else {
      respond(res, name, round);
    }
  } catch (error) {
    serverErrors.push(messageOf(error));
    if (!res.headersSent) res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ type: "error", error: { type: "invalid_request_error", message: "Synthetic proof fixture assertion failed" } }));
  }
}

try {
  check("bun_worker_runtime", () => assert(bunExecutable && existsSync(bunExecutable), "Run this proof with Bun; the Node supervisor must retain its executable."));
  const roots = process.env.Z0INT_OMP_ROOT
    ? [resolve(process.env.Z0INT_OMP_ROOT)]
    : [
        "/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@oh-my-pi/pi-coding-agent",
        join(homedir(), ".bun/install/global/node_modules/@oh-my-pi/pi-coding-agent"),
      ];
  const ompRoot = roots.find(root => existsSync(join(root, "src/sdk.ts")));
  check("actual_installed_sdk_available", () => {
    assert(ompRoot, `Installed native OMP SDK not found; checked ${roots.join(", ")}. Set Z0INT_OMP_ROOT to its pi-coding-agent package directory. No fallback is allowed.`);
    const aiRoot = join(ompRoot, "..", "pi-ai");
    assert(existsSync(join(aiRoot, "src/index.ts")), `Native pi-ai source is unavailable at ${aiRoot}; no substitute serializer is allowed.`);
    assert(existsSync(join(ompRoot, "src/config/model-registry.ts")), "Native ModelRegistry source is required");
    return {
      omp_root: ompRoot, omp_version: JSON.parse(readFileSync(join(ompRoot, "package.json"), "utf8")).version,
      pi_ai_root: aiRoot, pi_ai_version: JSON.parse(readFileSync(join(aiRoot, "package.json"), "utf8")).version,
      bridge: join(repo, "src/z0int/optchat/sonnet.mjs"), python_bridge: join(repo, "src/z0int/optchat/compact.py"),
    };
  });
  home = mkdtempSync(join(tmpdir(), "optchat-sonnet-proof-home-"));
  const agentDir = join(home, "agent");
  const cwd = join(home, "fixture");
  const bin = join(home, "bin");
  for (const directory of [agentDir, cwd, bin, join(home, "tmp"), join(home, "config"), join(home, "cache"), join(home, "data")]) mkdirSync(directory, { mode: 0o700 });
  writeFileSync(join(home, "bunfig.toml"), "[install]\nauto = \"disable\"\n");
  writeFileSync(join(agentDir, "config.yml"), "{}\n");
  writeFileSync(join(agentDir, "agent.yml"), "{}\n");
  server = createServer((req, res) => { void handle(req, res); });
  server.on("connection", socket => { sockets.add(socket); socket.once("close", () => sockets.delete(socket)); });
  await new Promise((resolveReady, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveReady);
  });
  const address = server.address();
  assert(address && typeof address !== "string" && address.address === "127.0.0.1");
  origin = `http://127.0.0.1:${address.port}`;
  // models.yml `providers.*.models` is a custom-model definition. validateProviderConfiguration
  // then requires provider-level baseUrl; a same-id row with only model.baseUrl fails the whole
  // file (tryLoad status=error), so apiKey is never installed and getApiKey returns undefined.
  // Provider-level apiKey is AuthStorage.keys.setConfig (override, not fallback). Provider-level
  // baseUrl overlays the bundled catalog, keeping native thinking/context for claude-sonnet-4-6.
  const config = { providers: { anthropic: {
    api: "anthropic-messages", auth: "apiKey", apiKey: dummyKey, baseUrl: origin,
  } } };
  // JSON is valid YAML. These fields come from installed models-config-schema-bundle.ts.
  writeFileSync(join(agentDir, "models.yml"), JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
  const invalidAgentDir = join(home, "invalid-agent");
  mkdirSync(invalidAgentDir, { mode: 0o700 });
  const invalidConfig = { providers: { anthropic: {
    api: "anthropic-messages", apiKey: dummyKey, models: [{ id: model, baseUrl: origin }],
  } } };
  writeFileSync(join(invalidAgentDir, "models.yml"), JSON.stringify(invalidConfig) + "\n", { mode: 0o600 });
  const guard = join(home, "loopback-guard.mjs");
  const guardLog = join(home, "guard.jsonl");
  writeFileSync(guard, guardSource);
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  writeFileSync(join(bin, "bun"), `#!/bin/sh\nexec ${quote(bunExecutable)} --no-install --no-env-file --config=${quote(join(home, "bunfig.toml"))} --preload=${quote(guard)} "$@"\n`, { mode: 0o700 });
  const context = "<chat>\nCONTEXT_HEAD\n" + "a".repeat(50_000) + "CONTEXT_MIDDLE_50000" + "b".repeat(30_000) + "CONTEXT_MIDDLE_80000" + "c".repeat(20_000) + "CONTEXT_MIDDLE_100000" + "d".repeat(20_000) + "\nCONTEXT_TAIL\n</chat>";
  const source = "SOURCE_HEAD\n" + "e".repeat(32_000) + "SOURCE_IMPORTANT_MIDDLE" + "界".repeat(12_000) + "f".repeat(20_000) + "\nSOURCE_TAIL";
  const thinkingPlan = (text, round) => ({
    text, thinking: `SCRIPTED_THINKING_${round}_not_real_inference`,
    signature: Buffer.from(`opaque-scripted-native-signature-${round}`).toString("base64"),
  });
  const facts = [
    "user: preserve café names and exact Unicode bytes when recording the repair.",
    "The first candidate retains the selected native Sonnet model and medium thinking effort.",
    "talk: compactor sources include the entire user message, with its original newlines.",
    "The stable context contains every summarized prefix line, without structural tree addresses.",
    "echo: a loopback Anthropic stream supplies distinct native thinking and signature blocks.",
    "Size corrections stay in the same conversation and replay the previous assistant unchanged.",
    "A second worker owns concurrent requests independently, with no cross-request reply mixing.",
    "Closing the bridge must abort pending fetches, join its reader, and reap the owned process.",
    "An empty response fails the node rather than creating a successful blank summary.",
    "The proof checks wire caching only and makes no claim about live provider cache hits.",
  ];
  const firstOversized = facts.join(" ");
  const secondOversized = facts.slice(0, 7).join(" ");
  assert(Buffer.byteLength(firstOversized) > Buffer.byteLength(secondOversized));
  assert(Buffer.byteLength(secondOversized) > 512);
  fixtureData = { context, source, invalid_agent_dir: invalidAgentDir, dummy_key: dummyKey, plans: {
    retry: [thinkingPlan(firstOversized, 1), thinkingPlan(secondOversized, 2), thinkingPlan("user: FINAL_SYNTHETIC_SUMMARY_WITHIN_512_BYTES", 3)],
    "concurrent-a": [thinkingPlan("user: FIXTURE_CONCURRENT_A_DISTINCT_REPLY", "a")],
    "concurrent-b": [thinkingPlan("user: FIXTURE_CONCURRENT_B_DISTINCT_REPLY", "b")],
    empty: [thinkingPlan(" \n\t ", "empty")],
    "empty-plain": [{ text: "", outputTokens: 0 }, thinkingPlan("user: UNEXPECTED_RESAMPLE_AFTER_EMPTY_COMPLETION", "unexpected")],
    stall: [thinkingPlan("q".repeat(650), "stall")],
    mismatch: [thinkingPlan(firstOversized, "mismatch")],
    eof: [thinkingPlan("user: OWNED_WORKER_EOF_REPLY", "eof")],
  } };
  const fixturePath = join(home, "fixture.json");
  const driverPath = join(home, "driver.py");
  writeFileSync(fixturePath, JSON.stringify(fixtureData));
  writeFileSync(driverPath, pythonSource);
  const env = {
    PATH: bin + delimiter + (process.env.PATH || "/usr/bin:/bin"), HOME: home, LANG: "C.UTF-8", LC_ALL: "C.UTF-8",
    TMPDIR: join(home, "tmp"), XDG_CONFIG_HOME: join(home, "config"), XDG_CACHE_HOME: join(home, "cache"), XDG_DATA_HOME: join(home, "data"),
    PYTHONPATH: join(repo, "src"), PYTHONNOUSERSITE: "1", PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1",
    Z0INT_HOME: home, Z0INT_OMP_ROOT: ompRoot, Z0INT_OPTCHAT_AGENT_DIR: agentDir, Z0INT_OPTCHAT_MODEL: model,
    OPTCHAT_PROOF_ORIGIN: origin, OPTCHAT_PROOF_GUARD_LOG: guardLog,
  };
  check("production_environment_not_inherited", () => {
    assert(!Object.keys(env).some(name => /AUTH|TOKEN|BROKER|PROXY|ANTHROPIC|OPENAI|FOUNDRY|BEDROCK|VERTEX|PI_CONFIG|BUN_OPTIONS|NODE_OPTIONS/.test(name)), "Provider/auth/broker overrides must not enter the fixture environment");
    return { inheritance: "explicit allowlist only", temp_home: true, temp_agent_dir: true, temp_cwd: true, python_bytecode_disabled: true, bun_auto_install_disabled: true, dotenv_loading_disabled: true, credential_mechanism: "models.yml providers.anthropic.apiKey+baseUrl (dummy literal + loopback), native ModelRegistry.setConfig override + AuthStorage.keys.get", per_model_loopback_override: model };
  });
  let stdoutBuffer = "";
  let stderr = "";
  let protocolError;
  driver = spawn(process.env.Z0INT_PYTHON || "python3", [driverPath, fixturePath], { cwd, env, detached: true, stdio: ["pipe", "pipe", "pipe"] });
  driver.stderr.on("data", chunk => { stderr = (stderr + String(chunk)).slice(-16_000); });
  driver.stdout.on("data", chunk => {
    stdoutBuffer += String(chunk);
    for (;;) {
      const newline = stdoutBuffer.indexOf("\n");
      if (newline < 0) break;
      const line = stdoutBuffer.slice(0, newline);
      stdoutBuffer = stdoutBuffer.slice(newline + 1);
      try {
        const value = object(JSON.parse(line));
        if (value.event === "native-input") nativeInput = value;
        else if (value.event === "result") { assert(!driverResult, "Duplicate driver result"); driverResult = value; }
        else assert.equal(value.event, "awaiting-abort-request", "Unexpected Python stdout record");
      } catch (error) { protocolError ??= messageOf(error); }
    }
  });
  driver.once("error", error => driverExited.reject(error));
  driver.once("close", (code, signal) => driverExited.resolve({ code, signal }));
  const deadline = Promise.withResolvers();
  const timeout = setTimeout(() => deadline.reject(new Error("Native Sonnet proof exceeded its 120-second deadline; no scripted-success fallback is permitted.")), 120_000);
  let exit;
  try { exit = await Promise.race([driverExited.promise, deadline.promise]); }
  finally { clearTimeout(timeout); }
  if (existsSync(guardLog)) guardEvents = readFileSync(guardLog, "utf8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
  check("actual_python_bridge_completed", () => {
    assert(!protocolError, protocolError);
    assert.equal(stdoutBuffer, "", "Python result protocol must end on a whole JSON line");
    const diagnostic = stderr.split("\n").filter(line => !/authorization|x-api-key|api.?key|token|secret|bearer/i.test(line)).join("\n");
    assert.equal(exit.code, 0, [driverResult?.error, messageOf(diagnostic)].filter(Boolean).join("\n") || "Python driver did not exit successfully");
    assert.equal(exit.signal, null);
    assert(driverResult?.ok, driverResult?.error || "Missing Python result");
    assert.deepEqual(serverErrors, [], "Provider fixture rejected a native request");
    return { exit, implementation: "actual source SonnetBridge and enforce; packaged sonnet.mjs; installed native pi-ai Anthropic provider" };
  });
  check("native_route_without_fallback", () => {
    assert.equal(requests.length, 11);
    for (const row of requests) {
      assert.equal(row.body.model, model);
      assert.equal(row.body.stream, true);
      assert.equal(row.fixture_credential_only, true);
      assert(["adaptive", "enabled"].includes(row.body.thinking?.type), "Native thinking must be enabled");
      assert.equal(row.body.output_config?.effort, "medium", "The creator's compactor uses medium effort");
    }
    return { provider: "anthropic", model, transport: "native anthropic-messages", requests: requests.length, real_inference_calls: 0 };
  });
  check("full_long_source_and_context_on_provider_wire", () => {
    assert(context.length >= 100_000);
    assert(source.length > 60_000);
    for (const row of requests) {
      const first = row.body.messages.find(message => message.role === "user");
      assert.equal(textOf(first.content), context + stepFor(row.case), `${row.case}: full initial text must be unmodified`);
      assert.equal(textOf(row.body.system), nativeInput.system, "Actual compactor system prompt must survive serialization");
      for (const marker of ["CONTEXT_HEAD", "CONTEXT_MIDDLE_50000", "CONTEXT_MIDDLE_80000", "CONTEXT_MIDDLE_100000", "CONTEXT_TAIL", "SOURCE_HEAD", "SOURCE_IMPORTANT_MIDDLE", "SOURCE_TAIL"]) assert(textOf(first.content).includes(marker), `Missing ${marker}`);
    }
    return { context_characters: context.length, context_utf8_bytes: Buffer.byteLength(context), context_sha256: digest(context), source_characters: source.length, source_utf8_bytes: Buffer.byteLength(source), source_sha256: digest(source), requests_checked: requests.length };
  });
  check("context_breakpoint_and_automatic_request_end_cache", () => {
    for (const row of requests) {
      const first = row.body.messages.find(message => message.role === "user");
      assert(Array.isArray(first.content));
      assert.equal(first.content[0].type, "text");
      assert.equal(first.content[0].text, context, "Explicit cache breakpoint must be exactly the entire stable context");
      assert.deepEqual(first.content[0].cache_control, { type: "ephemeral" });
      assert.deepEqual(row.body.cache_control, { type: "ephemeral" }, "Automatic request-end caching must be on the emitted provider payload");
      const blockMarks = [];
      for (const block of row.body.system ?? []) if (block.cache_control) blockMarks.push(block);
      for (const message of row.body.messages) if (Array.isArray(message.content)) for (const block of message.content) if (block.cache_control) blockMarks.push(block);
      assert.equal(blockMarks.length, 1, "Only the stable context may retain an explicit cache breakpoint");
      assert.equal(blockMarks[0], first.content[0]);
    }
    return { explicit_context_breakpoint: true, automatic_request_end_cache: true, actual_provider_cache_hit_proven: false, explanation: "Wire conformance only; scripted usage contains zero cache reads/writes." };
  });
  check("same_initial_text_across_actual_enforce_size_retries", () => {
    const retries = requests.filter(row => row.case === "retry");
    assert.deepEqual(retries.map(row => row.round), [1, 2, 3]);
    assert.deepEqual(retries.map(row => row.body.messages.length), [1, 3, 5]);
    for (const row of retries.slice(1)) assert.deepEqual(row.body.messages[0], retries[0].body.messages[0], "Initial native user blocks must not change across size retries");
    assert.equal(driverResult.results.retry.tries.length, 3);
    for (const row of retries.slice(1)) {
      const index = row.round - 2;
      const assistant = row.body.messages.at(-2);
      assert.equal(assistant.role, "assistant");
      assert.equal(textOf(assistant.content), fixtureData.plans.retry[index].text);
      const correction = row.body.messages.at(-1);
      assert.equal(correction.role, "user");
      const bytes = Buffer.byteLength(fixtureData.plans.retry[index].text);
      assert(textOf(correction.content).includes(`That line is ${bytes} bytes; the limit is 512.`));
      assert(textOf(correction.content).includes("| ← LIMIT"));
    }
    return driverResult.results.retry;
  });
  check("native_thinking_and_signature_replay", () => {
    for (const row of requests.filter(row => row.case === "retry" && row.round > 1)) {
      const assistants = row.body.messages.filter(message => message.role === "assistant");
      assert.equal(assistants.length, row.round - 1);
      for (const [index, message] of assistants.entries()) {
        const plan = fixtureData.plans.retry[index];
        const thinking = message.content.find(block => block.type === "thinking");
        assert(thinking, "Bridge must replay the actual native assistant, not a reconstructed text-only response");
        assert.equal(thinking.thinking, plan.thinking);
        assert.equal(thinking.signature, plan.signature, "Split signature SSE deltas must be preserved verbatim on replay");
        assert.equal(textOf(message.content), plan.text);
      }
    }
    return { native_assistant_blocks_checked: 3, split_signature_deltas: true, signatures_are_scripted_opaque_fixtures: true };
  });
  check("independent_concurrent_requests", () => {
    assert(concurrentPeak >= 2 && peakRequests >= 2, "The fixture only releases concurrent responses after two independent requests are actually in flight");
    assert.equal(rounds.get("concurrent-a"), 1);
    assert.equal(rounds.get("concurrent-b"), 1);
    assert.notEqual(driverResult.results.concurrent["concurrent-a"], driverResult.results.concurrent["concurrent-b"]);
    return { provider_peak_in_flight: peakRequests, concurrent_barrier_peak: concurrentPeak, distinct_reply_ids_preserved: true };
  });
  check("empty_reply_rejected_without_false_success", () => {
    assert.equal(rounds.get("empty"), 1);
    assert.equal(driverResult.results.empty.rejected, true);
    return driverResult.results.empty;
  });
  check("genuinely_empty_completion_fails_without_sdk_resampling", () => {
    assert.equal(rounds.get("empty-plain"), 1);
    assert.equal(driverResult.results.empty_plain.rejected, true);
    return driverResult.results.empty_plain;
  });
  check("native_stall_fails_without_hidden_sdk_resampling", () => {
    assert.equal(rounds.get("stall"), 1, "Only the pump may retry a failed node, after ten seconds");
    assert.equal(driverResult.results.stall.rejected, true);
    return driverResult.results.stall;
  });
  check("tampered_conversation_rejected_before_provider_io", () => {
    assert.equal(rounds.get("mismatch"), 1, "The changed-initial-text size retry must not reach the provider");
    assert.equal(driverResult.results.conversation_mismatch.rejected, true);
    return driverResult.results.conversation_mismatch;
  });
  check("invalid_native_config_fails_before_credential_fallback", () => {
    assert.equal(driverResult.results.invalid_config.rejected, true);
    const pid = driverResult.results.invalid_config.worker.pid;
    assert(!guardEvents.some(event => event.pid === pid && event.event === "fetch"), "Invalid config must fail before any provider request");
    return { rejected: true, dummy_default_credential_present: true, provider_requests: 0, worker_exit: driverResult.results.invalid_config.worker.exit_code };
  });
  const abortDeadline = setTimeout(() => abortClosed.reject(new Error("Aborted native provider socket remained open after worker EOF")), 5_000);
  let abortedRequest;
  try { abortedRequest = await abortClosed.promise; }
  finally { clearTimeout(abortDeadline); }
  check("clean_owned_worker_eof_and_abort", () => {
    assert.equal(driverResult.workers.length, 3);
    for (const worker of driverResult.workers) {
      assert.equal(worker.exit_code, 0);
      assert.deepEqual(worker.termination_escalations, [], "Clean EOF must not rely on terminate/kill fallback");
      assert.equal(worker.reader_joined, true);
      assert.equal(worker.stdin_closed, true);
      assert.equal(worker.stdout_closed, true);
      assert.equal(worker.pending_after_close, 0);
    }
    assert.equal(driverResult.results.abort.pending_rejected, true);
    assert.equal(abortedRequest.disconnected_before_response, true);
    const abortPid = driverResult.results.abort.pid;
    assert(guardEvents.some(event => event.pid === abortPid && event.event === "fetch-aborted"), "Owned worker EOF must deliver an actual AbortSignal to its pending native fetch");
    return { workers: driverResult.workers, in_flight_provider_disconnected: true, pending_python_future_rejected: true, native_fetch_abort_signal_observed: true };
  });
  check("fail_closed_loopback_network_guard", () => {
    assert(!guardEvents.some(event => event.event === "denied"), "Unexpected off-fixture network or subprocess attempt was denied; the proof must fail, never silently continue");
    assert.equal(guardEvents.filter(event => event.event === "fetch").length, requests.length);
    for (const worker of driverResult.workers) {
      assert.equal(guardEvents.filter(event => event.pid === worker.pid && event.event === "ready").length, 1);
      assert.equal(guardEvents.filter(event => event.pid === worker.pid && event.event === "selftest-denied").length, 4);
    }
    return { host: "127.0.0.1", exact_owned_origin_only: true, offhost_requests_sent: 0, negative_guard_selftests: 12, redirects_forbidden: true, auth_headers_not_recorded: true };
  });
} catch (error) {
  failure = messageOf(error);
} finally {
  // This detached process group is created by this proof only; never signal an operator process.
  if (driver?.pid) {
    try { process.kill(-driver.pid, "SIGTERM"); } catch (error) { if (error.code !== "ESRCH") failure ??= messageOf(error); }
    if (driver.exitCode === null && driver.signalCode === null) {
      const grace = setTimeout(() => {
        try { process.kill(-driver.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") failure ??= messageOf(error); }
      }, 2_000);
      try { await driverExited.promise.catch(() => {}); }
      finally { clearTimeout(grace); }
      // Also reap descendants if the Python process exited before its real worker.
      try { process.kill(-driver.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") failure ??= messageOf(error); }
    }
  }
  if (server) {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolveClosed => server.close(resolveClosed));
  }
  if (home) {
    const guardLog = join(home, "guard.jsonl");
    if (existsSync(guardLog)) {
      try { guardEvents = readFileSync(guardLog, "utf8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line)); }
      catch (error) { failure ??= messageOf(error); }
    }
    rmSync(home, { recursive: true, force: true });
  }
  const report = {
    ok: !failure, kind: "scripted-loopback-native-sonnet-wire-proof", model: `anthropic/${model}`,
    real_inference_calls: 0, real_provider_cache_hits_proven: false,
    checks, ...(failure ? { error: failure } : {}), server_errors: serverErrors,
    requests_file: "requests.json", guard_events_file: "guard-events.json", python_result: driverResult ?? null,
    temp_fixture_removed: home ? !existsSync(home) : true,
    assumptions: [
      "Bun and Python 3 are installed; Z0INT_PYTHON may select the Python executable.",
      "Z0INT_OMP_ROOT points to the actual pi-coding-agent package, with sibling pi-ai; otherwise the bridge's known installed layouts are searched.",
      "Installed models.yml supports native providers.anthropic api/auth/apiKey plus provider-level baseUrl (not a custom models[] row); no SDK files are modified.",
      "All provider responses, reasoning and opaque signatures are scripted synthetic SSE; no real Sonnet inference or Anthropic cache hit is claimed.",
      "Only synthetic input and actual packaged compactor instructions are captured; production environment/auth/broker/provider settings are not inherited.",
    ],
  };
  writeFileSync(join(output, "requests.json"), JSON.stringify(requests, null, 2) + "\n", { mode: 0o600 });
  writeFileSync(join(output, "guard-events.json"), JSON.stringify(guardEvents, null, 2) + "\n", { mode: 0o600 });
  writeFileSync(join(output, "proof.json"), JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
  console.log(JSON.stringify({ ok: report.ok, checks: checks.length, requests: requests.length, real_inference_calls: 0, receipt: join(output, "proof.json"), ...(failure ? { error: failure } : {}) }));
  if (failure) process.exitCode = 1;
}
