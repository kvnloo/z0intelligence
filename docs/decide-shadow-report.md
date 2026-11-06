# Shadow report reference

`z0int.decide_shadow report` reads existing decision metadata. It does not run
`tap`, select live routes, or change configuration. The `tap` command does read
source conversations. GPT must not run it under PRIV2.

| Option | Meaning |
| --- | --- |
| `--rows PATH` | Decision-metadata JSONL input. Defaults to the existing shadow log. |
| `--end TIMESTAMP` | Inclusive Unix timestamp at the end of the report window. Defaults to now. |
| `--hours HOURS` | Positive finite window length. Defaults to 24. |

The report filters both window boundaries before deduplication. `window` records
the requested bounds, first and last event timestamps, and observed event span.
A request for 24 hours does not mean that 24 hours of observations exist.
`vllm_sr_coverage` is a traffic-gap heuristic, not measured collector uptime.
A quiet source and a missing capture can produce the same gap.

`agree_pct` compares provider and model only. It does not establish agreement on
harness, effort, account, or successful execution. `cost_delta_usd` is the
API-equivalent estimate for the decided choice minus the actual choice, using
the actual token counts for both. Non-route decisions use zero estimated cost.
This number is not billed savings and does not measure counterfactual token use.
Unpriced disagreements are counted separately.

For a reproducible report, retain the input file, its SHA-256, the exact source
revision, pricing revision, and an explicit `--end`. Existing rows do not carry
the full historical role table or burn/saturation snapshots. A report cannot
reconstruct or verify an earlier decision from those rows alone.

Run verification through the fleet from the repository root:

```bash
fleet-build --node mbp --local-data --wait 1800 \
  --src-include src/z0int/ --src-include manifests/roles.v1.json \
  --src-include tests/conftest.py --src-include tests/test_decide.py \
  --src-include tests/test_decide_shadow.py --src-include tests/test_decide_shadow_window.py \
  -- env PYTHONPATH=src python3 -m pytest -q \
  tests/test_decide.py tests/test_decide_shadow.py tests/test_decide_shadow_window.py
```

The subprocess regression exercises the report module with synthetic metadata
and an empty HOME. It needs no source conversations, model inference, live
configuration, or pricing calls. Real cost reports still use the existing
Hermes pricing implementation.
