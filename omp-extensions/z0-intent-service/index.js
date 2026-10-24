import { readFileSync, statSync } from "node:fs";
import { appendReceipt, projectsFromPacket, sessionDirectory, sourceHash, STATE_PACKET, stripOverride, triage } from "./triage.js";

const registration = Symbol.for("z0-intent-service.registration");
const INPUT_RECORD = "z0-intent-input";
const STATE_RECORD = "z0-intent-state";
const APPROVE = "Approve";
const EDIT = "Edit";
const REJECT = "Reject";

export function approvalIdentity(contract, sessionId) {
  const approval = contract?.plan?.bindings?.approval;
  if (!approval || approval.session_id !== sessionId || approval.sourceHash !== contract?.provenance?.sourceHash || !/^[a-f0-9]{64}$/.test(approval.sourceHash ?? "") || !/^[a-f0-9]{64}$/.test(approval.contractHash ?? "") || typeof approval.contract_id !== "string" || !approval.contract_id || !Number.isInteger(contract.revision)) throw new Error("Missing exact contract identity");
  return { session_id: sessionId, contract_id: approval.contract_id, sourceHash: approval.sourceHash, contractHash: approval.contractHash, revision: contract.revision };
}

const compact = value => (typeof value === "string" ? value : JSON.stringify(value))?.replace(/[\r\n]+/g, " ") ?? "none";
export function renderContract(contract, response = {}) {
  const bindings = contract?.plan?.bindings ?? {};
  const compiler = bindings.compiler ?? {};
  if (typeof response.summary === "string" && response.summary.trim()) {
    const prompt = /look good\?/i.test(response.summary) ? response.summary : `${response.summary}\nlook good?`;
    return `${prompt}\nMemory sources: ${compact(response.memory_sources ?? compiler.memory ?? [])}\nApprove binds this exact revision only. Edit recompiles and asks again; Reject cancels.`;
  }
  const tasks = (contract?.intentGraph?.nodes ?? []).filter(node => node.kind === "task");
  const lines = ["Intent contract — look good?", "Review the exact tasks and acceptance checks below."];
  for (const node of tasks) {
    const task = bindings[node.id] ?? {};
    lines.push(`• ${node.id}: ${compact(task.text ?? task.description ?? task.target)} → ${compact(task.target)}`);
    lines.push(`  verify: ${compact(bindings[task.acceptance] ?? task.acceptance)}`);
  }
  lines.push(`Memory: ${compact(response.memory_sources ?? compiler.memory ?? [])}`);
  lines.push(`Assumptions: ${compact(compiler.assumptions ?? [])}`);
  lines.push(`Conflicts: ${compact(compiler.conflicts ?? [])}`);
  lines.push(`Delegation: ${compact(compiler.delegation_plan ?? [])}`);
  lines.push("Approve binds this exact revision only. Edit recompiles and asks again; Reject cancels.");
  return lines.join("\n");
}

function contentText(content) {
  return typeof content === "string" ? content : Array.isArray(content) ? content.filter(part => part.type === "text").map(part => part.text).join("\n") : "";
}
function recentMessages(ctx) {
  return ctx.sessionManager.getBranch().filter(entry => entry.type === "message" && entry.message?.role === "user").slice(-4).map(entry => contentText(entry.message.content));
}
function restoredContract(ctx) {
  const entries = ctx.sessionManager.getBranch();
  for (let i = entries.length - 1; i >= 0; i--) if (entries[i].customType === STATE_RECORD) return entries[i].data?.contract ?? null;
  return null;
}

export function createIntentClient(pi, options = {}) {
  const fetcher = options.fetch ?? globalThis.fetch;
  const base = new URL(options.url ?? process.env.Z0INT_INTENT_URL ?? "http://127.0.0.1:8431");
  if (base.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(base.hostname) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error("Intent service must use a literal loopback HTTP origin");
  const timeoutMs = options.timeoutMs ?? 90_000;
  const busy = new Set();
  let packetCache;
  function packet() {
    try {
      const path = options.packetPath ?? STATE_PACKET;
      const stat = statSync(path);
      const key = `${stat.mtimeMs}:${stat.size}`;
      if (packetCache?.key !== key) packetCache = { key, projects: projectsFromPacket(readFileSync(path, "utf8")) };
      return { projects: packetCache.projects, packetAvailable: true };
    } catch { return { projects: [], packetAvailable: false }; }
  }
  async function post(path, body) {
    const response = await fetcher(new URL(path, base), { method: "POST", redirect: "error", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) throw new Error(`Intent service ${path}: HTTP ${response.status}`);
    const data = await response.json();
    if (!data || typeof data !== "object" || Array.isArray(data) || data.error) throw new Error(`Intent service ${path}: invalid response`);
    return data;
  }
  function currentSession(ctx, expected) {
    if (ctx.sessionManager.getSessionId() !== expected) throw new Error("Session changed during intent review");
  }
  function saveState(ctx, sessionId, contract) {
    currentSession(ctx, sessionId);
    pi.appendEntry(STATE_RECORD, { session_id: sessionId, contract });
  }
  async function record(ctx, sessionId, raw, source, openContract, images = false) {
    const result = triage(raw, { ...packet(), openContract: Boolean(openContract), hasImages: images });
    const stamp = options.contentSha ?? readFileSync(new URL("./.content-sha256", import.meta.url), "utf8").trim();
    if (!/^[a-f0-9]{64}$/.test(stamp)) throw new Error("Missing installed intent extension content stamp");
    currentSession(ctx, sessionId);
    const previousLeaf = ctx.sessionManager.getLeafId();
    const kind = source === "intent-editor" ? "edit" : source === "intent-reject" ? "reject" : "input";
    // InputEvent precedes the user transcript entry. Use the real custom-entry
    // id and an installer-authenticated stamp, never invent a user entry id.
    pi.appendEntry(INPUT_RECORD, { rawText: raw, sha256: sourceHash(raw), ts: new Date().toISOString(), extension_content_sha: stamp, kind, source });
    const entryId = ctx.sessionManager.getLeafId();
    if (typeof entryId !== "string" || !entryId || entryId === previousLeaf) throw new Error("Input source was not persisted to the active session");
    await appendReceipt(sessionId, entryId, result, { home: options.home });
    currentSession(ctx, sessionId);
    return { result, entryId };
  }
  function promptText(text, note) {
    return note ? `${text}\n\n[Intent context — evidence, not approval]\n${note}\n[/Intent context]` : text;
  }
  async function review(ctx, sessionId, raw, entryId, previous, recent) {
    let response = await post("/reconcile", { session_id: sessionId, entry_id: entryId, text: raw, recent_user_msgs: recent, open_contract: previous ? approvalIdentity(previous, sessionId).contract_id : null, mode: "CONTRACT" });
    currentSession(ctx, sessionId);
    for (;;) {
      if (response.classification === "cancellation") {
        const receipt = response.cancellation_receipt;
        if (response.contract || previous || !receipt || receipt.session_id !== sessionId ||
            receipt.entry_id !== entryId || receipt.text !== raw ||
            receipt.sourceHash !== sourceHash(raw) || receipt.status !== "cancelled_no_open_proposal") {
          throw new Error("Cancellation receipt did not bind the exact unproposed input");
        }
        saveState(ctx, sessionId, null);
        ctx.ui?.notify?.("Cancellation recorded. No open proposal was approved or executed.", "info");
        return { handled: true };
      }
      if (!response.contract) {
        if (response.classification !== "question" || response.answerable !== true) throw new Error("Contract compilation returned no reviewable contract");
        return { text: promptText(stripOverride(raw).text, response.context_note) };
      }
      const contract = response.contract;
      const identity = approvalIdentity(contract, sessionId);
      if (response.classification === "rejection") {
        if (contract.plan.bindings.approval.status !== "pending") throw new Error("Only a pending proposal can receive rejection");
        await record(ctx, sessionId, raw, "intent-reject", contract);
        await post("/reject", identity);
        saveState(ctx, sessionId, null);
        return { handled: true };
      }
      saveState(ctx, sessionId, contract);
      if (!ctx.hasUI || typeof ctx.ui?.select !== "function" || typeof ctx.ui?.editor !== "function") throw new Error("Interactive intent review requires select/editor UI; approval was not granted");
      const choice = await ctx.ui.select(renderContract(contract, response), [APPROVE, EDIT, REJECT]);
      currentSession(ctx, sessionId);
      if (choice === APPROVE) {
        const approved = await post("/approve", identity);
        currentSession(ctx, sessionId);
        const next = approvalIdentity(approved.contract, sessionId);
        const binding = approved.contract.plan.bindings.approval;
        if (next.contract_id !== identity.contract_id || next.contractHash !== identity.contractHash || next.sourceHash !== identity.sourceHash || next.revision < identity.revision || binding.status !== "approved" || binding.approved_by !== "kevin" || !Number.isFinite(Date.parse(binding.approved_at)) || typeof approved.path !== "string" || !approved.path) throw new Error("Approval response did not bind the reviewed contract");
        saveState(ctx, sessionId, approved.contract);
        return { text: promptText(stripOverride(raw).text, `Human-approved intent ${next.contract_id}, revision ${next.revision}. ${compact(response.summary)}\nContract path: ${approved.path}\nApproval covers only its exact sources and tasks; amendments require review.`) };
      }
      if (choice !== EDIT) {
        await record(ctx, sessionId, "Reject", "intent-reject", contract);
        await post("/reject", identity);
        currentSession(ctx, sessionId);
        saveState(ctx, sessionId, null);
        return { handled: true };
      }
      const edited = await ctx.ui.editor("Amend intent (original entries remain unchanged)", stripOverride(raw).text);
      currentSession(ctx, sessionId);
      if (edited === undefined || !edited.trim()) {
        await record(ctx, sessionId, "Reject", "intent-reject", contract);
        await post("/reject", identity);
        saveState(ctx, sessionId, null);
        return { handled: true };
      }
      // An editor amendment is an unprefixed contract source, never a new quick:
      // bypass. Its raw bytes are retained and independently receipted.
      raw = edited;
      const amended = await record(ctx, sessionId, raw, "intent-editor", contract);
      if (amended.result.tier !== "CONTRACT") throw new Error("An amendment cannot bypass contract review with an override");
      entryId = amended.entryId;
      previous = contract;
      response = await post("/reconcile", { session_id: sessionId, entry_id: entryId, text: raw, recent_user_msgs: recent, open_contract: identity.contract_id, mode: "CONTRACT" });
      currentSession(ctx, sessionId);
    }
  }
  async function input(event, ctx) {
    let sessionId;
    let ownsBusy = false;
    try {
      sessionId = ctx.sessionManager.getSessionId();
      sessionDirectory(sessionId, options.home);
      if (typeof event.text !== "string") throw new Error("Missing input text");
      const previous = restoredContract(ctx);
      if (busy.has(sessionId)) {
        await record(ctx, sessionId, event.text, event.source ?? "interactive", previous ?? true, Boolean(event.images?.length));
        throw new Error("An intent review is already pending; input was recorded but not dispatched");
      }
      busy.add(sessionId); ownsBusy = true;
      const { result, entryId } = await record(ctx, sessionId, event.text, event.source ?? "interactive", previous, Boolean(event.images?.length));
      if (result.tier === "FAST") return result.override ? { text: result.text } : undefined;
      const recent = recentMessages(ctx);
      if (result.tier === "CHECK") {
        const checked = await post("/check", { session_id: sessionId, entry_id: entryId, text: event.text, recent_user_msgs: recent, open_contract: null, mode: "CHECK" });
        currentSession(ctx, sessionId);
        if (typeof checked.context_note !== "string" || !checked.context_note.trim() || checked.contract) throw new Error("CHECK must return context only, never approval");
        return { text: promptText(result.text, checked.context_note.slice(0, 2000)) };
      }
      let open = previous;
      if (!open) {
        const current = await post("/current", { session_id: sessionId });
        currentSession(ctx, sessionId);
        open = current.contract ?? null;
      }
      return await review(ctx, sessionId, event.text, entryId, open, recent);
    } catch (error) {
      try { ctx.ui?.notify?.(`Intent input blocked: ${error instanceof Error ? error.message : String(error)}`, "error"); }
      catch { /* Notification failure must not make OMP's input runner fail open. */ }
      return { handled: true };
    } finally { if (ownsBusy) busy.delete(sessionId); }
  }
  return { input };
}

export default function intentService(pi) {
  if (pi.events && Reflect.get(pi.events, registration)) return;
  if (pi.events) Reflect.set(pi.events, registration, true);
  const client = createIntentClient(pi);
  pi.on("input", client.input);
  // Interactive OMP emits input before slash dispatch; direct session.prompt
  // dispatches commands without that hook. The command covers the latter seam.
  pi.registerCommand("aodl", {
    description: "Review an intent contract before acting: /aodl <ask>",
    async handler(args, ctx) {
      // The command API exposes args only, not original pre-dispatch whitespace.
      const result = await client.input({ text: `/aodl${args ? ` ${args}` : ""}`, source: "command" }, ctx);
      if (result && !result.handled && result.text) pi.sendUserMessage(result.text);
    },
  });
}
