#!/usr/bin/env node
/** Private, evidence-only reconstruction. Never executes a verifier or a recorded tool. */
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { chmod, readFile, writeFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const usage = `Usage: node scripts/replay-intent-gate.mjs
  --labels /private/ask-audit/labels.jsonl
  --messages /private/ask-audit/messages.jsonl
  --reconstruction /private/aodl-intent-gate/replay.json
  [--prepare] [--section /private/replay-section.txt]
  [--output /path/to/anonymous-aggregate.json] [--checker /path/to/checker.js]
  [--sessions-root /mounted/session/root]

--prepare resolves and records source evidence without loading the checker.
Without --prepare, check() evaluates I1-I4 conditional on reviewer-reconstructed,
HYPOTHETICALLY approved contracts. It does not fabricate historical approval,
verifier receipts, delegation state, or tool results. No recorded tool executes.
Private reconstruction and section files must remain outside this checkout.
The optional sessions root replaces the original sessions/ prefix after relocation.
Only the aggregate output is suitable for publication. No per-row data goes there.
`;
const options = {};
for (let i = 2; i < process.argv.length; i++) {
  const key = process.argv[i];
  if (key === '--help') { console.log(usage); process.exit(0); }
  if (key === '--prepare') { options.prepare = true; continue; }
  if (!['--labels', '--messages', '--reconstruction', '--section', '--output', '--checker', '--sessions-root'].includes(key) || !process.argv[i + 1] || process.argv[i + 1].startsWith('--')) throw new Error('Invalid replay arguments. Use --help.');
  options[key.slice(2)] = resolve(process.argv[++i]);
}
for (const name of ['labels', 'messages', 'reconstruction']) if (!options[name]) throw new Error(`Missing --${name}. Use --help.`);
function privatePath(path) {
  const rel = relative(root, path);
  if (!rel || (!rel.startsWith(`..${sep}`) && rel !== '..' && !isAbsolute(rel))) throw new Error('Private replay files must remain outside the source checkout.');
}
privatePath(options.reconstruction);
if (options.section) privatePath(options.section);
const sha = value => createHash('sha256').update(value).digest('hex');
async function* jsonLines(path) {
  const input = createReadStream(path, { encoding: 'utf8' });
  const lines = createInterface({ input, crlfDelay: Infinity });
  try { for await (const line of lines) if (line.trim()) yield { value: JSON.parse(line), digest: sha(line) }; }
  finally { lines.close(); input.destroy(); }
}
function entryProjection(entry, digest) {
  const message = entry.message ?? {};
  const content = typeof message.content === 'string' ? [{ type: 'text', text: message.content }] : message.content ?? [];
  let phase;
  for (const part of content) {
    if (typeof part.textSignature !== 'string') continue;
    try { phase = JSON.parse(part.textSignature).phase ?? phase; } catch { /* Older signatures are opaque strings. */ }
  }
  return {
    id: entry.id, timestamp: entry.timestamp ?? message.timestamp, role: message.role,
    texts: content.filter(part => part.type === 'text').map(part => part.text),
    toolCalls: content.filter(part => part.type === 'toolCall'),
    ...(message.toolCallId ? { toolCallId: message.toolCallId } : {}),
    ...(message.toolName ? { toolName: message.toolName } : {}),
    ...(typeof message.isError === 'boolean' ? { isError: message.isError } : {}),
    ...(message.stopReason ? { stopReason: message.stopReason } : {}),
    ...(phase ? { phase } : {}), sourceLineSha256: digest,
  };
}
// Same byte-preserving boundaries as ingestUser. Preparation does not import the engine.
function sourceSentences(texts) {
  const result = [];
  texts.forEach((text, block) => {
    let start = 0;
    const add = end => {
      if (text.slice(start, end).trim()) result.push({ id: `s${block}.${start}.${end}`, text: text.slice(start, end), block, start, end });
      start = end;
    };
    for (let i = 0; i < text.length; i++) if (text[i] === '\n' || (/[.!?]/.test(text[i]) && (i + 1 === text.length || /\s/.test(text[i + 1])))) add(i + 1);
    add(text.length);
  });
  return result;
}
async function savePrivate(path, value) {
  await writeFile(path, value, { mode: 0o600 });
  await chmod(path, 0o600);
}
const reconstruction = JSON.parse(await readFile(options.reconstruction, 'utf8'));
if (reconstruction.rows?.length !== 20 || new Set(reconstruction.rows.map(row => row.reviewId)).size !== 20) throw new Error('Reconstruction requires 20 distinct confirmed audit labels.');
const selectedIds = new Set(reconstruction.rows.map(row => row.reviewId));
const labels = new Map();
for await (const { value, digest } of jsonLines(options.labels)) if (selectedIds.has(value.review_id)) labels.set(value.review_id, { ...value, sourceLineSha256: digest });
if (labels.size !== 20 || [...labels.values()].some(label => label.outcome === 'not-a-miss' || label.harness !== 'omp')) throw new Error('Every reconstruction must reference a confirmed OMP audit failure.');
const sourcePaths = new Map();
for await (const { value } of jsonLines(options.messages)) if ([...labels.values()].some(label => label.session === value.session)) sourcePaths.set(value.session, value.source);
function relocated(path) {
  if (!options['sessions-root']) return path;
  const marker = `${sep}sessions${sep}`;
  const at = path.indexOf(marker);
  if (at < 0) throw new Error('Cannot relocate session source without sessions/ marker.');
  return resolve(options['sessions-root'], path.slice(at + marker.length));
}
const requirements = new Map();
for (const row of reconstruction.rows) {
  const label = labels.get(row.reviewId), ask = label.evidence.find(item => item.kind === 'ask');
  if (!ask) throw new Error('Label lacks original ask evidence.');
  row.askSessionId ??= ask.session;
  row.askEntryId ??= ask.entry_id;
  row.failureSessionId = label.session;
  row.correctionEntryId = label.entry_id;
  row.correctionTimestamp = label.ts;
  row.labelOutcome = label.outcome;
  row.labelCause = label.cause;
  row.labelReason = label.reason;
  if (row.labelLineSha256 && row.labelLineSha256 !== label.sourceLineSha256) throw new Error('A selected audit label changed since reconstruction.');
  row.labelLineSha256 = label.sourceLineSha256;
  row.auditEvidence = label.evidence;
  for (const sid of [row.askSessionId, label.session]) {
    if (!sourcePaths.has(sid)) throw new Error('Original session path missing from private messages input.');
    requirements.set(sid, sourcePaths.get(sid));
  }
}
const entries = new Map();
for (const [sid, source] of requirements) {
  const projected = [];
  for await (const { value, digest } of jsonLines(relocated(source))) if (value.type === 'message') projected.push(entryProjection(value, digest));
  entries.set(sid, projected);
}
let engine;
if (!options.prepare) engine = await import(pathToFileURL(options.checker ?? resolve(root, 'omp-extensions/z0-relay-guard/checker.js')).href);
for (const row of reconstruction.rows) {
  const askRows = entries.get(row.askSessionId), failureRows = entries.get(row.failureSessionId);
  const ask = askRows.find(entry => entry.id === row.askEntryId);
  const momentIndex = failureRows.findIndex(entry => entry.id === row.momentEntryId);
  if (!ask || ask.role !== 'user' || momentIndex < 0) throw new Error('Missing original ask or recorded failure moment.');
  const moment = failureRows[momentIndex];
  if (Date.parse(ask.timestamp) > Date.parse(moment.timestamp)) throw new Error('Ask postdates alleged failure. Select a genuine earlier ask.');
  const correction = failureRows.find(entry => entry.id === row.correctionEntryId);
  if (!correction) throw new Error('Correction entry missing from original session.');
  if ((row.ask?.sourceLineSha256 && row.ask.sourceLineSha256 !== ask.sourceLineSha256) || (row.momentEvidence?.sourceLineSha256 && row.momentEvidence.sourceLineSha256 !== moment.sourceLineSha256)) throw new Error('An original source entry changed since reconstruction.');
  row.askTimestamp = ask.timestamp;
  row.failureTimestamp = moment.timestamp;
  row.sourceSessions = { ask: requirements.get(row.askSessionId), failure: requirements.get(row.failureSessionId) };
  row.ask = ask;
  row.momentEvidence = moment;
  row.correctionEvidence = correction;
  const askIndex = failureRows.findIndex(entry => entry.id === row.askEntryId);
  // A cross-session ask does not imply the second session contained the first one's history.
  row.recordedTranscript = failureRows.slice(askIndex >= 0 ? Math.max(0, askIndex - (row.contextBeforeAskEntries ?? 0)) : 0, momentIndex + 1);
  row.crossSessionContextKnown = row.askSessionId === row.failureSessionId;
  row.recordedToolResultCount = row.recordedTranscript.filter(entry => entry.role === 'toolResult').length;
  row.historicalApproval = 'no-formal-intent-contract-recorded';
  row.hypotheticalApproval = 'Reviewer reconstruction only. Historical conversational approvals do not approve this new formal contract.';
  row.verifier = { type: 'human_review', target: row.target };
  row.sentenceAnnotations = sourceSentences(ask.texts).map(sentence => {
    const isAsk = row.askFragments.some(fragment => sentence.text.includes(fragment));
    return { ...sentence, classification: isAsk ? 'add' : 'not-an-ask',
      ...(isAsk ? {} : { reason: row.contextReason }) };
  });
  if (!row.sentenceAnnotations.some(sentence => sentence.classification === 'add')) throw new Error('No annotated ask sentence matched original source.');
  row.checkerStatus = options.prepare ? 'not-run-parent-verification-required' : 'counterfactual-I1-I4';
  delete row.gateResult;
  if (options.prepare) continue;
  const doc = engine.createDocument(`private-replay-${row.reviewId}`);
  const source = engine.ingestUser(doc, { id: ask.id, timestamp: ask.timestamp, message: { role: 'user', content: ask.texts.map(text => ({ type: 'text', text })) } });
  const taskIds = [];
  for (const sentence of source.sentences) {
    const annotation = row.sentenceAnnotations.find(item => item.id === sentence.id && item.text === sentence.text);
    if (!annotation) throw new Error('Sentence boundaries changed. Reconstruct against the current engine before replay.');
    if (annotation.classification === 'not-an-ask') {
      engine.updateIntent(doc, { op: 'not-an-ask', entryId: ask.id, sentenceId: sentence.id, reason: annotation.reason });
      continue;
    }
    const taskId = `replay.${row.reviewId}.t${taskIds.length}`;
    engine.updateIntent(doc, { op: 'add', taskId, entryId: ask.id, sentenceId: sentence.id, target: row.target, acceptance: row.verifier });
    taskIds.push(taskId);
  }
  // Raw tool successes are not acceptance receipts. In particular, exit 0 alone
  // cannot establish visual identity, shipped state, or product correctness.
  const claims = row.completionClaimObserved ? taskIds : [];
  const turnEnd = moment.role === 'assistant' && (moment.phase === 'final_answer' || (!moment.phase && moment.stopReason === 'stop'));
  row.turnEndObserved = turnEnd;
  row.gateResult = engine.check(doc, { claims, turnEnd, mutating: false });
  row.claimProjection = { taskIds: claims, basis: row.claimBasis ?? 'No completion claim projected.' };
  row.receiptProjection = 'No verifier/delegation/approval events synthesized. Recorded transcript remains evidence, not invented engine state.';
}
reconstruction.mode = options.prepare ? 'prepared-not-executed' : 'counterfactual-replay';
reconstruction.historicalApprovalCount = 0;
reconstruction.evaluationScope = 'I1-I4 conditional on hypothetical approval. I0 excluded by nonmutating check. No historical prevention or accuracy claim.';
reconstruction.lawCoverage = { I1: 'Exact source coverage and observed turn-end unresolved obligations.', I2: 'Only explicitly annotated recorded completion claims. No historical verifier receipts are synthesized.', I3: 'Reconstructed target and verifier consistency only. Historical wrong-property semantics are not machine-provable here.', I4: 'No historical contract task IDs exist in relays. Semantic narrowing is documented, not forced into invented forwarded IDs.' };
reconstruction.canary = { status: 'blocked', reason: 'Parent reported OPENROUTER_API_KEY absent. No substitute provider used.' };
await savePrivate(options.reconstruction, `${JSON.stringify(reconstruction, null, 2)}\n`);
const lawCounts = {}, limitationCounts = {};
for (const row of reconstruction.rows) {
  for (const law of new Set((row.gateResult?.violations ?? []).map(item => item.law))) lawCounts[law] = (lawCounts[law] ?? 0) + 1;
  for (const limitation of row.limitations) limitationCounts[limitation] = (limitationCounts[limitation] ?? 0) + 1;
}
const aggregate = {
  mode: reconstruction.mode, rows: reconstruction.rows.length,
  historicalContractApprovals: 0, hypotheticalApprovedReconstruction: true,
  checkerRuns: options.prepare ? 0 : reconstruction.rows.length,
  counterfactualBlockedRows: options.prepare ? null : reconstruction.rows.filter(row => !row.gateResult.ok).length,
  lawCounts, limitationCounts,
  interpretation: 'Structural replay only. Missing historical instrumentation is not proof that no real check or live delegate existed. Manual semantics and unknown identities limit causal attribution.',
  canary: reconstruction.canary.status,
};
if (options.section) {
  const lines = [
    'PRIVATE: AODL intent gate historical reconstruction',
    `Status: ${reconstruction.mode}. Checker runs: ${aggregate.checkerRuns}.`,
    'Twenty confirmed labels, not twenty independent incidents or a random sample.',
    'No formal Kevin-approved intent contracts are recorded. Ordinary conversational approvals, including yes, do occur.',
    'I1-I4 are evaluated only under a hypothetical approved reconstruction. I0 is not counted.',
    'No verifier, approval, or delegation result is invented. Recorded tools never execute.',
    'All reconstructed acceptance verifiers require human review of the original target.',
    'I4 limitation: historical relays have no contract task IDs. No missing carried IDs are fabricated.',
    'A structural block caused by missing instrumentation is not demonstrated prevention.',
    'False not-an-ask: reasoned misclassification can erase a genuine ask without a structural violation.',
    'Wrong property: an immutable but wrong acceptance property can still pass. Human approval must resolve it.',
    'Identity not inferable: pane, asset, process, and remote-head identities cannot be guessed from descriptions.',
    'The script does not infer live delegation from a named worker or an intention to delegate.',
    'Canary blocked: parent reported OPENROUTER_API_KEY absent. No substitute provider.',
    '',
  ];
  for (const row of reconstruction.rows) lines.push(
    `Review ${row.reviewId} | ask ${row.askSessionId}/${row.askEntryId} @ ${row.askTimestamp}`,
    `Failure moment ${row.failureSessionId}/${row.momentEntryId} @ ${row.failureTimestamp}`,
    `Audit correction ${row.correctionEntryId} @ ${row.correctionTimestamp}`,
    `Expected law (hypothesis): ${row.law}. Reason: ${row.reason}`,
    `Target: ${row.target}. Verifier: human_review. Limitations: ${row.limitations.join(', ')}.`,
    `Moment evidence: ${(row.momentEvidence.texts.join('\n') || JSON.stringify(row.momentEvidence.toolCalls)).slice(0, 1200)}`,
    `Recorded tool results through moment: ${row.recordedToolResultCount}. Result: ${row.gateResult ? JSON.stringify(row.gateResult) : 'NOT RUN'}.`,
    '',
  );
  await savePrivate(options.section, `${lines.join('\n')}\n`);
}
const publicText = `${JSON.stringify(aggregate, null, 2)}\n`;
if (options.output) {
  if ([options.reconstruction, options.section, options.labels, options.messages, options.checker].includes(options.output)) throw new Error('Aggregate output must not overwrite an input or private report.');
  await writeFile(options.output, publicText);
}
process.stdout.write(publicText);
