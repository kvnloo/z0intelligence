import { openSync, readSync, closeSync, readFileSync, readdirSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

// OMP loads index.js directly. No dependencies, generated files, or second history store.
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const fail = message => { throw new Error(message); };

function headerAt(path) {
  const fd = openSync(path, "r");
  try {
    const bytes = Buffer.alloc(4096);
    let first = "";
    for (;;) {
      const count = readSync(fd, bytes);
      if (!count) fail("Missing session header");
      first += bytes.toString("utf8", 0, count);
      const end = first.indexOf("\n");
      if (end !== -1) return JSON.parse(first.slice(0, end));
      if (first.length > 1024 * 1024) fail("Session header exceeds 1 MiB");
    }
  } finally { closeSync(fd); }
}

function parentPath(parent, file, sessionDir) {
  if (isAbsolute(parent)) return parent;
  if (parent.endsWith(".jsonl")) return resolve(dirname(file), parent);
  // Full-history forks record a session id, while task children record a path.
  // Resolve ids against actual headers in the API-provided directory, not filenames.
  const matches = [];
  for (const item of readdirSync(sessionDir, { withFileTypes: true })) {
    if (!item.isFile() || !item.name.endsWith(".jsonl")) continue;
    const path = join(sessionDir, item.name);
    if (headerAt(path).id === parent) matches.push(path);
  }
  if (matches.length !== 1) fail("Cannot resolve parent session id uniquely");
  return matches[0];
}

function userTexts(entries) {
  const users = new Map();
  for (const entry of entries) {
    if (entry.type !== "message" || entry.message?.role !== "user") continue;
    if (typeof entry.id !== "string" || !entry.id) fail("User entry has no id");
    const content = entry.message.content;
    const texts = typeof content === "string" ? [content] : Array.isArray(content)
      ? content.filter(part => part?.type === "text").map(part => part.text) : fail("Invalid user content");
    if (texts.some(text => typeof text !== "string")) fail("Invalid user text");
    if (users.has(entry.id)) fail("Duplicate user entry id");
    // Do not join text blocks: a quote must not invent bytes between blocks.
    users.set(entry.id, texts);
  }
  return users;
}

function directives(manager) {
  let file = manager.getSessionFile();
  let header = manager.getHeader();
  let entries = manager.getEntries();
  const visited = new Set();
  if (!header || typeof header.id !== "string") fail("Session header unavailable");
  while (header.parentSession) {
    if (typeof header.parentSession !== "string") fail("Invalid parent session reference");
    if (!file) fail("Parent session requires a session file");
    const next = parentPath(header.parentSession, file, manager.getSessionDir());
    if (visited.has(next) || visited.size >= 64) fail("Cyclic or excessive session ancestry");
    visited.add(next);
    file = next;
    const rows = readFileSync(file, "utf8").split("\n");
    // A partial last row is an error, not permission to treat the run as headless.
    const parsed = rows.filter(line => line.length).map(line => JSON.parse(line));
    header = parsed[0];
    if (!record(header) || header.type !== "session" || typeof header.id !== "string") fail("Invalid parent session header");
    entries = parsed.slice(1);
  }
  // Root history is authoritative. A child task's synthetic user prompt is not Kevin.
  // Current-session entries come from OMP's read-only manager, including pending writes.
  return { users: userTexts(entries), session: header.id };
}

/** Split literal shell words, preserving quoted newlines and rejecting expansion. */
function shellCommands(command) {
  const commands = [];
  let words = [], word = "", active = false, quote = "", dynamic = false;
  const endWord = () => { if (active) words.push({ text: word, dynamic }); word = ""; active = false; dynamic = false; };
  const endCommand = () => { endWord(); if (words.length) commands.push(words); words = []; };
  for (let i = 0; i < command.length; i++) {
    const c = command[i];
    if (quote === "'") { if (c === "'") quote = ""; else word += c; continue; }
    if (c === "\\") {
      const next = command[++i];
      if (next === undefined) fail("Incomplete shell escape");
      if (next === "\n") continue;
      if (quote === '"' && !['$', '`', '"', '\\'].includes(next)) word += "\\";
      word += next; active = true; continue;
    }
    if (quote === '"') { if (c === '"') quote = ""; else { word += c; if (c === "$" || c === "`") dynamic = true; } continue; }
    if (c === "'" || c === '"') { quote = c; active = true; continue; }
    if (c === "#" && !active) { while (i < command.length && command[i] !== "\n") i++; endCommand(); continue; }
    if (";|&()<>\n".includes(c)) { endCommand(); continue; }
    if (/\s/.test(c)) { endWord(); continue; }
    if (c === "$" || c === "`") dynamic = true;
    word += c; active = true;
  }
  if (quote) fail("Unclosed shell quote");
  endCommand();
  return commands;
}

function payloads(name, raw) {
  const input = typeof raw === "string" ? JSON.parse(raw) : raw;
  if (!record(input)) fail("Invalid tool arguments");
  if (name === "task") {
    if (typeof input.context !== "string" || !Array.isArray(input.tasks) || !input.tasks.length) fail("Invalid task context or tasks");
    return [input.context, ...input.tasks.map(task => {
      if (!record(task) || typeof task.task !== "string") fail("Invalid task text");
      return task.task;
    })];
  }
  if (name === "write") {
    if (typeof input.path !== "string") fail("Invalid write path");
    if (!input.path.startsWith("agent://")) return null;
    if (typeof input.content !== "string") fail("Invalid agent message");
    return [input.content];
  }
  if (name !== "bash") return null;
  if (typeof input.command !== "string") fail("Invalid bash command");
  const found = [];
  for (const words of shellCommands(input.command)) {
    for (let i = 0; i < words.length; i++) {
      if (!/(^|\/)tern$/.test(words[i].text)) continue;
      const action = words[i + 1]?.text;
      const start = action === "run" ? i + 3
        : action === "send" && words[i + 3]?.text === "paste" ? i + 4 : -1;
      if (start === -1) continue;
      if (words.slice(i).some(word => word.dynamic)) fail("Relay shell expansion cannot be checked");
      if (start >= words.length) fail("Relay payload is missing");
      found.push(words.slice(start).map(word => word.text).join(" "));
    }
  }
  if (!found.length && /\btern\s+(?:run\b|send\s+\S+\s+paste\b)/.test(input.command)) fail("Cannot parse literal tern relay");
  return found.length ? found : null;
}

function acknowledgement(payload) {
  // Deterministic, conservative language: never let an arbitrary request hide behind ACK-ONLY.
  return Array.from(payload).length < 200
    && /^ACK-ONLY[ \t]*:?[ \t]*(?:ok(?:ay)?|ack(?:nowledged)?|received|thanks|thank you|yes|no|understood|noted|done)[.! \t]*$/i.test(payload);
}

function coverage(payload, users) {
  const lines = payload.split(/\r?\n/);
  const start = lines.findIndex(line => line.startsWith("SOURCE:"));
  if (start === -1) fail("Missing COVERAGE block with SOURCE");
  const ids = lines[start].slice("SOURCE:".length).split(",").map(id => id.trim());
  if (!ids.length || ids.some(id => !id) || new Set(ids).size !== ids.length) fail("Invalid or duplicate SOURCE ids");
  const expected = new Map();
  for (const id of ids) {
    const texts = users.get(id);
    if (!texts) fail(`SOURCE ${id} is not a root-session user entry`);
    for (const text of texts) {
      // A punctuation run is one boundary. Boundary punctuation and edge whitespace are not sentence text.
      for (const sentence of text.split(/[.?!\r\n]+/).map(part => part.trim()).filter(Boolean)) {
        expected.set(sentence, (expected.get(sentence) ?? 0) + 1);
      }
    }
  }
  let index = start + 1;
  for (; index < lines.length && !lines[index].startsWith("ADDED-BY-AGENT:"); index++) {
    const line = lines[index];
    const match = /^- ("(?:[^"\\]|\\.)*") -> (covered|not-an-ask|dropped: .+|superseded by \S+)$/.exec(line);
    if (!match) fail(`Invalid coverage line: ${line}`);
    const sentence = JSON.parse(match[1]);
    const remaining = expected.get(sentence) ?? 0;
    if (!remaining) fail(`Duplicate or unknown coverage sentence: ${JSON.stringify(sentence)}`);
    expected.set(sentence, remaining - 1);
    if (match[2].startsWith("dropped:") && !match[2].slice(8).trim()) fail("Dropped sentence requires a reason");
    if (match[2].startsWith("superseded by ")) {
      const newer = match[2].slice("superseded by ".length);
      const newerIndex = ids.indexOf(newer);
      if (newerIndex === -1) fail(`Superseding id ${newer} must be cited in SOURCE`);
      const earlier = ids.slice(0, newerIndex).some(id => users.get(id).some(text =>
        text.split(/[.?!\r\n]+/).map(part => part.trim()).includes(sentence)));
      if (!earlier) fail(`Superseding id ${newer} must follow the sentence's source`);
    }
  }
  const missing = [...expected].filter(([, count]) => count > 0).map(([sentence, count]) => `${JSON.stringify(sentence)} (${count})`);
  if (missing.length) fail(`Missing coverage sentences: ${missing.join(", ")}`);
  if (index === lines.length || !lines[index].slice("ADDED-BY-AGENT:".length).trim()) fail("Missing ADDED-BY-AGENT declaration");
  if (!lines.slice(index + 1).some(line => line.trim())) fail("Missing rewritten relay body after COVERAGE block");
  return lines.slice(start, index + 1).join("\n");
}

export default function relayGuard(pi) {
  const log = (level, message, fields) => pi.logger[level]("z0-relay-guard: " + message, fields);
  const notes = new Map();
  pi.registerFlag("show", { type: "boolean", default: false, description: "Show relay coverage in chat after the tool completes" });
  pi.on("tool_result", (event, ctx) => {
    const pending = notes.get(event.toolCallId);
    notes.delete(event.toolCallId);
    if (!pending || pending.session !== ctx.sessionManager.getSessionId()) return;
    pi.sendMessage({
      customType: "z0-relay-coverage",
      content: `Relay coverage (${event.toolName}${event.isError ? ", tool failed" : ""}):\n\n${pending.blocks.join("\n\n")}`,
      display: true,
    }, { triggerTurn: false });
  });
  pi.on("session_shutdown", () => notes.clear());
  pi.on("tool_call", (event, ctx) => {
    notes.delete(event.toolCallId);
    let matched = event.toolName === "task";
    let latest = [];
    try {
      // Classification survives invalid JSON so matched calls fail closed.
      const raw = typeof event.input === "string" ? event.input : JSON.stringify(event.input);
      matched ||= event.toolName === "write" && /agent:\/\//.test(raw ?? "");
      const hint = record(event.input) ? event.input.command : raw;
      matched ||= event.toolName === "bash" && typeof hint === "string"
        && /\btern\s+(?:run\b|send\s+\S+\s+paste\b)/.test(hint);
      const outgoing = payloads(event.toolName, event.input);
      if (outgoing === null) return;
      matched = true;
      if (outgoing.every(acknowledgement)) return;
      const { users, session } = directives(ctx.sessionManager);
      latest = [...users.keys()].slice(-5);
      if (!users.size) {
        log("warn", "allow: zero user entries (headless run)", { session_id: session, tool: event.toolName });
        return;
      }
      const blocks = outgoing.filter(payload => !acknowledgement(payload)).map(payload => coverage(payload, users));
      if (pi.getFlag("show") === true) notes.set(event.toolCallId, { session: ctx.sessionManager.getSessionId(), blocks });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!matched) { log("warn", "unmatched tool parse error", { tool: event.toolName, error: message }); return; }
      if (!latest.length) {
        try { latest = [...directives(ctx.sessionManager).users.keys()].slice(-5); } catch { /* History errors remain closed. */ }
      }
      const reason = `z0-relay-guard BLOCKED: ${message}. Latest user entry ids: ${latest.join(", ") || "unavailable"}. Required COVERAGE format: SOURCE: <entry id>[, <entry id>]. Then one line per sentence: - "<exact sentence>" -> covered | dropped: <reason> | superseded by <later cited id> | not-an-ask. End with ADDED-BY-AGENT: <items or none>, then the rewritten body. No Parent: exemption. Pure acknowledgements only: ACK-ONLY: <ack>, under 200 characters.`;
      log("warn", "blocked relay", { tool: event.toolName, error: message });
      return { block: true, reason };
    }
  });
}
