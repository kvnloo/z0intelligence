import { createHash, randomUUID } from "node:crypto";
import { createRunner, normalizeVerifier } from "./verifiers.js";

const object = v => v !== null && typeof v === "object" && !Array.isArray(v);
const ensure = (condition, message) => { if (!condition) throw new Error(message); };
const nonempty = v => typeof v === "string" && v.trim().length > 0;
const time = value => typeof value === "number" ? value : typeof value === "string" ? Date.parse(value) : NaN;
const validTime = value => Number.isFinite(time(value));
const stable = value => Array.isArray(value) ? value.map(stable) : object(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
const hash = value => createHash("sha256").update(JSON.stringify(stable(value))).digest("hex");
const idPattern = /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/;
const bindings = doc => { ensure(object(doc?.plan?.bindings), "Missing intent bindings"); return doc.plan.bindings; };
const tasks = doc => doc.intentGraph.nodes.filter(node => node.kind === "task").map(node => [node.id, bindings(doc)[node.id]]);
const node = (id, kind) => ({ id, kind, ports: [{ id: "in", direction: "in", schema: "intent-contract" }, { id: "out", direction: "out", schema: "intent-contract" }], capabilities: [], lifecycle: "declared" });
const sourceHash = sources => hash(Object.entries(sources).map(([id, source]) => ({ id, timestamp: source.timestamp, texts: source.texts })));

/** Approval covers the immutable compiled contract; runtime evidence never rewrites it. */
export function contractHash(doc) {
  const copy = structuredClone(doc);
  delete copy.eventLog;
  delete copy.revision;
  delete copy.plan.bindings.approval;
  for (const n of copy.intentGraph.nodes) {
    delete n.lifecycle;
    if (n.kind === "task" && object(copy.plan.bindings[n.id])) {
      for (const key of ["status", "reason", "delegate"]) delete copy.plan.bindings[n.id][key];
    }
  }
  return hash(copy);
}

export function createDocument(sessionId) {
  ensure(nonempty(sessionId), "Session id required");
  return { specVersion: "0.2", graphId: `intent:${sessionId}`, revision: 0,
    intentGraph: { nodes: [node("intent.root", "stateStore")], edges: [] },
    policies: { protocol: "intent-contract", fanIn: "all", kinds: ["sequence"] },
    constraints: { budgets: { attention: 0 }, termination: { on: "verified-or-reasoned-drop" } },
    provenance: { source: "omp-root-user-history", sourceHash: sourceHash({}) },
    plan: { bindings: { sessionId, sources: {} } }, eventLog: [] };
}

export function appendEvent(doc, type, payload, parents = []) {
  ensure(["spawn", "bind", "route", "retry", "cancel", "addNode", "removeNode", "addEdge", "removeEdge", "stateUpdate", "snapshot"].includes(type), "Noncanonical AODL event type");
  ensure(object(payload) && Array.isArray(parents) && parents.every(parent => doc.eventLog.some(event => event.eventId === parent)), "Invalid event payload or causal parents");
  const event = { eventId: `event:${randomUUID()}`, type, sourceHash: doc.provenance.sourceHash, revision: ++doc.revision, causalParents: [...parents], payload: structuredClone(payload) };
  doc.eventLog.push(event);
  return event;
}

/** Preserve exact slices, including punctuation and edge whitespace; never join text blocks. */
function sentences(texts) {
  const result = [];
  texts.forEach((text, block) => {
    let start = 0;
    const add = end => { if (text.slice(start, end).trim()) result.push({ id: `s${block}.${start}.${end}`, text: text.slice(start, end), block, start, end, classification: "unclassified" }); start = end; };
    for (let i = 0; i < text.length; i++) {
      if (text[i] === "\n" || (/[.!?]/.test(text[i]) && (i + 1 === text.length || /\s/.test(text[i + 1])))) add(i + 1);
    }
    add(text.length);
  });
  return result;
}

function userSource(entry) {
  ensure(nonempty(entry?.id) && entry.message?.role === "user", "Only authenticated user entries are source");
  const content = entry.message.content;
  const texts = typeof content === "string" ? [content] : Array.isArray(content) ? content.filter(part => part?.type === "text").map(part => part.text) : null;
  ensure(texts && texts.every(text => typeof text === "string"), "Invalid user text blocks");
  const timestamp = entry.timestamp ?? entry.message.timestamp;
  ensure(validTime(timestamp), "User entry requires real timestamp");
  return { timestamp, texts, sentences: sentences(texts) };
}

export function ingestUser(doc, entry) {
  const b = bindings(doc), source = userSource(entry);
  if (Object.hasOwn(b.sources, entry.id)) {
    const previous = b.sources[entry.id];
    ensure(hash({ timestamp: previous.timestamp, texts: previous.texts }) === hash({ timestamp: source.timestamp, texts: source.texts }), "User source id changed content");
    return previous;
  }
  ensure(!b.approval, "Approved source snapshot is immutable; recompile a new contract");
  b.sources[entry.id] = source;
  doc.provenance.sourceHash = sourceHash(b.sources);
  appendEvent(doc, "stateUpdate", { kind: "source", entryId: entry.id, timestamp: source.timestamp });
  return source;
}

const findSentence = (doc, entryId, sentenceId) => {
  const source = bindings(doc).sources[entryId];
  const sentence = source?.sentences.find(value => value.id === sentenceId);
  ensure(sentence, "Unknown source sentence");
  return { source, sentence };
};
const findTask = (doc, taskId) => {
  ensure(doc.intentGraph.nodes.some(value => value.id === taskId && value.kind === "task"), "Unknown task id");
  const task = bindings(doc)[taskId];
  ensure(object(task), "Missing task binding");
  return task;
};

function edge(doc, from, to, relation) {
  doc.intentGraph.edges.push({ id: `${from}:${to}`, relation, from, to, fromPort: "out", toPort: "in", delivery: { order: "ordered", idempotent: true }, authority: { grant: [], delegationDepth: 0 }, provenance: { sourceHash: doc.provenance.sourceHash } });
}

/** Internal service/compiler API, not an agent-facing compile tool. */
export function updateIntent(doc, input) {
  ensure(object(input), "Intent update object required");
  const b = bindings(doc);
  if (input.op === "add") {
    ensure(!b.approval, "Cannot add to an approved contract");
    const { source, sentence } = findSentence(doc, input.entryId, input.sentenceId);
    ensure(sentence.classification === "unclassified", "Sentence already classified");
    ensure(idPattern.test(input.taskId) && input.taskId.length <= 116 && !Object.hasOwn(b, input.taskId) && !doc.intentGraph.nodes.some(n => n.id === input.taskId), "Invalid or duplicate task id");
    ensure(nonempty(input.target), "Task needs real target");
    const verifier = normalizeVerifier(input.acceptance, input.target);
    const verifierId = `${input.taskId}.acceptance`;
    ensure(!Object.hasOwn(b, verifierId) && !doc.intentGraph.nodes.some(n => n.id === verifierId), "Duplicate verifier id");
    b[verifierId] = verifier;
    const task = { entryId: input.entryId, sentenceId: input.sentenceId, text: sentence.text, askedAt: source.timestamp, target: input.target, acceptance: verifierId, status: "open" };
    b[input.taskId] = task;
    sentence.classification = "add";
    doc.intentGraph.nodes.push(node(input.taskId, "task"), node(verifierId, "verifier"));
    edge(doc, "intent.root", input.taskId, "dependency"); edge(doc, input.taskId, verifierId, "verification");
    appendEvent(doc, "addNode", { kind: "intent_add", taskId: input.taskId, verifierId, ...task, verifier });
    return task;
  }
  if (input.op === "not-an-ask" || (input.op === "drop" && !input.taskId)) {
    ensure(!b.approval, "Cannot reclassify approved source sentences");
    const { sentence } = findSentence(doc, input.entryId, input.sentenceId);
    ensure(sentence.classification === "unclassified", "Sentence already classified");
    ensure(nonempty(input.reason), "Classification requires reason");
    sentence.classification = input.op; sentence.reason = input.reason;
    appendEvent(doc, "stateUpdate", { kind: "classification", entryId: input.entryId, sentenceId: input.sentenceId, classification: input.op, reason: input.reason });
    return sentence;
  }
  const task = findTask(doc, input.taskId);
  ensure(!Object.hasOwn(input, "target") && !Object.hasOwn(input, "acceptance"), "Task target and acceptance are immutable after add");
  if (input.op === "drop") {
    ensure(task.status !== "verified" && nonempty(input.reason), "Dropping requires unresolved task and reason");
    task.status = "dropped"; task.reason = input.reason;
    appendEvent(doc, "stateUpdate", { kind: "drop", taskId: input.taskId, reason: input.reason });
    return task;
  }
  if (input.op === "delegate") return registerDelegate(doc, input.taskId, input.delegateId, { live: false });
  throw new Error("Unknown intent operation; verification and delegate results require trusted runtime APIs");
}

export function registerDelegate(doc, taskId, delegateId, { live = true } = {}) {
  const task = findTask(doc, taskId);
  ensure(["open", "delegated"].includes(task.status) && nonempty(delegateId) && typeof live === "boolean", "Invalid live delegation");
  task.status = "delegated"; task.delegate = { id: delegateId, live, resultReceived: false };
  appendEvent(doc, "stateUpdate", { kind: "delegate", taskId, delegateId, live, timestamp: new Date().toISOString() });
  return task;
}

export function registerDelegateResult(doc, taskId, delegateId, result) {
  const task = findTask(doc, taskId);
  ensure(task.status === "delegated" && task.delegate?.id === delegateId && result !== undefined && result !== null, "Delegate result must match active delegation");
  const timestamp = new Date().toISOString();
  task.delegate.live = false; task.delegate.resultReceived = true;
  appendEvent(doc, "stateUpdate", { kind: "delegate_result", taskId, delegateId, timestamp, result });
  return task;
}

export function registerHumanApproval(doc, entry) {
  const source = userSource(entry);
  ensure(source.sentences.some(sentence => /^(?:APPROVE\s+\S|I approve\s+\S)/.test(sentence.text.trim())), "User entry contains no explicit outcome approval");
  ensure(!doc.eventLog.some(event => event.payload?.kind === "human_approval_source" && event.payload.entryId === entry.id), "Duplicate approval source entry");
  return appendEvent(doc, "stateUpdate", { kind: "human_approval_source", entryId: entry.id, ...source });
}

function approvedOutcome(doc, task, reference) {
  if (!object(reference)) return false;
  const source = bindings(doc).sources[reference.entryId] ?? doc.eventLog.find(event => event.type === "stateUpdate" && event.payload?.kind === "human_approval_source" && event.payload.entryId === reference.entryId)?.payload;
  const sentence = source?.sentences?.find(value => value.id === reference.sentenceId);
  return Boolean(sentence && validTime(source.timestamp) && time(source.timestamp) >= time(task.askedAt) && time(source.timestamp) <= Date.now() && sentences(source.texts).some(actual => actual.id === sentence.id && actual.text === sentence.text) && [ `APPROVE ${task.target}`, `I approve ${task.target}.`, `I approve ${task.target}` ].includes(sentence.text.trim()));
}

function receiptIssues(doc, taskId, task, verified) {
  const issues = [];
  const add = (law, reason) => issues.push({ law, taskId, reason });
  const v = bindings(doc)[task.acceptance], p = verified?.payload;
  if (!p) { add("I2", "No verified stateUpdate receipt"); return issues; }
  if (verified.type !== "stateUpdate" || p.kind !== "verified" || p.taskId !== taskId || p.verifierId !== task.acceptance || p.verified !== true || p.passed !== true) add("I2", "Invalid verified update");
  if (p.target !== task.target || v?.target !== task.target) add("I3", "Verification target differs from task identity");
  const matches = doc.eventLog.filter(event => event.type === "stateUpdate" && event.payload?.kind === "tool_result" && event.payload.toolCallId === p.toolCallId);
  if (!nonempty(p.toolCallId) || matches.length !== 1) { add("I2", "Missing or reused verifier tool call id"); return issues; }
  const execution = matches[0], r = execution.payload;
  if (r.taskId !== taskId || r.verifierId !== task.acceptance || r.toolName !== `intent_verifier:${v?.type}` || r.executed !== true || r.passed !== true || r.exitCode !== 0 || r.isError !== false) add("I2", "Tool result is not an actual passing task verifier execution");
  if (r.target !== task.target) add("I3", "Execution target mismatch");
  if (!validTime(r.startedAt) || !validTime(r.finishedAt) || time(r.startedAt) < time(task.askedAt) || time(r.finishedAt) < time(r.startedAt) || time(r.finishedAt) > Date.now() || r.startedAt !== p.startedAt || r.finishedAt !== p.finishedAt) add("I2", "Stale, future, or inconsistent execution timestamps");
  if (doc.eventLog.indexOf(execution) >= doc.eventLog.indexOf(verified) || !verified.causalParents?.includes(execution.eventId)) add("I2", "Verification lacks causal execution provenance");
  if (execution.sourceHash !== verified.sourceHash || verified.sourceHash !== doc.provenance.sourceHash || execution.revision >= verified.revision) add("I2", "Receipt revision or source provenance mismatch");
  if (doc.eventLog.some(event => event !== verified && event.payload?.kind === "verified" && event.payload.toolCallId === p.toolCallId)) add("I2", "Verifier execution reused by another completion");
  if (task.delegate) {
    const delegation = doc.eventLog.filter(event => event.type === "stateUpdate" && event.payload?.kind === "delegate" && event.payload.taskId === taskId).at(-1);
    const result = doc.eventLog.find(event => event.type === "stateUpdate" && event.payload?.kind === "delegate_result" && event.payload.taskId === taskId && event.payload.delegateId === task.delegate.id && event.payload.result !== null && event.payload.result !== undefined && time(event.payload.timestamp) >= time(delegation?.payload.timestamp) && time(event.payload.timestamp) <= time(r.startedAt) && doc.eventLog.indexOf(event) > doc.eventLog.indexOf(delegation) && doc.eventLog.indexOf(event) < doc.eventLog.indexOf(execution));
    if (!task.delegate.resultReceived || delegation?.payload.delegateId !== task.delegate.id || !result) add("I2", "Delegated task lacks current result before verification");
  }
  if (v?.type === "human_review" && !approvedOutcome(doc, task, r.humanApproval)) add("I2", "Human review lacks explicit authenticated user outcome approval");
  return issues;
}

export async function verifyTask(doc, taskId, runner = createRunner()) {
  const task = findTask(doc, taskId);
  const preconditionErrors = check(doc).violations;
  ensure(!preconditionErrors.length, preconditionErrors.map(issue => issue.reason).join("; "));
  ensure(["open", "delegated"].includes(task.status), "Only unresolved tasks can be verified");
  ensure(!task.delegate || task.delegate.resultReceived === true, "Await delegated result before verification");
  const verifier = normalizeVerifier(bindings(doc)[task.acceptance], task.target);
  ensure(typeof runner === "function", "Verifier runner required");
  const immutable = contractHash(doc), invocation = Date.now();
  if (task.delegate) {
    const delegation = doc.eventLog.filter(event => event.type === "stateUpdate" && event.payload?.kind === "delegate" && event.payload.taskId === taskId).at(-1);
    const actualResult = doc.eventLog.find(event => event.type === "stateUpdate" && event.payload?.kind === "delegate_result" && event.payload.taskId === taskId && event.payload.delegateId === task.delegate.id && event.payload.result !== null && event.payload.result !== undefined && time(event.payload.timestamp) >= time(delegation?.payload.timestamp) && time(event.payload.timestamp) <= invocation && doc.eventLog.indexOf(event) > doc.eventLog.indexOf(delegation));
    ensure(delegation?.payload.delegateId === task.delegate.id && actualResult, "Await current result from delegated task before verification");
  }
  let result;
  try { result = await runner(structuredClone(verifier)); }
  catch (error) { result = { executed: true, passed: false, exitCode: 1, error: String(error), toolCallId: `intent-verifier:${randomUUID()}`, target: task.target, startedAt: new Date(invocation).toISOString(), finishedAt: new Date().toISOString() }; }
  ensure(contractHash(doc) === immutable, "Contract changed during verifier execution");
  ensure(object(result) && result.executed === true && typeof result.passed === "boolean" && Number.isInteger(result.exitCode) && nonempty(result.toolCallId), "Runner returned no actual execution receipt");
  ensure(result.target === task.target, "Runner target identity mismatch");
  ensure(validTime(result.startedAt) && validTime(result.finishedAt) && time(result.startedAt) >= Math.max(invocation, time(task.askedAt)) && time(result.finishedAt) >= time(result.startedAt) && time(result.finishedAt) <= Date.now(), "Runner receipt is stale or invalid");
  ensure(!doc.eventLog.some(event => event.payload?.toolCallId === result.toolCallId), "Runner tool call id already used");
  const passed = result.passed === true && result.exitCode === 0 && result.isError !== true && (verifier.type !== "human_review" || approvedOutcome(doc, task, result.humanApproval));
  const execution = appendEvent(doc, "stateUpdate", { ...result, kind: "tool_result", taskId, verifierId: task.acceptance, toolName: `intent_verifier:${verifier.type}`, passed, isError: result.exitCode !== 0 || result.isError === true });
  if (!passed) return { verified: false, taskId, receipt: execution, reason: result.error ?? "Acceptance verifier did not pass" };
  const payload = { kind: "verified", taskId, verifierId: task.acceptance, verified: true, passed: true, toolCallId: result.toolCallId, target: task.target, startedAt: result.startedAt, finishedAt: result.finishedAt };
  const candidate = { eventId: `event:${randomUUID()}`, type: "stateUpdate", sourceHash: doc.provenance.sourceHash, revision: doc.revision + 1, causalParents: [execution.eventId], payload };
  const issues = receiptIssues({ ...doc, eventLog: [...doc.eventLog, candidate] }, taskId, task, candidate);
  ensure(!issues.length, issues.map(issue => issue.reason).join("; "));
  const verified = appendEvent(doc, "stateUpdate", payload, [execution.eventId]);
  task.status = "verified";
  doc.intentGraph.nodes.find(n => n.id === taskId).lifecycle = "succeeded";
  return { verified: true, taskId, receipt: verified };
}

export function check(doc, { claims = [], relayTaskIds = [], forwardedTaskIds = [], turnEnd = false, mutating = false, requiresContract = true, sessionId } = {}) {
  const violations = [], openTasks = [];
  const add = (law, reason, taskId) => violations.push({ law, ...(taskId ? { taskId } : {}), reason });
  try {
    const b = bindings(doc), entries = tasks(doc), known = new Map(entries);
    if (mutating && requiresContract) {
      const approval = b.approval;
      if (!object(approval) || approval.status !== "approved" || approval.approved_by !== "kevin" || !validTime(approval.approved_at) || time(approval.approved_at) > Date.now() || approval.sourceHash !== doc.provenance.sourceHash || approval.contractHash !== contractHash(doc) || !nonempty(approval.contract_id) || approval.session_id !== (sessionId ?? b.sessionId)) add("I0", "Mutation requires Kevin-approved exact session contract");
    }
    if (doc.provenance.sourceHash !== sourceHash(b.sources)) add("I1", "Source text snapshot provenance changed");
    for (const [entryId, source] of Object.entries(b.sources)) {
      const expected = sentences(source.texts);
      if (!validTime(source.timestamp) || expected.length !== source.sentences?.length || expected.some((sentence, index) => ["id", "text", "block", "start", "end"].some(key => source.sentences[index][key] !== sentence[key]))) add("I1", "Source sentence bytes or boundaries changed");
      for (const sentence of source.sentences ?? []) {
        const linked = entries.filter(([, task]) => task?.entryId === entryId && task?.sentenceId === sentence.id);
        if (sentence.classification === "add") {
          if (linked.length !== 1) add("I1", "Added sentence requires exactly one task");
        } else if (["drop", "not-an-ask"].includes(sentence.classification)) {
          if (!nonempty(sentence.reason) || linked.length) add("I1", "Non-task classification needs reason and no task");
        } else add("I1", "Unclassified source sentence blocks the gate");
      }
    }
    const eventIds = new Set();
    let previousRevision = -1;
    for (const event of doc.eventLog) {
      if (!nonempty(event.eventId) || eventIds.has(event.eventId) || !Number.isInteger(event.revision) || event.revision <= previousRevision || event.revision > doc.revision || !["spawn", "bind", "route", "retry", "cancel", "addNode", "removeNode", "addEdge", "removeEdge", "stateUpdate", "snapshot"].includes(event.type) || !object(event.payload) || !/^[a-f0-9]{64}$/.test(event.sourceHash ?? "") || !Array.isArray(event.causalParents) || event.causalParents.some(parent => !eventIds.has(parent))) add("I2", "Invalid canonical event provenance");
      eventIds.add(event.eventId);
      previousRevision = event.revision;
    }
    for (const [taskId, task] of entries) {
      if (!object(task)) { add("I1", "Task has no binding", taskId); continue; }
      if (!["open", "delegated", "dropped", "verified"].includes(task.status)) add("I1", "Unknown task state", taskId);
      let source, sentence;
      try { ({ source, sentence } = findSentence(doc, task.entryId, task.sentenceId)); }
      catch { add("I1", "Task has no exact user sentence", taskId); }
      if (sentence && (sentence.classification !== "add" || task.text !== sentence.text || task.askedAt !== source.timestamp)) add("I1", "Task changed original ask", taskId);
      try { normalizeVerifier(b[task.acceptance], task.target); }
      catch (error) { add("I3", error.message, taskId); }
      const addition = doc.eventLog.find(event => event.type === "addNode" && event.payload?.kind === "intent_add" && event.payload.taskId === taskId);
      if (!addition || addition.payload.target !== task.target || addition.payload.verifierId !== task.acceptance || hash(addition.payload.verifier) !== hash(b[task.acceptance])) add("I3", "Task target or acceptance changed after add", taskId);
      if (task.status === "verified") {
        const verified = doc.eventLog.filter(event => event.payload?.kind === "verified" && event.payload.taskId === taskId).at(-1);
        violations.push(...receiptIssues(doc, taskId, task, verified));
      } else if (task.status === "dropped") {
        if (!nonempty(task.reason) || !doc.eventLog.some(event => event.type === "stateUpdate" && event.payload?.kind === "drop" && event.payload.taskId === taskId && event.payload.reason === task.reason)) add("I1", "Dropped task lacks reasoned runtime update", taskId);
      } else {
        openTasks.push(taskId);
        const latestDelegate = doc.eventLog.filter(event => event.type === "stateUpdate" && ["delegate", "delegate_result"].includes(event.payload?.kind) && event.payload.taskId === taskId).at(-1);
        const live = task.status === "delegated" && task.delegate?.live === true && task.delegate?.resultReceived === false && latestDelegate?.payload.kind === "delegate" && latestDelegate.payload.delegateId === task.delegate.id && latestDelegate.payload.live === true;
        if (turnEnd && !live) add("I1", "Turn ends with unresolved task and no live delegation", taskId);
      }
    }
    for (const id of claims) {
      const task = known.get(id);
      if (!task || task.status !== "verified") add("I2", "Done or covered claim requires verified stateUpdate", id);
    }
    for (const id of relayTaskIds) if (!known.has(id)) add("I4", "Relay carries unknown task id", id);
    for (const id of forwardedTaskIds) if (!known.has(id) || !relayTaskIds.includes(id)) add("I4", "Forwarded task missing carried identity", id);
  } catch (error) { add("I1", `Malformed intent contract: ${error.message}`); }
  return { ok: violations.length === 0, violations, openTasks };
}
