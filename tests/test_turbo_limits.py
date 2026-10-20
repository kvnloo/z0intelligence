"""Provider metadata and actual CLI admission regressions, no live inference."""

import time
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message
from types import SimpleNamespace

import pytest
from z0int.cli import main
from z0int.turbo import runtime, adapters


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GROQ_API_KEY", "test-not-secret")


def test_groq_request_headers_are_daily_not_minute():
    with runtime.Admission(
        "groq", time.monotonic() + 2, runtime.root() / "absent"
    ) as admission:
        result = {
            "model": "test",
            "rate_headers": {
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-reset-requests": "1h",
                "x-ratelimit-limit-tokens": "8000",
            },
        }
        admission.observe(result)
    dims = result["quota"]["dimensions"]
    assert dims["rpd"]["limit"] == 1000 and dims["rpd"]["remaining"] == 999
    assert dims["rpm"]["limit"] is None
    assert dims["tpm"]["limit"] == 8000


def test_429_preserves_longer_reset_and_redacts_body(tmp_path):
    headers = Message()
    headers["Retry-After"] = "5"
    headers["x-ratelimit-reset-tokens"] = "2m"
    headers["Authorization"] = "must-not-leak"
    exc = HTTPError("https://provider", 429, "limit", headers, None)
    with patch.object(adapters.urllib.request, "urlopen", side_effect=exc):
        with pytest.raises(adapters.ProviderError) as got:
            adapters.chat(
                SimpleNamespace(provider="groq", model="test"),
                "prompt",
                20,
                time.monotonic() + 2,
                tmp_path / "stop",
            )
    assert got.value.retry_after >= 120
    assert "authorization" not in got.value.rate_headers
    assert "must-not-leak" not in str(got.value)


def test_cli_mode_next_task_consumed_and_normal_when_off(capsys):
    assert main(["mode", "turbo", "on"]) == 0
    capsys.readouterr()
    seen = []

    def execute(self):
        seen.append(self.normal)
        return {"quality": {"verified": True}}

    with patch.object(runtime.Runner, "execute", execute):
        assert main(["run", "--task", "one"]) == 0
        assert main(["run", "--task", "two"]) == 0
        assert main(["turbo", "run", "--task", "explicit"]) == 0
    assert seen == [False, True, False]
    assert runtime.status()[
        "runs"
    ]  # real starts persisted, fake execute never claimed proof


def test_off_marks_only_active_runs(capsys):
    active = runtime.Runner("active")
    complete = runtime.Runner("complete")
    complete.event("completed", "finished")
    assert main(["mode", "turbo", "off"]) == 0
    assert active.stop.exists() and not complete.stop.exists()


def test_bad_checks_rejected_before_run(tmp_path, capsys):
    checks = tmp_path / "checks.json"
    checks.write_text('["echo unsafe"]')
    with patch.object(runtime, "Runner") as runner:
        assert (
            main(["turbo", "run", "--task", "task", "--checks-file", str(checks)]) == 2
        )
        runner.assert_not_called()
