# Reproduce the bounded CPU wave

First restore the losslessly packed reviewed raw files (standard library only):

```sh
python benchmarks/thermocontext/unpack_results.py
```

Every reconstructed byte is checked against the reviewed file hashes. Existing identical files are accepted; differing files are never overwritten. The split archive changes transport layout only.

From repository root, Python3.12:

```sh
python -m venv .venv-thermocontext
.venv-thermocontext/bin/pip install -r benchmarks/thermocontext/requirements-thrml.txt
export JAX_PLATFORMS=cpu OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export XLA_FLAGS=--xla_cpu_multi_thread_eigen=false
PY=.venv-thermocontext/bin/python
OUT=results/thermocontext/my-new-run
mkdir -p "$OUT"
# The unchanged prototype prints JSON; shell noclobber keeps output create-only.
(set -C; "$PY" benchmarks/thermocontext/phase_a_prototype.py > "$OUT/original-reproduction.json")
"$PY" -m pytest -q benchmarks/thermocontext/test_thrml_experiment.py
"$PY" -m benchmarks.thermocontext.thrml_experiment --formulation fixed_original --output "$OUT/fixed-thrml.jsonl"
"$PY" -m benchmarks.thermocontext.run_budget_cohort --formulation adaptive_price --output "$OUT/adaptive-price.jsonl"
"$PY" -m benchmarks.thermocontext.run_budget_cohort --formulation prefix_token_slack --output "$OUT/prefix-token.jsonl"
"$PY" -m benchmarks.thermocontext.run_budget_cohort --formulation prefix_cardinality_buckets --output "$OUT/prefix-cardinality.jsonl"
"$PY" -m benchmarks.thermocontext.run_deterministic --output "$OUT/deterministic-frontier.jsonl"
"$PY" -m benchmarks.thermocontext.summarize_thrml "$OUT" --output "$OUT/summary.json"
```

The deterministic driver deliberately reproduces the observed attempted cohort (N8/12/16 tasks0–11, N32 tasks0–9). The original execution used the same loop inline; this file preserves that command's logic. A future changed stochastic outcome may stop at a different task; do not silently relabel that as the same cohort. The focused DP parity test uses the committed original reproduction as its immutable calibration fixture.

The runs used official THRML0.1.4 at the pinned SHA, JAX0.11.2, Equinox0.13.8, NumPy2.2.6 on Debian13 CPU. No GPU/TSU or provider execution. The environment/protocol/source manifest and raw rows are committed. No model package installation or project onboarding was needed for this isolated experiment.

## Offline evidence check

```sh
"$PY" -m benchmarks.thermocontext.summarize_thrml results/thermocontext/2026-10-02-thrml-wave1 --output /tmp/thermocontext-summary-new.json
(cd results/thermocontext/2026-10-02-thrml-wave1 && sha256sum -c SHA256SUMS)
```

All outputs refuse existing paths. Never overwrite historical rows. Read protocol_thrml_wave1.json, its addendum and implementation-policy-clarification.json before interpreting stop decisions. Do not increase budgets after failure.
