import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const roots = process.env.Z0INT_OMP_ROOT
  ? [process.env.Z0INT_OMP_ROOT]
  : [
      "/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@oh-my-pi/pi-coding-agent",
      join(homedir(), ".bun/install/global/node_modules/@oh-my-pi/pi-coding-agent"),
    ];
const ompRoot = roots.find(root => existsSync(join(root, "src/sdk.ts")));
const sessions = new Map();
const active = new Map();
let runtime;
let closed = false;

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function textOf(message) {
  return message.content.filter(block => block.type === "text").map(block => block.text).join("");
}

function requestOf(value) {
  if (!record(value) || typeof value.id !== "string" || !value.id || typeof value.session !== "string" || !value.session) {
    throw new Error("Compactor request requires string id and session.");
  }
  if (!Array.isArray(value.messages) || value.messages.length < 2) {
    throw new Error("Compactor request requires system and user messages.");
  }
  for (const [index, message] of value.messages.entries()) {
    if (!record(message) || message.role !== (index === 0 ? "system" : index % 2 ? "user" : "assistant")) {
      throw new Error("Compactor conversation must alternate user and assistant messages after its system prompt.");
    }
    if (typeof message.content !== "string") {
      if (message.role !== "user" || !Array.isArray(message.content) || !message.content.every(block => record(block) && block.type === "text" && typeof block.text === "string")) {
        throw new Error("Compactor message content must be text.");
      }
    }
  }
  return value;
}

async function loadRuntime() {
  if (!ompRoot) throw new Error("Installed OMP SDK not found. Set Z0INT_OMP_ROOT to its package directory.");
  const sdk = await import(pathToFileURL(join(ompRoot, "src/sdk.ts")).href);
  const { ModelRegistry } = await import(pathToFileURL(join(ompRoot, "src/config/model-registry.ts")).href);
  const aiRoot = join(ompRoot, "..", "pi-ai");
  const ai = await import(pathToFileURL(join(aiRoot, "src/index.ts")).href);
  const agentDir = process.env.Z0INT_OPTCHAT_AGENT_DIR || undefined;
  const cwd = process.cwd();
  const settings = await sdk.Settings.loadReadOnly({ agentDir, cwd });
  const auth = await sdk.discoverAuthStorage(agentDir, { settings, cwd });
  try {
    const registry = new ModelRegistry(auth, agentDir ? join(agentDir, "models.yml") : undefined, { settings });
    if (registry.getError()) {
      throw new Error("Native Anthropic model configuration is invalid. Check models.yml.");
    }
    const modelId = process.env.Z0INT_OPTCHAT_MODEL || "claude-sonnet-4-6";
    const model = registry.find("anthropic", modelId);
    if (!model || model.api !== "anthropic-messages" || !model.id.includes("sonnet")) {
      throw new Error(`Configured native Anthropic Sonnet model is unavailable: ${modelId}.`);
    }
    if (model.contextWindow < 200_000) throw new Error("The Sonnet compactor requires at least a 200,000-token context.");
    return { ai, auth, registry, model };
  } catch (error) {
    auth.close();
    throw error;
  }
}

function getRuntime() {
  runtime ??= loadRuntime();
  return runtime;
}

async function getCredential(loaded, session, signal) {
  let apiKey = await loaded.registry.getApiKey(loaded.model, session, { signal });
  if (!apiKey) {
    await loaded.auth.credentials.reload();
    apiKey = await loaded.registry.getApiKey(loaded.model, session, { signal });
  }
  if (!apiKey) throw new Error("Anthropic credentials are missing. Run omp login anthropic.");
  return apiKey;
}

function cacheContext(payload, context) {
  if (!record(payload) || !Array.isArray(payload.messages)) throw new Error("Expected an Anthropic messages payload.");
  const first = payload.messages.find(message => message.role === "user");
  if (!first) throw new Error("Compactor payload has no user message.");
  for (const message of payload.messages) {
    if (!Array.isArray(message.content)) continue;
    for (const block of message.content) if (record(block)) delete block.cache_control;
  }
  if (Array.isArray(payload.system)) {
    for (const block of payload.system) if (record(block)) delete block.cache_control;
  }
  const blocks = typeof first.content === "string" ? [{ type: "text", text: first.content }] : first.content;
  if (!Array.isArray(blocks) || !blocks.length || blocks[0].type !== "text" || !blocks[0].text.startsWith(context)) {
    throw new Error("Anthropic serialization changed the full compactor context.");
  }
  const trailing = blocks[0].text.slice(context.length);
  first.content = [
    { type: "text", text: context, cache_control: { type: "ephemeral" } },
    ...(trailing ? [{ type: "text", text: trailing }] : []),
    ...blocks.slice(1),
  ];
  payload.cache_control = { type: "ephemeral" };
  return payload;
}

function conversation(request) {
  const initial = JSON.stringify(request.messages.slice(0, 2));
  let state = sessions.get(request.session);
  if (request.messages.length === 2) {
    state = { initial, assistants: [], timestamps: new Map() };
    sessions.set(request.session, state);
  }
  if (!state || state.initial !== initial) throw new Error("Compactor retry lost its original conversation.");
  let assistantIndex = 0;
  const messages = request.messages.slice(1).map((message, index) => {
    if (message.role === "assistant") {
      const native = state.assistants[assistantIndex++];
      if (!native || textOf(native).trim() !== message.content) throw new Error("Compactor retry does not match its native assistant response.");
      return native;
    }
    if (!state.timestamps.has(index)) state.timestamps.set(index, Date.now());
    return { role: "user", content: message.content, timestamp: state.timestamps.get(index) };
  });
  const context = request.messages[1].content;
  if (!Array.isArray(context) || context.length !== 2) throw new Error("Compactor requires two initial text blocks.");
  return { state, messages, context: context[0].text };
}

function answer(value) {
  if (!closed) process.stdout.write(JSON.stringify(value) + "\n");
}

async function complete(request) {
  if (active.has(request.id)) throw new Error("Duplicate active compactor request id.");
  const controller = new AbortController();
  active.set(request.id, controller);
  let apiKey;
  try {
    const loaded = await getRuntime();
    apiKey = await getCredential(loaded, request.session, controller.signal);
    const { state, messages, context } = conversation(request);
    // One native attempt. SDK completeSimple silently resamples thinking-loop errors;
    // failed nodes belong to the pump's ten-second retry policy.
    const response = loaded.ai.streamSimple(
      loaded.model,
      { systemPrompt: [request.messages[0].content], messages },
      {
        apiKey,
        signal: controller.signal,
        reasoning: "medium",
        maxTokens: 8192,
        cacheRetention: "short",
        statefulResponses: false,
        // Return a genuine empty completion to enforce, which fails the node.
        // Do not let the provider wrapper silently resample it first.
        acceptEmptyResponse: true,
        onPayload: payload => cacheContext(payload, context),
      },
    );
    for await (const _event of response) {}
    const assistant = await response.result();
    if (assistant.stopReason === "error" || assistant.stopReason === "aborted") {
      throw new Error(assistant.errorMessage || `Sonnet compactor stopped: ${assistant.stopReason}.`);
    }
    const text = textOf(assistant);
    state.assistants.push(assistant);
    if (!text.trim() || Buffer.byteLength(text.trim(), "utf8") <= 512 || state.assistants.length >= 5) sessions.delete(request.session);
    answer({ id: request.id, ok: true, text, model: loaded.model.id, usage: assistant.usage });
  } catch (error) {
    sessions.delete(request.session);
    const raw = error instanceof Error ? error.message : String(error);
    const message = apiKey ? raw.replaceAll(apiKey, "[redacted]") : raw;
    answer({ id: request.id, ok: false, error: message });
  } finally {
    active.delete(request.id);
  }
}

async function shutdown() {
  if (closed) return;
  closed = true;
  for (const controller of active.values()) controller.abort();
  sessions.clear();
  try { (await runtime)?.auth.close(); } catch { /* Startup failure has no open store. */ }
  process.exit(0);
}

if (process.argv.includes("--check")) {
  try {
    const loaded = await getRuntime();
    await getCredential(loaded, "optchat-readiness-check");
    answer({ ok: true, provider: loaded.model.provider, model: loaded.model.id, contextWindow: loaded.model.contextWindow });
  } catch (error) {
    answer({ ok: false, error: error instanceof Error ? error.message : String(error) });
    process.exitCode = 1;
  } finally { try { (await runtime)?.auth.close(); } catch {} }
} else {
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  input.on("line", line => {
    let value;
    try { value = requestOf(JSON.parse(line)); }
    catch (error) {
      answer({ id: record(value) && typeof value.id === "string" ? value.id : null, ok: false, error: error instanceof Error ? error.message : String(error) });
      return;
    }
    void complete(value).catch(error => answer({ id: value.id, ok: false, error: error instanceof Error ? error.message : String(error) }));
  });
  input.on("close", () => void shutdown());
  process.once("SIGTERM", () => void shutdown());
  process.once("SIGINT", () => void shutdown());
}
