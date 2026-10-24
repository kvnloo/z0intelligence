// HOTL compilation reuses the intent gate's single canonical checker implementation.
import { pathToFileURL, fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { readFileSync } from 'node:fs';
const checkerPath = process.env.Z0INT_INTENT_CHECKER || resolve(dirname(fileURLToPath(import.meta.url)), '../../omp-extensions/z0-relay-guard/checker.js');
const { createDocument, ingestUser, updateIntent, contractHash } = await import(pathToFileURL(checkerPath));

export function prepare(input) {
  const doc = input.open ? structuredClone(input.open) : createDocument(input.session_id);
  if (doc.plan.bindings.sessionId !== input.session_id) throw new Error('Open contract session mismatch');
  delete doc.plan.bindings.approval;
  ingestUser(doc, { id: input.entry_id, timestamp: input.timestamp,
    message: { role: 'user', content: input.text } });
  return doc;
}

export function finish({ doc, draft, model_id, records, contract_id, parent_contract_id, parent_contractHash }) {
  const b = doc.plan.bindings;
  const current = Object.entries(b.sources).flatMap(([entryId, s]) => s.sentences
    .filter(s => s.classification === 'unclassified').map(s => ({ entryId, ...s })));
  if (!Array.isArray(draft.sentences) || draft.sentences.length !== current.length) throw new Error('Every new source sentence needs one classification');
  const used = new Set();
  for (const source of current) {
    const matches = draft.sentences.filter(s => s.entryId === source.entryId && s.sentenceId === source.id);
    if (matches.length !== 1) throw new Error('Missing or duplicate exact source sentence');
    const decision = matches[0];
    if (decision.classification === 'add') {
      const matches = draft.tasks.filter(t => t.entryId === source.entryId && t.sentenceId === source.id);
      if (matches.length !== 1) throw new Error('An actionable sentence needs exactly one task');
      const task = matches[0]; used.add(task);
      const taskId = `task.${source.entryId.replace(/[^A-Za-z0-9_.:-]/g, '-').slice(0, 64)}.${used.size}`;
      updateIntent(doc, { op: 'add', taskId, entryId: source.entryId, sentenceId: source.id,
        target: task.target, acceptance: task.verifier,
        preconditions: (task.preconditions || []).map(p => ({ ...p.verifier, claim: p.claim })) });
      Object.assign(b[taskId], { interpretation: task.interpretation, executor: task.executor,
        assumptions: task.assumptions || [], conflicts: task.conflicts || [] });
    } else if (decision.classification === 'not-an-ask') {
      updateIntent(doc, { op: 'not-an-ask', entryId: source.entryId, sentenceId: source.id, reason: decision.reason });
    } else throw new Error('Unsupported source classification');
  }
  if (used.size !== draft.tasks.length) throw new Error('Task has no exact actionable source sentence');
  for (const change of draft.amendments || []) {
    const task = b[change.taskId];
    if (!task || !doc.intentGraph.nodes.some(n => n.id === change.taskId && n.kind === 'task')) throw new Error('Unknown amendment target');
    if (change.op !== 'drop' || !change.reason) throw new Error('Amendment must explicitly drop superseded task with reason');
    updateIntent(doc, change);
  }
  b.compiler = { model_id, classification: draft.classification, memory: records,
    assumptions: draft.assumptions || [], conflicts: draft.conflicts || [],
    delegation_plan: draft.delegation_plan || [], parent_contract_id: parent_contract_id || null,
    parent_contractHash: parent_contractHash || null };
  const sourceHash = doc.provenance.sourceHash;
  b.approval = { status: 'pending', session_id: b.sessionId, contract_id, sourceHash, contractHash: contractHash(doc) };
  return doc;
}

const input = JSON.parse(readFileSync(0, 'utf8'));
try {
  const result = input.op === 'prepare' ? prepare(input) : input.op === 'hash' ? contractHash(input.doc) : finish(input);
  console.log(JSON.stringify(result));
} catch (error) { console.error(error.message); process.exitCode = 1; }
