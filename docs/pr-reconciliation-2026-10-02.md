# Downstream reconciliation — 2026-10-02

Baseline: `0563ed7a52071cca63ccc91b8c9040207ad23e5d` on `master`.

All 59 pull requests were inspected, including their descriptions, changed-file inventories, review history and recorded owner constraints. At the audit snapshot, 21 were closed (18 already merged; three superseded documentation proposals) and 38 were open. This reconciliation integrates the original heads of **35 relevant open PRs**, including their stacked dependencies, with merge commits. The remaining three are intentional archives, not missing production features.

The non-PR `hermes-opportunities` branch at `f80da33700f1db2472571845c782928bd931e834` is also integrated. It supplies the State Packet and Decision Opportunity implementation that README previously described without containing the source. Its authored benchmark records remain historical evidence, not new measurements.

## Resulting stack and activation boundaries

- EventLog and OptMem foundations, with the scoped/bitemporal memory contract.
- State Packet and Decision Opportunity runtime, opt-in Hermes decisions, Claude Code hooks, posture and local worker evidence.
- Agent Orchestrator authority-bound spawn decisions, observations and reports.
- AgentWeb planning, bounded choices, context packing, reliability/outcome/expectation/assignment/batch observations, capabilities and outcome coverage. Dispatch overload responses preserve `X-Z0-Execution: not_started`.
- TypeSafe SDK 0.7.2 with automatic retries disabled; deterministic service/unit coverage uses mock transports.
- AODL canonical Python admission, drift receipts and optional governed-worker transport. `aodl-contract` is pinned at `68231658f0ec0338464c0916a2329b9587444312`; readiness reports protocol 3 and retained compatibility with protocol 2.
- Pure mutation disposition/outcome contracts and separately evaluated mutation-risk fixtures. Frozen earlier cohorts are not rewritten to manufacture coverage.
- OMP local cognition shadow extension, dependency maintenance and architecture metadata.

Integration does not establish model quality, authorize paid calls, or promote a shadow path into default routing. Harness execution ownership, the pinned AODL ontology and evidence-gated dispatch authority remain their canonical owners. The published Bend-native plugin remains a consumer; Bend admission experiments are specification/parity work, not a qualified runtime acceleration claim.

AgentWeb currently has two endpoint-specific envelopes using the same protocol label. `/v1/agentweb` uses the canonical RFC envelope (`protocol_version` and advisory support); `/v1/plan` and the lab endpoints use the lab `schema` envelope. They retain distinct strict validators. Clients must use the envelope documented for their endpoint; a future versioned migration remains separate work.

## Joint integration repairs

The independent PR receipts were insufficient to establish combined correctness. Joint testing found and repaired an undefined AODL drift-parent variable, concurrent drift replay races, ignored generic mutation-risk predictions, a Julia interpreter-pin check that did not enforce its result, health probes that could download uncached weights, obsolete SDK mock seams, a workstation-specific subprocess interpreter and shell JSON escaping. The architecture export is now valid YAML, includes the recovered owners, and has structural path/dependency checks.

## Verification

The final offline Python suite is run with Python 3.13.5, CPU Torch 2.10.0, the pinned project test dependencies and the canonical Kerdoios quota module at `cdb43b05a328a50649d5120bffcdecea09492219`. No live inference or provider qualification is inferred from these tests.

```bash
python -m pip install torch==2.10.0+cpu --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[test]'
# Checkout kvnloo/kerdoios at cdb43b05a328a50649d5120bffcdecea09492219 first.
export Z0INT_KERDOIOS_ROOT=/absolute/path/to/kerdoios
export Z0INT_HOME=/temporary/isolated/z0int-tests
export Z0INT_PYTHON="$(command -v python)"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m pytest -q -m 'not live'
bun test omp-extensions/local-cognition/index.test.ts
node --test tests/test-governed-client.mjs
(cd results/raw && sha256sum -c SHA256SUMS)
python benchmarks/verify_published.py
```

Final results: **920 Python tests passed**, 8 skipped and 5 live tests deselected; **12 OMP tests passed** (46 assertions); **4 governed-client tests passed**; all published raw checksums passed and **69 published summary claims verified**. The baseline, tested separately, had nine failures, including missing optional quota test setup and obsolete workstation/mock assumptions. No headline result or frozen raw evidence was changed.

CI now schedules the full offline suite on Python 3.11 and 3.13 for relevant source/test/manifest changes, using the exact Kerdoios revision. Local results above qualify Python 3.13; the matrix results must be checked independently on GitHub. The Avrea comparison is available only by explicit workflow-dispatch opt-in; no third-party runner configuration or timing advantage is claimed.

## Explicitly retained PRs

- #10: historical aggregate source branch; its owner explicitly identifies it as a source archive, not a merge unit.
- #21: oversized historical NanoJev sweep; owner requests independently measured salvage, not wholesale merge. Already merged and newly selected pieces are preserved through their dedicated PRs.
- #108: ThermoContext experimental archive whose decision is `KILL_CURRENT_COHORT_PROMOTION`. Its N32 and native context studies fail their promotion gates. Keep the reproducible negative evidence on its branch; do not import 6,462 archived files into the normal runtime or imply useful token savings.

## Complete pull-request disposition

The head pins below refer to the audited PR heads. “Integrated” means that exact head is an ancestor of the reconciliation branch; GitHub may close stacked PRs as integrated rather than assign each an independent merge event. Closed PRs are reported as historical dispositions and were not reopened.

| PR | Audited scope | Head | Disposition |
| --- | --- | --- | --- |
| [#2](https://github.com/kvnloo/z0intelligence/pull/2) | chore(deps): bump actions/checkout from 4 to 7 | `d44ed5288357` | Integrated, original commits preserved |
| [#3](https://github.com/kvnloo/z0intelligence/pull/3) | chore(deps): bump ossf/scorecard-action from 2.4.2 to 2.4.4 | `170b937f3a7b` | Integrated, original commits preserved |
| [#4](https://github.com/kvnloo/z0intelligence/pull/4) | chore(deps): bump github/codeql-action from 3 to 4 | `c0daf50569e9` | Integrated, original commits preserved |
| [#5](https://github.com/kvnloo/z0intelligence/pull/5) | chore(deps): bump actions/stale from 9 to 11 | `282a8e180ebd` | Integrated, original commits preserved |
| [#6](https://github.com/kvnloo/z0intelligence/pull/6) | chore(deps): bump actions/labeler from 5 to 7 | `9767ff1b0fc8` | Integrated, original commits preserved |
| [#7](https://github.com/kvnloo/z0intelligence/pull/7) | feat: critical path stack → master | `05b284a4a075` | Previously merged |
| [#8](https://github.com/kvnloo/z0intelligence/pull/8) | docs: canonical URLs after rename to z0intelligence | `b3baecab7299` | Previously merged |
| [#9](https://github.com/kvnloo/z0intelligence/pull/9) | feat: Phase A network cutover (preflight, artifacts, autoresearch) | `00e015dc5550` | Previously merged |
| [#10](https://github.com/kvnloo/z0intelligence/pull/10) | feat: harness adapters + OS compiler + contrastive evidence | `cf687a7ec56f` | Retain source archive; owner says not a merge unit |
| [#15](https://github.com/kvnloo/z0intelligence/pull/15) | docs: make JEV the seed observer before runtime promotion | `9ddda3165ca7` | Previously closed / superseded |
| [#16](https://github.com/kvnloo/z0intelligence/pull/16) | docs: sequence JEV dogfooding before formal evals | `44ba145ac026` | Previously closed / superseded |
| [#18](https://github.com/kvnloo/z0intelligence/pull/18) | docs: make JEV the seed observer before runtime promotion | `fb2874a16de4` | Previously closed / superseded |
| [#19](https://github.com/kvnloo/z0intelligence/pull/19) | docs: make JEV the seed observer before runtime promotion | `fb2874a16de4` | Previously merged |
| [#21](https://github.com/kvnloo/z0intelligence/pull/21) | feat(cognition): steal sweep on matched candidate sets — nanojev removes 61% of hammer3b calls | `c76f621d9328` | Retain salvage archive; owner says do not merge wholesale |
| [#29](https://github.com/kvnloo/z0intelligence/pull/29) | roadmap: add RLCDAlignBench detector + calibration wave | `a6a7de09dcf5` | Previously merged |
| [#30](https://github.com/kvnloo/z0intelligence/pull/30) | feat: add minimal RLCDAlignBench detector lane | `1f8f4342a450` | Previously merged |
| [#32](https://github.com/kvnloo/z0intelligence/pull/32) | reconcile(z0intelligence): promote the local lineage onto canonical (linear history) | `ba09b1e70877` | Previously merged |
| [#33](https://github.com/kvnloo/z0intelligence/pull/33) | chore(trim): wave 1 -- remove dead modules, a duplicate verifier, and legacy aliases | `ad96f0f44f11` | Previously merged |
| [#34](https://github.com/kvnloo/z0intelligence/pull/34) | Canonicalize capability routing and authority-owned execution | `61dc2b6c501a` | Previously merged |
| [#35](https://github.com/kvnloo/z0intelligence/pull/35) | feat(cognition): promote the local-cognition OMP extension to canonical | `a9cbbed2b92a` | Integrated, original commits preserved |
| [#36](https://github.com/kvnloo/z0intelligence/pull/36) | chore(trim): wave 2 -- remove the log-only legacy bridge and the vacuous P0.7A test | `6e55c1c3586e` | Integrated, original commits preserved |
| [#37](https://github.com/kvnloo/z0intelligence/pull/37) | feat: propagate Tokenomics measurement state | `48737563f197` | Previously merged |
| [#38](https://github.com/kvnloo/z0intelligence/pull/38) | fix(omp): accumulate complete provider usage across turns | `2052a64e526d` | Previously merged |
| [#40](https://github.com/kvnloo/z0intelligence/pull/40) | feat: add Image JevBench multimodal decision entry | `21cd31edb2df` | Previously merged |
| [#41](https://github.com/kvnloo/z0intelligence/pull/41) | Declare torchvision for the image decision extra | `5d8273dd1079` | Previously merged |
| [#42](https://github.com/kvnloo/z0intelligence/pull/42) | feat(backends): add Julia-1 as a measured, default-off finite-choice decision backend | `e9de67d0b974` | Previously merged |
| [#43](https://github.com/kvnloo/z0intelligence/pull/43) | feat(julia-claims): per-turn OMP decision extractor — Decision Dataset v2 | `a290f1c70292` | Previously merged |
| [#50](https://github.com/kvnloo/z0intelligence/pull/50) | lab: admit AgentWeb/Emma as a first-class z0 harness | `7aa6be0ab0df` | Integrated, original commits preserved |
| [#52](https://github.com/kvnloo/z0intelligence/pull/52) | lab: migrate z0 Jev transport to official TypeSafe SDK | `ff977c505a0b` | Integrated, original commits preserved |
| [#61](https://github.com/kvnloo/z0intelligence/pull/61) | feat: Agent Orchestrator shadow decision + outcome bridge | `c4fb622a97ae` | Integrated, original commits preserved |
| [#64](https://github.com/kvnloo/z0intelligence/pull/64) | Fix stale NanoJev defaults and document Laya → Jev verification | `cd00c337d281` | Previously merged |
| [#65](https://github.com/kvnloo/z0intelligence/pull/65) | docs: expose z0int core architecture to z0archy | `d0ce0423b41d` | Previously merged |
| [#67](https://github.com/kvnloo/z0intelligence/pull/67) | feat(memory): canonical append-only event ledger | `e69f7ade7b57` | Integrated, original commits preserved |
| [#68](https://github.com/kvnloo/z0intelligence/pull/68) | feat(memory): add scoped lifelong memory contract v1 | `1b799c76751c` | Integrated, original commits preserved |
| [#69](https://github.com/kvnloo/z0intelligence/pull/69) | feat(memory): OptMem temporal projection + bounded cover | `cb00a59d1af1` | Integrated, original commits preserved |
| [#70](https://github.com/kvnloo/z0intelligence/pull/70) | docs: rebuild README around current z0intelligence architecture | `613865a1862d` | Previously merged |
| [#72](https://github.com/kvnloo/z0intelligence/pull/72) | RFC: define AgentWeb ↔ z0 bridge protocol v1 | `5ed93a9f8a91` | Integrated, original commits preserved |
| [#73](https://github.com/kvnloo/z0intelligence/pull/73) | feat(aodl): canonical structural admission gate | `ba43be05c450` | Integrated, original commits preserved |
| [#75](https://github.com/kvnloo/z0intelligence/pull/75) | lab(agentweb): validate bridge protocol v1 | `4e7ae5d3f4e1` | Integrated, original commits preserved |
| [#77](https://github.com/kvnloo/z0intelligence/pull/77) | docs: expose z0int authority boundaries to z0archy | `12ed2523c95f` | Integrated, original commits preserved |
| [#78](https://github.com/kvnloo/z0intelligence/pull/78) | feat(aodl): enforce structural admission before remote dispatch | `80c8d19b8268` | Integrated, original commits preserved |
| [#79](https://github.com/kvnloo/z0intelligence/pull/79) | feat: frozen Agent Orchestrator promotion evidence report | `aebe8bcecb29` | Integrated, original commits preserved |
| [#80](https://github.com/kvnloo/z0intelligence/pull/80) | feat(aodl): record runtime drift without rewriting intent | `1ef20610334c` | Integrated, original commits preserved |
| [#81](https://github.com/kvnloo/z0intelligence/pull/81) | lab(agentweb): carry bridge v1 over existing authority endpoints | `328ef7e1806a` | Integrated, original commits preserved |
| [#83](https://github.com/kvnloo/z0intelligence/pull/83) | feat: measure Agent Orchestrator shadow decision economics | `a1a78c8b4165` | Integrated, original commits preserved |
| [#84](https://github.com/kvnloo/z0intelligence/pull/84) | lab(agentweb): expose bridge capability negotiation | `69650edb7796` | Integrated, original commits preserved |
| [#86](https://github.com/kvnloo/z0intelligence/pull/86) | feat: matched AO candidate-reference deltas | `345e65155226` | Integrated, original commits preserved |
| [#87](https://github.com/kvnloo/z0intelligence/pull/87) | security(agentweb): reject nested credentials/raw identity at bridge ingress | `3503cc41a664` | Integrated, original commits preserved |
| [#88](https://github.com/kvnloo/z0intelligence/pull/88) | feat(aodl): first host-governed remote worker canary | `28db1eecbe9f` | Integrated, original commits preserved |
| [#89](https://github.com/kvnloo/z0intelligence/pull/89) | feat: sidecar AO experiment pair registry | `6e178d50cac5` | Integrated, original commits preserved |
| [#91](https://github.com/kvnloo/z0intelligence/pull/91) | lab(agentweb): ingest immutable windowed outcome observations | `828a3b6d13a4` | Integrated, original commits preserved |
| [#93](https://github.com/kvnloo/z0intelligence/pull/93) | lab(agentweb): add outcome expectation coverage promotion gate | `be28541f1f91` | Integrated, original commits preserved |
| [#97](https://github.com/kvnloo/z0intelligence/pull/97) | lab(agentweb): add randomized outcome evidence review barrier | `a4ff12553f91` | Integrated, original commits preserved |
| [#99](https://github.com/kvnloo/z0intelligence/pull/99) | lab(agentweb): durable verified-event dedup + outcome fan-out | `66f8d5843124` | Integrated, original commits preserved |
| [#101](https://github.com/kvnloo/z0intelligence/pull/101) | lab(agentweb): verify and preserve context evidence provenance | `37e1b033703c` | Integrated, original commits preserved |
| [#103](https://github.com/kvnloo/z0intelligence/pull/103) | lab(agentweb): make overload pre-execution semantics machine-verifiable | `925226439ff7` | Integrated, original commits preserved |
| [#105](https://github.com/kvnloo/z0intelligence/pull/105) | research(receipts): separate cognition retries from mutation outcomes | `2c3820fbd082` | Integrated, original commits preserved |
| [#107](https://github.com/kvnloo/z0intelligence/pull/107) | ci: benchmark Avrea runner against GitHub | `0888e731048a` | Integrated, original commits preserved |
| [#108](https://github.com/kvnloo/z0intelligence/pull/108) | experiment: real THRML audit and N32 stop; KILL cohort promotion | `987022a0e876` | Retain stopped experiment; no cohort promotion |
