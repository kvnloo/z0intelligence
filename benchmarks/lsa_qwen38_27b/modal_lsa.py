"""Latent Sparse Attention (arXiv:2610.42147) on Qwen3.8-27B, run with the authors' own harness on Modal H200.

Faithfulness contract: the image is the paper's serving image (`ghcr.io/prefrontal-ai/sglang:lsa-v0.1.0`), the
code is Prefrontal-AI/latent-sparse-attention at a pinned commit, the model is Qwen/Qwen3.8-27b at the revision
the repo's asset card pins, the maps are the authors' `d-lsa-weights-v1` release, and every scored/timed run goes
through the repo's own `run_experiment` with the repo's own config files. The only substitutions are the launch
layer (Modal instead of Slurm+enroot; see docker_shim.py and driver.py) and the overrides passed on the command
line, which are recorded in each run's meta.json by the harness itself.

Steps (each `modal run --detach benchmarks/lsa_qwen38_27b/modal_lsa.py::<step>`):
  probe        image sanity: python, sglang, plugin import, GPU
  fetch        model snapshot + released maps into the volumes (CPU)
  ruler        RULER 4k/32k cells for Qwen's tokenizer (CPU)
  evaluate     one config on one H200: --kind accuracy|kernels --config <repo-relative yaml> --overrides "a=b c=d"
  status       list records and logs on the volume
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
LSA_REPO = "https://github.com/Prefrontal-AI/latent-sparse-attention"
LSA_SHA = "97b82d695b74ca341965a16bbc2a951c8b0c567a"
IMAGE = "ghcr.io/prefrontal-ai/sglang:lsa-v0.1.0"
MODEL_REPO = "Qwen/Qwen3.8-27b"
MODEL_REV = "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
GPU = os.environ.get("LSA_MODAL_GPU", "H200")
VENV = "/opt/lsa-venv"

image = (
    modal.Image.from_registry(IMAGE)
    .run_commands(
        "python3 -m pip install --quiet uv==0.11.7 || pip install --quiet uv==0.11.7",
        f"git clone --quiet {LSA_REPO} /opt/lsa && cd /opt/lsa && git checkout --quiet {LSA_SHA}",
        f"cd /opt/lsa && UV_PROJECT_ENVIRONMENT={VENV} uv sync --frozen --extra evaluation --python 3.12",
        "cd /opt/lsa && python3 -m pip install --no-deps --quiet .",
    )
    .add_local_file(str(HERE / "docker_shim.py"), "/usr/local/bin/docker", copy=True)
    .add_local_file(str(HERE / "site_modal.yaml"), "/opt/lsa/configs/site/modal.yaml", copy=True)
    .add_local_file(str(HERE / "driver.py"), "/opt/z0/driver.py", copy=True)
    # The harness client (lsa CLI, suite, graders) lives in its own venv; the server keeps the image's python,
    # which `python -m sglang...` inside the launch line must resolve to - so only `lsa` is linked onto PATH
    .run_commands("chmod +x /usr/local/bin/docker", f"ln -sf {VENV}/bin/lsa /usr/local/bin/lsa",
                  # the copy pip installs beside sglang resolves sites relative to site-packages
                  "mkdir -p /opt/sglang/lib/python3.12/configs/site && "
                  "cp /opt/lsa/configs/site/modal.yaml /opt/sglang/lib/python3.12/configs/site/modal.yaml")
    .env({"LSA_SITE": "modal", "LSA_ROOT": "/lsa", "HF_HOME": "/hf", "PYTHONUNBUFFERED": "1"})
)
root_vol = modal.Volume.from_name("lsa-root", create_if_missing=True)
hf_vol = modal.Volume.from_name("lsa-hf", create_if_missing=True)
VOLS = {"/lsa": root_vol, "/hf": hf_vol}
app = modal.App("z0int-lsa-qwen38-27b")


def _sh(cmd: str, log: str | None = None, commit_every: int = 0) -> int:
    """Run a shell line with the harness's environment contract (`lsa env`), tee to a volume log."""
    line = f'cd /opt/lsa && eval "$({VENV}/bin/lsa env)" && export HF_HUB_OFFLINE=${{HF_HUB_OFFLINE:-1}} && {cmd}'
    print(f"$ {cmd}", flush=True)
    stop = threading.Event()
    if commit_every:
        def _committer() -> None:
            while not stop.wait(commit_every):
                try:
                    root_vol.commit()
                except Exception as e:  # noqa: BLE001
                    print(f"[commit] {e}", flush=True)
        threading.Thread(target=_committer, daemon=True).start()
    fh = open(log, "a") if log else None  # noqa: SIM115
    p = subprocess.Popen(["bash", "-lc", line], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert p.stdout
    for out in p.stdout:
        print(out, end="", flush=True)
        if fh:
            fh.write(out)
            fh.flush()
    rc = p.wait()
    stop.set()
    if fh:
        fh.write(f"\n[exit {rc}]\n")
        fh.close()
    root_vol.commit()
    return rc


@app.function(image=image, gpu=GPU, volumes=VOLS, timeout=1200)
def probe() -> None:
    _sh("python3 -c 'import sys,sglang,torch;print(sys.version);print(\"sglang\",sglang.__version__,\"torch\",torch.__version__,torch.version.cuda)'"
        " && python3 -c 'import lsa.sglang.plugin as p;print(\"plugin ok\",p.__file__)'"
        " && lsa --help | head -20 && nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv"
        " && which python python3 && git -C /opt/lsa rev-parse HEAD && df -h /lsa /hf")


@app.function(image=image, volumes=VOLS, timeout=6 * 3600, cpu=8, memory=16384)
def fetch() -> None:
    os.makedirs("/lsa/models/maps", exist_ok=True)
    os.makedirs("/lsa/logs", exist_ok=True)
    rel = f"{LSA_REPO}/releases/download/d-lsa-weights-v1"
    maps = ["qwen38_27b__long-16k-1M-heavy-key-and-query-d2048.pt",
            "qwen38_27b__long-16k-1M-heavy-key-and-query-pca-step0.pt"]
    dl = " && ".join(f"curl -fsSL -o {m} {rel}/{m}" for m in [*maps, "SHA256SUMS"])
    rc = _sh(f"cd /lsa/models/maps && {dl} && sha256sum --check --ignore-missing SHA256SUMS", "/lsa/logs/fetch.log")
    if rc:
        raise SystemExit(rc)
    rc = _sh(f"HF_HUB_OFFLINE=0 HF_HUB_ENABLE_HF_TRANSFER=0 hf download {MODEL_REPO} --revision {MODEL_REV} "
             f"--max-workers 16 > /lsa/logs/hf.log 2>&1; tail -3 /lsa/logs/hf.log; "
             f"mkdir -p /hf/hub/models--Qwen--Qwen3.8-27b/refs && printf {MODEL_REV} > /hf/hub/models--Qwen--Qwen3.8-27b/refs/main && "
             f"ls -laL /hf/hub/models--Qwen--Qwen3.8-27b/snapshots/{MODEL_REV} | head -40", "/lsa/logs/fetch.log")
    hf_vol.commit()
    if rc:
        raise SystemExit(rc)


@app.function(image=image, volumes=VOLS, timeout=6 * 3600, cpu=32, memory=65536)
def ruler(lengths: str = "4096,32768", jobs: int = 16) -> None:
    rc = _sh(f"HF_HUB_OFFLINE=1 {VENV}/bin/python /opt/z0/driver.py ruler /lsa/data/benchmarks-qwen38 {MODEL_REPO} {lengths} {jobs}",
             "/lsa/logs/ruler.log", commit_every=300)
    if rc:
        raise SystemExit(rc)


@app.function(image=image, gpu=GPU, volumes=VOLS, timeout=24 * 3600, cpu=16, memory=131072)
def evaluate(kind: str, config: str, overrides: str = "", tag: str = "") -> None:
    os.makedirs("/lsa/records", exist_ok=True)
    name = tag or Path(config).stem
    log = f"/lsa/logs/{kind}-{name}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    ov = " ".join(shlex.quote(o) for o in ["run.runner=docker", *shlex.split(overrides)])
    rc = _sh(f"cd /lsa/records && {VENV}/bin/python /opt/z0/driver.py {kind} /opt/lsa/{config} {ov}", log, commit_every=180)
    if rc:
        raise SystemExit(rc)


@app.function(image=image, volumes=VOLS, timeout=3 * 3600, cpu=8, memory=98304)
def kernel_prep() -> None:
    """The kernel configs' inputs: the five-layer real-weight cut (the repo's own `lsa kernels cut`) and a
    ~300k-token Gutenberg prompt. The paper's `gutenberg_300k.txt` is not released; War and Peace (PG #2600,
    public domain) cut to 300,000 Qwen tokens stands in for it, and the harness repeats it to each context."""
    snap = f"/hf/hub/models--Qwen--Qwen3.8-27b/snapshots/{MODEL_REV}"
    rc = _sh(f"test -d /lsa/models/qwen38_27b_real5 || {VENV}/bin/python /opt/z0/driver.py cut {snap} "
             f"/lsa/models/qwen38_27b_real5 5; ls -la /lsa/models/qwen38_27b_real5", "/lsa/logs/kernel_prep.log")
    if rc:
        raise SystemExit(rc)
    rc = _sh("mkdir -p /lsa/data/prompts && cd /lsa/data/prompts && "
             "curl -fsSL -o pg2600.txt https://www.gutenberg.org/cache/epub/2600/pg2600.txt && sha256sum pg2600.txt && "
             f"python -c \"from transformers import AutoTokenizer as A;t=A.from_pretrained('{snap}');"
             "s=open('pg2600.txt',encoding='utf-8').read();s=s[s.index('*** START'):];i=t(s)['input_ids'];"
             "print('tokens',len(i));open('gutenberg_300k.txt','w').write(t.decode(i[:300000]))\" && "
             "wc -c gutenberg_300k.txt && sha256sum gutenberg_300k.txt", "/lsa/logs/kernel_prep.log")
    if rc:
        raise SystemExit(rc)


@app.function(image=image, volumes=VOLS, timeout=600)
def status() -> None:
    root_vol.reload()
    _sh("ls -la /lsa/logs; find /lsa/records -maxdepth 4 -name '*.json' | head -50; "
        "du -sh /lsa/* 2>/dev/null; ls /lsa/data/benchmarks-qwen38/ruler 2>/dev/null | head -40")


@app.local_entrypoint()
def main(step: str = "probe", kind: str = "accuracy", config: str = "", overrides: str = "", tag: str = "",
         lengths: str = "4096,32768") -> None:
    if step == "probe":
        probe.remote()
    elif step == "fetch":
        fetch.remote()
    elif step == "ruler":
        ruler.remote(lengths)
    elif step == "evaluate":
        evaluate.remote(kind, config, overrides, tag)
    elif step == "kernel_prep":
        kernel_prep.remote()
    elif step == "status":
        status.remote()
    else:
        raise SystemExit(f"unknown step {step}")
