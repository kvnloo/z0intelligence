"""Shared pieces of the SLM weight-editing eval (Experiment 8, scaled down): targets, data, grading, checkpoints.

Kept importable without torch/vLLM (the sandbox prelude and the session loop import it).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

Z0 = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int"))
STATE = Z0 / "state" / "weight-space" / "slm"
CACHE = Z0 / "cache" / "weight-space"
LOGS = CACHE / "lm_understand-logs"
PRACTICE = LOGS / "exp8_math" / "practice"
INSTRUCTION = "Please reason step by step, and put your final answer within \\boxed{}."

# Each target: HF id + pinned revision, chat mode, the model card's sampling for that mode, tensors the editor may not
# touch (final norm, embeddings, output layer: as in the paper), and the token caps of this scaled-down protocol.
TARGETS = {
    "qwen35-0.8b": {
        "hf": "Qwen/Qwen3.5-0.8B", "rev": "2fc06364715b967f1860aea9cf38778875588b17", "thinking": True,
        "sampling": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5},
        "frozen_re": r"(^|\.)(norm\.weight|embed_tokens\.weight)$|^lm_head\.weight$",
        "tensor_re": r"^model\.language_model\.",
        "practice_max_tokens": 4096, "eval_max_tokens": 4096,
        "about": "Qwen3.5-0.8B, thinking on (the paper's Experiment 8 target; its model card warns it can loop)",
    },
    "qwen3-0.6b": {
        "hf": "Qwen/Qwen3-0.6B", "rev": "c1899de289a04d12100db370d81485cdf75e47ca", "thinking": True,
        "sampling": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5},
        "frozen_re": r"(^|\.)(norm\.weight|embed_tokens\.weight)$|^lm_head\.weight$",
        "tensor_re": r"^model\.",
        "practice_max_tokens": 4096, "eval_max_tokens": 4096,
        "about": "Qwen3-0.6B, thinking on",
    },
    "gemma3-270m": {
        "hf": "unsloth/gemma-3-270m-it", "rev": "23cf460f6bb16954176b3ddcc8d4f250501458a9", "thinking": False,
        "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
        "frozen_re": r"(^|\.)(norm\.weight|embed_tokens\.weight)$|^lm_head\.weight$",
        "tensor_re": r"^model\.",
        "practice_max_tokens": 2048, "eval_max_tokens": 4096,
        "about": "Gemma 3 270M instruct (the paper's follow-up target: it does not loop; ungated unsloth mirror of google/gemma-3-270m-it)",
    },
    "functiongemma-270m": {
        "hf": "unsloth/functiongemma-270m-it", "rev": "79eaaa84125b897f5a0ad6638362b864fec813ad", "thinking": False,
        "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
        "frozen_re": r"(^|\.)(norm\.weight|embed_tokens\.weight)$|^lm_head\.weight$",
        "tensor_re": r"^model\.",
        "practice_max_tokens": 256, "eval_max_tokens": 256,
        "about": "FunctionGemma 270M (z0 slm-router bounded choice 51/84; picks dangerous near-misses); task = toolchoice.py",
    },
}

# Forward-pass limits of this scaled-down protocol (the paper: 512 generations, 1,024 texts, 32,768 tokens)
GEN_MAX = 64           # generations per pass (problems x n); "both" = GEN_MAX // 2 per model
TF_MAX_TEXTS = 64
TF_MAX_TOKENS = 2048
EVAL_N = 32            # pilot: fixed test problems per benchmark, one sample each; not a full-paper replication


def model_dir(target: str) -> Path:
    t = TARGETS[target]
    # Avoid importing the hub/downloader stack on every turn when the pinned snapshot is already local.
    home = Path(os.environ.get('HF_HOME', Path.home() / '.cache' / 'huggingface'))
    cached = home / 'hub' / ('models--' + t['hf'].replace('/', '--')) / 'snapshots' / str(t['rev'])
    if (cached / 'config.json').exists() and any(cached.glob('*.safetensors')):
        return cached
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(t["hf"], revision=t["rev"],
                                  allow_patterns=["*.json", "*.safetensors", "*.jinja", "*.model", "*.txt"]))


def practice(ids: list[str] | None = None) -> dict[str, dict]:
    rows = {}
    for f in ("gsm8k_train.jsonl", "math_train.jsonl", "aime.jsonl"):
        for line in (PRACTICE / f).read_text().split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            if ids is None or r["id"] in ids:
                rows[r["id"]] = r
    return rows


DATA_REVS = {"math500": None, "gsm8k": None}  # pinned by pin_data() on first use, recorded in every eval


def pin_data() -> dict:
    """Resolve and freeze the dataset revisions (written to CACHE/data_revs.json on first use)."""
    f = CACHE / "data_revs.json"
    if f.exists():
        DATA_REVS.update(json.loads(f.read_text()))
    else:
        from huggingface_hub import HfApi
        api = HfApi()
        DATA_REVS.update({"math500": api.dataset_info("HuggingFaceH4/MATH-500").sha,
                          "gsm8k": api.dataset_info("openai/gsm8k").sha})
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(DATA_REVS))
    return dict(DATA_REVS)


def eval_items() -> list[dict]:
    """A fixed, seed-chosen subset of MATH-500 and GSM8K test (never shown to the editor)."""
    pin_data()
    import random

    from huggingface_hub import hf_hub_download
    out = []
    p = hf_hub_download("HuggingFaceH4/MATH-500", "test.jsonl", repo_type="dataset", revision=DATA_REVS["math500"])
    m = [json.loads(x) for x in Path(p).read_text().split("\n") if x.strip()]
    for r in random.Random(0).sample(m, EVAL_N):
        out.append({"id": "math500:" + r["unique_id"], "source": "math500", "problem": r["problem"],
                    "answer": r["answer"]})
    import pyarrow.parquet as pq
    p = hf_hub_download("openai/gsm8k", "main/test-00000-of-00001.parquet", repo_type="dataset", revision=DATA_REVS["gsm8k"])
    g = pq.read_table(p).to_pylist()
    for i in sorted(random.Random(0).sample(range(len(g)), EVAL_N)):
        out.append({"id": f"gsm8k_test:{i}", "source": "gsm8k", "problem": g[i]["question"],
                    "answer": g[i]["answer"].split("####")[-1].strip().replace(",", "")})
    return out


# ------------------------------------------------------------------ grading (last \boxed{}, math-verify)
_BOX = re.compile(r"\\(?:boxed|fbox)\s*\{")


def last_boxed(text: str) -> str | None:
    if "</think>" in text:
        text = text.split("</think>")[-1]
    ms = list(_BOX.finditer(text))
    if not ms:
        return None
    i = ms[-1].end()
    depth, j = 1, i
    while j < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        j += 1
    return text[i:j - 1].strip() if depth == 0 else None


def is_correct(pred: str | None, gold: str) -> bool:
    if pred is None:
        return False
    try:
        from math_verify import parse, verify
        g, p = parse("\\boxed{" + gold + "}"), parse("\\boxed{" + pred + "}")
        if g and p and verify(g, p):
            return True
    except Exception:  # noqa: BLE001, S110 - fallback below is exact normalization, not an assumed pass
        pass
    norm = lambda s: re.sub(r"[\s$]|\\[!,;]|\\text\{[^}]*\}|\\left|\\right", "", s).rstrip(".")
    return norm(pred) == norm(gold)


def edit_hash(edit_dir: Path) -> str:
    h = hashlib.sha256()
    if edit_dir.is_dir():
        for f in sorted(edit_dir.glob("*.npy")):
            h.update(f.name.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:16]
