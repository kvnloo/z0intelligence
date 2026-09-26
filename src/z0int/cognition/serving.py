"""Process management for local GGUF serving.

Shared by the serving probe and the tool-calling evaluation so there is exactly
one implementation of "start a llama-server for this GGUF, wait for health, then
release the GPU".

A 12 GB card cannot hold several 7B-9B models at once, so this module is built
around one resident model at a time and waits for VRAM to actually come back
before returning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
import contextlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

DEFAULT_LLAMA_SERVER = (
    "/mnt/zer0models/workspace/zer0/oss/.work/llama.cpp/build/bin/llama-server"
)
DEFAULT_GGUF_DIR = "/mnt/zer0models/zer0-models/gguf"

# model_id -> path under the GGUF root
GGUF_LAYOUT: dict[str, str] = {
    "functiongemma_270m": "functiongemma-270m/functiongemma-270m-it-q8_0.gguf",
    "hammer2.1_3b": "hammer2.1-3b/Hammer2.1-3b.Q4_K_M.gguf",
    "hammer2.1_7b": "hammer2.1-7b/Hammer2.1-7b-Q4_K_M.gguf",
    "nemotron_orchestrator_8b": "nvidia-orchestrator-8b/nvidia_Orchestrator-8B-Q4_K_M.gguf",
    "qwen3.5_9b": "qwen3.5-9b/Qwen_Qwen3.5-9B-Q4_K_M.gguf",
    "qwen3.5_4b": "qwen3.5-4b/Qwen_Qwen3.5-4B-Q4_K_M.gguf",
}


def wait_health(base_url: str, timeout_s: float = 240.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=4) as resp:
                if json.loads(resp.read().decode()).get("status") == "ok":
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(1.0)
    return False


def free_port(start: int = 18100, span: int = 60) -> int:
    for port in range(start, start + span):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("no free port available")


def llama_build(binary: str) -> str:
    """llama-server prints its banner on stderr, so capture both streams."""
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
        for line in (proc.stdout + proc.stderr).splitlines():
            line = line.strip()
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().split()[0]
    except (subprocess.SubprocessError, OSError):
        pass
    return "unknown"


def quant_from_name(name: str) -> str:
    lowered = name.lower()
    for tag in ("q8_0", "q6_k", "q5_k_m", "q4_k_m", "q4_0", "q3_k_m", "iq4_xs", "f16", "bf16"):
        if tag in lowered:
            return tag.upper()
    return "unknown"


class LlamaServer:
    """One llama-server process holding one GGUF."""

    def __init__(
        self,
        *,
        binary: str,
        gguf: Path,
        context: int,
        port: int | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.binary = binary
        self.gguf = gguf
        self.context = context
        self.port = port or free_port()
        self.extra_args = extra_args
        self.proc: subprocess.Popen[bytes] | None = None
        self.log = Path(f"/tmp/z0int-llama-{self.port}.log")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> LlamaServer:
        cmd = [
            self.binary,
            "-m", str(self.gguf),
            "-c", str(self.context),
            "-ngl", "999",
            "--jinja",
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-fa", "auto",
            "--no-warmup",
            *self.extra_args,
        ]
        handle = self.log.open("wb")
        self.proc = subprocess.Popen(
            cmd, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True
        )
        if not wait_health(self.base_url):
            self.stop()
            raise RuntimeError(f"llama-server did not become ready; see {self.log}")
        return self

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except OSError:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=25)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except OSError:
                    self.proc.kill()
        self.proc = None
        # Let the driver actually release VRAM before the next model loads.
        time.sleep(3.0)

    def __enter__(self) -> LlamaServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


@contextlib.contextmanager
def serve_model(
    model_id: str,
    *,
    binary: str = DEFAULT_LLAMA_SERVER,
    gguf_dir: str | Path = DEFAULT_GGUF_DIR,
    context: int = 4096,
    extra_args: tuple[str, ...] = (),
) -> Iterator[LlamaServer]:
    """Serve one model_id for the duration of the with-block."""
    if model_id not in GGUF_LAYOUT:
        raise KeyError(f"no GGUF layout for {model_id}")
    gguf = Path(gguf_dir) / GGUF_LAYOUT[model_id]
    if not gguf.is_file():
        raise FileNotFoundError(f"missing GGUF for {model_id}: {gguf}")
    server = LlamaServer(
        binary=binary, gguf=gguf, context=context, extra_args=extra_args
    ).start()
    try:
        yield server
    finally:
        server.stop()


__all__ = [
    "DEFAULT_GGUF_DIR",
    "DEFAULT_LLAMA_SERVER",
    "GGUF_LAYOUT",
    "LlamaServer",
    "free_port",
    "llama_build",
    "quant_from_name",
    "serve_model",
    "wait_health",
]
