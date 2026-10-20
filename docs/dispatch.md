# Factory job dispatcher (`z0int dispatch`)

`z0int.dispatch` assigns typed factory jobs to the cheapest execution lane that is good
enough and has capacity now. It is stdlib-only, so batch runners can call
`python -m z0int.dispatch` without the ML stack.

## Inputs

- **Jobs** (JSONL or a JSON array): `id`, `kind` (`scan|label|qa|review|test|code`),
  `size` (`S|M|L`), `quality` (0–1, the minimum acceptable lane quality), `privacy`
  (`public|internal|private`), optional `deadline_s`, `avoid_family` (QA must not run on
  the family it checks), `tokens`.
- **Capacity**: Kerdoios `kerdoios.capacity.v1` (`python -m kerdoios capacity`): burn-pace
  pacing for paid pools, the Nous spend cap, per-lane limits derived from evidence, and live
  cooldowns. Set `KERDOIOS_SRC` to a kerdoios checkout if the package is not installed.

## Decision

Cost is a shadow price:

| lane state | cost |
|---|---|
| subscription, `under-burn`, inside its hourly headroom × share | −0.5 (perishable quota: use it first) |
| subscription, `on-pace`, inside headroom | 0.2 |
| subscription past headroom, `hard-throttle`, or burn-pace `avoid` | infeasible |
| free lane | 0.02 + 0.1 × fraction of its window already used |
| capped (Nous) | dollars + pressure on the remaining cap; +1.0 for bulk `scan`/`label` |
| metered | 0.1 + dollars (only when Kerdoios allows metered lanes) |
| agentic lane on a one-shot `label` | +0.6 harness overhead |

Hard rules: `code` goes to opus/sol only; a job's privacy may not exceed what the
provider's data policy allows; quality must meet the job's bar; QA avoids the checked
family. A job waits for a busy lane rather than taking one that is more than 0.6 dearer, so
bulk work never drains the capped plan while free lanes are merely busy.

Quality per (lane, kind) is a Beta-style posterior: seed priors (Opus highest) plus recorded
outcomes, where QA agreement outranks "finished with valid JSON". The subscription cost per
job (% of the weekly window) starts at a low-confidence prior and is learned from burn-pace
snapshots taken at batch start and end.

## Commands

```bash
python -m z0int.dispatch capacity                     # lanes as the dispatcher sees them
python -m z0int.dispatch plan --jobs jobs.jsonl       # dry run over a 1 h horizon
python -m z0int.dispatch assign --jobs - --batch B --in-flight '{"opus": 2}' --ceiling '{"opus": 6, "ds": 2}'
python -m z0int.dispatch record < outcome.json        # status, schema_valid, seconds, nous_notice
python -m z0int.dispatch qa --job-id J --agree 0.8 --qa-lane opus
python -m z0int.dispatch batch start|end --batch B    # burn-pace + Nous cap snapshots
python -m z0int.dispatch report --batch B
```

`z0int dispatch …` is the same through the main CLI.

## Ledger

SQLite at `Z0INT_DISPATCH_LEDGER` (default `~/.z0int/state/dispatch/ledger.sqlite`):
`decisions` (lane, cost, quality estimate, alternatives and rejection reasons, capacity
timestamp), `outcomes` (status, schema validity, QA agreement, seconds, dollars) and
`batches` (burn-pace and Nous cap before/after). Outcomes are forwarded to Kerdoios
(`capacity observe`) so throttles cool a lane down for every caller.

## Consumer

The OSS factory kitchen runner (`tools/kitchen/lanes.py`, lane `dispatch`) asks `assign`
whenever slots free up and records every outcome.
