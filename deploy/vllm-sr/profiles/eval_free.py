#!/usr/bin/env python3
"""Rank free models for router profiles: accuracy, reliability, TTFT, decode speed, per task family.

Run under dsh-env-exec with the provider keys. One worker per model, tasks sequential with a per-provider
gap that stays inside published RPM. Appends every call to results.jsonl (resumable: done calls skip).
Usage: eval_free.py [--repeats 2] [--only provider/model,...]
"""
import argparse, json, os, ssl, threading, time, urllib.request
from pathlib import Path

from tasks import TASKS

HERE = Path(__file__).resolve().parent
CTX = ssl.create_default_context()
BASES = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", 2.5),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", 1.0),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", 2.0),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", 4.0),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", 7.0),
    # end-to-end through the deployed router's virtual models (no key; the router holds them)
    "router": (os.environ.get("VSR_URL", "http://10.43.216.55:8899/v1"), "VSR_KEY", 3.0),
}
MODELS = [
    "groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b", "groq/qwen/qwen3.8-27b",
    "cerebras/gpt-oss-120b", "cerebras/qwen-3.8-27b",
    "nvidia/deepseek-ai/deepseek-v4.1-flash", "nvidia/google/gemma-4-31b-it", "nvidia/minimaxai/minimax-m3.1-flash",
    "nvidia/moonshotai/kimi-k2.6", "nvidia/mistralai/mistral-large-4-1t-a52b", "nvidia/nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nvidia/nemotron-3-super-120b-a12b", "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/z-ai/glm-5.3-flash",
    "nvidia/stepfun-ai/step-5-preview",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter/google/gemma-4-31b-it:free",
    "openrouter/thinkingmachines/inkling:free", "openrouter/dots-studio/dots-3-note-preview:free",
    "openrouter/xingchen-agi/xing4.0-29b-a4b:free", "openrouter/nvidia/nemotron-3.5-super-vl:free",
    "gemini/gemini-3.8-flash", "gemini/gemini-3.5-flash", "gemini/gemini-3.5-flash-lite", "gemini/gemini-3.1-pro-preview",
]
LOCK = threading.Lock()


def call(provider, model, prompt, timeout=180):
    base, env, _ = BASES[provider]
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True,
            "max_tokens": 4096, "temperature": 0}
    if provider == "openrouter":
        body["provider"] = {"max_price": {"prompt": 0, "completion": 0, "request": 0}, "allow_fallbacks": False}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {os.environ.get(env, 'none')}", "Content-Type": "application/json",
                                          "User-Agent": "z0int-vllm-sr-eval/1"})
    t0 = time.time(); ttft = None; text = []; reasoning_chars = 0; usage = None
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            usage = ev.get("usage") or usage
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                piece = d.get("content") or ""
                rpiece = d.get("reasoning") or d.get("reasoning_content") or ""
                if (piece or rpiece) and ttft is None:
                    ttft = time.time() - t0
                text.append(piece); reasoning_chars += len(rpiece)
    total = time.time() - t0
    out = "".join(text)
    return {"ttft_s": ttft, "total_s": total, "text": out, "reasoning_chars": reasoning_chars, "usage": usage}


def worker(spec, repeats, results_path, done):
    provider, model = spec.split("/", 1)
    gap = BASES[provider][2]
    for rep in range(repeats):
        for task in TASKS:
            key = (spec, task["id"], rep)
            if key in done:
                continue
            row = {"model": spec, "provider": provider, "task": task["id"], "family": task["family"], "rep": rep,
                   "at": time.time()}
            try:
                r = call(provider, model, task["prompt"])
                row.update({k: r[k] for k in ("ttft_s", "total_s", "reasoning_chars", "usage")})
                row["out_chars"] = len(r["text"])
                row["ok"] = bool(r["text"].strip())
                try:
                    row["correct"] = bool(task["grade"](r["text"]))
                except Exception:
                    row["correct"] = False
                row["sample"] = r["text"][:300]
            except Exception as e:
                code = getattr(e, "code", None)
                row.update({"ok": False, "correct": False, "error": f"{type(e).__name__} {code or ''} {str(e)[:120]}"})
                if code == 429:
                    time.sleep(gap * 6)
            with LOCK, open(results_path, "a") as f:
                f.write(json.dumps(row) + "\n")
            time.sleep(gap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(HERE / "results.jsonl"))
    a = ap.parse_args()
    models = a.only.split(",") if a.only else MODELS
    done = set()
    if Path(a.out).exists():
        for l in open(a.out):
            r = json.loads(l)
            if r.get("ok") or "error" not in r or "429" not in r.get("error", ""):
                done.add((r["model"], r["task"], r["rep"]))
    threads = [threading.Thread(target=worker, args=(m, a.repeats, a.out, done)) for m in models]
    for t in threads: t.start()
    for t in threads: t.join()


if __name__ == "__main__":
    main()
