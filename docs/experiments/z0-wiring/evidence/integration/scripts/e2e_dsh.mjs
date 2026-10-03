// Integration copy of the C5 e2e driver: the plugin with REAL spawn (python -m z0int.hook_adapter --harness dsh, detached) and a fake
// z0 service on a private loopback port. Synthetic prompts only. argv[2] = 'on' | 'off' (capture), prints JSON.
import http from 'node:http'
const capture = process.argv[2] !== 'off'
const p = await import(process.env.C5_PLUGIN)
const seen = []
const server = http.createServer((req, res) => { let b = ''; req.on('data', (d) => { b += d }); req.on('end', () => {
  seen.push(req.url); res.writeHead(200, {'content-type': 'application/json'})
  res.end(JSON.stringify({ok: true, mode: 'shadow', executed: false, route: {kind: 'PARENT_ONLY', executed: false}})) }) })
const port = Number(process.env.C5_PORT || 11551)
await new Promise((r) => server.listen(port, '127.0.0.1', r))
const hs = {}
p.apply({on: (e, fn) => { (hs[e] ??= []).push(fn) }}, {capture, router: false,
        shadow: {url: `http://127.0.0.1:${port}`, sample_rate: 1, max_inflight: 4}})
const waterfall = (payload, cfg) => { const l = hs['agent/request'] ?? []; const run = (i) => i < l.length ? l[i](payload, () => run(i + 1)) : Promise.resolve(cfg); return run(0) }
const stop = async (payload) => { for (const fn of hs['agent/turn-stopping'] ?? []) await fn(payload) }
const stream = async (opts, chunks) => { const l = hs['llm/stream'] ?? []; const run = (i) => i < l.length ? l[i](opts, () => run(i + 1)) : (async function* () { yield* chunks })(); let s = ''; for await (const c of run(0)) s += JSON.stringify(c); return s }
const root = (sid, text) => ({id: `session-${sid}`, parentId: null, session: {header: {id: sid}}, frozenMessages: [{role: 'user', content: [{type: 'text', text}]}]})
const child = {id: 'e2e-child-uuid', parentId: null, session: {header: {id: 'e2e-c1', parentSession: 'e2e-s1', origin: 'subagent', delegationDepth: 1}},
               frozenMessages: [{role: 'user', content: 'SYNTH child task'}]}
const transcript = []
const turns = [['e2e-s1', 1, 'SYNTH-E2E-PROMPT one: list the files'], ['e2e-s1', 2, 'SYNTH-E2E-PROMPT two: and summarise them'],
               ['e2e-s2', 1, 'SYNTH-E2E-PROMPT three: what changed?']]
for (const [sid, n, text] of turns) {
  const agent = root(sid, text)
  const cfg = {provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'low'}
  for (const step of [1, 2]) transcript.push(JSON.stringify(await waterfall({agent, turn: n, step}, cfg)))
  if (sid === 'e2e-s1' && n === 1) transcript.push(JSON.stringify(await waterfall({agent: child, turn: 1, step: 1}, {...cfg})))
  transcript.push(await stream({sessionId: sid, messages: agent.frozenMessages},
    [{type: 'text-delta', index: 0, text: `SYNTH-E2E-RESPONSE ${sid}:${n}`}, {type: 'finish', reason: 'stop'}]))
  await stop({agent, turn: n})
}
await new Promise((r) => setTimeout(r, 1500))
server.close()
console.log(JSON.stringify({capture, transcript, shadow_requests: seen, counters: p.counters,
                            keys: turns.map(([sid, n]) => p.canonicalTurnKey(sid, `session-${sid}:${n}`))}))
