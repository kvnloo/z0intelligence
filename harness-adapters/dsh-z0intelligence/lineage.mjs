// Task lineage and turn identity for DSH requests (from hermes-jev-skills dsh/plugin/lineage.js @9ea5777).
//
// Canonical DSH session metadata (parentSession, origin, delegationDepth) is the ONLY lineage source: nothing is
// inferred from session-id or agent-id shape, except the root-agent test DSH itself guarantees (see isRootAgent).
// Pure and dependency-free apart from node:crypto; no prompt text ever enters a lineage record.
import {createHash} from 'node:crypto'

/** One physical inference; session-scoped because (turn, step, attempt) recurs in every session of a tree. */
function operationId(sessionId, turn, step, attempt) {
  return `${sessionId ?? 'unknown'}:${num(turn)}:${num(step)}:${num(attempt)}`
}

function num(v) {
  return Number.isInteger(v) ? v : Number.isFinite(v) ? Math.trunc(v) : -1
}

function hash32(s) {
  let h1 = 0x811c9dc5
  let h2 = 0x01000193
  for (let i = 0; i < s.length; i++) {
    h1 = Math.imul(h1 ^ s.charCodeAt(i), 0x01000193) >>> 0
    h2 = Math.imul(h2 + s.charCodeAt(i) + i, 0x85ebca6b) >>> 0
  }
  return (h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0')).repeat(2).slice(0, 32)
}

function lineageOf(header) {
  if (!header || typeof header !== 'object') {
    return {session_id: null, parent_session_id: null, origin: null, delegation_depth: null}
  }
  return {
    session_id: header.id ?? header.sessionId ?? null,
    parent_session_id: header.parentSession ?? null, // absent on a root, which is what makes it a root
    origin: header.origin ?? null,
    delegation_depth: header.delegationDepth ?? null,
  }
}

/** Role from proven evidence only; anything unrecognized stays `subagent`. */
function roleFor({depth, origin}) {
  if (depth === 0) return 'root'
  if (origin === 'rlm_worker' || origin === 'rlm_synthesis' || origin === 'verifier') return origin
  return 'subagent'
}

/** `parentId` is null for RLM children too; a session's root agent is named `session-<sessionId>`. */
export function isRootAgent(agent) {
  const parent = agent?.parentId ?? agent?.parent?.id ?? null
  if (parent !== null && parent !== undefined) return false
  return String(agent?.id ?? '').startsWith('session-')
}

const headerOf = (agent) => agent?.session?.header ?? agent?.session?.sessionHeader ?? null

/** The DSH lineage turn_key (`<agent id>:<turn>`), aliased to the canonical key as `dsh.lineage_turn_key`. */
export function lineageTurnKey(agent, turn) {
  return `${agent?.id ?? 'agent'}:${turn}`
}

/** harness_id.turn_key('dsh', session, turn): sha256("z0int.turn_key.v0\0dsh\0<session>\0<turn>")[:32]. */
export function canonicalTurnKey(sessionId, turnId) {
  return createHash('sha256').update(['z0int.turn_key.v0', 'dsh', String(sessionId ?? ''), String(turnId ?? '')].join('\0'))
    .digest('hex').slice(0, 32)
}

/** Content-free lineage of one request. A child's chain cannot be walked from inside the hook: it stays null. */
export function attribution(agent, turn, step, attempt = 0) {
  const header = headerOf(agent)
  const lin = lineageOf(header)
  const sessionId = lin.session_id ?? null
  let rootSessionId = null, traceId = null, depth = null, rootResolution
  if (sessionId === null) rootResolution = 'no_session_metadata'
  else if (lin.parent_session_id == null) {
    // A root is its own tree root; the task trace id is stable across replays, one per tree.
    rootSessionId = sessionId
    traceId = hash32(`task:${sessionId}`)
    depth = 0
    rootResolution = 'self_root'
  } else rootResolution = 'unresolved_in_hook'
  return {
    session_id: sessionId,
    parent_session_id: lin.parent_session_id,
    root_session_id: rootSessionId,
    trace_id: traceId,
    root_resolution: rootResolution,
    agent_id: agent?.id ?? null,
    origin: lin.origin,
    delegation_depth: lin.delegation_depth,
    role: depth === 0 ? 'root' : roleFor({depth: depth ?? 1, origin: lin.origin}),
    turn: turn ?? null,
    step: step ?? null,
    attempt,
    operation_id: sessionId ? operationId(sessionId, turn, step, attempt) : null,
  }
}

function textOf(content) {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content.map((b) => (b && typeof b === 'object' && b.type === 'text' ? b.text : '')).filter(Boolean).join('\n')
  }
  return ''
}

const userEvent = (ev) => ev?.type === 'user/message' && !(ev.data?.source?.kind && ev.data.source.kind !== 'user')

/** Latest human user message of this request: frozen messages, then the session surface, then the snapshot. */
export function latestUserText(agent) {
  try {
    const fm = agent?.frozenMessages
    if (Array.isArray(fm)) {
      for (let i = fm.length - 1; i >= 0; i--) {
        const m = fm[i]
        if (!m || m.role !== 'user') continue
        if (m.source && typeof m.source.kind === 'string' && m.source.kind !== 'user') continue
        const text = textOf(m.content)
        if (text && text.trim()) return text
      }
    }
  } catch {}
  try {
    const s = agent?.session
    for (const seq of [...(s?.surface?.nodes ?? [])].reverse()) {
      const ev = s.eventAt(seq)
      const text = userEvent(ev) ? textOf(ev.data?.message?.content ?? ev.data?.content) : ''
      if (text && text.trim()) return text
    }
  } catch {}
  try {
    const snap = agent?.session?.eventsSnapshot
    const list = Array.isArray(snap) ? snap : Array.isArray(snap?.events) ? snap.events : []
    for (const ev of [...list].reverse()) {
      const text = userEvent(ev) ? textOf(ev.data?.message?.content ?? ev.data?.content) : ''
      if (text && text.trim()) return text
    }
  } catch {}
  return null
}

export const cwdOf = (agent) => headerOf(agent)?.cwd ?? null
