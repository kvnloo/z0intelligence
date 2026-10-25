import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createLookup, actionKind } from './index.js';

function fixture(t) {
  const home = mkdtempSync(join(tmpdir(), 'look-first-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const session = 'session1', turn = 'entry1', text = 'Check option capacity';
  const sourceHash = createHash('sha256').update(text).digest('hex');
  mkdirSync(join(home, 'intent', session), { recursive: true });
  writeFileSync(join(home, 'intent', session, 'triage.jsonl'), JSON.stringify({ entry_id: turn, tier: 'CHECK', sourceHash }) + '\n');
  let activeSession = session;
  const ctx = { sessionManager: { getSessionId: () => activeSession, getBranch: () => [{ id: turn, customType: 'z0-intent-input', data: { rawText: text } }] }, ui: { notify() {} } };
  const entries = [];
  const pi = { appendEntry: (...args) => entries.push(args), exec: async (_command, args) => ({ code: 0, stdout: JSON.stringify({ schema: 'z0.consultation.v1', query: args[1], hits: [{ source: 'docs/METHOD.md:9' }], turn, session, sourceHash, ts: new Date().toISOString(), status: 'ok', block: '<z0-lookup>\n2 options [source: docs/METHOD.md:9]\n</z0-lookup>' }) }) };
  return { home, ctx, pi, entries, switchSession: () => { activeSession = 'other'; } };
}

test('CHECK input injects block and journals same-turn receipt before design', async t => {
  const f = fixture(t);
  const result = await createLookup(f.pi, { home: f.home }).beforeStart({}, f.ctx);
  assert.match(result.message.content, /<z0-lookup>/);
  const rows = readFileSync(join(f.home, 'intent', 'session1', 'consultations.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(rows[0].turn, 'entry1');
  assert.equal(rows[0].schema, 'z0.consultation.v1');
  assert.equal(rows[1].action, 'design');
});

test('task/edit evidence returns before first call and exact retry proceeds', async t => {
  const f = fixture(t), client = createLookup(f.pi, { home: f.home });
  await client.beforeStart({}, f.ctx);
  const event = { toolName: 'task', input: { task: 'Check capacity' }, toolCallId: 'tool1' };
  assert.equal((await client.beforeTool(event, f.ctx)).block, true);
  assert.equal(await client.beforeTool({ ...event, toolCallId: 'tool2' }, f.ctx), undefined);
  assert.equal((await client.beforeTool({ ...event, input: { task: 'different' } }, f.ctx)).block, true);
});

test('lookup failure remains report-only and cannot issue successful receipt', async t => {
  const f = fixture(t); f.pi.exec = async () => { throw new Error('offline'); };
  const result = await createLookup(f.pi, { home: f.home }).beforeStart({}, f.ctx);
  assert.match(result.message.content, /unavailable/);
  assert.equal(f.entries[0][1].status, 'unavailable');
});

test('session switch refuses to persist replacement-session receipt', async t => {
  const f = fixture(t), original = f.pi.exec;
  f.pi.exec = async (...args) => { const result = await original(...args); f.switchSession(); return result; };
  await createLookup(f.pi, { home: f.home }).beforeStart({}, f.ctx);
  assert.equal(f.entries.length, 0);
});

test('direct and namespaced editing/delegation hooks include shell and eval', () => {
  for (const name of ['edit', 'functions.write', 'bash', 'functions.eval']) assert.equal(actionKind(name), 'edit');
  assert.equal(actionKind('functions.task'), 'delegation');
  assert.equal(actionKind('read'), null);
});
