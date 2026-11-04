"""GPU side of the SLM weight-editing eval: checkpoint building, generation (vLLM), teacher-forced reads (HF).

Run as a subprocess (one job, one process, so GPU memory is returned when it exits), under a host-wide GPU lock:
  python gpu.py generate REQ.json OUT_DIR      REQ: {target, edit_dir|null, items:[{id,problem|prompt,answer?,seed}],
                                                     max_tokens, n}
  python gpu.py teacher_forced REQ.json OUT_DIR REQ: {target, edit_dir|null, texts|chats, enable_thinking, return:{...}}
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

LOCK = C.STATE / "gpu.lock"


def checkpoint(target: str, edit_dir: str | None) -> Path:
    """Base model dir, or a cached copy with the edited tensors swapped in (cast to the base dtype)."""
    base = C.model_dir(target)
    if not edit_dir or not any(Path(edit_dir).glob("*.npy")):
        return base
    h = C.edit_hash(Path(edit_dir))
    out = C.STATE / "ckpt" / target / h
    if (out / "DONE").exists():
        return out
    import numpy as np
    import torch
    from safetensors.torch import load_file, save_file
    tmp = out.with_suffix(".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    edits = {f.stem: f for f in Path(edit_dir).glob("*.npy")}
    import re
    bad = [k for k in edits if re.search(C.TARGETS[target]["frozen_re"], k) or not re.search(C.TARGETS[target]["tensor_re"], k)]
    assert not bad, f"refused out-of-scope tensors: {bad[:5]}"
    used = set()
    for f in base.iterdir():
        if f.suffix == ".safetensors":
            sd = load_file(str(f))
            for k in list(sd):
                if k in edits:
                    new = torch.from_numpy(np.load(edits[k])).to(sd[k].dtype)
                    assert new.shape == sd[k].shape, (k, new.shape, sd[k].shape)
                    sd[k] = new.contiguous()
                    used.add(k)
            save_file(sd, str(tmp / f.name), metadata={"format": "pt"})
        else:
            shutil.copy2(f.resolve(), tmp / f.name)
    missing = set(edits) - used
    assert not missing, f"edited tensors not in the model: {sorted(missing)[:5]}"
    (tmp / "DONE").write_text(json.dumps({"edit_hash": h, "tensors": sorted(used)}))
    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)
    return out


def _chat(tok, target, user, enable_thinking=None):
    th = C.TARGETS[target]["thinking"] if enable_thinking is None else enable_thinking
    kw = {"enable_thinking": th} if "qwen" in target else {}
    return tok.apply_chat_template([{"role": "user", "content": user}], tokenize=False, add_generation_prompt=True, **kw)


DEVICE = os.environ.get("WSP_DEVICE", "cuda")  # "cpu" for CPU-only workers (e.g. the MBP fleet for a 270M model)


def _load_hf(ck: Path):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(str(ck))
    dev = DEVICE
    dt = torch.bfloat16 if dev == "cuda" else torch.float32  # CPU: fp32 (bf16 matmuls are slow/unsupported on many CPUs)
    model = AutoModelForCausalLM.from_pretrained(str(ck), dtype=dt).to(dev).eval()
    return tok, model


def generate(req: dict, out: Path) -> dict:
    """HF transformers generation (vLLM's host-RAM footprint does not fit this host's headroom). Batches are formed
    in a fixed order and seeded per batch, so "original" and "edited" passes over the same items share their random
    streams. Presence penalty is the OpenAI/vLLM rule: subtract it once from every token already generated."""
    import torch
    from transformers import LogitsProcessor, LogitsProcessorList
    t = C.TARGETS[req["target"]]
    ck = checkpoint(req["target"], req.get("edit_dir"))
    tok, model = _load_hf(ck)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    samp = dict(t["sampling"])
    pp = float(samp.pop("presence_penalty", 0.0))

    class Presence(LogitsProcessor):
        def __init__(self, start):
            self.start = start

        def __call__(self, input_ids, scores):
            gen = input_ids[:, self.start:]
            if pp and gen.shape[1]:
                mask = torch.zeros_like(scores, dtype=torch.bool)
                mask.scatter_(1, gen, True)
                scores = scores - pp * mask.to(scores.dtype)
            return scores

    jobs = []
    for it in req["items"]:
        if it.get("messages"):  # tool-calling items (toolchoice.py): the model's own chat template renders the tools
            text = tok.apply_chat_template(it["messages"], tools=it.get("tools"), tokenize=False, add_generation_prompt=True)
        else:
            text = _chat(tok, req["target"], it.get("prompt") or (it["problem"] + "\n" + C.INSTRUCTION))
        for k in range(req.get("n", 1)):
            jobs.append((it, k, text))
    bs = int(os.environ.get("WSP_GEN_BATCH", "2"))
    rows, t0 = [], time.time()
    eos = [i for i in {tok.eos_token_id, *(model.generation_config.eos_token_id if isinstance(
        model.generation_config.eos_token_id, list) else [model.generation_config.eos_token_id])} if i is not None]
    for b0 in range(0, len(jobs), bs):
        chunk = jobs[b0:b0 + bs]
        enc = tok([j[2] for j in chunk], return_tensors="pt", padding=True, add_special_tokens=False).to(DEVICE)
        L = enc.input_ids.shape[1]
        torch.manual_seed(1_000_003 * (b0 // bs + 1) + int(req.get("seed", 0)))
        with torch.no_grad():
            o = model.generate(**enc, max_new_tokens=req["max_tokens"], do_sample=True, eos_token_id=eos,
                               pad_token_id=tok.pad_token_id, logits_processor=LogitsProcessorList([Presence(L)]),
                               **samp)
        for (it, k, _), seq in zip(chunk, o[:, L:].tolist()):
            stop = next((j for j, token in enumerate(seq) if token in eos), None)
            ended = stop is not None
            n = stop + 1 if stop is not None else len(seq)
            text = tok.decode(seq[:n], skip_special_tokens=False)
            for special in (tok.eos_token, tok.pad_token):
                if special:
                    text = text.replace(special, '')
            pred = None if it.get("messages") else C.last_boxed(text)
            gold = it.get("answer")
            rows.append({"id": it["id"], "sample": k, "source": it.get("source"), "text": text, "tokens": n,
                         "cut": (not ended) and n >= req["max_tokens"], "pred": pred,
                         "correct": (C.is_correct(pred, gold) if gold is not None else None)})
        print(f"batch {b0 // bs + 1}/{-(-len(jobs) // bs)} {time.time() - t0:.0f}s", flush=True)
    with open(out / "gen.jsonl", "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    return {"generations": len(rows), "wall_s": round(time.time() - t0, 1), "checkpoint": str(ck), "engine": "hf"}


def teacher_forced(req: dict, out: Path) -> dict:
    import numpy as np
    import torch
    ck = checkpoint(req["target"], req.get("edit_dir"))
    tok, model = _load_hf(ck)
    texts = req.get("texts")
    if texts is None:
        kw = {"enable_thinking": bool(req.get("enable_thinking"))} if "qwen" in req["target"] else {}
        texts = [tok.apply_chat_template(c, tokenize=False, **kw) for c in req["chats"]]
    ret = req.get("return", {})
    layers = model.model.language_model.layers if hasattr(model.model, "language_model") else model.model.layers
    mlp_layers = ret.get("mlp_act") or []
    store: dict[int, torch.Tensor] = {}
    hooks = [layers[i].mlp.down_proj.register_forward_pre_hook(
        lambda m, a, i=i: store.__setitem__(i, a[0].detach().float().cpu())) for i in mlp_layers]
    lps, hs, mlps, lens = [], {l: [] for l in ret.get("hidden_states") or []}, {l: [] for l in mlp_layers}, []
    topk = int(ret.get("topk") or 0)
    tk_ids, tk_lp = [], []
    with torch.no_grad():
        for text in texts:
            ids = tok(text, return_tensors="pt", truncation=True, max_length=C.TF_MAX_TOKENS).input_ids.to(DEVICE)
            o = model(ids, output_hidden_states=bool(hs))
            logp = torch.log_softmax(o.logits[0, :-1].float(), -1)
            lps.append(logp.gather(1, ids[0, 1:, None])[:, 0].cpu().numpy())
            if topk:
                v, ix = logp.topk(topk, -1)
                tk_ids.append(ix.cpu().numpy())
                tk_lp.append(v.cpu().numpy())
            for l, values in hs.items():
                values.append(o.hidden_states[l][0].float().cpu().numpy())
            for l, values in mlps.items():
                values.append(store[l][0].numpy())
            lens.append(ids.shape[1])
    for h in hooks:
        h.remove()
    L = max(lens)

    def pad(arrs, extra=()):
        a = np.zeros((len(arrs), L) + tuple(extra), dtype=np.float32)
        for i, x in enumerate(arrs):
            a[i, :len(x)] = x
        return a
    if ret.get("token_logprobs", True):
        np.save(out / "token_logprobs.npy", pad(lps))  # position t = log p(token t+1 | prefix)
    if topk:
        np.save(out / "topk_ids.npy", pad(tk_ids, (topk,)).astype(np.int64))
        np.save(out / "topk_logprobs.npy", pad(tk_lp, (topk,)))
    for l, v in hs.items():
        np.save(out / f"hidden_{l}.npy", pad(v, (v[0].shape[-1],)).astype(np.float16))
    for l, v in mlps.items():
        np.save(out / f"mlp_act_{l}.npy", pad(v, (v[0].shape[-1],)).astype(np.float16))
    meta = {"lengths": lens, "files": sorted(p.name for p in out.glob("*.npy")),
            "note": "right-padded; token_logprobs[i, t] = log p(token t+1); hidden_l = residual after layer l "
                    "(0 = embeddings); mlp_act_l = input of layer l's down_proj; float16"}
    (out / "meta.json").write_text(json.dumps(meta))
    return {"texts": len(texts), "tokens": int(sum(lens)), "files": meta["files"]}


def main():
    mode, req_path, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    req = json.loads(req_path.read_text())
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lk:
        t0 = time.time()
        fcntl.flock(lk, fcntl.LOCK_EX)
        waited = time.time() - t0
        res = {"generate": generate, "teacher_forced": teacher_forced}[mode](req, out)
    res["lock_wait_s"] = round(waited, 1)
    (out / "result.json").write_text(json.dumps(res))
    print(json.dumps(res))


if __name__ == "__main__":
    main()
