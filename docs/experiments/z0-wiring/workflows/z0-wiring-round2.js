export const meta = {
  name: 'z0-wiring-round2',
  description: 'Round 2: fix+reverify C2 (labels) and C7 (memory core), then build/verify C6 (learning tick) and C8 (memory wiring), merge into integrate/wiring-20261003, rerun cross-harness E2E incl. memory, push',
  phases: [
    { title: 'Fix', detail: 'C2 and C7 second TDD fix round on their existing branches' },
    { title: 'Verify', detail: 'blind re-verification' },
    { title: 'Build', detail: 'C6 and C8 TDD build on verified deps + blind verify + one fix round' },
    { title: 'Integrate', detail: 'merge verified branches into integrate/wiring-20261003; full suite; memory + learning e2e' },
    { title: 'Publish', detail: 'push branches; refresh PR bodies, ACTIVATE.md, SUMMARY.md' },
  ],
}
const W = '/mnt/zer0models/z0-wt/wiring'
const COMMON = `You are part of round 2 of the z0 wiring build. Read and follow EXACTLY the HARD RULES (const RULES) and TDD PROTOCOL (const TDD) text in ${W}/z0-wiring-build.js, plus ${W}/spec/PLAN.json (your component entry) and ${W}/spec/SPEC.md. Round-1 outcome: ${W}/publish/SUMMARY.md and ${W}/evidence/integration/E2E.md.
Live state changed since round 1 (owner-approved): AgentsView is now v0.44 (dataVersion 113, ~/.claude-home + deepseek-harness + omo indexed; live data dir read-only for you via sqlite mode=ro) and the TencentDB Agent Memory gateway runs at 127.0.0.1:8420 (docs: ${W}/ops/tencentdb/README.md, ${W}/ops/agentsview/README.md). Tests must still use synthetic fixtures/isolated homes; for TencentDB use a stub or a separate test tenant (never the default tenant, synthetic text only).
Owner decisions: memory injection into cloud models is opt-in per harness (default off; shadow first); Hermes target profile for activation is chiefstaff/clean (activation only, not now).`
const IMPL = { type: 'object', properties: { id: {type:'string'}, worktree: {type:'string'}, branch: {type:'string'}, base: {type:'string'}, head: {type:'string'},
  red_ok: {type:'boolean'}, green_ok: {type:'boolean'}, suite: {type:'string'}, e2e: {type:'string'}, evidence_dir: {type:'string'}, notes: {type:'string'}, hard_rule_breach: {type:'string'} },
  required: ['id','worktree','branch','base','head','red_ok','green_ok','suite','e2e','evidence_dir','notes','hard_rule_breach'] }
const VERD = { type: 'object', properties: { verdict: {type:'string', enum:['PASS','REVISE']}, issues: {type:'array', items:{type:'object', properties:{severity:{type:'string'}, file:{type:'string'}, problem:{type:'string'}, fix:{type:'string'}}, required:['severity','file','problem','fix']}}, hard_rule_breach: {type:'string'} }, required: ['verdict','issues','hard_rule_breach'] }
const isBreach = v => v && !/^\s*none\b/i.test(String(v)); const breaches = []
const note = (w, v) => { if (isBreach(v)) { breaches.push(`${w}: ${v}`); log(`HARD-RULE REPORT ${w}: ${v}`) } }
const verify = (id, impl, lbl) => agent(`${COMMON}
You are a BLIND verifier (strictest-maintainer style; default skeptical) for component ${id}. Code: worktree ${impl.worktree}, branch ${impl.branch}, base ${impl.base}, head ${impl.head}. Evidence (claims, not facts): ${impl.evidence_dir}.
Independently: new tests FAIL on base for the intended reason and PASS on head; full suite no new failures vs base; run the component's isolated_e2e yourself in your own isolated homes; read the whole diff for reuse, boundaries (harness-adapters/ in z0intelligence, no harness core change), minimal surface, safety (inert shadows, no text in exported rows, secrets scrubbed, no live paths/ports, fail-open). Also confirm every prior blocker/major issue listed in ${W}/round2/${id.slice(0,2)}-last-verdict.json (if it exists) is truly fixed. Do not modify the branch. PASS only if all hold.`, { label: lbl, phase: 'Verify', schema: VERD, effort: 'high' })
const fix = (id, cur, findings, lbl) => agent(`${COMMON}
Fix component ${id} on its EXISTING worktree/branch (${cur.worktree}, ${cur.branch}; new commits only, no rewrite) using TDD: for every blocker/major finding, first a failing test, then the fix; minor where cheap. Re-run red/green/suite/e2e and refresh evidence in ${W}/evidence/${id}/round2/.
Findings to fix: ${findings}`, { label: lbl, phase: 'Fix', schema: IMPL })
const build = (id, deps) => agent(`${COMMON}
Build component ${id} with the TDD PROTOCOL. Same-repo dependencies (base your branch on a merge of these heads, in a new worktree ${W}/wt/${id}): ${JSON.stringify(deps)}.`, { label: `build:${id}`, phase: 'Build', schema: IMPL })

async function cycle(id, impl, vfirst) {
  let v = vfirst || await verify(id, impl, `verify:${id}`)
  if (!v) return { id, ok: false, impl }
  note(`verify:${id}`, v.hard_rule_breach)
  if (v.verdict === 'PASS') return { id, ok: true, impl }
  const f = await fix(id, impl, JSON.stringify(v.issues), `fix:${id}`); if (!f) return { id, ok: false, impl }
  note(`fix:${id}`, f.hard_rule_breach)
  const v2 = await verify(id, f, `reverify:${id}`); if (v2) note(`reverify:${id}`, v2.hard_rule_breach)
  return { id, ok: !!(v2 && v2.verdict === 'PASS'), impl: f, verdict: v2 }
}
const START = {
  C2: { id: 'C2-loop-consumers', worktree: `${W}/wt/C2-loop-consumers`, branch: 'feat/wire-loop-consumers-20261003', head: '352edf2', base: 'feat/wire-loop-core-20261003 (C1)' },
  C7: { id: 'C7-memory-core', worktree: `${W}/wt/C7-memory-core`, branch: 'feat/wire-memory-core-20261003', head: '8d619cf', base: 'feat/wire-loop-core-20261003 (C1)' },
}
const [c2, c7] = await parallel(['C2', 'C7'].map(k => async () => {
  const st = START[k]
  const f = await fix(st.id, st, `read ${W}/round2/${k}-last-verdict.json (all blocker+major issues) and the round-1 open problems for ${st.id} in ${W}/publish/SUMMARY.md`, `fix:${st.id}`)
  if (!f) return { id: st.id, ok: false }
  note(`fix:${st.id}`, f.hard_rule_breach)
  return cycle(st.id, f)
}))
log(`C2 ${c2 && c2.ok ? 'PASS' : 'NOT VERIFIED'}; C7 ${c7 && c7.ok ? 'PASS' : 'NOT VERIFIED'}`)
const [c6, c8] = await parallel([
  async () => { if (!c2 || !c2.ok) return { id: 'C6-learning-tick', ok: false, skipped: true }; const b = await build('C6-learning-tick', [{ id: 'C2-loop-consumers', branch: c2.impl.branch, head: c2.impl.head, worktree: c2.impl.worktree }]); return b ? cycle('C6-learning-tick', b) : { id: 'C6-learning-tick', ok: false } },
  async () => { if (!c7 || !c7.ok) return { id: 'C8-memory-wiring', ok: false, skipped: true }; const b = await build('C8-memory-wiring', [{ id: 'C7-memory-core', branch: c7.impl.branch, head: c7.impl.head, worktree: c7.impl.worktree }, { note: 'shims to wire live on integrate/wiring-20261003 (C1,C3,C4a,C5 merged) — base on a merge of C7 head + integrate/wiring-20261003' }]); return b ? cycle('C8-memory-wiring', b) : { id: 'C8-memory-wiring', ok: false } },
])
const all = [c2, c7, c6, c8].filter(Boolean)
const ok = all.filter(x => x.ok)
phase('Integrate')
const integ = ok.length ? await agent(`${COMMON}
Merge the newly verified branches into integrate/wiring-20261003 (worktree ${W}/wt/integrate, currently e02bcf5, already contains C1/C3/C4a/C5) with git merge --no-ff in dependency order (C2, C7, C6, C8 — only those verified: ${JSON.stringify(ok.map(x => ({ id: x.id, branch: x.impl.branch, head: x.impl.head })))}). Resolve conflicts minimally with a test that fails on either side alone and passes on the merge (SPEC Appendix B lists expected conflicts: Hermes pre_llm_call, DSH index.mjs, .mcp.json). Run the full suite under the shared quiet lock (no new failures vs 6fee859 base set). Re-install the pinned venv ${W}/venv-integrate from the worktree.
Then rerun the cross-harness isolated e2e (${W}/homes/integration-r2/), reusing round 1's harness drivers in ${W}/evidence/integration/, and add: harness-generic verify/export for all 7 harnesses (non-zero rows, honest labels), the learning tick run once via its own entry point (sufficiency reported honestly; never promotes), unified memory read from EVERY harness seam through the z0-memory MCP / inject seam in shadow (same substrate, provenance + scope enforced, secret-scrub probe with planted fake keys), MemoryUseReceipt rows present, injection default OFF for cloud models. Write ${W}/evidence/integration/E2E-round2.md.
Return JSON-ish summary text: pass/fail per check, merges, conflicts, open problems, hard_rule_breach.`, { label: 'integration-r2', phase: 'Integrate' }) : null
phase('Publish')
const pub = await agent(`${COMMON}
Publish round 2: scan then push (new commits only, no force, --no-follow-tags) every verified branch and integrate/wiring-20261003 to kvnloo/z0intelligence; for branches that remain unverified push to their "-unverified" names. Update PR bodies in ${W}/publish/, rewrite ${W}/publish/ACTIVATE.md for the now-current live state (AgentsView v0.44 + sync timer DONE; TencentDB service DONE; remaining: install z0 plugins per harness from the integrate SHA via master merge (owner chose: merge integrate into master after review), Hermes chiefstaff plugin replace, learning tick timer, per-harness memory injection opt-in), and ${W}/publish/SUMMARY.md. Do not open PRs, do not merge to master, activate nothing.
Results: ${JSON.stringify(all.map(x => ({ id: x.id, ok: x.ok, branch: x.impl && x.impl.branch, head: x.impl && x.impl.head })))}; integration: ${JSON.stringify(integ).slice(0, 4000)}`, { label: 'publish-r2', phase: 'Publish' })
return { results: all.map(x => ({ id: x.id, ok: x.ok, branch: x.impl && x.impl.branch, head: x.impl && x.impl.head })), integration: integ, publish: pub, breaches }
