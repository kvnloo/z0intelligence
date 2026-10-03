// Jev shadow plane for DSH turns: reached only through a configured z0 service URL (default none, so off).
//
// Replaces hermes-jev-dsh's per-turn shadow_decide.py fanout (no second NanoJev runtime): one POST to the z0
// service's pure shadow route (/v1/plan: route against a snapshot, never executed), sampled deterministically
// per turn key and bounded by an in-flight cap. Fire-and-forget: the turn never waits and nothing it returns
// reaches the request, the model or the user. Rows (z0int.dsh.shadow_decision.v0) carry ids and the route kind,
// never prompt text. The live service port 11501 is refused unless shadow.allow_live_service is true.
const SCHEMA = 'z0int.dsh.shadow_decision.v0'
const LIVE_PORT = '11501'
const offRecorded = new Set() // shadow_plane=off is written once per process (per reason)

/** Stable [0,1) FNV-1a hash of a turn key: a replay of the same turn makes the same sampling decision. */
export function sampleHash(key) {
  let h = 2166136261
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i)
    h = Math.imul(h, 16777619) >>> 0
  }
  return h / 4294967296
}

const clamp01 = (n, d) => (!Number.isFinite(n) ? d : n < 0 ? 0 : n > 1 ? 1 : n)

function serviceUrl(s) {
  if (!s.url) return {off: 'no_service_url'}
  let url
  try { url = new URL(String(s.url)) } catch { return {off: 'invalid_service_url'} }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return {off: 'invalid_service_url'}
  if (url.port === LIVE_PORT && s.allow_live_service !== true) return {off: 'live_service_refused'}
  return {endpoint: new URL(s.path ?? '/v1/plan', url).href}
}

/** decide({sessionId, lineageKey, turnKey, prompt}) for one root user turn; a no-op when the plane is off. */
export function createShadow(config, {fetch, write, counters, now = Date.now}) {
  const s = config?.shadow ?? {}
  const {off, endpoint} = serviceUrl(s)
  if (off) {
    if (!offRecorded.has(off)) {
      offRecorded.add(off)
      write({schema: SCHEMA, harness: 'dsh', shadow_plane: 'off', reason: off, student_changed_execution: false})
    }
    return () => {}
  }
  const rate = clamp01(Number(s.sample_rate ?? 0.1), 0.1)
  const cap = Math.max(0, Number(s.max_inflight ?? 1) || 0)
  const timeoutMs = Number(s.timeout_ms ?? 60000)
  let inFlight = 0

  return function decide({sessionId, lineageKey, turnKey, prompt}) {
    const base = {schema: SCHEMA, harness: 'dsh', shadow_plane: 'on', session_id: sessionId, turn_key: turnKey,
                  lineage_turn_key: lineageKey, backend: 'z0_service', sample_rate: rate, student_changed_execution: false}
    if (rate < 1 && sampleHash(lineageKey) >= rate) {
      counters.sampled_out++
      write({...base, status: 'sampled_out'})
      return
    }
    if (inFlight >= cap) {
      counters.queue_saturated++
      write({...base, status: 'queue_saturated', in_flight: inFlight})
      return
    }
    inFlight++
    const started = now()
    const body = {harness: 'dsh', trace_id: turnKey, parent_agent: String(sessionId ?? 'dsh').slice(0, 200),
                  function: s.function ?? 'cheap_bounded_worker', task: String(prompt).slice(0, 4000), allow_remote: false}
    Promise.resolve()
      .then(() => fetch(endpoint, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body),
                                   signal: AbortSignal.timeout(timeoutMs)}))
      .then(async (res) => {
        if (!res.ok) throw Object.assign(new Error('http'), {name: 'HttpError', http_status: res.status})
        const j = await res.json()
        write({...base, status: 'ok', latency_ms: now() - started,
               decision: {mode: j?.mode ?? null, executed: j?.executed === true, kind: j?.route?.kind ?? null}})
      })
      .catch((e) => {
        counters.backend_unavailable++
        write({...base, status: 'backend_unavailable', latency_ms: now() - started, error: e?.name ?? 'Error',
               http_status: e?.http_status ?? null})
      })
      .finally(() => { inFlight-- })
      .catch(() => {})
  }
}
