import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setattr("z0int.quota_budget.project", lambda *args, **kwargs: None)


import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from z0int import provider_saturation as saturation
from z0int.worker_routing import blocked_provider, configuration, execute_plan
from z0int.receipt import append_receipt


def test_402_is_funds_exhausted_immediately(monkeypatch):
    first = saturation.acquire("groq", "funds-one", {"quota_model": "openai/gpt-oss-20b", "quota_reserved_tokens": 8})
    saturation.release(first["token"], "groq", 402, 5)
    state = saturation.snapshot("groq")
    assert not state["available"]
    assert state["reason"] == "funds_exhausted"
    now = __import__("time").time()
    monkeypatch.setattr(saturation.time, "time", lambda: now + 61)
    assert saturation.snapshot("groq")["available"]


def test_cursor_is_never_a_route():
    assert blocked_provider("cursor")
    assert blocked_provider("Cursor")
    assert not blocked_provider("nous")


def test_402_sidesteps_to_nous_not_cursor(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body["model"])
            if body["model"].startswith("gpt-5") or "cursor" in body["model"]:
                self.send_response(500)
                self.end_headers()
                return
            if "nemotron" in body["model"]:
                self.send_response(402)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"insufficient funds"}}')
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({
                "model": body["model"],
                "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0},
            }).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    policy, providers = configuration()
    providers = copy.deepcopy(providers)
    for name in ("openrouter", "nous", "vercel"):
        providers[name].update(base_url=f"http://127.0.0.1:{server.server_port}", api_key_env="TEST_SIDESTEP_KEY", auth="environment")
    monkeypatch.setenv("TEST_SIDESTEP_KEY", "local-test-only")
    plan = {
        "candidates": [{"provider": "openrouter", "model": policy["defaults"]["openrouter"]}],
        "source": "test",
        "category": "probe",
        "reason": "402 sidestep",
        "harness": "omp",
        "caller_trace_id": "sidestep-test",
    }
    try:
        result = execute_plan(
            {"task": "Reply pong", "parent_agent": "test", "max_tokens": 16},
            policy,
            providers,
            plan,
            receipt_sink=append_receipt,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert result["ok"]
    assert result["provider"] == "nous"
    assert "cursor" not in calls
    assert calls[0] == policy["defaults"]["openrouter"]
    assert calls[1] == policy["sidestep_order"][0]["model"]
