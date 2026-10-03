# TencentDB model-select benchmark

See `../PREREG.md` for the design. Quick use:

```bash
# corpus (frozen; must reproduce the sha in PREREG)
python3 bench/corpus/build_corpus.py
# one cell: candidate x conversation set x repeat
python3 bench/run_arm.py --cand nemotron-super-free --convs S --repeat 1 --run-id R0
python3 bench/run_arm.py --cand qwen3-8b-q4km --convs ALL --repeat 2 --run-id R1   # local: takes quiet-lane + gpu locks
python3 bench/run_arm.py --cand stub --stub --convs ALL --repeat 1 --run-id CTRL    # verbatim control, no model
# score a run directory (all candidates/repeats inside it)
python3 bench/score/score.py runs/R1 --out runs/R1/report.json --md runs/R1/report.md
# unit tests
python3 -m pytest bench/tests -q
```

Files: `harness/driver.ts` (runs inside the deployed memory-core image, calls the gateway's own
config loader, store, L1/L2/L3 runners and LLM runner), `harness/shim.py` (loopback
OpenAI-compatible proxy: effort injection, key holder, ledger, paid cap, pacing, stub mode),
`run_arm.py` (orchestrates shim + optional local llama-server + container), `score/score.py`
(deterministic scorer), `score/judge.py` (secondary local judge), `candidates.json` (routes).
