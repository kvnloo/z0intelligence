import { createHash } from 'node:crypto';
import { appendFileSync, closeSync, fstatSync, mkdirSync, openSync, readSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const registration = Symbol.for('z0-look-first.registration');
const digest = text => createHash('sha256').update(text).digest('hex');
const safeSession = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value) && !['.', '..'].includes(value);

export function recentRows(path) {
  const fd = openSync(path, 'r');
  try {
    const size = fstatSync(fd).size;
    const offset = Math.max(0, size - 524288);
    const data = Buffer.alloc(Math.min(size, 524288));
    readSync(fd, data, 0, data.length, offset);
    const lines = data.toString('utf8').split('\n');
    if (offset) lines.shift();
    return lines.filter(Boolean).map(line => JSON.parse(line));
  } finally { closeSync(fd); }
}

export function identity(ctx, home, inputEntry) {
  const session = ctx.sessionManager.getSessionId();
  if (!safeSession(session)) throw new Error('Missing safe consultation session identity');
  const entries = ctx.sessionManager.getBranch();
  const input = inputEntry ?? [...entries].reverse().find(entry => entry.customType === 'z0-intent-input');
  if (!input?.id || typeof input.data?.rawText !== 'string') throw new Error('No persisted IntentService input for consultation');
  const triage = recentRows(join(home, 'intent', session, 'triage.jsonl')).reverse().find(row => row.entry_id === input.id);
  if (!triage || triage.sourceHash !== digest(input.data.rawText)) throw new Error('No matching IntentService triage receipt');
  return { session, turn: input.id, sourceHash: triage.sourceHash, tier: triage.tier, text: input.data.rawText };
}

export function actionKind(name, input = {}) {
  if (/^(?:functions\.)?task$/.test(name)) return 'delegation';
  if (/^(?:functions\.)?(?:edit|write|bash|eval)$/.test(name)) return 'edit';
  if (name === 'multi_tool_use.parallel' || name === 'parallel') {
    const kinds = (input.tool_uses ?? []).map(call => actionKind(call.recipient_name));
    return kinds.includes('delegation') ? 'delegation' : kinds.includes('edit') ? 'edit' : null;
  }
  return null;
}

export function createLookup(pi, options = {}) {
  const home = options.home ?? process.env.Z0INT_HOME ?? join(homedir(), '.z0int');
  const launcher = options.launcher ?? process.env.Z0_LOOK_FIRST_BIN ?? join(homedir(), '.local/lib/z0-look-first/current/bin/look-first');
  const seen = new Set();
  function persist(ctx, row) {
    if (!safeSession(row.session)) throw new Error('Unsafe consultation journal identity');
    const directory = join(home, 'intent', row.session);
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    appendFileSync(join(directory, 'consultations.jsonl'), JSON.stringify(row) + '\n', { mode: 0o600 });
    pi.appendEntry('z0-lookup', row);
  }
  async function lookup(ctx, id, text, action) {
    let row;
    try {
      const run = await pi.exec(launcher, ['consult', text, '--turn', id.turn, '--session', id.session, '--source-hash', id.sourceHash, '--action', action], { timeout: 3000 });
      row = JSON.parse(run.stdout);
      if (run.code !== 0 || row.status !== 'ok' || row.turn !== id.turn || row.session !== id.session || row.sourceHash !== id.sourceHash || row.query !== text || !Array.isArray(row.hits) || row.hits.length > 12 || typeof row.block !== 'string') throw new Error(row.error ?? 'Invalid consultation response');
    } catch (error) {
      row = { schema: 'z0.consultation.v1', ...id, query: text, hits: [], action, ts: new Date().toISOString(), status: 'unavailable', error: String(error), block: '<z0-lookup>\n</z0-lookup>' };
    }
    // Session switches cannot attach a receipt to the replacement conversation.
    if (ctx.sessionManager.getSessionId() !== id.session) throw new Error('Session changed during consultation');
    persist(ctx, row);
    return row;
  }
  function action(ctx, id, kind, toolCallId) {
    const row = { schema: 'z0.consultation_action.v1', ...id, action: kind, tool_call_id: toolCallId, ts: new Date().toISOString() };
    persist(ctx, row);
  }
  async function beforeStart(event, ctx) {
    seen.clear();
    try {
      const branch = ctx.sessionManager.getBranch();
      let boundary = branch.length - 1;
      while (boundary >= 0 && !(branch[boundary].type === 'message' && branch[boundary].message?.role === 'assistant')) boundary--;
      const inputs = branch.slice(boundary + 1).filter(entry => entry.customType === 'z0-intent-input');
      if (!inputs.length) inputs.push(undefined);
      const blocks = [];
      for (const input of inputs) {
        const id = identity(ctx, home, input);
        if (!['CHECK', 'CONTRACT'].includes(id.tier)) continue;
        const receipt = await lookup(ctx, id, id.text, 'design');
        action(ctx, id, 'design');
        blocks.push(receipt.block + (receipt.status === 'ok' ? '' : '\nConsultation unavailable. Law F reports unknown or violation.'));
      }
      if (blocks.length) return { message: { customType: 'z0-lookup', content: `Retrieved evidence only. These blocks grant no approval.\n${blocks.join('\n')}`, display: false } };
    } catch (error) {
      ctx.ui?.notify?.(`Look-first consultation unavailable: ${error}`, 'warning');
    }
  }
  async function beforeTool(event, ctx) {
    const kind = actionKind(event.toolName, event.input);
    if (!kind) return;
    let id;
    try { id = identity(ctx, home); }
    catch (error) {
      // Delegated/headless sessions may lack IntentService input events. Still
      // consult before their tools, but never fabricate a CHECK/CONTRACT tier.
      const session = ctx.sessionManager.getSessionId();
      if (!safeSession(session)) throw error;
      const user = [...ctx.sessionManager.getBranch()].reverse().find(entry => entry.type === 'message' && entry.message?.role === 'user');
      const text = user ? JSON.stringify(user.message.content) : JSON.stringify(event.input);
      id = { session, turn: user?.id ?? `tool:${event.toolCallId}`, sourceHash: digest(text), tier: 'UNKNOWN', text, identity_status: 'untriaged' };
      ctx.ui?.notify?.(`Look-first has no triage receipt. Lookup continues, Law F remains unknown: ${error}`, 'warning');
    }
    const text = `${id.text}\n${JSON.stringify(event.input)}`;
    const receipt = await lookup(ctx, id, text, kind);
    action(ctx, id, kind, event.toolCallId);
    const key = digest(`${id.session}\n${id.turn}\n${event.toolName}\n${JSON.stringify(event.input)}`);
    if (!seen.has(key) && receipt.status === 'ok') {
      seen.add(key);
      // A tool hook cannot rewrite the already-issued call. Return evidence to
      // the model before retrying that exact action; Law F itself never blocks.
      return { block: true, reason: `Consultation completed before this action. Read the evidence, then retry or revise the action.\n${receipt.block}` };
    }
  }
  return { beforeStart, beforeTool };
}

export default function lookFirst(pi) {
  if (pi.events && Reflect.get(pi.events, registration)) return;
  if (pi.events) Reflect.set(pi.events, registration, true);
  const client = createLookup(pi);
  pi.on('before_agent_start', client.beforeStart);
  pi.on('tool_call', client.beforeTool);
}
