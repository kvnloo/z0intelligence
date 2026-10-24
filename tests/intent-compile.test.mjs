import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const script = fileURLToPath(new URL('../src/z0int/intent_compile.mjs', import.meta.url));
function bridge(input) {
  const result = spawnSync(process.execPath, [script], { input: JSON.stringify(input), encoding: 'utf8' });
  if (result.status !== 0) throw new Error(result.stderr);
  return JSON.parse(result.stdout);
}
function fixture(text = 'Build café.\n Check it!') {
  const doc = bridge({ op: 'prepare', session_id: 'test', entry_id: 'entry', text, timestamp: '2026-10-24T20:00:00Z' });
  const sentences = doc.plan.bindings.sources.entry.sentences;
  const draft = { classification: 'new_ask', sentences: sentences.map(s => ({ entryId: 'entry', sentenceId: s.id, classification: 'add' })),
    tasks: sentences.map(s => ({ entryId: 'entry', sentenceId: s.id, target: 'café artifact', interpretation: 'Requested artifact', executor: 'self', assumptions: [], conflicts: [], verifier: { type: 'human_review', target: 'café artifact' } })),
    assumptions: [], conflicts: [], delegation_plan: [], amendments: [] };
  return { op: 'finish', doc, draft, model_id: 'openai-codex/test', records: [], contract_id: 'contract-test' };
}

test('exact source slices and one task per actionable sentence survive compilation', () => {
  const input = fixture();
  const doc = bridge(input), b = doc.plan.bindings;
  const tasks = doc.intentGraph.nodes.filter(n => n.kind === 'task');
  assert.equal(tasks.length, 2);
  assert.deepEqual(tasks.map(n => b[n.id].text), ['Build café.', ' Check it!']);
  assert.equal(b.approval.status, 'pending');
  assert.equal(b.approval.contractHash, bridge({ op: 'hash', doc }));
  assert.ok(doc.intentGraph.nodes.every(n => ['task', 'verifier', 'stateStore'].includes(n.kind)));
});

test('rejects omissions, duplicate classifications, and tasks with invented source', () => {
  for (const change of [
    f => f.draft.sentences.pop(),
    f => { f.draft.sentences[1] = f.draft.sentences[0]; },
    f => { f.draft.tasks[0].sentenceId = 'invented'; },
  ]) {
    const input = fixture(); change(input);
    assert.throws(() => bridge(input), /sentence|source|classification/i);
  }
});

test('approval hash binds executor, compiler facts, and target not runtime task status', () => {
  const doc = bridge(fixture()), initial = bridge({ op: 'hash', doc });
  const id = doc.intentGraph.nodes.find(n => n.kind === 'task').id;
  doc.plan.bindings[id].status = 'verified';
  assert.equal(bridge({ op: 'hash', doc }), initial);
  doc.plan.bindings[id].executor = 'different-device';
  assert.notEqual(bridge({ op: 'hash', doc }), initial);
});
