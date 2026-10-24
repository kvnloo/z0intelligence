import { mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { coverageLine, sentences } from "./coverage.js";

const fail = message => { throw new Error(message); };
const stamp = value => typeof value === "number" && Number.isFinite(value) ? value
  : typeof value === "string" && Number.isFinite(Date.parse(value)) ? Date.parse(value) : undefined;
const messageText = message => typeof message?.content === "string" ? message.content
  : (message?.content ?? []).filter(part => part.type === "text").map(part => part.text).join("\n");
const succeeded = result => result.isError === false && result.details?.timedOut !== true
  && (result.details?.exitCode === undefined || result.details.exitCode === 0);

function parseAsks(text) {
  const lines = text.split(/\r?\n/);
  const starts = lines.flatMap((line, index) => line === "ASKS:" ? [index] : []);
  if (starts.length !== 1) fail("Final reply requires exactly one ASKS: block");
  const marks = [];
  let index = starts[0] + 1;
  for (; index < lines.length && lines[index].startsWith("- "); index++) marks.push(coverageLine(lines[index], "asks"));
  if (!marks.length) fail("ASKS block has no sentence marks");
  return marks;
}

function pendingAsks(source, state) {
  const passed = new Set(state.passed);
  const pending = [];
  for (const entry of source.entries) {
    const texts = source.users.get(entry.id);
    if (!texts) continue;
    let index = 0;
    for (const text of texts) for (const sentence of sentences(text)) {
      const key = `${entry.id}:${index++}`;
      if (!passed.has(entry.id) || state.delegated[key]) pending.push({ key, id: entry.id, sentence,
        timestamp: stamp(entry.message.timestamp) ?? stamp(entry.timestamp) });
    }
  }
  const keys = new Set(pending.map(ask => ask.key));
  for (const key of Object.keys(state.delegated)) if (!keys.has(key)) fail(`Open delegated ask ${key} disappeared from user history`);
  return pending;
}

function evidenceFor(manager, live) {
  const calls = new Map();
  const results = new Map();
  const sessionStart = stamp(manager.getHeader()?.timestamp);
  for (const entry of manager.getEntries()) {
    const message = entry.message;
    if (message?.role === "assistant" && Array.isArray(message.content)) {
      for (const part of message.content) if (part.type === "toolCall") calls.set(part.id, part);
    }
    if (message?.role !== "toolResult" || !calls.has(message.toolCallId)) continue;
    const call = calls.get(message.toolCallId);
    const at = stamp(message.timestamp) ?? stamp(entry.timestamp);
    // Full-history forks copy old entries. Those results were not produced in this session.
    if (sessionStart === undefined || at === undefined || at < sessionStart) continue;
    results.set(message.toolCallId, { ...message, at, input: call.arguments });
  }
  for (const [id, result] of live ?? []) results.set(id, result);
  return results;
}

function coveredProblem(ask, value, evidence) {
  if (ask.timestamp === undefined) return "ask timestamp is missing or invalid";
  // The optional explanation follows |. Every cited id must pass, not merely one of them.
  const ids = value.split("|", 1)[0].trim().split(/[\s,]+/).map(id => id.replace(/^tool:/, ""));
  if (!ids.length || ids.some(id => !id)) return "covered requires at least one tool-call id";
  for (const id of ids) {
    const result = evidence.get(id);
    if (!result) return `tool-call id ${id} has no result from this session`;
    if (!succeeded(result)) return `tool-call id ${id} failed, timed out, or has no explicit success`;
    if (result.at <= ask.timestamp) return `tool-call id ${id} is not newer than the ask`;
  }
}

function followedUp(agent, since, evidence) {
  return [...evidence.values()].some(result => {
    if (!succeeded(result) || result.at <= since) return false;
    if (result.toolName === "write") return result.input?.path === `agent://${agent}`
      && typeof result.input.content === "string" && result.input.content.trim()
      && !result.input.content.startsWith("ACK-ONLY");
    return result.toolName === "task" && Array.isArray(result.input?.tasks)
      && result.input.tasks.some(task => task.name === agent);
  });
}

function checkMarks(asks, marks, state, evidence) {
  const remaining = [...asks];
  const updates = {};
  const problems = [];
  for (const mark of marks) {
    const index = remaining.findIndex(ask => ask.sentence === mark.sentence);
    if (index === -1) { problems.push(`Duplicate or unknown ASKS sentence ${JSON.stringify(mark.sentence)}`); continue; }
    const [ask] = remaining.splice(index, 1);
    const previous = state.delegated[ask.key];
    let problem;
    if (mark.kind === "covered") problem = coveredProblem(ask, mark.value, evidence);
    else if (previous && mark.kind !== "delegated") problem = "delegated asks stay open until a later reply marks them covered";
    else if (mark.kind === "delegated") {
      if (previous && !followedUp(mark.value, previous.since, evidence)) problem = `stale delegation to ${mark.value}: no successful follow-up since the last accepted reply`;
      else updates[ask.key] = { agent: mark.value, since: Date.now() };
    }
    if (problem) problems.push(`${ask.id} ${JSON.stringify(ask.sentence)}: ${problem}`);
  }
  for (const ask of remaining) problems.push(`${ask.id} ${JSON.stringify(ask.sentence)}: unmarked${state.delegated[ask.key] ? " delegated ask" : ""}`);
  return { problems, updates };
}

export function registerAskChecks(pi, loadDirectives) {
  const states = new Map();
  const live = new Map();
  const calls = new Map();
  let serial = 0;
  const stateFile = id => join(process.env.Z0INT_HOME || join(homedir(), ".z0int"), "relay-guard", `${encodeURIComponent(id)}.json`);
  const fresh = id => ({ schema: 1, session_id: id, passed: [], delegated: {}, batch: "", reinjections: 0, surfaced: false });
  const load = id => {
    if (states.has(id)) return states.get(id);
    let state = fresh(id), error;
    try {
      state = JSON.parse(readFileSync(stateFile(id), "utf8"));
      if (state.schema !== 1 || state.session_id !== id || !Array.isArray(state.passed) || state.passed.some(id => typeof id !== "string")
        || !state.delegated || typeof state.delegated !== "object" || Array.isArray(state.delegated)
        || typeof state.batch !== "string" || !Number.isInteger(state.reinjections) || state.reinjections < 0 || state.reinjections > 3
        || typeof state.surfaced !== "boolean"
        || Object.values(state.delegated).some(value => !value || typeof value.agent !== "string" || !Number.isFinite(value.since))) fail("Invalid open-ask state");
    } catch (cause) {
      if (cause.code !== "ENOENT") { error = `Cannot read open-ask state: ${cause.message}`; state = fresh(id); }
    }
    const slot = { state, error };
    states.set(id, slot);
    return slot;
  };
  const save = state => {
    const path = stateFile(state.session_id);
    const directory = join(path, "..");
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const temporary = `${path}.${process.pid}.${++serial}.tmp`;
    try { writeFileSync(temporary, JSON.stringify(state) + "\n", { mode: 0o600, flag: "wx" }); renameSync(temporary, path); }
    finally { try { unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") throw error; } }
  };
  pi.on("tool_call", (event, ctx) => {
    const id = ctx.sessionManager.getSessionId();
    if (!calls.has(id)) calls.set(id, new Map());
    calls.get(id).set(event.toolCallId, { toolName: event.toolName, input: event.input });
  });
  pi.on("tool_result", (event, ctx) => {
    const id = ctx.sessionManager.getSessionId();
    if (!live.has(id)) live.set(id, new Map());
    live.get(id).set(event.toolCallId, { ...event, input: event.input ?? calls.get(id)?.get(event.toolCallId)?.input, at: Date.now() });
    calls.get(id)?.delete(event.toolCallId);
  });
  pi.on("session_shutdown", () => { states.clear(); live.clear(); calls.clear(); });
  pi.on("session_stop", (event, ctx) => {
    const id = ctx.sessionManager.getSessionId();
    const slot = load(id);
    const state = slot.state;
    let asks = [], problems = [], updates;
    try {
      if (event.session_id !== id) fail("Stop event session does not match its context");
      if (slot.error) fail(slot.error);
      const source = loadDirectives(ctx.sessionManager);
      asks = pendingAsks(source, state);
      if (asks.length) {
        const final = event.last_assistant_message ?? event.messages?.findLast(message => message.role === "assistant");
        const checked = checkMarks(asks, parseAsks(messageText(final)), state, evidenceFor(ctx.sessionManager, live.get(id)));
        problems = checked.problems; updates = checked.updates;
      } else updates = {};
      if (!problems.length) {
        const next = { ...state, passed: [...source.users.keys()], delegated: updates, batch: "", reinjections: 0, surfaced: false };
        save(next);
        slot.state = next;
        return;
      }
    } catch (error) {
      problems = [error.message, ...asks.map(ask => `${ask.id} ${JSON.stringify(ask.sentence)}`)];
    }
    const batch = asks.length ? asks.map(ask => ask.key).join("|") : "history-or-state-error";
    if (state.batch !== batch) { state.batch = batch; state.reinjections = 0; state.surfaced = false; }
    const report = `z0-relay-guard OPEN ASKS / DONE CLAIM blocked:\n${problems.map(problem => `- ${problem}`).join("\n")}\nRequired final reply format:\nASKS:\n- "<exact sentence>" -> covered: <tool-call id>[, <id>] | <evidence explanation>\nOr use delegated: <agent id>, dropped: <reason>, or not-an-ask. Covered ids must succeed in this session after the ask. Previously delegated asks require a successful follow-up or covered evidence.`;
    if (state.reinjections < 3) {
      state.reinjections++;
      try { save(state); } catch (error) { pi.logger.warn("z0-relay-guard: cannot save stop retry state", { error: error.message }); }
      return { decision: "block", reason: `${report}\nContinuation ${state.reinjections}/3.` };
    }
    if (!state.surfaced) {
      state.surfaced = true;
      try { save(state); } catch (error) { pi.logger.warn("z0-relay-guard: cannot save surfaced state", { error: error.message }); }
      const content = `${report}\nStopped automatic continuation after 3 reinjections. These asks remain unresolved. No covered state was committed.`;
      ctx.ui.notify(content, "error");
      pi.sendMessage({ customType: "z0-open-asks-unresolved", content, display: true }, { triggerTurn: false, deliverAs: "nextTurn" });
    }
  });
}
