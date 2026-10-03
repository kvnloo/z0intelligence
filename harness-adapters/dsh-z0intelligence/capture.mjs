// Observe-only DSH capture (z0int#62): normalized hook events to `Z0INT_PYTHON -m z0int.hook_adapter --harness dsh`.
//
// agent/request (waterfall) returns exactly what next() returned; agent/turn-stopping returns nothing. Per root
// user turn: one `prompt` event at step 1 and one `stop` event at the turn's stop boundary, each in a detached
// child, so the shared capture core writes z0int.dsh.opportunity_record.v0 + turn_outcome.v0 joined on the
// canonical turn_key (turn id = the DSH lineage turn_key). Every request also leaves a content-free
// z0int.dsh.lineage.v0 row. Any failure is counted and the turn proceeds unchanged (fail-open).
import {spawn as nodeSpawn} from 'node:child_process'
import {appendFile, mkdir} from 'node:fs/promises'
import {readFileSync} from 'node:fs'
import {homedir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {attribution, canonicalTurnKey, cwdOf, isRootAgent, latestUserText, lineageTurnKey} from './lineage.mjs'
import {createShadow} from './shadow.mjs'

const SRC = fileURLToPath(new URL('../../src', import.meta.url))
const OPEN_TURNS_MAX = 256

export const counters = {hook_spawn_failed: 0, write_failed: 0, observe_failed: 0, backend_unavailable: 0,
                         queue_saturated: 0, sampled_out: 0}

export const z0Home = (env) => env.Z0INT_HOME || join(homedir(), '.z0int')

/** Z0INT_CAPTURE=0 or config/capture.json {"enabled": false}: the same kill switch the Python core reads. */
export function captureEnabled(env) {
  if (env.Z0INT_CAPTURE === '0') return false
  try { return JSON.parse(readFileSync(join(z0Home(env), 'config', 'capture.json'), 'utf8'))?.enabled !== false }
  catch { return true }
}

let chain = Promise.resolve()
/** Ordered append of one row under $Z0INT_HOME/state/dsh/; never throws, never rejects. */
function writer(env) {
  const dir = join(z0Home(env), 'state', 'dsh')
  return (file, row) => {
    const line = JSON.stringify({...row, recorded_at: new Date().toISOString()}) + '\n'
    chain = chain.then(() => mkdir(dir, {recursive: true})).then(() => appendFile(join(dir, file), line))
      .catch(() => { counters.write_failed++ })
  }
}

export function registerCapture(ctx, config, {env, spawn = nodeSpawn, fetch = globalThis.fetch}) {
  const write = writer(env)
  const decide = createShadow(config, {fetch, counters, write: (row) => write('shadow_decisions.jsonl', row)})
  const python = env.Z0INT_PYTHON || 'python3'
  const childEnv = {...env, PYTHONPATH: SRC + (env.PYTHONPATH ? ':' + env.PYTHONPATH : '')}
  const open = new Map() // lineage turn_key -> {session_id, model}, from the prompt event to the stop event

  function failed(event) {
    counters.hook_spawn_failed++
    write('drops.jsonl', {schema: 'z0int.dsh.drop.v0', harness: 'dsh',
                          kind: event === 'prompt' ? 'opportunity_record' : 'turn_outcome', reason: 'spawn_failed', count: 1})
  }

  function send(event, payload) {
    try {
      const child = spawn(python, ['-m', 'z0int.hook_adapter', '--harness', 'dsh', event],
                          {detached: true, stdio: ['pipe', 'ignore', 'ignore'], env: childEnv})
      child.on('error', () => failed(event))
      child.stdin.on('error', () => {})
      child.stdin.end(JSON.stringify(payload))
      child.unref()
    } catch {
      failed(event)
    }
  }

  function observe({agent, turn, step}, cfg) {
    const root = isRootAgent(agent)
    const lin = attribution(agent, turn, step)
    const sessionId = lin.session_id ?? agent?.id ?? null
    const key = lineageTurnKey(agent, turn)
    const turnKey = root ? canonicalTurnKey(sessionId, key) : null
    write('lineage.jsonl', {schema: 'z0int.dsh.lineage.v0', harness: 'dsh', ...lin,
                            purpose: !root ? 'child' : step === 1 ? 'root' : 'continuation',
                            provider: cfg?.provider ?? null, model: cfg?.model ?? null,
                            lineage_turn_key: root ? key : null, turn_key: turnKey})
    if (!root || step !== 1 || open.has(key)) return
    const prompt = latestUserText(agent)
    if (!prompt || !prompt.trim()) return
    const model = cfg?.model ?? null
    open.set(key, {session_id: sessionId, model})
    if (open.size > OPEN_TURNS_MAX) open.delete(open.keys().next().value)
    send('prompt', {session_id: sessionId, turn_id: key, prompt, model, cwd: cwdOf(agent)})
    decide({sessionId, lineageKey: key, turnKey, prompt})
  }

  ctx.on('agent/request', async (payload, next) => {
    const cfg = await next()
    try { observe(payload ?? {}, cfg) } catch { counters.observe_failed++ }
    return cfg
  })

  ctx.on('agent/turn-stopping', (payload) => {
    try {
      const key = lineageTurnKey(payload?.agent, payload?.turn)
      const turn = open.get(key)
      if (!turn) return
      open.delete(key)
      send('stop', {session_id: turn.session_id, turn_id: key, model: turn.model, cwd: cwdOf(payload.agent)})
    } catch { counters.observe_failed++ }
  })
}
