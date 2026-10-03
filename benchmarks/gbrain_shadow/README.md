# GBrain shadow-policy eval

Deterministic contract fixtures for the first GBrain × AODL × z0intelligence slice.

The suite does **not** claim proactive-agent quality. It pins safety/semantic invariants before we collect real joined outcomes:

- empty complete delta -> `IGNORE`
- delivered memory change -> `OBSERVE`
- open thread -> `PREPARE`
- partial/degraded delivery -> `OBSERVE`, never a false `IGNORE`
- every candidate remains shadow-only, traffic-ineligible, and grants no authority
- `ACT` and `SURFACE` are forbidden in this baseline

Run:

```bash
PYTHONPATH=src python benchmarks/gbrain_shadow/run.py
pytest -q tests/test_gbrain_shadow_eval.py
```

The next eval layer joins `gbrain_candidate_id` to z0int decision/outcome receipts and measures precision, avoided attention, latency, and token cost before any promotion.
