"""Modal backend for the SLM weight-editing eval: GPU forward passes and the editor's no-network Python sandbox.

Enabled with WSP_BACKEND=modal (session.gpu_job and sandbox.run dispatch here). groot keeps the editor API calls
and the canonical per-session work dir; everything that needs RAM or a GPU runs on Modal.

Isolation (same guarantees as the local bubblewrap backend):
- Each session's /work is mirrored into its OWN Modal Volume, so editors never see each other's notes or edits.
- The sandbox has block_network=True, no GPU, practice data baked read-only into the image, and the model read
  from a models-only volume (ensure_model downloads model files only; no datasets ever land there).
- Held-out evaluation outputs are written only under the run's eval dir, never into a session volume.

Mirror rules (work dir <-> session volume):
- Before each sandbox run, local files that changed since the last sync are uploaded.
- After it, volume files are downloaded except large teacher-forced arrays (fwd_*/*.npy), which stay remote:
  the editor analyses them in the sandbox, and groot never needs them. Files the editor deleted (drop_edit) are
  deleted locally.

Deploy once: `modal deploy benchmarks/weight_space/slm/modal_backend.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import modal
from modal.volume import FileEntryType

HERE = Path(__file__).resolve().parent
APP_NAME = "z0int-weight-space"
GPU = os.environ.get("WSP_MODAL_GPU", "L4")
SYNC_STATE = ".modal-sync.json"

_PRACTICE = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int")) / "cache" / "weight-space" / \
    "lm_understand-logs" / "exp8_math" / "practice"

gpu_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.14.0", "transformers==5.20.2", "accelerate==1.15.0", "flash-linear-attention==0.6.0",
                 "math-verify==0.9.0", "safetensors", "numpy", "huggingface_hub")
    .env({"HF_HOME": "/hf", "Z0INT_HOME": "/z0", "WSP_GEN_BATCH": os.environ.get("WSP_MODAL_BATCH", "32"),
          "TOKENIZERS_PARALLELISM": "false"})
    .add_local_file(str(HERE / "common.py"), "/root/slm/common.py")
    .add_local_file(str(HERE / "gpu.py"), "/root/slm/gpu.py")
)
sandbox_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.14.0", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install("numpy", "scipy", "scikit-learn", "pandas", "sympy", "safetensors", "tokenizers")
    .add_local_dir(str(_PRACTICE), "/practice")
)
hf_vol = modal.Volume.from_name("wsp-hf-models", create_if_missing=True)
app = modal.App(APP_NAME)


def _slm():
    sys.path.insert(0, "/root/slm")
    import common as C  # noqa: PLC0415
    import gpu as G  # noqa: PLC0415
    return C, G


@app.function(image=gpu_image, volumes={"/hf": hf_vol}, timeout=3600, cpu=2, memory=8192)
def ensure_model(target: str) -> str:
    C, _ = _slm()
    p = C.model_dir(target)
    hf_vol.commit()
    return model_rel(Path(p))


@app.function(image=gpu_image, gpu=None if GPU == "local" else GPU, volumes={"/hf": hf_vol}, timeout=3 * 3600, memory=32768)
def gpu_job(mode: str, req: dict, session_vol: str | None, out_rel: str | None) -> dict:
    """One generate / teacher_forced job. Edits come from the session volume. Small outputs are returned; if
    out_rel is set, all outputs are also written into the session volume at that path."""
    C, G = _slm()
    out = Path("/tmp/out")
    out.mkdir(parents=True, exist_ok=True)
    edit_dir = None
    if req.get("edit_dir") and session_vol:
        vol = modal.Volume.from_name(session_vol)
        dest = Path("/tmp/edited")
        dest.mkdir(parents=True, exist_ok=True)
        for e in vol.listdir("edited"):
            if e.path.endswith(".npy"):
                with open(dest / Path(e.path).name, "wb") as f:
                    for chunk in vol.read_file(e.path):
                        f.write(chunk)
        edit_dir = str(dest) if any(dest.glob("*.npy")) else None
    res = {"generate": G.generate, "teacher_forced": G.teacher_forced}[mode](dict(req, edit_dir=edit_dir), out)
    files = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file() and p.stat().st_size < 64 << 20}
    if out_rel and session_vol:
        with modal.Volume.from_name(session_vol).batch_upload(force=True) as up:
            for p in out.iterdir():
                up.put_file(str(p), f"{out_rel}/{p.name}")
    return {"result": res, "files": files, "edit_hash": C.edit_hash(Path(edit_dir)) if edit_dir else None}


# ------------------------------------------------------------------ client side (runs on groot)

def enabled() -> bool:
    return os.environ.get("WSP_BACKEND", "local") == "modal"


def remote_gpu() -> bool:
    """WSP_MODAL_GPU=local: sandbox on Modal, forward passes on groot's GPU (Modal GPUs need a payment method)."""
    return enabled() and GPU != "local"


def _work_of(p: Path) -> Path | None:
    for a in [p, *p.parents]:
        if a.name == "work":
            return a
    return None


def volume_name(work: Path) -> str:
    key = f"{work.parent.parent.name}/{work.parent.name}"
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")[:40]
    return f"wsp-{slug}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def _vol(work: Path) -> modal.Volume:
    return modal.Volume.from_name(volume_name(work), create_if_missing=True)


def _keep_remote(rel: str, size: int) -> bool:
    return (re.match(r"fwd_\d+/", rel) is not None and rel.endswith(".npy")) or size > 512 << 20


def _load_state(work: Path) -> dict:
    p = work / SYNC_STATE
    return json.loads(p.read_text()) if p.exists() else {"local": {}, "remote": {}}


def sync_up(work: Path) -> int:
    st, vol, n = _load_state(work), _vol(work), 0
    changed = []
    for p in sorted(work.rglob("*")):
        rel = p.relative_to(work).as_posix()
        if not p.is_file() or rel == SYNC_STATE:
            continue
        sig = [p.stat().st_size, p.stat().st_mtime_ns]
        if st["local"].get(rel) != sig:
            changed.append((p, rel, sig))
    if changed:
        with vol.batch_upload(force=True) as up:
            for p, rel, _ in changed:
                up.put_file(str(p), rel)
        for _, rel, sig in changed:
            st["local"][rel] = sig
            n += 1
    (work / SYNC_STATE).write_text(json.dumps(st))
    return n


def sync_down(work: Path) -> dict:
    st, vol = _load_state(work), _vol(work)
    seen, got, dropped = {}, 0, 0
    for e in vol.listdir("/", recursive=True):
        if e.type != FileEntryType.FILE:
            continue
        rel = e.path.lstrip("/")
        seen[rel] = [e.size, int(e.mtime)]
        if rel == SYNC_STATE or _keep_remote(rel, e.size) or st["remote"].get(rel) == seen[rel]:
            continue
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        with open(tmp, "wb") as f:
            for chunk in vol.read_file(rel):
                f.write(chunk)
        tmp.replace(dst)
        st["local"][rel] = [dst.stat().st_size, dst.stat().st_mtime_ns]
        got += 1
    for rel in list(st["remote"]):
        if rel not in seen:  # deleted in the sandbox (e.g. drop_edit)
            (work / rel).unlink(missing_ok=True)
            st["local"].pop(rel, None)
            dropped += 1
    st["remote"] = seen
    (work / SYNC_STATE).write_text(json.dumps(st))
    return {"downloaded": got, "deleted": dropped}


def remote_gpu_job(mode: str, req: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "request.json").write_text(json.dumps(req))
    edit_work = _work_of(Path(req["edit_dir"])) if req.get("edit_dir") else None
    out_work = _work_of(out)
    if edit_work is not None:
        sync_up(edit_work)
    if out_work is not None and edit_work is not None and out_work != edit_work:
        raise RuntimeError("output and edits belong to different sessions")
    work = edit_work or out_work
    out_rel = out.relative_to(out_work).as_posix() if out_work is not None else None
    fn = modal.Function.from_name(APP_NAME, "gpu_job")
    t0 = time.time()
    r = fn.remote(mode, {k: v for k, v in req.items() if k != "edit_dir"} | {"edit_dir": bool(req.get("edit_dir"))},
                  volume_name(work) if work is not None else None, out_rel)
    for name, data in r["files"].items():
        (out / name).write_bytes(data)
    res = r["result"] | {"backend": f"modal:{GPU}", "remote_s": round(time.time() - t0, 1),
                         "remote_edit_hash": r["edit_hash"]}
    (out / "result.json").write_text(json.dumps(res))
    if out_work is not None:  # the outputs exist in the volume too; record them so they are not re-uploaded
        st = _load_state(out_work)
        for p in out.iterdir():
            st["local"][p.relative_to(out_work).as_posix()] = [p.stat().st_size, p.stat().st_mtime_ns]
        (out_work / SYNC_STATE).write_text(json.dumps(st))
    return res


def model_rel(model_dir: Path) -> str:
    s = model_dir.as_posix()
    i = s.find("/hub/models--")
    if i < 0:
        raise RuntimeError(f"model dir is not an HF hub snapshot: {model_dir}")
    return s[i + 1:]


def sandbox_exec(script: str, work: Path, model_dir: Path, timeout: int) -> str:
    """Run one editor code block in a fresh no-network Modal Sandbox; /work is the session volume."""
    sync_up(work)
    sb = modal.Sandbox.create(app=modal.App.lookup(APP_NAME, create_if_missing=True), image=sandbox_image,
                              volumes={"/work": _vol(work), "/hf": hf_vol.read_only()}, block_network=True,
                              cpu=4.0, memory=16384, timeout=timeout + 300, workdir="/work")
    t0 = time.time()
    try:
        sb.exec("ln", "-sfn", f"/hf/{model_rel(model_dir)}", "/model").wait()
        p = sb.exec("python", "-I", "-c", script, timeout=timeout,
                    env={"OMP_NUM_THREADS": "4", "HOME": "/tmp", "PYTHONUNBUFFERED": "1"})
        stdout, stderr = p.stdout.read(), p.stderr.read()
        p.wait()
        out = stdout + (("\n[stderr]\n" + stderr[-4000:]) if stderr.strip() else "")
        if time.time() - t0 >= timeout - 1:
            out += f"\n[timed out after {timeout} s]"
        sb.exec("sync").wait()
    finally:
        sb.terminate()
        try:
            sb.wait(raise_on_termination=False)
        except Exception:  # noqa: BLE001
            pass
    sync_down(work)
    return out
