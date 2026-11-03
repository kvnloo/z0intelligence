"""Modal backend smoke: model download, base generate, sandbox save_edit round-trip, edited generate, drop_edit.

  WSP_BACKEND=modal .venv-wsp/bin/python benchmarks/weight_space/slm/modal_smoke.py [target]
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["WSP_BACKEND"] = "modal"
import modal  # noqa: E402

import common as C  # noqa: E402
import modal_backend as MB  # noqa: E402
import sandbox  # noqa: E402
from session import gpu_job  # noqa: E402

target = sys.argv[1] if len(sys.argv) > 1 else "gemma3-270m"
t = C.TARGETS[target]
t0 = time.time()
rel = modal.Function.from_name(MB.APP_NAME, "ensure_model").remote(target)
print("model on volume:", rel, f"{time.time()-t0:.0f}s", flush=True)

work = C.STATE / "modal-smoke" / f"{target}__smoke" / "work"
shutil.rmtree(work.parent, ignore_errors=True)
work.mkdir(parents=True)
(work / ".sandbox.json").write_text(json.dumps({"tensor_re": t["tensor_re"], "frozen_re": t["frozen_re"]}))
log = work.parent / "log.jsonl"
mdir = Path("/hf") / rel  # only model_rel() is used remotely

SKIP_GPU = os.environ.get("SMOKE_SKIP_GPU") == "1"
items = [{"id": f"p{i}", "problem": "What is 17 * 23?", "answer": "391", "seed": i} for i in range(4)]
req = {"target": target, "edit_dir": None, "items": items, "max_tokens": 512, "n": 1}
if not SKIP_GPU:
    r = gpu_job("generate", req, work / ".gpu" / "base")
    rows = [json.loads(x) for x in (work / ".gpu" / "base" / "gen.jsonl").read_text().split("\n") if x.strip()]
    print("base generate:", {k: r.get(k) for k in ("backend", "remote_s")}, "correct",
          sum(C.is_correct(C.last_boxed(x.get("text", "")), "391") for x in rows), "/", len(rows), flush=True)

out = sandbox.run("import urllib.request\ntry:\n    urllib.request.urlopen('https://example.com', timeout=5); print('NET OPEN')\n"
                  "except Exception as e:\n    print('net blocked:', type(e).__name__)\n"
                  "print(len(NAMES), NAMES[0], torch.cuda.is_available())\n"
                  "n=[x for x in NAMES if 'mlp.down_proj' in x][0]\nw=load(n)\nsave_edit(n, w*1.0)\n"
                  "open('/work/notes.md','w').write('smoke note')\nedited()", work, mdir, C.PRACTICE, Path("."), log)
print("sandbox:", out.strip()[:600], flush=True)
print("local edits after sync:", sorted(p.name for p in (work / "edited").glob("*.npy")), (work / "notes.md").exists())

if not SKIP_GPU:
    r2 = gpu_job("generate", dict(req, edit_dir=str(work / "edited")), work / ".gpu" / "edited")
    print("edited generate:", {k: r2.get(k) for k in ("backend", "remote_s", "remote_edit_hash")},
          "local hash", C.edit_hash(work / "edited"), flush=True)

out = sandbox.run("drop_edit(edited()[0])\nedited()", work, mdir, C.PRACTICE, Path("."), log)
print("drop:", out.strip()[:200], "local edits now:", sorted(p.name for p in (work / "edited").glob("*.npy")))
print(f"total {time.time()-t0:.0f}s")
