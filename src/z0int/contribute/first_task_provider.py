"""Bounded local-only CW6 transport. No provider discovery or remote consent.

Set Z0INT_FIRST_TASK_PROVIDER to an executable local classifier (no arguments).
It reads one closed question JSON on stdin and returns the answer JSON on stdout.
`auto` currently uses the same local-first path: no implicit remote escalation.
No model is downloaded or started by this module.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import threading
from pathlib import Path

MAX_BYTES = 65536
TIMEOUT = 30


@contextlib.contextmanager
def deadline():
    if (not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread()
            or signal.getitimer(signal.ITIMER_REAL)[0]):
        raise TimeoutError("deadline_unavailable")
    old = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise TimeoutError("timeout")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def safe_payload(payload):
    """Abort on identity/env canaries rather than let escaping hide a match."""
    identities = [str(Path.home()), os.environ.get("USER", ""), socket.gethostname()]
    secrets = [v for k, v in os.environ.items() if len(v) >= 8 or any(
        name in k.upper() for name in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CANARY"))]
    def check(value):
        if isinstance(value, dict):
            return all(check(k) and check(v) for k, v in value.items())
        if isinstance(value, list):
            return all(check(v) for v in value)
        if not isinstance(value, str):
            return True
        return not any(v and v in value for v in identities + secrets)
    return check(payload)


def local_provider(payload):
    executable = os.environ.get("Z0INT_FIRST_TASK_PROVIDER")
    if not executable:
        raise LookupError("no_provider")
    # No shell, model selection, inherited proxy, network endpoint or credentials.
    import tempfile
    # Bound retained output and keep classifier cwd away from repository instructions.
    with tempfile.TemporaryDirectory(prefix="first-task-") as directory:
        with tempfile.TemporaryFile() as output:
            proc = subprocess.run([executable], input=json.dumps(payload), text=True,
                                  stdout=output, stderr=subprocess.DEVNULL, cwd=directory,
                                  timeout=TIMEOUT, env={"PATH": os.defpath}, check=False)
            if proc.returncode:
                raise RuntimeError("provider_failed")
            output.seek(0)
            raw = output.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("response_schema")
            return raw.decode("utf-8")


def parse(raw):
    if isinstance(raw, dict):  # in-process fake/embedded provider seam
        return raw
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
        raise ValueError("response_schema")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate_key")
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique)
