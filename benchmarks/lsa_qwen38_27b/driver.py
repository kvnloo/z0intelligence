"""Run one step of the paper's own pipeline in-process, inside the serving image (no Slurm, no submitit).

Called by modal_lsa.py as `/opt/lsa-venv/bin/python driver.py <mode> ...` after `eval "$(lsa env)"`.

  ruler    <root> <tokenizer> <lengths,comma> [jobs]   - the paper's RULER cells (pinned generators, 100 rows)
  accuracy <config.yaml> [override ...]                 - `lsa evaluate accuracy` minus the Slurm submit
  kernels  <config.yaml> [override ...]                 - `lsa evaluate kernels` minus the Slurm submit
  cut      <snapshot> <out> [layers]                    - `lsa kernels cut --weights` minus the Slurm submit

Every experiment goes through `lsa.evaluation.run.run_experiment`, the exact function a Slurm job of the paper's
harness calls (`run_on_first_task` -> `run_experiment`), so the server command line, the suite, the scoring and
the record are the harness's own.
"""
import os
import sys
import time
from pathlib import Path


def ruler(root: str, tokenizer: str, lengths: str, jobs: str = "8") -> None:
    from lsa.evaluation.accuracy.data import ruler as R  # noqa: PLC0415
    from lsa.evaluation.accuracy.data import ruler_card  # noqa: PLC0415

    card = ruler_card()
    wanted = [int(x) for x in lengths.split(",")]
    samples = {length: card.rows_at(length) for length in wanted}
    R.ensure()
    t = time.time()
    R.prepare_ruler(list(card.tasks), wanted, samples, os.path.abspath(root), tokenizer, False, int(jobs))
    print(f"ruler prepared {wanted} in {time.time() - t:.0f}s", flush=True)
    bad = R.check(list(card.tasks), wanted, os.path.abspath(root))
    print(f"ruler check problems: {bad}", flush=True)
    if bad:
        raise SystemExit(1)


def cut(snapshot: str, out: str, layers: str = "5") -> None:
    """`lsa kernels cut --layers N --weights` minus the Slurm submit: the same `cut` function, in-process."""
    from lsa.evaluation.performance.kernels.data.cut import cut as do_cut  # noqa: PLC0415

    print(do_cut(snapshot, out, int(layers), True), flush=True)


def evaluate(kind: str, config: str, overrides: list[str]) -> None:
    from lsa.evaluation.config import load  # noqa: PLC0415
    from lsa.evaluation.run import describe, require_image, require_output, run_experiment  # noqa: PLC0415

    experiments = load(config, overrides, kind)
    for experiment in experiments:
        require_output(experiment)
        require_image(experiment)
        experiment.run.output = str(Path(experiment.run.output).resolve())
        print(describe(experiment), flush=True)
        t = time.time()
        out = run_experiment(experiment, force=False)
        print(f"EXPERIMENT DONE {experiment.name} -> {out} in {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    mode, *rest = sys.argv[1:]
    if mode == "ruler":
        ruler(*rest)
    elif mode == "cut":
        cut(*rest)
    elif mode in ("accuracy", "kernels", "production"):
        evaluate(mode, rest[0], rest[1:])
    else:
        raise SystemExit(f"unknown mode {mode}")
