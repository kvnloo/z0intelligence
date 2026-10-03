# GStack context-value shadow experiment

Consumes the JSON shape from `gstack-context-bill --json` and normalizes
per-skill cost/benefit metadata for z0intelligence experiments.

This is deliberately asymmetric:

- token cost may be measured (`--exact`) or explicitly estimated by GStack
- benefit from `estimated_token_saving` is an **author declaration**
- value density is useful for ranking experiments, not proof of usefulness
- every row is shadow-only, traffic-ineligible, unverified, and grants no authority

The experiment exists to answer a later empirical question: does declared
saving/cost predict verified savings once candidate IDs are joined to real
task outcomes?

Run:

```bash
PYTHONPATH=src python benchmarks/gstack_context_value/run.py
pytest -q tests/test_gstack_context_value.py
```

Upstream provenance: garrytan/gstack#2889 by @sky-mirrors; native micro-slice
under review as garrytan/gstack#3021.
