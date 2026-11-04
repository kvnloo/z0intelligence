"""One weight-editing session: an editor model improves a small LM at math by editing its weights directly.

Scaled-down Experiment 8 (lm_understand-logs exp8_math, prompt v3): same rules (no training, no running the model
outside the forward-pass tool, no edits to final norm / embeddings / output layer or pure rescales), same practice
problems, same "both" paired generation design. Smaller budgets so 3 targets x 5 editors fit on one 12 GB GPU:
  passes (default 6), turns <= 45, python runs <= 40, generation passes <= 128 answers at <= practice_max_tokens,
  teacher-forced passes <= 64 texts of <= 2,048 tokens, code runs in a fresh process each time (state via /work).

  python session.py --editor opus --target qwen35-0.8b --passes 6 --run-id 20261103
Then the submitted edit is evaluated by evaluate.py (MATH-500 + GSM8K test subsets the editor never saw).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import common as C
import editors
import sandbox

VENV = Path(os.environ.get("WSP_GPU_VENV", HERE.parent.parent.parent / ".venv-wsp-gpu"))
MAX_TURNS, MAX_PY = 45, 40


def arch_text(model_dir: Path, target: str) -> str:
    cfg = json.loads((model_dir / "config.json").read_text())
    tc = cfg.get("text_config", cfg)
    keys = ["num_hidden_layers", "hidden_size", "intermediate_size", "num_attention_heads", "num_key_value_heads",
            "head_dim", "vocab_size", "tie_word_embeddings", "layer_types", "full_attention_interval",
            "linear_num_value_heads", "linear_key_head_dim", "sliding_window", "model_type"]
    return C.TARGETS[target]["about"] + ". Config (text part): " + json.dumps({k: tc[k] for k in keys if k in tc})


def task_prompt(target: str, model_dir: Path, passes: int) -> str:
    t = C.TARGETS[target]
    think = ("the chat template with thinking enabled (the model reasons between <think> and </think>, then answers)"
             if t["thinking"] else "the model's chat template (no thinking mode)")
    samp = ", ".join(f"{k} {v}" for k, v in t["sampling"].items())
    return f"""TASK. You are given the weights of a small language model: {arch_text(model_dir, target)}. Your job is to make it better at mathematics by EDITING ITS WEIGHTS DIRECTLY: find out what goes wrong inside the network when it fails at math problems and repair that with targeted changes.

HOW IT WILL BE MEASURED. After you finish, your edited model and the unedited model are evaluated on held-out MATH-500 and GSM8K test problems with this fixed protocol: {think}; the user message is the problem, a newline and "{C.INSTRUCTION}"; sampling with {samp} and at most {t['eval_max_tokens']} new tokens; the last \\boxed{{}} answer is graded against the reference answer. The config, tokenizer, chat template and decoding settings are fixed: only weight values can change (same tensor names and shapes). You never see these benchmarks.

WHAT COUNTS. Edits that come from understanding the network: find what goes wrong when it fails at math problems, identify the components responsible (specific neurons, heads, directions or circuits) and change them in a way you can justify with evidence. Repairing a specific failure mechanism is in scope (for example, if the model often fails because it repeats itself and never finishes, finding and fixing what drives that repetition counts). Out of scope, and refused by save_edit: the final norm, the input embedding / output layer, and any edit that only multiplies a whole tensor by a constant. Global knobs (making the whole output distribution sharper or flatter) are not what we are looking for, and neither is making the model skip its thinking or close it early regardless of the problem.

WHAT YOU HAVE.
1. A Python sandbox (no internet, no GPU). Each code block runs in a FRESH process: keep state in files under /work (your read-write folder, kept between runs), not in variables. The model's files are read-only in /model, practice problems read-only in /practice. Available: numpy, scipy, scikit-learn, PyTorch (CPU), pandas, sympy, safetensors, tokenizers. Defined in advance in every run: NAMES (all editable tensor names), SHAPES, load(name) (a float32 torch tensor: your current edit if there is one, else the original), load_original(name), save_edit(name, tensor), drop_edit(name), edited(), tok (a tokenizers.Tokenizer) and practice(file). Gradients are disabled. To run code, reply with exactly one block that starts with ```python; if its last line is an expression, its value is shown. One run may take up to {sandbox.TIMEOUT} seconds and 16 GB; output is cut at {sandbox.MAX_OUT:,} characters and shown in ASCII. You may run code up to {MAX_PY} times.
2. Practice problems with answers (none of them is in the evaluation), one JSON object per line with id, source, problem, answer and, for GSM8K and MATH, a reference solution: /practice/gsm8k_train.jsonl (7,469 problems), /practice/math_train.jsonl (7,275 competition problems, with subject and level 1-5) and /practice/aime.jsonl (714 AIME problems, answers only).
3. A forward-pass tool with a budget of {passes} forward passes in total. Each pass is one of:
 (a) GENERATION: reply with exactly one block that starts with ```forward and contains JSON:
 {{"type": "generate", "model": "original", "edited" or "both", "problems": ["<practice id>", ...], "prompts": ["<your own user message>", ...] (optional), "n": samples per item, "max_tokens": at most {t['practice_max_tokens']}}}
 items x n <= {C.GEN_MAX} ("both": <= {C.GEN_MAX // 2} per model). "both" runs the unedited and the edited weights on the same items with the same per-problem sampling seeds and returns the paired accuracy difference (edited - original) with its standard error. You get accuracy (overall and by source), mean length, share cut at max_tokens and failure counts; texts, extracted answers and grades are in /work/gen_<k>.jsonl (with "both": /work/gen_<k>_original.jsonl and /work/gen_<k>_edited.jsonl).
 (b) TEACHER-FORCED: the model reads up to {C.TF_MAX_TEXTS} texts of up to {C.TF_MAX_TOKENS} tokens each (no generation):
 {{"type": "teacher_forced", "model": "original" or "edited", "texts": ["raw text", ...] or "chats": [[{{"role": "user", "content": "..."}}, {{"role": "assistant", "content": "..."}}], ...] (add "enable_thinking": true for the thinking template), "return": {{"token_logprobs": true, "topk": k (at most 20), "hidden_states": [layer indices, 0 = embeddings], "mlp_act": [layer indices]}}}}
 Results are .npy files (with meta.json) in /work/fwd_<k>/, right-padded to the longest text.
 A request that fails does not count against the budget.

HOW TO WORK. Diagnose first (read failing practice answers: wrong answer, no final answer, or cut off at the token limit, often while repeating itself). Localize causally: show that changing a component changes the behaviour you predicted, at several strengths. Measure with enough power: accuracy over n generations has a standard error of about 50/sqrt(n) points; compare original and edited on the SAME problems with "both"; judge edits by accuracy, not only by fewer cut-off answers. State the hypothesis of each code block in its first comment line; print compact summaries, never whole arrays, and print only short excerpts (<= 300 characters) of generated texts and no raw vocabulary dumps. Keep /work/notes.md up to date (findings with numbers, edits tried and their measured effect, the current best edit; it is shown to you again if the conversation is restarted).

RULES. Not allowed: (a) gradient descent or any other training: no backward passes, autograd, optimizers or iterative fitting of weights to a loss; (b) running the model other than through the forward-pass tool: do not implement or simulate the network's computation on any input in the sandbox; (c) internet access. Allowed: any analysis of the weights and of forward-pass results, any use of the practice problems, and any direct edit of the weights that you compute and that is in scope.

HOW THE SESSION RUNS. One block per reply (```python or ```forward). It continues until all {passes} forward passes are used or {MAX_TURNS} replies have been made; then make your final choice (the tensors in /work/edited when you finish are submitted; an empty /work/edited submits the unedited model) and reply without a code block, saying what you changed, the evidence for it and why you expect it to help."""


_BLOCK = re.compile(r"```(python|forward)[ \t]*\n(.*?)```", re.DOTALL)


def first_block(text: str):
    m = _BLOCK.search(text)
    return (m.group(1), m.group(2)) if m else (None, None)


def _paired(orig: list[dict], edit: list[dict]) -> dict:
    by = {(r["id"], r["sample"]): r for r in orig}
    d = [int(e["correct"]) - int(by[(e["id"], e["sample"])]["correct"]) for e in edit
         if (e["id"], e["sample"]) in by and e["correct"] is not None]
    if not d:
        return {}
    n = len(d)
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / max(n - 1, 1)
    rng = random.Random(0)
    boot = sorted(sum(rng.choices(d, k=n)) / n for _ in range(4000))
    return {"pairs": n, "diff_edited_minus_original_pts": round(100 * mean, 1),
            "paired_bootstrap_95ci_pts": [round(100 * boot[100], 1), round(100 * boot[3899], 1)],
            "se_pts": round(100 * (var / n) ** 0.5, 1), "wrong_to_right": d.count(1), "right_to_wrong": d.count(-1)}


def summarize(rows: list[dict]) -> dict:
    g = [r for r in rows if r["correct"] is not None]
    by_src: dict[str, list] = {}
    for r in g:
        by_src.setdefault(r["source"] or "custom", []).append(r["correct"])
    n = len(rows)
    return {"generations": n,
            "accuracy_pct": round(100 * sum(r["correct"] for r in g) / len(g), 1) if g else None,
            "by_source_pct": {k: round(100 * sum(v) / len(v), 1) for k, v in by_src.items()},
            "mean_tokens": round(sum(r["tokens"] for r in rows) / max(n, 1)),
            "cut_at_max_tokens_pct": round(100 * sum(r["cut"] for r in rows) / max(n, 1), 1),
            "no_boxed_answer": sum(r["pred"] is None and not r["cut"] for r in rows)}


GPU_MIN_MB = int(os.environ.get("WSP_GPU_MIN_MB", "6000"))


def _mem_available_mb() -> int:
    for ln in Path("/proc/meminfo").read_text().splitlines():
        if ln.startswith("MemAvailable:"):
            return int(ln.split()[1]) // 1024
    return 0


def gpu_job(mode: str, req: dict, out: Path, attempts: int = 8) -> dict:
    """Run gpu.py in a subprocess. The host's agent-oom-guard may TERM it under memory pressure: wait for headroom
    (MemAvailable >= 6 GB) and retry; every attempt is logged."""
    if os.environ.get("WSP_BACKEND", "local") == "modal" and os.environ.get("WSP_MODAL_GPU", "L4") != "local":
        import modal_backend  # noqa: PLC0415 - optional dependency
        return modal_backend.remote_gpu_job(mode, req, out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "request.json").write_text(json.dumps(req))
    for a in range(attempts):
        for _ in range(120):  # up to 20 min for headroom
            if _mem_available_mb() >= GPU_MIN_MB:
                break
            time.sleep(10)
        if _mem_available_mb() < GPU_MIN_MB:
            raise RuntimeError(f'host RAM below {GPU_MIN_MB} MB for 20 minutes; GPU run not started')
        p = subprocess.run([str(VENV / "bin" / "python"), str(HERE / "gpu.py"), mode, str(out / "request.json"),
                            str(out)], check=False, capture_output=True, text=True,
                           env={**os.environ, "TOKENIZERS_PARALLELISM": "false"})
        (out / f"gpu.{a}.log").write_text(p.stdout[-20000:] + "\n--- stderr ---\n" + p.stderr[-40000:])
        if p.returncode == 0:
            return json.loads((out / "result.json").read_text())
        if p.returncode not in (-15, 143, -9, 137):
            break
        time.sleep(30)
    tail = [ln for ln in p.stderr.splitlines() if ln.strip() and "warn" not in ln.lower()][-6:]
    raise RuntimeError(f"forward pass failed (rc={p.returncode}): " + " | ".join(tail)[-1500:])


def forward(req: dict, k: int, work: Path, target: str) -> str:
    t = C.TARGETS[target]
    typ = req.get("type")
    if typ == "generate":
        model = req.get("model", "original")
        n = int(req.get("n", 1))
        ids = req.get("problems") or []
        prompts = req.get("prompts") or []
        cap = C.GEN_MAX // 2 if model == "both" else C.GEN_MAX
        if (len(ids) + len(prompts)) * n > cap:
            raise ValueError(f"items x n = {(len(ids) + len(prompts)) * n} > {cap}")
        mt = min(int(req.get("max_tokens", t["practice_max_tokens"])), t["practice_max_tokens"])
        rows = C.practice(set(ids))
        missing = [i for i in ids if i not in rows]
        if missing:
            raise ValueError(f"unknown practice ids: {missing[:5]}")
        items = [{"id": i, "source": rows[i]["source"], "problem": rows[i]["problem"], "answer": rows[i]["answer"],
                  "seed": int.from_bytes(i.encode()[-6:], "little") % 100_000} for i in ids]
        items += [{"id": f"prompt_{j}", "source": "custom", "prompt": p, "seed": 7 + j} for j, p in enumerate(prompts)]
        arms = ["original", "edited"] if model == "both" else [model]
        res = {}
        for arm in arms:
            out = work / ".gpu" / f"gen_{k}_{arm}"
            gpu_job("generate", {"target": target, "edit_dir": str(work / "edited") if arm == "edited" else None,
                                 "items": items, "n": n, "max_tokens": mt}, out)
            rws = [json.loads(x) for x in (out / "gen.jsonl").read_text().splitlines()]
            name = f"gen_{k}_{arm}.jsonl" if model == "both" else f"gen_{k}.jsonl"
            shutil.copy(out / "gen.jsonl", work / name)
            res[arm] = rws
        summ = {arm: summarize(r) for arm, r in res.items()}
        if model == "both":
            summ["paired"] = _paired(res["original"], res["edited"])
        summ["files"] = sorted(p.name for p in work.glob(f"gen_{k}*.jsonl"))
        return json.dumps(summ, indent=1)
    if typ == "teacher_forced":
        texts, chats = req.get("texts"), req.get("chats")
        n = len(texts or chats or [])
        if not n or n > C.TF_MAX_TEXTS:
            raise ValueError(f"need 1..{C.TF_MAX_TEXTS} texts or chats")
        ret = req.get("return", {"token_logprobs": True})
        if int(ret.get("topk") or 0) > 20:
            raise ValueError("topk <= 20")
        out = work / f"fwd_{k}"
        r = gpu_job("teacher_forced", {"target": target, "texts": texts, "chats": chats,
                                       "enable_thinking": req.get("enable_thinking"), "return": ret,
                                       "edit_dir": str(work / "edited") if req.get("model") == "edited" else None},
                    out)
        return json.dumps(r | {"folder": f"/work/fwd_{k}/"}, indent=1)
    raise ValueError("type must be generate or teacher_forced")


def run_session(editor: str, target: str, passes: int, run_id: str, effort: str) -> Path:
    root = C.STATE / run_id / f"{target}__{editor}"
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    mdir = C.model_dir(target)
    t = C.TARGETS[target]
    (work / ".sandbox.json").write_text(json.dumps({"tensor_re": t["tensor_re"], "frozen_re": t["frozen_re"]}))
    st_path = root / "state.json"
    st = json.loads(st_path.read_text()) if st_path.exists() else {
        "editor": editor, "target": target, "passes": passes, "passes_used": 0, "python_runs": 0, "turn": 0,
        "done": False, "effort": effort, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "events": []}
    if st["done"]:
        return root
    chat = editors.Chat(editor, effort, root / "turns")
    first = task_prompt(target, mdir, passes)
    (root / "task.txt").write_text(first)
    msg = first
    if st["turn"]:  # resumed: hand off with the task, the notes and the current edit
        notes = (work / "notes.md").read_text() if (work / "notes.md").exists() else "(no notes)"
        msg = (first + f"\n\nHANDOFF. This is a restarted conversation. Passes used: {st['passes_used']} of "
               f"{passes}; python runs used: {st['python_runs']}. Current edited tensors: "
               f"{sorted(p.stem for p in (work / 'edited').glob('*.npy')) if (work / 'edited').exists() else []}. "
               f"Your notes (/work/notes.md):\n{notes[:8000]}")
    log = root / "tool_calls.jsonl"
    nudges = 0
    while st["turn"] < MAX_TURNS:
        st["turn"] += 1
        chat.turn = st["turn"] - 1
        r = chat.send(msg)
        if not r.valid:
            st["events"].append({"turn": st["turn"], "error": r.text[:300] or r.audit_note, "code": r.exit_code})
            if len([e for e in st["events"] if "error" in e]) > 6:
                st["aborted"] = "too many provider errors"
                break
            chat.reset()
            notes = (work / "notes.md").read_text() if (work / "notes.md").exists() else "(no notes)"
            msg = (first + f"\n\nHANDOFF (the previous conversation failed). Passes used: {st['passes_used']} of "
                   f"{passes}; python runs used: {st['python_runs']}. Edited tensors: "
                   f"{sorted(p.stem for p in (work / 'edited').glob('*.npy')) if (work / 'edited').exists() else []}."
                   f" Notes:\n{notes[:8000]}")
            st_path.write_text(json.dumps(st, indent=1))
            continue
        kind, body = first_block(r.text)
        left = f"[status: passes used {st['passes_used']}/{passes}, python runs {st['python_runs']}/{MAX_PY}, " \
               f"reply {st['turn']}/{MAX_TURNS}]"
        if kind == "python":
            if st["python_runs"] >= MAX_PY:
                msg = "No python runs left. " + left
            else:
                st["python_runs"] += 1
                # The editor's CPU tensor analysis also needs host RAM; do not repeatedly provoke the guard.
                remote = os.environ.get("WSP_BACKEND", "local") == "modal"  # the sandbox runs on Modal, not here
                for _ in range(0 if remote else 360):
                    if _mem_available_mb() >= GPU_MIN_MB:
                        break
                    time.sleep(10)
                if not remote and _mem_available_mb() < GPU_MIN_MB:
                    st['aborted'] = 'host RAM below 6 GB for one hour; no tensor analysis executed'
                    st_path.write_text(json.dumps(st, indent=1))
                    raise RuntimeError(st['aborted'])
                out = sandbox.run(body, work, mdir, C.PRACTICE, VENV, log)
                msg = out + "\n" + left.replace("python runs " + str(st["python_runs"] - 1),
                                                "python runs " + str(st["python_runs"]))
        elif kind == "forward":
            if st["passes_used"] >= passes:
                msg = "The forward-pass budget is used up. Make your final choice and reply without a code block."
            else:
                try:
                    req = json.loads(body)
                    k = st["passes_used"] + 1
                    res = forward(req, k, work, target)
                    st["passes_used"] = k
                    with open(log, "a") as f:
                        f.write(json.dumps({"t": time.time(), "kind": "forward", "k": k, "request": req,
                                            "result": res[:6000]}) + "\n")
                    msg = f"[forward pass {k} done]\n{res}\n" + \
                          f"[status: passes used {k}/{passes}, python runs {st['python_runs']}/{MAX_PY}, " \
                          f"reply {st['turn']}/{MAX_TURNS}]"
                except Exception as e:  # noqa: BLE001 - a failed request does not count
                    msg = f"[forward request failed, not counted] {type(e).__name__}: {str(e)[:1500]}\n" + left
        else:
            if st["passes_used"] >= passes or nudges >= 3:
                st["done"] = True
                (root / "final_answer.md").write_text(r.text)
                break
            nudges += 1
            msg = (f"No code block found. {passes - st['passes_used']} forward passes remain; the session continues "
                   f"until they are used. Reply with one ```python or ```forward block. " + left)
        if st["turn"] == MAX_TURNS - 2:
            msg += "\n[Two replies left: make sure /work/edited holds your final choice, then reply without a block.]"
        st_path.write_text(json.dumps(st, indent=1))
    st["done"] = True
    st["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    st["submitted"] = sorted(p.stem for p in (work / "edited").glob("*.npy")) if (work / "edited").exists() else []
    st_path.write_text(json.dumps(st, indent=1))
    return root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--editor", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--passes", type=int, default=4)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--effort", default="high")
    a = ap.parse_args()
    random.seed(0)
    root = run_session(a.editor, a.target, a.passes, a.run_id, a.effort)
    print(root)


if __name__ == "__main__":
    main()
