export const meta = {
  name: 'z0-wiring-build',
  description: 'TDD build of the z0 wiring plan: per-component red→green in isolated worktrees, blind verification, cross-harness isolated E2E, fork-only publish + activation runbook (no live activation)',
  phases: [
    { title: 'Build', detail: 'one agent per component: red tests first, minimal implementation, full suite, isolated e2e, local commits' },
    { title: 'Verify', detail: 'blind reviewer per component: re-runs red on base and green on head, checks boundaries/safety/minimality' },
    { title: 'Fix', detail: 'one TDD fix round for REVISE verdicts, then re-verify' },
    { title: 'Integrate', detail: 'cross-harness isolated end-to-end: capture → verify/export → evolution-lab learner → unified memory reads' },
    { title: 'Publish', detail: 'push new branches to kvnloo repos, stage PR bodies, write ACTIVATE.md runbook; activate nothing' },
  ],
}

const W = '/mnt/zer0models/z0-wt/wiring'
const OWNER_ASK = args.ownerAsk
const comps = args.components.filter(c => c.build_now)
const skipped = args.components.filter(c => !c.build_now)
if (skipped.length) log(`not built (build_now=false): ${skipped.map(c => `${c.id} [${c.gated_by}]`).join('; ')}`)

const RULES = `
HARD RULES (all mandatory; report any breach honestly in hard_rule_breach, else "none"):
- Read ${W}/spec/SPEC.md, ${W}/spec/PLAN.json and ${W}/prior-findings.md before starting. The plan is the spec; do not widen scope.
- NO harness core changes (owner direction): integrate only via plugin / hook / extension / MCP / z0 HTTP API. All harness plugins live in kvnloo/z0intelligence harness-adapters/ (one harness-agnostic core + thin per-harness shims). No new repos. Never modify the kvnloo/hermes-agent fork, DSH core, OMP core, Codex or Claude Code code.
- NOTHING LIVE: never touch ~/.hermes or /workspace/hermes-home (no reads beyond what the spec already extracted), ~/.dsh (except reading plugin/profile sources), ~/.claude-home settings/plugins, ~/.codex, ~/.omp config, ~/.z0int live state/config, the live z0 service (127.0.0.1:11501 / 172.19.0.1:11501 — no HTTP calls), systemd units/timers (no enable/start/stop), agentsview daemon (no sync/serve against the real data dir). Every test/e2e uses isolated homes under ${W}/homes/<component-id>/ (HOME, Z0INT_HOME, HERMES_HOME, AGENTSVIEW_DATA_DIR, DSH profile dir, etc.) and a private z0 service on a private port in 11540-11559 if one is needed.
- Never run the DSH wrapper (/usr/bin/dsh, ~/.dsh/bin/*) — it triggers a credential refresh. Test DSH plugins with their selftest pattern / a mock DSH host API. Never read .env/auth/keystore/credential files; never print secrets. No paid API calls. Prefer fakes/mocks; any local-model call needs the component's acceptance to require it.
- Privacy: tests use synthetic fixtures only. Never commit real transcripts, prompts, receipts, decision logs, personal data or model weights. Training/export rows contain no prompt/response/command/path/claim text.
- Shadows are inert: nothing a shadow decides may reach the model, user or a tool. No automatic promotion; promotion past shadow needs owner approval. Governed dispatch/remote execution to DSH/Hermes stays gated by z0intelligence#95.
- CPU: run test suites, builds and e2e under the SHARED quiet-lane lock: \`flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock <cmd>\` (other workflows hold exclusive timed windows). Run code under /mnt/zer0models/github/cua-lanes/bin/hostless when it could touch a display/session. No GUI.
- Git: work only in your component worktree ${W}/wt/<component-id> on the planned new branch. Never git stash, never force-push, never rewrite others' branches, never push in Build/Verify/Fix (Publish pushes). Commits: author lesseradmin <veeman961@gmail.com>, message ends with a blank line then "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>".
- Scratch under ${W} (never /tmp). Evidence files under ${W}/evidence/<component-id>/.`

const TDD = `
TDD PROTOCOL (evidence is checked by a blind verifier):
1. Create the worktree: git -C <repo_path> fetch origin; git -C <repo_path> worktree add ${W}/wt/<id> -b <branch> <base> — base = the component's base_ref, or, when it depends on a component in the SAME repo, that dependency's branch head (given below). For a cross-repo dependency, point tests at the dependency's worktree (PYTHONPATH / file: dependency) instead.
2. Write the red tests listed in the component (behaviour contracts in the repo's own test style and location). Run them BEFORE any implementation; save the output to ${W}/evidence/<id>/red.txt. They must fail for the intended reason (assertion/missing behaviour), not a typo or import error of the test itself.
3. Implement the smallest change that makes them pass, reusing the modules named in "reuse" (extend, don't duplicate). Match surrounding code style and comment density.
4. Run the red tests again → ${W}/evidence/<id>/green.txt (must pass).
5. Run the repo's full test suite on head AND record the base failure set (run the suite on the base in a temporary worktree ${W}/wt/<id>-base, then remove it) → suite.txt. No new failures allowed; pre-existing failures must be the identical set.
6. Run the component's isolated_e2e exactly as specified (isolated homes only) → e2e.txt with the commands and the observed result.
7. Commit (one or a few focused commits) on the component branch. Do not push.`

const IMPL_SCHEMA = { type: 'object', properties: {
  id: { type: 'string' }, repo_path: { type: 'string' }, worktree: { type: 'string' }, branch: { type: 'string' },
  base: { type: 'string' }, head: { type: 'string' }, commits: { type: 'array', items: { type: 'string' } },
  files_changed: { type: 'array', items: { type: 'string' } },
  red_ok: { type: 'boolean', description: 'red tests failed for the intended reason before implementation' },
  green_ok: { type: 'boolean' }, suite: { type: 'string', description: 'e.g. "head 980 passed/11 failed; base 964/11; same failure set"' },
  e2e: { type: 'string' }, evidence_dir: { type: 'string' },
  deviations_from_plan: { type: 'array', items: { type: 'string' } },
  activation_notes: { type: 'string', description: 'exact live activation steps this component needs (NOT performed)' },
  hard_rule_breach: { type: 'string' },
}, required: ['id', 'worktree', 'branch', 'base', 'head', 'commits', 'files_changed', 'red_ok', 'green_ok', 'suite', 'e2e',
  'evidence_dir', 'deviations_from_plan', 'activation_notes', 'hard_rule_breach'] }

const VERDICT_SCHEMA = { type: 'object', properties: {
  verdict: { type: 'string', enum: ['PASS', 'REVISE'] },
  red_reproduced_on_base: { type: 'boolean' }, green_reproduced_on_head: { type: 'boolean' },
  suite_no_new_failures: { type: 'boolean' }, e2e_reproduced: { type: 'boolean' },
  issues: { type: 'array', items: { type: 'object', properties: {
    severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' }, problem: { type: 'string' },
    fix: { type: 'string' } }, required: ['severity', 'file', 'problem', 'fix'] } },
  hard_rule_breach: { type: 'string' },
}, required: ['verdict', 'red_reproduced_on_base', 'green_reproduced_on_head', 'suite_no_new_failures', 'e2e_reproduced', 'issues', 'hard_rule_breach'] }

const isBreach = v => v && !/^\s*none\b/i.test(String(v))
const breaches = []
const note = (who, v) => { if (isBreach(v)) { breaches.push(`${who}: ${v}`); log(`HARD-RULE REPORT ${who}: ${v}`) } }

const implPrompt = (c, deps) => `${OWNER_ASK}

You are the TDD implementer for ONE component of the z0 wiring plan.
Component: ${c.id} — "${c.title}". Its full spec (repo_path, base_ref, branch, harnesses, purpose, reuse, red_tests, acceptance_refs, isolated_e2e, activation, gated_by) is the entry with id "${c.id}" in ${W}/spec/PLAN.json — read it there (and the matching SPEC.md section) before starting; it is binding.
Completed dependencies (branch heads / worktrees you may build on): ${JSON.stringify(deps)}
${TDD}
${RULES}
Return the structured result. Put details in ${W}/evidence/${c.id}/NOTES.md.`

const verifyPrompt = (c, impl) => `You are a BLIND verifier for one component of the z0 wiring plan, in the style of the repo's strictest maintainer. You get the spec and where the code is — not the implementer's narrative. Default to skepticism.
Component: ${c.id} — its full spec is the entry with id "${c.id}" in ${W}/spec/PLAN.json (read it, plus the matching SPEC.md section).
Code: worktree ${impl.worktree}, branch ${impl.branch}, base ${impl.base}, head ${impl.head}. Evidence files (treat as claims to check, not facts): ${impl.evidence_dir}.
Do, independently: (1) in a temporary worktree at the base (${W}/wt/${c.id}-verify-base, remove it after), add the new test files from head and run them — they must FAIL for the intended reason; (2) run them on head — PASS; (3) run the full suite on head vs base — no new failures; (4) run the isolated_e2e yourself with your own isolated homes under ${W}/homes/${c.id}-verify/; (5) read the whole diff: reuse instead of reimplementation, correct owner repo/boundary (harness-adapters/ in z0intelligence for plugins), no harness core change, smallest surface, matches repo idioms, tests are behaviour contracts not implementation mirrors, safety (inert shadows, no raw text in exported rows, no live paths/ports, no secrets, no personal fixtures, fail-open hooks that never block a turn).
Do not modify the component branch. Return PASS only if all of (1)-(5) hold; otherwise REVISE with concrete issues.
${RULES}`

const fixPrompt = (c, impl, v) => `${OWNER_ASK}
You are fixing one component after a blind verifier returned REVISE. Fix every blocker and major issue (minor ones where cheap) with TDD: for each behavioural defect first add/adjust a test that fails, then fix. Re-run red/green/suite/e2e and refresh the evidence files.
Component: ${c.id} — full spec: entry "${c.id}" in ${W}/spec/PLAN.json.
Current state: ${JSON.stringify(impl)}
Verifier findings: ${JSON.stringify(v)}
${TDD}
${RULES}
Work in the same worktree/branch (${impl.worktree}, ${impl.branch}); new commits only (no history rewrite). Return the structured result.`

const done = {}
let remaining = [...comps]
let wave = 0
while (remaining.length) {
  const ids = new Set(comps.map(c => c.id))
  const ready = remaining.filter(c => (c.depends_on || []).filter(d => ids.has(d)).every(d => done[d] !== undefined))
  if (!ready.length) { log(`unresolvable dependencies for: ${remaining.map(c => c.id).join(', ')}`); break }
  wave++
  log(`wave ${wave}: ${ready.map(c => c.id).join(', ')}`)
  const results = await pipeline(ready,
    c => {
      const deps = (c.depends_on || []).filter(d => ids.has(d)).map(d => done[d])
      const failedDep = deps.find(d => !d || !d.ok)
      if (failedDep) { log(`skip ${c.id}: dependency not verified`); return null }
      return agent(implPrompt(c, deps.map(d => d.impl)), { label: `build:${c.id}`, phase: 'Build', schema: IMPL_SCHEMA })
    },
    (impl, c) => impl && agent(verifyPrompt(c, impl), { label: `verify:${c.id}`, phase: 'Verify', schema: VERDICT_SCHEMA, effort: 'high' })
      .then(v => ({ impl, v })),
    async (iv, c) => {
      if (!iv || !iv.v) return iv
      note(`build:${c.id}`, iv.impl.hard_rule_breach); note(`verify:${c.id}`, iv.v.hard_rule_breach)
      if (iv.v.verdict === 'PASS') return iv
      const fixed = await agent(fixPrompt(c, iv.impl, iv.v), { label: `fix:${c.id}`, phase: 'Fix', schema: IMPL_SCHEMA })
      if (!fixed) return iv
      note(`fix:${c.id}`, fixed.hard_rule_breach)
      const v2 = await agent(verifyPrompt(c, fixed), { label: `reverify:${c.id}`, phase: 'Fix', schema: VERDICT_SCHEMA, effort: 'high' })
      if (v2) note(`reverify:${c.id}`, v2.hard_rule_breach)
      return { impl: fixed, v: v2 || iv.v, fixed: true }
    })
  ready.forEach((c, i) => {
    const r = results[i]
    done[c.id] = r && r.impl ? { ok: !!(r.v && r.v.verdict === 'PASS'), impl: r.impl, verdict: r.v, fixed: !!r.fixed } : { ok: false }
    log(`${c.id}: ${done[c.id].ok ? 'PASS' : 'NOT VERIFIED'}`)
  })
  remaining = remaining.filter(c => !ready.includes(c))
}

const built = comps.map(c => ({ id: c.id, ...(done[c.id] || { ok: false }) }))
const passed = built.filter(b => b.ok)

phase('Integrate')
const INTEG_SCHEMA = { type: 'object', properties: {
  pass: { type: 'boolean' }, report: { type: 'string', description: 'path of E2E.md' },
  checks: { type: 'array', items: { type: 'object', properties: { check: { type: 'string' }, result: { type: 'string' } }, required: ['check', 'result'] } },
  fixes_committed: { type: 'array', items: { type: 'string' } }, open_problems: { type: 'array', items: { type: 'string' } },
  hard_rule_breach: { type: 'string' },
}, required: ['pass', 'report', 'checks', 'fixes_committed', 'open_problems', 'hard_rule_breach'] }
const integ = passed.length ? await agent(`${OWNER_ASK}

Prove the verified components work TOGETHER, end to end, in one fully isolated environment (${W}/homes/integration/: its own HOME, Z0INT_HOME, HERMES_HOME, AGENTSVIEW_DATA_DIR copy of a small synthetic DB or a fresh one, DSH mock host, OMP/Codex/Claude Code hook payload fixtures; private z0 service port in 11540-11559 if needed).
Verified components: ${JSON.stringify(passed.map(b => ({ id: b.id, branch: b.impl.branch, head: b.impl.head, worktree: b.impl.worktree })))}
Not verified (do not rely on them): ${JSON.stringify(built.filter(b => !b.ok).map(b => b.id))}
Plan definition of done and continuous-learning spec: the definition_of_done and continuous_learning fields of ${W}/spec/PLAN.json (read them).
First create the integration branch in /mnt/zer0models/z0-wt/z0intelligence: a new worktree ${W}/wt/integrate on branch integrate/wiring-20261003 from origin/feat/shadow-loop-v0, then merge each verified component branch in dependency order (git merge --no-ff, no rewrite; resolve conflicts minimally and record them). Run the full z0intelligence suite on it under the shared quiet lock (no new failures vs the base). Pin a fresh venv for the e2e at ${W}/venv-integrate (uv venv + editable install of the integrate worktree) — never reuse or modify /mnt/zer0models/z0-wt/venv-claude-code (the live Claude Code hooks use it).
Exercise, for EVERY harness shim that exists: synthetic turns → capture records (DecisionOpportunity/observed/outcome) in the isolated Z0INT_HOME → \`z0int outcomes verify\` + export → evolution-lab learner run (the continuous-learning job, run ONCE manually under the shared quiet lock; it must report sufficiency honestly, e.g. INSUFFICIENT_DATA on synthetic volume) → unified-memory read from every harness seam (same canonical substrate, provenance + scope enforced) → memory-use receipts written. Confirm shadows are inert (the turn output is byte-identical with shadows on/off) and exported rows carry no text.
If an integration defect needs a code fix, fix it on the owning component branch with TDD (failing test first, new commit, no rewrite) and list it. Write ${W}/evidence/integration/E2E.md.
${RULES}`, { label: 'integration-e2e', phase: 'Integrate', schema: INTEG_SCHEMA, effort: 'high' }) : null
if (integ) note('integration', integ.hard_rule_breach)

phase('Publish')
const PUB_SCHEMA = { type: 'object', properties: {
  pushed: { type: 'array', items: { type: 'object', properties: { repo: { type: 'string' }, branch: { type: 'string' }, head: { type: 'string' } }, required: ['repo', 'branch', 'head'] } },
  pr_bodies: { type: 'array', items: { type: 'string' } }, runbook: { type: 'string' }, summary: { type: 'string' },
  refused: { type: 'array', items: { type: 'string' } }, hard_rule_breach: { type: 'string' },
}, required: ['pushed', 'pr_bodies', 'runbook', 'summary', 'refused', 'hard_rule_breach'] }
const pub = await agent(`Publish the z0 wiring build to the owner's own repos and write the activation runbook. Activate NOTHING.
Built components (verified flag per item): ${JSON.stringify(built.map(b => ({ id: b.id, ok: b.ok, branch: b.impl && b.impl.branch, head: b.impl && b.impl.head, worktree: b.impl && b.impl.worktree, activation_notes: b.impl && b.impl.activation_notes })))}
Integration: ${JSON.stringify(integ)}
Plan: ${W}/spec/PLAN.json (activation steps per component), ${W}/spec/SPEC.md.
1. Include the integration branch integrate/wiring-20261003 (worktree ${W}/wt/integrate) if the Integrate stage created it. For every branch (verified or not), scan its commits for secrets, personal data, absolute home paths and weights before pushing (refuse to push any that fail and list them). Push each branch as a NEW branch to its kvnloo/* origin: \`git push origin <branch> --no-follow-tags\` (never force, never to upstream/third-party repos, never to main/master). Unverified components may be pushed only with a "-unverified" suffix branch name.
2. Write a PR body per pushed branch to ${W}/publish/pr-<repo>-<branch-slug>.md (what/why/tests/evidence/activation; end with "🤖 Generated with [Claude Code](https://claude.com/claude-code)"). Do NOT open PRs and do not comment on any issue.
3. Write ${W}/publish/ACTIVATE.md: the ordered live activation runbook — for each harness and for the continuous-learning scheduler and the unified-memory sync: exact commands/config entries, what live file/service each touches, a backup command run first, a one-line rollback, a post-activation check, and which steps need owner approval (all live changes do). Include: unifying the live z0 service onto the merged branch (it currently runs a closed-PR branch), agentsview sync restart, plugin installs per harness, systemd user timer for the learner (with flock -s on the quiet-lane lock).
4. Write ${W}/publish/SUMMARY.md: what was built, verified, pushed, what remains gated (#95), open problems.
${RULES.replace('never push in Build/Verify/Fix (Publish pushes)', 'you are the Publish stage: push only as specified above')}`,
  { label: 'publish', phase: 'Publish', schema: PUB_SCHEMA })
if (pub) note('publish', pub.hard_rule_breach)

return { built: built.map(b => ({ id: b.id, ok: b.ok, fixed: b.fixed, branch: b.impl && b.impl.branch, head: b.impl && b.impl.head })),
  skipped: skipped.map(c => c.id), integration: integ, publish: pub, breaches }
