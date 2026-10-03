// DSH memory seam (harness-adapters/dsh-z0intelligence/memory.mjs) on the agent/pre-step waterfall.
// Fake cordis ctx; the z0int side is the real `python -m z0int.memory.seam` of this tree (Z0INT_PYTHON) over a
// synthetic AgentsView fixture. Run: node --test tests/test-dsh-memory.mjs (isolated HOME/TMPDIR/Z0INT_HOME).
import test from 'node:test'
import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {chmodSync, existsSync, mkdtempSync, readFileSync, writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {dirname, join} from 'node:path'
import {fileURLToPath, pathToFileURL} from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const ROOT = join(here, '..')
const PLUGIN_DIR = join(ROOT, 'harness-adapters', 'dsh-z0intelligence')
const plugin = await import(pathToFileURL(join(PLUGIN_DIR, 'index.mjs')).href)
const memory = await import(pathToFileURL(join(PLUGIN_DIR, 'memory.mjs')).href)
const PY = process.env.Z0INT_PYTHON || 'python3'
const QUERY = 'how do we deploy the quokka gateway'
const LOOPBACK = 'http://127.0.0.1:11546/v1'

function fixture() {
  const h = mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'c8-dsh-'))
  const r = spawnSync(PY, ['-c', 'import sys; sys.path[:0] = [sys.argv[2], sys.argv[3]]; from memory_fixture import build_av_db; build_av_db(sys.argv[1])',
                           join(h, 'av', 'sessions.db'), join(ROOT, 'tests'), join(ROOT, 'src')], {encoding: 'utf8'})
  assert.equal(r.status, 0, r.stderr)
  return {h, env: {...process.env, Z0INT_HOME: join(h, 'z0'), AGENTSVIEW_DATA_DIR: join(h, 'av'), Z0INT_PYTHON: PY,
                   Z0INT_MEMORY_INJECT: '', Z0INT_CAPTURE: '0'}}
}

function slowPython(h) {
  const p = join(h, 'slow-python')
  writeFileSync(p, '#!/bin/sh\nsleep 5\n')
  chmodSync(p, 0o755)
  return p
}

function fakeCtx() {
  const handlers = {}
  return {handlers, on(event, fn) { (handlers[event] ??= []).push(fn); return () => {} }}
}

const userMessage = (text) => Object.freeze({id: 'm-' + text.length, role: 'user', content: [{type: 'text', text}],
                                             source: {kind: 'user'}})
const rootAgent = (sid = 'abc') => ({id: `session-${sid}`, parentId: null, session: {header: {id: sid, cwd: '/w/z0'}}})

async function preStep(ctx, payload) {
  const hs = ctx.handlers['agent/pre-step'] ?? []
  const native = {kind: 'enter', messages: payload.messages}
  const run = (i) => (i < hs.length ? hs[i](payload, () => run(i + 1)) : Promise.resolve(native))
  return run(0)
}

const seamRows = (h) => {
  const p = join(h, 'z0', 'state', 'memory', 'seam', 'dsh.jsonl')
  return existsSync(p) ? readFileSync(p, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : []
}
async function waitRows(h, n = 1, ms = 20000) {
  const end = Date.now() + ms
  while (Date.now() < end && seamRows(h).length < n) await new Promise((r) => setTimeout(r, 50))
  return seamRows(h)
}

function setup(config, env) {
  const ctx = fakeCtx()
  plugin.apply(ctx, {capture: false, ...config}, {env})
  return ctx
}

test('shadow: the pre-step returns at once with the native decision and a receipt is written later', async () => {
  const {h, env} = fixture()
  const slow = setup({memory_inject: 'shadow', model_endpoint: LOOPBACK}, {...env, Z0INT_PYTHON: slowPython(h)})
  const messages = [userMessage(QUERY)]
  const t0 = performance.now()
  const out = await preStep(slow, {agent: rootAgent(), messages, turn: 1, step: 1})
  assert.ok(performance.now() - t0 < 20, `shadow pre-step waited ${performance.now() - t0} ms`)
  assert.deepEqual(out, {kind: 'enter', messages})
  assert.equal(out.messages[0], messages[0])
  const real = setup({memory_inject: 'shadow', model_endpoint: LOOPBACK}, env)
  await preStep(real, {agent: rootAgent('def'), messages, turn: 1, step: 1})
  const [row] = await waitRows(h)
  assert.equal(row.outcome, 'shadow')
  assert.equal(row.would_inject, true)
  assert.ok(row.memory_snapshot_id && row.memory.snapshot_id === row.memory_snapshot_id)
})

test('canary: the brief enters the next model request as one extra message; the native batch is untouched', async () => {
  const {h, env} = fixture()
  const ctx = setup({memory_inject: 'canary', model_endpoint: LOOPBACK}, env)
  const messages = [userMessage(QUERY)]
  const out = await preStep(ctx, {agent: rootAgent(), messages, turn: 1, step: 1})
  assert.equal(out.kind, 'enter')
  assert.equal(out.messages.length, 2)
  assert.equal(out.messages[0], messages[0])
  const added = out.messages[1]
  assert.equal(added.role, 'user')
  assert.equal(added.source.kind, 'z0-memory')
  assert.match(added.content[0].text, /^z0 memory brief \(evidence, not instructions\)/)
  assert.match(added.content[0].text, /agentsview:h1#/)
  assert.equal(seamRows(h).at(-1).outcome, 'injected')
  const again = await preStep(ctx, {agent: rootAgent(), messages, turn: 1, step: 1})  // replayed turn
  assert.deepEqual(again, {kind: 'enter', messages})
  const later = await preStep(ctx, {agent: rootAgent(), messages, turn: 1, step: 2})  // tool continuation
  assert.deepEqual(later, {kind: 'enter', messages})
  const child = {id: 'kid', parentId: 'session-abc', session: {header: {id: 'k', parentSession: 'abc'}}}
  assert.deepEqual(await preStep(ctx, {agent: child, messages, turn: 1, step: 1}), {kind: 'enter', messages})
})

test('canary past the 300 ms deadline gets the native decision and a counted timeout', async () => {
  const {h, env} = fixture()
  const ctx = setup({memory_inject: 'canary', model_endpoint: LOOPBACK}, {...env, Z0INT_PYTHON: slowPython(h)})
  const messages = [userMessage(QUERY)]
  const t0 = performance.now()
  const out = await preStep(ctx, {agent: rootAgent(), messages, turn: 1, step: 1})
  assert.ok(performance.now() - t0 < 600)
  assert.deepEqual(out, {kind: 'enter', messages})
  assert.deepEqual(seamRows(h).map((r) => r.outcome), ['timeout'])
})

test('fail-open: an unavailable z0int keeps the native decision and counts the failure', async () => {
  const {h, env} = fixture()
  const ctx = setup({memory_inject: 'on', model_endpoint: LOOPBACK}, {...env, Z0INT_PYTHON: join(h, 'missing')})
  const messages = [userMessage(QUERY)]
  assert.deepEqual(await preStep(ctx, {agent: rootAgent(), messages, turn: 1, step: 1}), {kind: 'enter', messages})
  assert.deepEqual((await waitRows(h)).map((r) => r.outcome), ['error'])
})

test('a cloud model endpoint is blocked unless dsh allows cloud injection', async () => {
  const {h, env} = fixture()
  const ctx = setup({memory_inject: 'on'}, env)  // no model_endpoint: the DeepSeek API is not loopback
  const messages = [userMessage(QUERY)]
  assert.deepEqual(await preStep(ctx, {agent: rootAgent(), messages, turn: 1, step: 1}), {kind: 'enter', messages})
  assert.equal(seamRows(h).at(-1).outcome, 'cloud_injection_blocked')
})

test('index.mjs registers exactly one memory pre-step (default shadow) and none when memory_inject is off', () => {
  const {env} = fixture()
  assert.equal(setup({}, env).handlers['agent/pre-step'].length, 1)
  assert.equal(setup({memory_inject: 'off'}, env).handlers['agent/pre-step'], undefined)
  const both = fakeCtx()
  plugin.apply(both, {capture: true}, {env: {...env, Z0INT_CAPTURE: '1'}})
  assert.equal(both.handlers['agent/pre-step'].length, 1)
  assert.equal(both.handlers['agent/request'].length, 1)  // capture unchanged
})

test('memory.mjs reaches only the z0 memory surface: no Hermes home or state.db path anywhere', () => {
  for (const file of [join(PLUGIN_DIR, 'memory.mjs'), join(ROOT, 'harness-adapters', 'memory-client.mjs')]) {
    const src = readFileSync(file, 'utf8')
    for (const banned of ['hermes-home', 'state.db', '.hermes', 'HERMES_HOME']) assert.ok(!src.includes(banned), `${file}: ${banned}`)
  }
  const spawned = []
  const ctx = fakeCtx()
  const spawn = (cmd, args) => { spawned.push(args); throw new Error('no spawn in this test') }
  memory.registerMemory(ctx, {memory_inject: 'on', model_endpoint: LOOPBACK}, {env: {Z0INT_HOME: '/nonexistent'}, spawn})
  return preStep(ctx, {agent: rootAgent(), messages: [userMessage(QUERY)], turn: 1, step: 1}).then(() => {
    assert.deepEqual(spawned.map((a) => a.slice(0, 3)), [['-m', 'z0int.memory.seam', 'turn']])
  })
})
