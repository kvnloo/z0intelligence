import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { appendEvent, check, contractHash, registerDelegate, registerDelegateResult, registerHumanApproval, registerVisibleDrop, updateIntent, verifyTask, verifyPrecondition } from "./checker.js";
import { createRunner } from "./verifiers.js";

const fail = message => { throw new Error(message); };
const sha = text => createHash("sha256").update(text, "utf8").digest("hex");
const textOf = message => typeof message?.content === "string" ? message.content : (message?.content ?? []).filter(p => p.type === "text").map(p => p.text).join("\n");
const readOnly = new Set(["read", "grep", "find", "glob", "web_search", "hermes_memory", "tdai_memory_search", "z0_file_search"]);
const stripOverride = text => text.replace(/^\s*(?:\/aodl\b|quick:)[ \t]*/i, "").trim();
const deliveredText = text => {
  const marker = "\n\n[Intent context — evidence, not approval]\n";
  const at = text.lastIndexOf(marker);
  return stripOverride(at !== -1 && text.trimEnd().endsWith("[/Intent context]") ? text.slice(0, at) : text);
};
const atomic = (path, value) => { mkdirSync(join(path, ".."), { recursive: true, mode: 0o700 }); const tmp = `${path}.${randomUUID()}.tmp`; writeFileSync(tmp, JSON.stringify(value) + "\n", { mode: 0o600, flag: "wx" }); renameSync(tmp, path); };
const taskIds = doc => doc.intentGraph.nodes.filter(n => n.kind === "task").map(n => n.id);
const openIds = doc => taskIds(doc).filter(id => ["open", "delegated"].includes(doc.plan.bindings[id].status));
const carried = payload => {
  const lines = payload.split(/\r?\n/).filter(line => line.startsWith("TASKS:"));
  if (lines.length !== 1) fail("Each relay requires exactly one TASKS: <task ids> line");
  const ids = lines[0].slice(6).trim().split(/[\s,]+/).filter(Boolean);
  if (new Set(ids).size !== ids.length) fail("Duplicate relay task id");
  return ids;
};

/** Native hooks only. Source compilation and Kevin's approval belong to IntentService. */
export function registerIntentGate(pi, helpers, options = {}) {
  const home = options.home ?? homedir();
  const base = options.root ?? process.env.Z0INT_HOME ?? join(home, ".z0int");
  const sourceStamp = options.sourceStamp ?? join(home, ".omp/agent/extensions/z0-intent-service/.content-sha256");
  const states = new Map(), calls = new Map(), delegates = new Map();
  const show = content => pi.sendMessage({ customType: "z0-aodl-intent", content, display: true }, { triggerTurn: false });
  const directory = session => join(base, "intent", encodeURIComponent(session));
  const save = slot => atomic(slot.path, slot.doc);

  function sourceFor(ctx) {
    const source = helpers.directives(ctx.sessionManager);
    let current, pending;
    for (const e of source.entries) {
      if (e.type === "custom" && e.customType === "z0-intent-input" && ["input", "edit"].includes(e.data?.kind)) {
        pending = e;
      } else if (source.users.has(e.id)) {
        if (pending) {
          const d = pending.data;
          let stamp;
          try { stamp = readFileSync(sourceStamp, "utf8").trim(); } catch { fail("Intent source stamp unavailable"); }
          if (!/^[a-f0-9]{64}$/.test(stamp) || d.extension_content_sha !== stamp || typeof d.rawText !== "string" || d.sha256 !== sha(d.rawText)) fail("source_mismatch: invalid stamped input");
          if (deliveredText(textOf(e.message)) !== stripOverride(d.rawText)) fail("source_mismatch: input differs from following user entry");
          current = { id: pending.id, timestamp: d.ts, texts: [d.rawText] };
          pending = undefined;
        } else current = { id: e.id, timestamp: e.timestamp, texts: source.users.get(e.id) };
      }
    }
    if (pending) fail("source_mismatch: stamped input lacks following user entry");
    return { ...source, current };
  }

  function load(ctx) {
    const source = sourceFor(ctx), dir = directory(source.session), slots = [];
    let files;
    try { files = readdirSync(dir).filter(name => /^[A-Za-z0-9_.:-]+\.json$/.test(name)).sort(); }
    catch (error) { if (error.code !== "ENOENT") throw error; files = []; }
    const approved = files.map(filename => ({ filename, doc: JSON.parse(readFileSync(join(dir, filename), "utf8")) }))
      .filter(item => item.doc.plan?.bindings?.approval?.status === "approved");
    const superseded = new Set();
    for (const { doc: child } of approved) {
      const compiler = child.plan.bindings.compiler;
      if (!compiler?.parent_contract_id) continue;
      const parent = approved.find(item => item.doc.plan.bindings.approval.contract_id === compiler.parent_contract_id)?.doc;
      if (!parent || compiler.parent_contractHash !== parent.plan.bindings.approval.contractHash) fail("Approved amendment has no exact parent contract");
      const auth = check(child, { mutating: true, verifyPrecondition: true, sessionId: source.session });
      if (!auth.ok) fail("Approved amendment authority is invalid");
      for (const id of taskIds(parent)) {
        const before = parent.plan.bindings[id], after = child.plan.bindings[id];
        if (!after || ["entryId", "sentenceId", "text", "askedAt", "target", "acceptance"].some(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]))
          || JSON.stringify(parent.plan.bindings[before.acceptance]) !== JSON.stringify(child.plan.bindings[after.acceptance])) fail("Amendment silently removes or changes an earlier task");
      }
      superseded.add(compiler.parent_contract_id);
    }
    if (approved.length && approved.every(item => superseded.has(item.doc.plan.bindings.approval.contract_id))) fail("Cyclic contract amendment lineage");
    for (const { filename, doc: original } of approved) {
      const approval = original.plan?.bindings?.approval;
      if (approval?.status !== "approved") continue;
      if (approval.contract_id !== filename.slice(0, -5) || approval.session_id !== source.session) fail("Approved contract path/session mismatch");
      const validated = check(original, { mutating: true, verifyPrecondition: true, sessionId: source.session });
      if (!validated.ok) fail(validated.violations.map(v => `${v.law}: ${v.reason}`).join("\n"));
      // Bind every source citation to real root session bytes, including stamped raw input.
      for (const [id, binding] of Object.entries(original.plan.bindings.sources)) {
        const entry = source.entries.find(e => e.id === id);
        const texts = source.users.get(id) ?? (entry?.type === "custom" && entry.customType === "z0-intent-input" && entry.data?.extension_content_sha === readFileSync(sourceStamp, "utf8").trim() && entry.data.sha256 === sha(entry.data.rawText) ? [entry.data.rawText] : null);
        if (!texts || JSON.stringify(texts) !== JSON.stringify(binding.texts)) fail("source_mismatch: approved contract changed source bytes");
      }
      if (superseded.has(approval.contract_id)) continue;
      const key = `${source.session}/${approval.contract_id}`;
      let slot = states.get(key);
      if (!slot) {
        const path = join(dir, "runtime", filename);
        let doc;
        try { doc = JSON.parse(readFileSync(path, "utf8")); } catch (error) { if (error.code !== "ENOENT") throw error; doc = original; }
        if (contractHash(doc) !== approval.contractHash || JSON.stringify(doc.plan.bindings.approval) !== JSON.stringify(approval)) fail("Runtime does not match approved contract");
        for (const id of taskIds(doc)) if (doc.plan.bindings[id].delegate?.live) {
          doc.plan.bindings[id].delegate.live = false;
          appendEvent(doc, "retry", { kind: "delegate_liveness_unknown_after_restart", taskId: id });
        }
        slot = { doc, path, retries: 0, surfaced: false, key };
        states.set(key, slot);
        show(`Approved AODL contract ${approval.contract_id}:\n${taskIds(doc).map(id => `${id}: ${doc.plan.bindings[id].text}\nAcceptance: ${JSON.stringify(doc.plan.bindings[doc.plan.bindings[id].acceptance])}`).join("\n")}`);
      }
      slots.push(slot);
    }
    let tier = "CONTRACT";
    if (source.current) {
      let receipts = [];
      try { receipts = readFileSync(join(dir, "triage.jsonl"), "utf8").split("\n").filter(Boolean).map(line => JSON.parse(line)); }
      catch (error) { if (error.code !== "ENOENT") throw error; }
      const receipt = receipts.filter(r => r.entry_id === source.current.id && r.sourceHash === sha(source.current.texts.join("\n"))).at(-1);
      if (receipt && ["FAST", "CHECK", "CONTRACT"].includes(receipt.tier)) tier = receipt.tier;
    }
    return { source, slots, tier };
  }

  const authority = loaded => loaded.tier !== "CONTRACT" || (loaded.source.current && loaded.slots.some(slot => Object.hasOwn(slot.doc.plan.bindings.sources, loaded.source.current.id)));
  const reject = error => ({ block: true, reason: `AODL gate BLOCKED: ${error.message ?? error}. Use IntentService to reconcile the source and obtain Kevin's approval. Only the intent tool may verify, view, drop with reason, or attach a real delegate.` });

  pi.registerFlag("show", { type: "boolean", default: false, description: "Show AODL relay receipts" });
  const z = pi.zod;
  pi.registerTool({
    name: "intent", label: "AODL intent", approval: "exec", loadMode: "essential",
    description: "View Kevin-approved tasks, execute acceptance verifier, drop with reason, or attach an already live delegate. This tool cannot compile, approve, or change acceptance. Use explicit TASKS: ids in relays. Completion claims use DONE: ids or COVERED: ids.",
    parameters: z.object({ op: z.enum(["view", "verify", "verify-precondition", "drop", "delegate"]), taskId: z.string().optional(), verifierId: z.string().optional(), reason: z.string().optional(), delegateId: z.string().optional() }),
    async execute(toolCallId, input, signal, onUpdate, ctx) {
      const loaded = load(ctx);
      if (input.op === "view") return { content: [{ type: "text", text: JSON.stringify(loaded.slots.map(s => ({ ...s.doc, overdueTasks: check(s.doc).overdueTasks }))) }] };
      if (!authority(loaded)) fail("Current CONTRACT message lacks Kevin-approved intent");
      const slot = loaded.slots.find(s => taskIds(s.doc).includes(input.taskId));
      if (!slot) fail("Unknown approved task id");
      let result;
      if (input.op === "drop") {
        updateIntent(slot.doc, { op: "drop", taskId: input.taskId, reason: input.reason });
        const request = slot.doc.eventLog.at(-1);
        save(slot);
        await show(`Kevin: dropping approved task ${input.taskId} from contract ${slot.doc.plan.bindings.approval.contract_id}.\nReason: ${input.reason}`);
        registerVisibleDrop(slot.doc, input.taskId, input.reason, request.eventId);
        result = slot.doc.plan.bindings[input.taskId];
      }
      else if (input.op === "delegate") {
        const live = delegates.get(input.delegateId);
        if (!live || live.session !== loaded.source.session || live.status !== "started") fail("Delegate has no live native lifecycle receipt");
        result = registerDelegate(slot.doc, input.taskId, input.delegateId, { live: true });
      } else {
        for (const e of loaded.source.entries) if (loaded.source.users.has(e.id)
          && /^(?:APPROVE\s+\S|I approve\s+\S)/m.test(textOf(e.message))
          && !slot.doc.eventLog.some(v => v.payload?.kind === "human_approval_source" && v.payload.entryId === e.id)) registerHumanApproval(slot.doc, e);
        const runner = createRunner({ cwd: ctx.cwd, signal,
          metricQuery: async (query, target) => {
            const url = new URL("/api/v1/query", target); url.searchParams.set("query", query);
            const response = await fetch(url, { signal }); if (!response.ok) fail(`Metric HTTP ${response.status}`);
            const body = await response.json(); if (body.status !== "success") fail("Metric query failed");
            const rows = body.data?.result; const value = body.data?.resultType === "scalar" ? body.data.result?.[1] : Array.isArray(rows) && rows.length === 1 ? rows[0].value?.[1] : undefined;
            if (value === undefined || !Number.isFinite(Number(value))) fail("Metric query must yield one numeric value"); return { value: Number(value), target };
          },
          humanReview: async target => {
            const sources = slot.doc.eventLog.filter(e => e.payload?.kind === "human_approval_source").map(e => e.payload);
            for (const source of sources.reverse()) for (const s of source.sentences ?? []) if ([`APPROVE ${target}`, `I approve ${target}.`].includes(s.text.trim())) return { approved: true, actor: "user", entryId: source.entryId, sentenceId: s.id, target };
            return { approved: false };
          } });
        result = input.op === "verify-precondition"
          ? await verifyPrecondition(slot.doc, input.taskId, input.verifierId, runner)
          : await verifyTask(slot.doc, input.taskId, runner);
      }
      save(slot); show(`Intent ${input.op}: ${input.taskId}\n${JSON.stringify(result)}`);
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  });

  pi.on("tool_call", (event, ctx) => {
    try {
      const loaded = load(ctx), input = typeof event.input === "string" ? JSON.parse(event.input) : event.input;
      const mutating = !readOnly.has(event.toolName) && !(event.toolName === "intent" && input?.op === "view");
      if (mutating && !authority(loaded)) fail("I0: Current CONTRACT source has no approved exact contract");
      for (const slot of loaded.slots) {
        const result = check(slot.doc, { mutating, requiresContract: mutating, verifyPrecondition: event.toolName === "intent" && ["verify-precondition", "drop"].includes(input?.op), sessionId: loaded.source.session });
        if (!result.ok) fail(result.violations.map(v => `${v.law}: ${v.reason}`).join("\n"));
      }
      const outgoing = helpers.payloads(event.toolName, input);
      const forwarded = [];
      if (outgoing && loaded.slots.length && !outgoing.every(helpers.acknowledgement)) {
        for (const payload of outgoing.filter(p => !helpers.acknowledgement(p))) {
          const ids = carried(payload);
          for (const slot of loaded.slots) {
            const known = taskIds(slot.doc);
            const result = check(slot.doc, { relayTaskIds: ids.filter(id => known.includes(id)), forwardedTaskIds: openIds(slot.doc) });
            if (!result.ok) fail(result.violations.map(v => `${v.law}: ${v.reason}`).join("\n"));
          }
          if (ids.some(id => !loaded.slots.some(s => taskIds(s.doc).includes(id)))) fail("I4: Unknown carried task id");
          forwarded.push(...ids);
        }
      }
      const key = `${ctx.sessionManager.getSessionId()}/${event.toolCallId}`;
      calls.set(key, { sourceSession: loaded.source.session, slots: loaded.slots, toolName: event.toolName, startedAt: new Date().toISOString(), forwarded: [...new Set(forwarded)] });
    } catch (error) { return reject(error); }
  });

  pi.on("tool_result", (event, ctx) => {
    const key = `${ctx.sessionManager.getSessionId()}/${event.toolCallId}`, call = calls.get(key);
    if (!call) return;
    if (event.toolName !== call.toolName) { pi.logger.warn("AODL result tool name mismatch", { toolCallId: event.toolCallId }); return; }
    calls.delete(key);
    for (const slot of call.slots) {
      appendEvent(slot.doc, "stateUpdate", { kind: "tool_result", toolCallId: event.toolCallId, toolName: event.toolName, exitCode: event.details?.exitCode ?? null, isError: event.isError, timedOut: event.details?.timedOut === true, startedAt: call.startedAt, finishedAt: new Date().toISOString(), timestampKind: "host-receipt", executed: true });
      save(slot);
    }
    if (pi.getFlag("show") === true && call.forwarded.length) show(`Relay receipt ${event.toolCallId}: TASKS: ${call.forwarded.join(", ")}${event.isError ? " (failed)" : ""}`);
  });

  if (pi.events?.on) pi.events.on("task:subagent:lifecycle", payload => {
    const call = [...calls.values()].find(c => c.toolName === "task" && payload.parentToolCallId && [...calls].some(([key, value]) => value === c && key.endsWith(`/${payload.parentToolCallId}`)));
    if (payload.status === "started" && call) {
      delegates.set(payload.id, { session: call.sourceSession, status: "started", slots: call.slots, forwarded: call.forwarded });
      for (const slot of call.slots) for (const id of call.forwarded.filter(id => taskIds(slot.doc).includes(id))) { registerDelegate(slot.doc, id, payload.id, { live: true }); save(slot); }
    } else {
      const delegate = delegates.get(payload.id); if (!delegate) return;
      delegate.status = payload.status;
      if (!payload.sessionFile) {
        for (const slot of delegate.slots) for (const id of delegate.forwarded.filter(id => taskIds(slot.doc).includes(id))) { slot.doc.plan.bindings[id].delegate.live = false; save(slot); }
        return;
      }
      const entries = readFileSync(payload.sessionFile, "utf8").split("\n").filter(Boolean).map(line => JSON.parse(line));
      const final = entries.findLast(e => e.message?.role === "assistant" && textOf(e.message).trim());
      if (!final) {
        for (const slot of delegate.slots) for (const id of delegate.forwarded.filter(id => taskIds(slot.doc).includes(id))) { slot.doc.plan.bindings[id].delegate.live = false; save(slot); }
        return;
      }
      const result = { status: payload.status, sessionFile: payload.sessionFile, entryId: final.id, text: textOf(final.message) };
      for (const slot of delegate.slots) for (const id of delegate.forwarded.filter(id => taskIds(slot.doc).includes(id) && slot.doc.plan.bindings[id].delegate?.id === payload.id)) { registerDelegateResult(slot.doc, id, payload.id, result); save(slot); }
    }
  });

  pi.on("session_stop", (event, ctx) => {
    let loaded;
    try {
      loaded = load(ctx);
      const finalText = textOf(event.last_assistant_message ?? event.messages?.findLast(m => m.role === "assistant"));
      const declared = [...finalText.matchAll(/^(?:DONE|COVERED):[ \t]*(.+)$/gm)].flatMap(m => m[1].split(/[\s,]+/).filter(Boolean));
      const implicit = /\b(?:done|covered|completed|finished|implemented)\b/i.test(finalText);
      const problems = loaded.slots.flatMap(slot => {
        const known = taskIds(slot.doc);
        const claims = declared.filter(id => known.includes(id));
        const checked = check(slot.doc, { turnEnd: true, claims: implicit && !declared.length ? known : claims });
        return checked.violations.map(v => `${v.law} ${v.taskId ?? ""}: ${v.reason}`);
      });
      for (const id of declared) if (!loaded.slots.some(s => taskIds(s.doc).includes(id))) problems.push(`I2: Unknown done claim ${id}`);
      if (!problems.length) {
        atomic(join(directory(loaded.source.session), "runtime", "gate-control.json"), { batch: "", retries: 0, surfaced: false });
        states.delete(`error:${ctx.sessionManager.getSessionId()}`);
        return;
      }
      fail(problems.join("\n"));
    } catch (error) {
      const slots = loaded?.slots ?? [];
      const statePath = join(directory(loaded?.source.session ?? ctx.sessionManager.getSessionId()), "runtime", "gate-control.json");
      const batch = `${loaded?.source.current?.id ?? "source-error"}:${slots.map(s => s.doc.plan.bindings.approval.contractHash).join(",")}`;
      let active = states.get(`error:${ctx.sessionManager.getSessionId()}`);
      if (!active) {
        try { active = JSON.parse(readFileSync(statePath, "utf8")); }
        catch (cause) { if (cause.code !== "ENOENT") { ctx.ui.notify("AODL gate retry state is unreadable. No automatic continuation is safe.", "error"); show(`AODL gate storage error: ${cause.message}. Tasks remain open.`); return; } }
      }
      if (!active || active.batch !== batch) active = { batch, retries: 0, surfaced: false };
      if (!Number.isInteger(active.retries) || active.retries < 0 || active.retries > 3 || typeof active.surfaced !== "boolean") { ctx.ui.notify("AODL gate retry state is corrupt. Tasks remain open.", "error"); return; }
      states.set(`error:${ctx.sessionManager.getSessionId()}`, active);
      const report = `AODL open tasks / claim gate:\n${error.message}\n${slots.flatMap(s => openIds(s.doc).map(id => `${id}: ${s.doc.plan.bindings[id].text}`)).join("\n")}`;
      if (active.retries < 3) {
        active.retries++;
        try { atomic(statePath, active); } catch (cause) { ctx.ui.notify(`AODL gate cannot preserve retry state: ${cause.message}. Tasks remain open.`, "error"); return; }
        return { decision: "block", reason: `${report}\nContinuation ${active.retries}/3. Execute each acceptance verifier or drop with a reason. Live native delegates may remain open.` };
      }
      if (!active.surfaced) {
        active.surfaced = true;
        try { atomic(statePath, active); } catch (cause) { pi.logger.warn("AODL gate cannot preserve surfaced state", { error: cause.message }); }
        ctx.ui.notify(`${report}\nAutomatic continuation cap reached. These tasks remain open.`, "error");
        show(`${report}\nKevin: automatic continuation stopped after 3 reinjections. No completion was recorded.`);
      }
    }
  });
  pi.on("session_shutdown", () => { states.clear(); calls.clear(); delegates.clear(); });
}
