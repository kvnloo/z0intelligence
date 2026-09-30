# Addendum: results of the pre-registered groot admission run (2026-09-30)

The pre-registration is `PREREGISTRATION.md`, commit 5d28db7, pushed before any scored item was
run. The machine-readable summary is `results.json`. The raw rows are `groot_qwen3_14b_q4.jsonl`
and `groot_qwen3_8b_q6.jsonl`, each with its `.log`.

## Outcome
| Arm | Accuracy | Confident errors (≥0.9) | Warm p50 | Warm p90 | Cold row 1 | VRAM (llama-server) | Bar |
|---|---|---|---|---|---|---|---|
| Baseline: 8B Q4_K_M | 43/48 | 1 | 89.8 ms | 107 ms | 15.0 s | ~6.0 GB | not admitted |
| **A. 14B Q4_K_M** | **44/48** | **1** | **106.7 ms** | 120 ms | 17.6 s | 9.88 GB (10.6 GB card total) | **ADMITTED, exactly at the bar** |
| B. 8B Q6_K | 43/48 | 1 | 83.9 ms | 92 ms | 11.4 s | 7.47 GB | not admitted (accuracy 43 < 44) |

For comparison, Jev scores 46/48.

## Honest reading
- The 14B clears the bar by the minimum possible margin: 44 correct, where the bar is 44. Against the
  8B it differs on 5 items:
  - It fixes 3 (`1105577d`, `b863998d`, and `6149a17b`, which the 8B got wrong at 0.48 confidence).
  - It breaks 2 (`84318570`: gold insufficient, answered contradicted; `0ebd2997`: the error moves
    from insufficient to contradicted).
  - With n = 48 this is not evidence that the 14B is a better model than the 8B. It only shows the
    14B passed the gate that was agreed in advance.
- Its errors lean toward contradicted: all 4 of the 14B's errors predict "contradicted". The one
  confident error, `6149a17b`, is supported→contradicted at 0.944. That error is new; the 8B had
  the same item wrong but at 0.48 confidence. The 8B's confident error (`37cebc37`, 0.996) becomes
  a 0.79 error for the 14B. So the confident-error count holds at 1, but on a different item.
- Q6_K vs Q4_K_M on the 8B does not help:
  - The score is the same 43/48.
  - It errs on a different item (`f46f392e`) and fixes `6149a17b`.
  - It is slightly faster, probably because of different kernels. That is not a quality lever
    here.

## Co-residency with qwen3-8b-q4km: no, and it fails loudly (operational hazard)
- With the 8B resident, a request for the 14B returns HTTP 500 "failed to load" after about 9 s.
  - The cause is a `cudaMalloc` out-of-memory on an 8.16 GB buffer. Because `-ngl 99` is set by the
    user, `common_fit_params` aborts instead of partially offloading.
  - The 8B stays loaded and keeps serving.
- The reverse is symmetric: with the 14B resident, an 8B request returns HTTP 500.
- The router does not evict a resident model to make room while it is under `--models-max 2`. The
  second model keeps failing until the first is unloaded (`POST /models/unload`) or idle-sleeps
  after 900 s.
- In practice, an explicit 14B call can make every automatic groot call (the 8B default) fail for
  up to 15 minutes, and the other way around.
- Also unchanged by this work: Ollama on groot shares the same card.

## Deviations from the pre-registration
1. The first arm-A invocation crashed on import before any request was made: `tokenomics` was
   missing from the fresh venv. No row was written. After installing the pinned `[test]` extra,
   the arm ran once.
2. The 14B was unloaded between arm A and arm B so that B's load would not hit an out-of-memory
   error against it. This is an infrastructure step only; the scored procedure was unchanged.

There were no transport resumes, no reruns, and no prompt, readout, or server-flag changes.

## Follow-through (per the pre-registration)
- An evidenced $0 route `groot/qwen3-14b-q4km` was added to `~/.z0int/config/worker_routing.local.json`.
  - The file was backed up first as `.bak-groot14b-1790807550`.
  - The route cites `~/.z0int/evidence/groot-qwen3-14b-q4km.json` by sha256. That evidence file
    records the live call, the GGUF sha256, the bench path and sha256, and the co-residency caveat.
- A routed completion was verified through `delegate_worker(provider=groot, model=qwen3-14b-q4km)`
  on the `feat/groot-offload-provider` code: ok, output "ok", 163 ms attempt latency, free_only,
  canonical receipt written.
- **`local_order` and the groot default model (`qwen3-8b-q4km`) are unchanged.** Automatic
  local-only planning still resolves to groot/qwen3-8b-q4km. The 14B is reachable only by explicit
  selection.
- Before any later promotion of the 14B to default, one of two things has to happen:
  - the router must evict on OOM, or
  - farm policy must hold only one of 8B/14B at a time (for example `--models-max 1`, or dropping
    the 8B route when the 14B becomes the default).
  Otherwise the 500s described above will occur.
