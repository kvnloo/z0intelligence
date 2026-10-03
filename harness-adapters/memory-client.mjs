// The z0 memory seam for JS hosts (DSH, OMP, OMO): one harness-agnostic client over `python -m z0int.memory.seam`.
//
// memory_inject off | shadow | canary | on (default shadow; env Z0INT_MEMORY_INJECT when the shim config is unset):
//   shadow     one detached child per turn (`seam shadow`), never awaited: the host turn does not wait. At most
//              MAX_SHADOW_CHILDREN run at once per host; past that a turn writes a counted queue_saturated row.
//   canary/on  `seam turn` answers {"context"} within the 300 ms deadline it counts from `started_at` (stamped
//              here, so interpreter start-up is inside the budget); this side kills it a little later as a
//              backstop. Past it, or on any failure, the caller gets undefined (native context) and a counted row.
// The z0 side owns replay, the task-project scope, cloud-egress opt-in per harness, the single injection owner and
// the scrub. `endpoint` must be the harness's own model setting (never a generic env var); a replayed turn is counted
// here (counters.replay) and writes nothing.
import {spawn as nodeSpawn} from 'node:child_process'
import {appendFile, mkdir} from 'node:fs/promises'
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs'
import {homedir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'

const SRC = fileURLToPath(new URL('../src', import.meta.url))
export const MODES = ['off', 'shadow', 'canary', 'on']
export const DEADLINE_MS = 300
const BACKSTOP_MS = 150
const MAX_QUERY_CHARS = 2000
export const MAX_SHADOW_CHILDREN = 4  // as C1 capture's harness_capture.MAX_CHILDREN
export const counters = {shadow: 0, injected: 0, timeout: 0, error: 0, replay: 0, queue_saturated: 0}

const z0Home = (env) => env.Z0INT_HOME || join(homedir(), '.z0int')
const seamDir = (env) => join(z0Home(env), 'state', 'memory', 'seam')

export function memoryMode(value, env = process.env) {
  if (killed(env)) return 'off'
  const mode = value || env.Z0INT_MEMORY_INJECT || 'shadow'
  return MODES.includes(mode) ? mode : 'off'
}

/** The capture kill switch (Z0INT_CAPTURE=0 or config/capture.json {"enabled": false}) keeps memory native too. */
function killed(env) {
  if (env.Z0INT_CAPTURE === '0') return true
  try { return JSON.parse(readFileSync(join(z0Home(env), 'config', 'capture.json'), 'utf8'))?.enabled === false }
  catch { return false }
}

/** A counted shim-side row (the z0 side writes every other row); never throws. */
async function row(env, harness, mode, fields) {
  try {
    await mkdir(seamDir(env), {recursive: true})
    await appendFile(join(seamDir(env), `${harness}.jsonl`), JSON.stringify({
      schema: 'z0int.memory_seam.v0', harness, mode, injected: false, would_inject: false, source: 'shim',
      ...fields, recorded_at: new Date().toISOString()}) + '\n')
  } catch { /* a read-only home only loses the row */ }
}

/** Whether the host has a model-visible push seam (recorded for the acceptance eval: B=UNSUPPORTED otherwise). */
export function recordPushStatus(env, harness, status) {
  try {
    mkdirSync(seamDir(env), {recursive: true})
    const path = join(seamDir(env), 'push_status.json')
    let all = {}
    try { all = JSON.parse(readFileSync(path, 'utf8')) } catch { all = {} }
    writeFileSync(path, JSON.stringify({...all, [harness]: status}) + '\n')
  } catch { /* best effort, once at load */ }
}

export function createMemoryClient({harness, mode, env = process.env, spawn = nodeSpawn, injector,
                                    deadlineMs = DEADLINE_MS} = {}) {
  mode = memoryMode(mode, env)
  // Read per call: a long-lived host may change its environment after the extension loaded.
  const python = () => env.Z0INT_PYTHON || 'python3'
  const childEnv = () => ({...env, PYTHONPATH: SRC + (env.PYTHONPATH ? ':' + env.PYTHONPATH : '')})

  function job({sessionId, turnId, query, endpoint, cwd, excludeLayers}) {
    return {session_id: String(sessionId ?? harness), turn_id: String(turnId ?? ''),
            query: String(query).slice(0, MAX_QUERY_CHARS), mode, endpoint: endpoint ?? null, cwd: cwd ?? null,
            injector: injector ?? null, exclude_layers: excludeLayers ?? [], started_at: Date.now() / 1000}
  }

  let inFlight = 0
  function shadow(j) {
    if (inFlight >= MAX_SHADOW_CHILDREN) {
      counters.queue_saturated++
      void row(env, harness, mode, {outcome: 'queue_saturated', in_flight: inFlight})
      return undefined
    }
    try {
      const child = spawn(python(), ['-m', 'z0int.memory.seam', 'shadow', '--harness', harness],
                          {detached: true, stdio: ['pipe', 'ignore', 'ignore'], env: childEnv()})
      inFlight++
      let released = false
      const release = () => { if (!released) { released = true; inFlight-- } }
      child.on?.('exit', release)
      child.on?.('error', () => { release(); counters.error++; void row(env, harness, mode, {outcome: 'error', error: 'spawn'}) })
      child.stdin.on?.('error', () => {})
      child.stdin.end(JSON.stringify(j))
      child.unref?.()
      counters.shadow++
    } catch {
      counters.error++
      void row(env, harness, mode, {outcome: 'error', error: 'spawn'})
    }
    return undefined
  }

  function waitTurn(j) {
    return new Promise((resolve) => {
      let out = '', settled = false, child
      const done = (value, fields) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        if (fields) {
          counters[fields.outcome]++
          void row(env, harness, mode, fields).then(() => resolve(value))
        } else resolve(value)
      }
      const timer = setTimeout(() => { try { child?.kill('SIGKILL') } catch {} done(undefined, {outcome: 'timeout'}) },
                               deadlineMs + BACKSTOP_MS)
      try {
        child = spawn(python(), ['-m', 'z0int.memory.seam', 'turn', '--harness', harness],
                      {stdio: ['pipe', 'pipe', 'ignore'], env: childEnv()})
      } catch {
        done(undefined, {outcome: 'error', error: 'spawn'})
        return
      }
      child.on('error', () => done(undefined, {outcome: 'error', error: 'spawn'}))
      child.stdin.on('error', () => {})
      child.stdout.on('data', (d) => { out += d; if (out.length > 200000) child.kill('SIGKILL') })
      child.on('close', () => {
        let res
        try { res = JSON.parse(out) } catch { done(undefined, {outcome: 'error', error: 'bad_output'}); return }
        if (res?.context) counters.injected++
        else if (res?.outcome === 'replay') counters.replay++
        done(res?.context || undefined)
      })
      child.stdin.end(JSON.stringify(j))
    })
  }

  return {
    mode,
    /** The brief for the next model request (canary/on), or undefined (native context). Never rejects. */
    async turn(args) {
      if (mode === 'off' || !String(args?.query ?? '').trim()) return undefined
      const j = job(args)
      return mode === 'shadow' ? shadow(j) : waitTurn(j)
    },
  }
}
