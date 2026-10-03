// DSH plugin (harness-adapters/dsh-z0intelligence): observe-only capture, gated router, inert Jev shadow plane.
// Fake cordis ctx + mocked spawn; the only sockets are a fake z0 service on a private loopback port (11540-11559).
// Run: node --test tests/test-dsh-plugin.mjs  (with an isolated HOME/TMPDIR/Z0INT_HOME).
import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import {EventEmitter} from 'node:events'
import {spawnSync} from 'node:child_process'
import {existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {dirname, join} from 'node:path'
import {fileURLToPath, pathToFileURL} from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const PLUGIN_DIR = join(here, '..', 'harness-adapters', 'dsh-z0intelligence')
const INDEX = pathToFileURL(join(PLUGIN_DIR, 'index.mjs')).href
const plugin = await import(INDEX)

const PRIVATE = 'PRIVATE-PROMPT-7f3a: refactor the billing module for acme'

function home() {
  const h = mkdtempSync(join(tmpdir(), 'c5-z0-'))
  return h
}

function fakeCtx() {
  const handlers = {}
  return {handlers, on(event, fn) { (handlers[event] ??= []).push(fn); return () => {} }}
}

function fakeSpawn(calls, mode) {
  return (cmd, args, opts) => {
    if (mode === 'throw') throw new Error('spawn EAGAIN')
    const child = new EventEmitter()
    let input = ''
    child.stdin = Object.assign(new EventEmitter(), {
      write(s) { input += s },
      end(s) { if (s) input += s; calls.push({cmd, args, opts, payload: JSON.parse(input)}) },
    })
    child.unref = () => {}
    if (mode === 'error') setImmediate(() => child.emit('error', Object.assign(new Error('spawn ENOENT'), {code: 'ENOENT'})))
    return child
  }
}

const rootAgent = (sid = 'abc', text = 'Plan the release notes for v2') => ({
  id: `session-${sid}`, parentId: null,
  session: {header: {id: sid, cwd: null}},
  frozenMessages: [{role: 'user', content: [{type: 'text', text}]}],
})
const childAgent = () => ({
  id: '9b2f1c7e-child', parentId: null,
  session: {header: {id: 'child-1', parentSession: 'abc', origin: 'subagent', delegationDepth: 1}},
  frozenMessages: [{role: 'user', content: [{type: 'text', text: 'sub task'}]}],
})
const CFG = () => Object.freeze({provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'low', maxTokens: 256000})

// agent/request is a waterfall: each listener gets next() for the rest of the chain.
function request(ctx, payload, cfg) {
  const hs = ctx.handlers['agent/request'] ?? []
  const run = (i) => (i < hs.length ? hs[i](payload, () => run(i + 1)) : Promise.resolve(cfg))
  return run(0)
}
async function stopping(ctx, payload) {
  for (const h of ctx.handlers['agent/turn-stopping'] ?? []) await h(payload)
}
async function* streamOf(ctx, options, chunks) {
  const hs = ctx.handlers['llm/stream'] ?? []
  const run = (i) => (i < hs.length ? hs[i](options, () => run(i + 1)) : (async function* () { yield* chunks })())
  yield* run(0)
}
async function turn(ctx, agent, n = 1, cfg = CFG()) {
  const a = await request(ctx, {agent, turn: n, step: 1}, cfg)
  const b = await request(ctx, {agent, turn: n, step: 2}, cfg) // tool continuation in the same turn
  await stopping(ctx, {agent, turn: n})
  return [a, b, cfg]
}
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms))
const rowsOf = (h, name) => {
  const p = join(h, 'state', 'dsh', name)
  return existsSync(p) ? readFileSync(p, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : []
}
async function waitRows(h, name, n, ms = 3000) {
  const end = Date.now() + ms
  while (Date.now() < end) { if (rowsOf(h, name).length >= n) break; await tick(20) }
  return rowsOf(h, name)
}

// A fake z0 service on a private loopback port in 11540-11559.
async function fakeService({delayMs = 0} = {}) {
  const seen = []
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (d) => { body += d })
    req.on('end', () => {
      seen.push({path: req.url, body: JSON.parse(body || '{}')})
      setTimeout(() => {
        res.writeHead(200, {'content-type': 'application/json'})
        res.end(JSON.stringify({ok: true, mode: 'shadow', executed: false,
                                route: {kind: 'PARENT_ONLY', reason: 'tiny task', executed: false}}))
      }, delayMs)
    })
  })
  for (let port = 11540; port <= 11559; port++) {
    const ok = await new Promise((r) => { server.once('error', () => r(false)); server.listen(port, '127.0.0.1', () => r(true)) })
    if (ok) return {server, seen, port, url: `http://127.0.0.1:${port}`}
  }
  throw new Error('no free private port in 11540-11559')
}

function runChild(script, env) {
  const r = spawnSync(process.execPath, ['--unhandled-rejections=strict', '--input-type=module', '-e', script],
                      {env: {...process.env, ...env}, encoding: 'utf8', timeout: 20000})
  return r
}

// ----------------------------------------------------------------------------- 1. observe-only capture
test('1 apply registers an observe-only agent/request middleware; next() comes back unchanged', async () => {
  const h = home(), calls = []
  const ctx = fakeCtx()
  plugin.apply(ctx, {capture: true}, {spawn: fakeSpawn(calls), env: {Z0INT_HOME: h, Z0INT_PYTHON: '/venv/bin/python'}})
  assert.equal(ctx.handlers['agent/request']?.length, 1)
  const cfg = CFG()
  const [a, b] = await turn(ctx, rootAgent(), 1, cfg)
  assert.equal(a, cfg, 'the exact config object from next() is returned')
  assert.equal(b, cfg)
  assert.deepEqual(a, CFG())
})

test('1 the output stream is byte-identical with capture on or off', async () => {
  const chunks = [{type: 'block-start', index: 0, blockType: 'text'}, {type: 'text-delta', index: 0, text: 'hi there'},
                  {type: 'block-end', index: 0, block: {type: 'text', text: 'hi there'}}, {type: 'finish', reason: 'stop'}]
  async function run(capture) {
    const ctx = fakeCtx()
    plugin.apply(ctx, {capture}, {spawn: fakeSpawn([]), env: {Z0INT_HOME: home(), Z0INT_PYTHON: '/venv/bin/python'}})
    const agent = rootAgent('s-stream')
    const cfg = await request(ctx, {agent, turn: 1, step: 1}, CFG())
    let out = JSON.stringify(cfg)
    for await (const c of streamOf(ctx, {sessionId: 's-stream', messages: agent.frozenMessages}, chunks)) out += JSON.stringify(c)
    await stopping(ctx, {agent, turn: 1})
    return {out, ctx}
  }
  const on = await run(true), off = await run(false)
  assert.equal(on.out, off.out)
  assert.equal(on.ctx.handlers['agent/request']?.length, 1, 'capture on registers the observer')
  assert.equal(off.ctx.handlers['agent/request'], undefined, 'capture off registers nothing')
})

// ----------------------------------------------------------------------------- 2. router gate (#95)
test('2 the llm/stream router registers only when config.router===true AND automatic.json dsh is enabled', () => {
  const withAuto = (value) => {
    const h = home()
    if (value !== undefined) {
      mkdirSync(join(h, 'config'), {recursive: true})
      writeFileSync(join(h, 'config', 'automatic.json'), JSON.stringify(value))
    }
    return h
  }
  const routerCount = (config, h, extra = {}) => {
    const ctx = fakeCtx()
    plugin.apply(ctx, config, {spawn: fakeSpawn([]), env: {Z0INT_HOME: h, ...extra}})
    return ctx.handlers['llm/stream']?.length ?? 0
  }
  assert.equal(routerCount({}, withAuto()), 0, 'default config: router off')
  assert.equal(routerCount({router: true}, withAuto()), 0, 'router:true but no automatic.json')
  assert.equal(routerCount({router: true}, withAuto({dsh: {enabled: false}})), 0)
  assert.equal(routerCount({router: 'yes'}, withAuto({dsh: {enabled: true}})), 0, 'only the boolean true counts')
  assert.equal(routerCount({}, withAuto({dsh: {enabled: true}})), 0, 'automatic dsh on but router not configured')
  assert.equal(routerCount({router: true}, withAuto({dsh: {enabled: true}})), 1, 'both gates open')
  assert.equal(routerCount({router: true}, withAuto({dsh: {enabled: true}}), {Z0INT_AUTO_DSH: '0'}), 0, 'env kill switch')
})

// ----------------------------------------------------------------------------- 3. one normalized event per hook
test('3 a user turn sends one prompt and one stop event to hook_adapter --harness dsh, detached', async () => {
  const h = home(), calls = []
  const ctx = fakeCtx()
  plugin.apply(ctx, {capture: true}, {spawn: fakeSpawn(calls), env: {Z0INT_HOME: h, Z0INT_PYTHON: '/venv/bin/python'}})
  await turn(ctx, rootAgent('abc', 'Plan the release notes for v2'), 1)
  await request(ctx, {agent: childAgent(), turn: 1, step: 1}, CFG()) // a subagent request: lineage only
  await stopping(ctx, {agent: childAgent(), turn: 1})
  await stopping(ctx, {agent: rootAgent('abc'), turn: 1}) // a repeated stop boundary for the same turn
  assert.deepEqual(calls.map((c) => c.args.at(-1)), ['prompt', 'stop'])
  for (const c of calls) {
    assert.equal(c.cmd, '/venv/bin/python')
    assert.deepEqual(c.args, ['-m', 'z0int.hook_adapter', '--harness', 'dsh', c.args.at(-1)])
    assert.equal(c.opts.detached, true)
    assert.ok(!('hook_event_name' in c.payload) && !('prompt_id' in c.payload) && !('hookEventName' in c.payload),
              'normalized events never carry another harness\'s hook keys')
    assert.equal(c.payload.session_id, 'abc')
    assert.equal(c.payload.turn_id, 'session-abc:1', 'the DSH lineage turn_key is the turn id')
  }
  const [prompt, stop] = calls.map((c) => c.payload)
  assert.equal(prompt.prompt, 'Plan the release notes for v2')
  assert.equal(prompt.model, 'deepseek-flash')
  assert.ok(!('prompt' in stop), 'the stop event carries no prompt text')
})

test('3 the canonical turn_key is computed the same way as harness_id.turn_key', () => {
  // sha256("z0int.turn_key.v0\0dsh\0abc\0session-abc:1")[:32], checked against Python in test_dsh_capture.py
  assert.match(plugin.canonicalTurnKey('abc', 'session-abc:1'), /^[0-9a-f]{32}$/)
  assert.notEqual(plugin.canonicalTurnKey('abc', 'session-abc:1'), plugin.canonicalTurnKey('abc', 'session-abc:2'))
})

// ----------------------------------------------------------------------------- 4. forbidden paths
const FORBIDDEN = [/\.omp\/\.env/, /(^|[\/'\"`])\.env\b/m,  // a dotenv path, not the JS spread `...env`
                    /hermes-home/, /openjev/, /\/home\/kvn/, /KEY_FILE/, /API_KEY/, /keystore/i]
function pluginFiles(dir = PLUGIN_DIR) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? (n === 'node_modules' ? [] : pluginFiles(p)) : [p]
  })
}

test('4 the plugin source and the env it gives the hook child never reference key files or private trees', async () => {
  const files = [...pluginFiles(), join(PLUGIN_DIR, '..', 'automatic-client.mjs')]
  assert.ok(files.some((f) => f.endsWith('capture.mjs')) && files.some((f) => f.endsWith('shadow.mjs'))
            && files.some((f) => f.endsWith('lineage.mjs')), 'capture/shadow/lineage modules exist')
  for (const f of files) {
    const text = readFileSync(f, 'utf8')
    for (const re of FORBIDDEN) assert.doesNotMatch(text, re, `${f} matches ${re}`)
  }
  const calls = [], base = {Z0INT_HOME: home(), Z0INT_PYTHON: '/venv/bin/python', PATH: '/usr/bin'}
  const ctx = fakeCtx()
  plugin.apply(ctx, {capture: true}, {spawn: fakeSpawn(calls), env: base})
  await turn(ctx, rootAgent(), 1)
  assert.equal(calls.length, 2)
  for (const c of calls) {
    const added = Object.keys(c.opts.env).filter((k) => c.opts.env[k] !== base[k])
    assert.deepEqual(added, ['PYTHONPATH'], 'the plugin adds only PYTHONPATH to the child env')
    for (const v of Object.values(c.opts.env)) for (const re of FORBIDDEN) assert.doesNotMatch(String(v), re)
  }
})

// ----------------------------------------------------------------------------- 5. shadow plane
test('5 default config: no network call, shadow_plane=off recorded once per process', () => {
  const h = home()
  const r = runChild(`
    const p = await import(${JSON.stringify(INDEX)})
    let fetches = 0
    const deps = {spawn: () => { throw new Error('no spawn here') }, fetch: async () => { fetches++; throw new Error('x') },
                  env: {Z0INT_HOME: ${JSON.stringify(h)}, Z0INT_PYTHON: '/venv/bin/python'}}
    const on = (hs) => ({on: (e, fn) => { (hs[e] ??= []).push(fn) }})
    for (let i = 0; i < 2; i++) {
      const hs = {}
      p.apply(on(hs), {capture: true}, deps)
      for (let t = 1; t <= 3; t++)
        await hs['agent/request'][0]({agent: {id: 'session-d' + i, parentId: null, session: {header: {id: 'd' + i}},
          frozenMessages: [{role: 'user', content: 'hello'}]}, turn: t, step: 1}, async () => ({model: 'm'}))
    }
    await new Promise((r) => setTimeout(r, 200))
    console.log(JSON.stringify({fetches}))
  `, {})
  assert.equal(r.status, 0, r.stderr)
  assert.deepEqual(JSON.parse(r.stdout.trim().split('\n').at(-1)), {fetches: 0})
  const rows = rowsOf(h, 'shadow_decisions.jsonl')
  assert.equal(rows.length, 1, JSON.stringify(rows))
  assert.equal(rows[0].schema, 'z0int.dsh.shadow_decision.v0')
  assert.equal(rows[0].shadow_plane, 'off')
  assert.equal(rows[0].reason, 'no_service_url')
})

test('5 with a fake z0 service it samples deterministically at the configured rate and writes shadow_decision rows', async () => {
  const svc = await fakeService()
  try {
    const h = home(), ctx = fakeCtx()
    plugin.apply(ctx, {capture: true, shadow: {url: svc.url, sample_rate: 0.5, max_inflight: 100}},
                 {spawn: fakeSpawn([]), env: {Z0INT_HOME: h, Z0INT_PYTHON: '/venv/bin/python'}})
    const sids = Array.from({length: 20}, (_, i) => `samp-${i}`)
    const results = await Promise.all(sids.map(async (sid) => {
      const cfg = CFG()
      return [await request(ctx, {agent: rootAgent(sid), turn: 1, step: 1}, cfg), cfg]
    }))
    for (const [got, cfg] of results) assert.equal(got, cfg, 'a request is never altered')
    const rows = await waitRows(h, 'shadow_decisions.jsonl', 20)
    const expected = sids.filter((sid) => plugin.sampleHash(`session-${sid}:1`) < 0.5)
    assert.ok(expected.length > 0 && expected.length < 20)
    const ok = rows.filter((r) => r.status === 'ok'), out = rows.filter((r) => r.status === 'sampled_out')
    assert.deepEqual(ok.map((r) => r.session_id).sort(), [...expected].sort())
    assert.equal(out.length, 20 - expected.length)
    assert.equal(svc.seen.length, expected.length)
    for (const r of rows) {
      assert.equal(r.schema, 'z0int.dsh.shadow_decision.v0')
      assert.equal(r.harness, 'dsh')
      assert.equal(r.turn_key, plugin.canonicalTurnKey(r.session_id, r.lineage_turn_key))
      assert.equal(r.student_changed_execution, false)
      assert.equal(r.sample_rate, 0.5)
    }
    assert.deepEqual(ok[0].decision, {mode: 'shadow', executed: false, kind: 'PARENT_ONLY'})
    assert.equal(svc.seen[0].path, '/v1/plan')
    assert.equal(svc.seen[0].body.harness, 'dsh')
    assert.equal(svc.seen[0].body.allow_remote, false)
  } finally { svc.server.close() }
})

test('5 the max-inflight cap holds: one call in flight, the rest recorded queue_saturated', async () => {
  const svc = await fakeService({delayMs: 300})
  try {
    const h = home(), ctx = fakeCtx()
    plugin.apply(ctx, {capture: true, shadow: {url: svc.url, sample_rate: 1, max_inflight: 1}},
                 {spawn: fakeSpawn([]), env: {Z0INT_HOME: h, Z0INT_PYTHON: '/venv/bin/python'}})
    const t0 = Date.now()
    await Promise.all(['q1', 'q2', 'q3', 'q4'].map((sid) => request(ctx, {agent: rootAgent(sid), turn: 1, step: 1}, CFG())))
    assert.ok(Date.now() - t0 < 250, 'the turn never waits for the shadow')
    const rows = await waitRows(h, 'shadow_decisions.jsonl', 4)
    assert.equal(rows.filter((r) => r.status === 'ok').length, 1)
    assert.equal(rows.filter((r) => r.status === 'queue_saturated').length, 3)
    assert.equal(svc.seen.length, 1)
  } finally { svc.server.close() }
})

test('5 service down gives backend_unavailable, counted', async () => {
  const svc = await fakeService()
  const url = svc.url
  await new Promise((r) => svc.server.close(r))
  const h = home(), ctx = fakeCtx()
  const before = plugin.counters.backend_unavailable
  plugin.apply(ctx, {capture: true, shadow: {url, sample_rate: 1}},
               {spawn: fakeSpawn([]), env: {Z0INT_HOME: h, Z0INT_PYTHON: '/venv/bin/python'}})
  const cfg = CFG()
  assert.equal(await request(ctx, {agent: rootAgent('down'), turn: 1, step: 1}, cfg), cfg)
  const rows = await waitRows(h, 'shadow_decisions.jsonl', 1)
  assert.equal(rows[0].status, 'backend_unavailable')
  assert.equal(plugin.counters.backend_unavailable, before + 1)
})

test('5 port 11501 is refused unless allow_live_service', async () => {
  for (const url of ['http://127.0.0.1:11501', 'http://172.19.0.1:11501/']) {
    let fetches = 0
    const ctx = fakeCtx()
    plugin.apply(ctx, {capture: true, shadow: {url, sample_rate: 1}},
                 {spawn: fakeSpawn([]), fetch: async () => { fetches++; throw new Error('must not run') },
                  env: {Z0INT_HOME: home(), Z0INT_PYTHON: '/venv/bin/python'}})
    await request(ctx, {agent: rootAgent('live'), turn: 1, step: 1}, CFG())
    await tick(50)
    assert.equal(fetches, 0, url)
  }
  const seen = []
  const ctx = fakeCtx()
  plugin.apply(ctx, {capture: true, shadow: {url: 'http://127.0.0.1:11501', sample_rate: 1, allow_live_service: true}},
               {spawn: fakeSpawn([]), env: {Z0INT_HOME: home(), Z0INT_PYTHON: '/venv/bin/python'},
                fetch: async (u) => { seen.push(String(u)); return {ok: true, json: async () => ({ok: true, mode: 'shadow', executed: false, route: {kind: 'PARENT_ONLY'}})} }})
  await request(ctx, {agent: rootAgent('live2'), turn: 1, step: 1}, CFG())
  await tick(50)
  assert.deepEqual(seen, ['http://127.0.0.1:11501/v1/plan'])
})

// ----------------------------------------------------------------------------- 6. fail-open
test('6 a missing python lets the turn proceed and leaves a counted drop row', async () => {
  const h = home(), ctx = fakeCtx()
  const before = plugin.counters.hook_spawn_failed
  plugin.apply(ctx, {capture: true}, {env: {Z0INT_HOME: h, Z0INT_PYTHON: join(h, 'no-such-python'), PATH: '/usr/bin:/bin'}})
  const cfg = CFG()
  const [a] = await turn(ctx, rootAgent('nopy'), 1, cfg)
  assert.equal(a, cfg)
  const drops = await waitRows(h, 'drops.jsonl', 2)
  assert.equal(plugin.counters.hook_spawn_failed, before + 2)
  assert.deepEqual(drops.map((d) => [d.schema, d.reason]), [['z0int.dsh.drop.v0', 'spawn_failed'], ['z0int.dsh.drop.v0', 'spawn_failed']])
})

test('6 a spawn that throws or errors is counted and never reaches the turn', async () => {
  for (const mode of ['throw', 'error']) {
    const h = home(), ctx = fakeCtx()
    const before = plugin.counters.hook_spawn_failed
    plugin.apply(ctx, {capture: true}, {spawn: fakeSpawn([], mode), env: {Z0INT_HOME: h, Z0INT_PYTHON: '/venv/bin/python'}})
    const cfg = CFG()
    assert.equal(await request(ctx, {agent: rootAgent(mode), turn: 1, step: 1}, cfg), cfg)
    await stopping(ctx, {agent: rootAgent(mode), turn: 1})
    await waitRows(h, 'drops.jsonl', 2)
    assert.equal(plugin.counters.hook_spawn_failed, before + 2, mode)
  }
})

test('6 no unhandled rejection under --unhandled-rejections=strict on every failure path', async () => {
  const svc = await fakeService()
  const url = svc.url
  await new Promise((r) => svc.server.close(r))
  const h = home()
  const asFile = join(h, 'z0-is-a-file')
  writeFileSync(asFile, 'x') // every state write fails (ENOTDIR)
  const r = runChild(`
    const p = await import(${JSON.stringify(INDEX)})
    const hs = {}
    p.apply({on: (e, fn) => { (hs[e] ??= []).push(fn) }}, {capture: true, shadow: {url: ${JSON.stringify(url)}, sample_rate: 1}},
            {env: {Z0INT_HOME: ${JSON.stringify(asFile)}, Z0INT_PYTHON: '/nonexistent/python', PATH: '/usr/bin'},
             fetch: async () => { throw new Error('boom') }})
    const agent = {id: 'session-x', parentId: null, session: {header: {id: 'x'}}, frozenMessages: [{role: 'user', content: 'hi'}]}
    const cfg = {model: 'm'}
    const got = await hs['agent/request'][0]({agent, turn: 1, step: 1}, async () => cfg)
    await hs['agent/request'][0]({agent: {id: 'kid', session: {header: {id: 'k', parentSession: 'x'}}}, turn: 1, step: 1}, async () => cfg)
    await hs['agent/request'][0]({agent: null, turn: 1, step: 1}, async () => cfg)
    for (const fn of hs['agent/turn-stopping'] ?? []) await fn({agent, turn: 1})
    await new Promise((r) => setTimeout(r, 300))
    console.log(JSON.stringify({same: got === cfg, counters: p.counters}))
  `, {})
  assert.equal(r.status, 0, r.stderr)
  const out = JSON.parse(r.stdout.trim().split('\n').at(-1))
  assert.equal(out.same, true)
  assert.ok(out.counters.hook_spawn_failed >= 2 && out.counters.write_failed >= 1 && out.counters.backend_unavailable >= 1,
            JSON.stringify(out.counters))
})

// ----------------------------------------------------------------------------- 7. privacy
test('7 lineage and shadow rows hold no prompt or response text', async () => {
  const svc = await fakeService()
  try {
    const h = home(), ctx = fakeCtx()
    plugin.apply(ctx, {capture: true, shadow: {url: svc.url, sample_rate: 1, max_inflight: 4}},
                 {spawn: fakeSpawn([]), env: {Z0INT_HOME: h, Z0INT_PYTHON: '/venv/bin/python'}})
    await turn(ctx, rootAgent('priv', PRIVATE), 1)
    await request(ctx, {agent: childAgent(), turn: 1, step: 1}, CFG())
    const shadow = await waitRows(h, 'shadow_decisions.jsonl', 1)
    const lineage = await waitRows(h, 'lineage.jsonl', 3)
    assert.equal(shadow.length, 1)
    assert.ok(svc.seen[0].body.task.includes('refactor the billing'), 'the shadow service gets the task')
    assert.deepEqual(lineage.map((r) => r.role), ['root', 'root', 'subagent'])
    assert.deepEqual(lineage.map((r) => r.purpose), ['root', 'continuation', 'child'])
    for (const r of lineage) {
      assert.equal(r.schema, 'z0int.dsh.lineage.v0')
      assert.ok(r.operation_id && 'trace_id' in r && 'parent_session_id' in r)
    }
    assert.equal(lineage[0].turn_key, plugin.canonicalTurnKey('priv', 'session-priv:1'))
    const all = readdirSync(join(h, 'state', 'dsh')).map((n) => readFileSync(join(h, 'state', 'dsh', n), 'utf8')).join('\n')
    for (const word of ['PRIVATE-PROMPT', 'billing', 'acme', 'hi there']) assert.ok(!all.includes(word), word)
  } finally { svc.server.close() }
})

// ----------------------------------------------------------------------------- 8. no direct Hermes DB reader
test('8 the plugin does not ship, export or load memory.js', async () => {
  assert.ok(!existsSync(join(PLUGIN_DIR, 'memory.js')))
  const pkg = JSON.parse(readFileSync(join(PLUGIN_DIR, 'package.json'), 'utf8'))
  assert.ok(!JSON.stringify(pkg).includes('memory.js'))
  for (const f of pluginFiles()) {
    const text = readFileSync(f, 'utf8')
    assert.doesNotMatch(text, /memory\.js|state\.db|TencentDB|\.hermes\b/i, f)
  }
  assert.ok(!Object.keys(plugin).some((k) => /memory|recall/i.test(k)), Object.keys(plugin).join(','))
  assert.deepEqual(Object.keys(plugin).sort(), ['apply', 'canonicalTurnKey', 'counters', 'name', 'sampleHash'])
})
