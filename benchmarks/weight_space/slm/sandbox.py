"""The editor's Python sandbox (bubblewrap, after the paper's harness/sandbox.py): no network, no GPU, a clean
environment, read-only /model and /practice, read-write /work kept between runs. Each code block runs in a fresh
process (state persists through files in /work, not variables). Prelude: NAMES, SHAPES, load, load_original,
save_edit, drop_edit, edited, tok, practice()."""
from __future__ import annotations

import json
import resource
import subprocess
import sys
import time
from pathlib import Path

TIMEOUT, MAX_OUT, MEM = 600, 10_000, 16 << 30

PRELUDE = r'''
import json, os, re, sys
import numpy as np, torch
from safetensors import safe_open
torch.set_grad_enabled(False)
np.set_printoptions(precision=4, suppress=True, linewidth=160)
_CFG = json.load(open("/work/.sandbox.json"))
MODEL, WORK, PRACTICE = "/model", "/work", "/practice"
_FILES = {}
for _f in sorted(os.listdir(MODEL)):
    if _f.endswith(".safetensors"):
        with safe_open(os.path.join(MODEL, _f), "pt") as _h:
            for _k in _h.keys():
                if re.search(_CFG["tensor_re"], _k):
                    _FILES[_k] = os.path.join(MODEL, _f)
NAMES = sorted(_FILES)
_FROZEN = re.compile(_CFG["frozen_re"])
def _shape(name):
    with safe_open(_FILES[name], "pt") as h:
        return tuple(h.get_slice(name).get_shape())
SHAPES = {n: _shape(n) for n in NAMES}
def load_original(name):
    with safe_open(_FILES[name], "pt") as h:
        return h.get_tensor(name).float()
def load(name):
    p = os.path.join(WORK, "edited", name + ".npy")
    return torch.from_numpy(np.load(p)) if os.path.exists(p) else load_original(name)
def save_edit(name, t):
    assert name in _FILES, f"unknown tensor {name}"
    assert not _FROZEN.search(name), f"refused: {name} is out of scope (final norm / embeddings / output layer)"
    t = torch.as_tensor(t).float()
    o = load_original(name)
    assert tuple(t.shape) == tuple(o.shape), (name, tuple(t.shape), tuple(o.shape))
    assert torch.isfinite(t).all(), "non-finite values"
    d = float((o * o).sum())
    if d > 0:
        c = float((t * o).sum()) / d
        if c != 1.0 and torch.allclose(t, c * o, rtol=1e-4, atol=1e-6):
            raise AssertionError("refused: an edit that only multiplies the whole tensor by a constant")
    os.makedirs(os.path.join(WORK, "edited"), exist_ok=True)
    np.save(os.path.join(WORK, "edited", name + ".npy"), t.numpy().astype(np.float32))
    return f"saved {name}: {int((t != o).sum())} of {o.numel()} entries differ"
def drop_edit(name):
    p = os.path.join(WORK, "edited", name + ".npy")
    if os.path.exists(p): os.remove(p)
def edited():
    d = os.path.join(WORK, "edited")
    return sorted(f[:-4] for f in os.listdir(d)) if os.path.isdir(d) else []
try:
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(os.path.join(MODEL, "tokenizer.json"))
except Exception as _e:
    tok = None
def practice(name):
    return [json.loads(l) for l in open(os.path.join(PRACTICE, name))]
'''


def model_view(model_dir: Path) -> Path:
    """A plain folder with the model's resolved files (reflink copies). Binding the HF hub instead would expose
    every cached dataset, including the held-out benchmarks."""
    view = Path(str(model_dir).replace("/", "_").strip("_"))
    view = Path.home() / ".z0int" / "state" / "weight-space" / "slm" / "view" / view.name[-80:]
    if not (view / ".done").exists():
        view.mkdir(parents=True, exist_ok=True)
        for f in model_dir.iterdir():
            subprocess.run(["cp", "--reflink=auto", str(f.resolve()), str(view / f.name)], check=True)
        (view / ".done").write_text("")
    return view


def _limits():
    resource.setrlimit(resource.RLIMIT_AS, (MEM, MEM))


def run(code: str, work: Path, model_dir: Path, practice_dir: Path, venv: Path, log: Path) -> str:
    py = venv / "bin" / "python"
    real_py = py.resolve()
    # the uv-managed interpreters the venv points at (bind the parent: the venv link goes through a version alias)
    pyroot = real_py.parent.parent.parent
    view = model_view(model_dir)
    cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
           "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
           "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
           "--ro-bind", str(venv), str(venv), "--ro-bind", str(pyroot), str(pyroot),
           "--ro-bind", str(view), "/model",
           "--ro-bind", str(practice_dir), "/practice", "--bind", str(work), "/work", "--chdir", "/work",
           "--setenv", "HOME", "/tmp", "--setenv", "PATH", "/usr/bin", "--setenv", "OMP_NUM_THREADS", "6",
           "--setenv", "CUDA_VISIBLE_DEVICES", "", "--setenv", "PYTHONUNBUFFERED", "1",
           str(py), "-I", "-c", PRELUDE + "\n" + _with_last_expr(code)]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=TIMEOUT, preexec_fn=_limits)
        out = p.stdout + (("\n[stderr]\n" + p.stderr[-4000:]) if p.stderr.strip() else "")
    except subprocess.TimeoutExpired:
        out = f"[timed out after {TIMEOUT} s]"
    out = out.encode("ascii", "replace").decode()
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + f"\n[output cut at {MAX_OUT:,} characters]"
    with open(log, "a") as f:
        f.write(json.dumps({"t": time.time(), "kind": "python", "s": round(time.time() - t0, 2), "code": code,
                            "out": out[:6000]}) + "\n")
    return out or "(no output)"


def _with_last_expr(code: str) -> str:
    """Show the value of a final expression, as a notebook would."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        src = ast.get_source_segment(code, last) or ""
        lines = code.splitlines()
        head = "\n".join(lines[:last.lineno - 1])
        return head + "\n_v = (" + src + ")\nif _v is not None: print(repr(_v)[:8000])\n"
    return code


if __name__ == "__main__":  # smoke: python sandbox.py WORK MODEL_DIR PRACTICE VENV
    w, m, pr, v = map(Path, sys.argv[1:5])
    print(run("len(NAMES), NAMES[:3]", w, m, pr, v, w / "tool_calls.jsonl"))
