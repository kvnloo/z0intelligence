import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const root = resolve(process.argv[2]);
const { validateBundle, fingerprints, assertCurrent } = await import(pathToFileURL(resolve(root, 'native/openui-preview/src/bundle.mjs')));
const files = (await readdir(resolve(root, 'artifacts/bundles'))).filter(f => f.endsWith('.hvisual.json'));
assert.equal(files.length, 1);
const bundle = validateBundle(JSON.parse(await readFile(resolve(root, 'artifacts/bundles', files[0]), 'utf8')));
const board = JSON.parse(await readFile(resolve(root, 'artifacts/intent-board.json'), 'utf8'));
assert.deepEqual(bundle.artifact.document, board);
assertCurrent(bundle, 'ayo:Z14', await fingerprints());
assert.deepEqual(bundle.selection.view.sections.map(s => s.kind), ['DeclaredIntent', 'ContractGates', 'WorkSlices', 'ProofReceipts']);
console.log('PASS existing native bundle schema, source fingerprints and complete host-document binding');
for (const [name, mutate] of [
  ['changed artifact', b => { b.artifact.document.contract.approval = 'approved'; }],
  ['invented approval section', b => { b.selection.view.sections.push({kind: 'ApproveContract'}); }],
]) {
  const bad = structuredClone(bundle);
  mutate(bad);
  assert.throws(() => validateBundle(bad));
  console.log('PASS native bundle rejects ' + name);
}
const renderer = await readFile(resolve(root, 'native/openui-preview/tern/host.luau'), 'utf8');
assert.ok(renderer.includes('text = p.label') && renderer.includes('s.title .. " — " .. s.where'));
console.log('PASS pinned renderer includes visible precondition and implementation-path bindings. Static source check, NOT live rendering.');
