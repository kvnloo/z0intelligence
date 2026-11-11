"""Hermetic unit tests for free_fanout's plan, slot, report and CLI helpers."""

import json
import sys
import types

import pytest

from z0int import free_fanout as ff


SLOT = {"slot": 2, "provider": "nous", "model": "m"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Fail closed on unstubbed routing and keep configuration off disk."""
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setattr(ff, "blocked_provider", lambda p: str(p).lower().startswith("cursor"))
    monkeypatch.setattr(ff, "configuration", lambda: ({}, {}))

    def unexpected_route(*_args, **_kwargs):
        raise AssertionError("unexpected real routing or execution")

    monkeypatch.setattr(ff, "free_route", unexpected_route)
    monkeypatch.setattr(ff, "execute_plan", unexpected_route)


def test_plan_for_shape():
    assert ff._plan_for({"provider": "p", "model": "m"}) == {
        "candidates": [{"provider": "p", "model": "m"}],
        "source": "z0int.free_fanout",
        "category": "probe",
        "reason": "free fanout probe",
        "harness": "omp",
        "caller_trace_id": "free-fanout",
    }


def test_fanout_plan_inserts_root_once_and_calls_build_fanout(monkeypatch, tmp_path):
    calls = []
    mod = types.ModuleType("kerdoios.free_fanout")
    mod.build_fanout = lambda a, workers: calls.append((a, workers)) or {"slots": []}
    monkeypatch.setitem(sys.modules, "kerdoios", types.ModuleType("kerdoios"))
    monkeypatch.setitem(sys.modules, "kerdoios.free_fanout", mod)

    root = tmp_path / "kroot"
    monkeypatch.setattr(ff, "KERDOIOS_ROOT", root)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert ff.fanout_plan(3) == {"slots": []}
    assert calls == [([], 3)]
    assert sys.path[0] == str(root)

    assert ff.fanout_plan() == {"slots": []}
    assert calls == [([], 3), ([], 5)]
    assert sys.path.count(str(root)) == 1


def test_one_passes_free_only_task_to_execute_plan(monkeypatch):
    policy, providers = {"pol": 1}, {"prov": 1}
    seen = {}
    monkeypatch.setattr(ff, "free_route", lambda policy, p, m: {"ok": 1})

    def record(task_args, received_policy, received_providers, plan, **kwargs):
        seen.update(
            task_args=task_args,
            policy=received_policy,
            providers=received_providers,
            plan=plan,
            kwargs=kwargs,
        )
        return {"ok": True, "attempts": []}

    monkeypatch.setattr(ff, "execute_plan", record)
    row = ff._one(SLOT, policy, providers)

    assert seen["task_args"] == {
        "task": "Reply with the single word pong.",
        "parent_agent": "free-fanout",
        "max_tokens": 128,
        "free_only": True,
    }
    assert seen["policy"] is policy
    assert seen["providers"] is providers
    assert seen["plan"] == ff._plan_for(SLOT)
    assert seen["kwargs"] == {"receipt_sink": ff.append_receipt}
    assert row["ok"] is True


def test_one_projects_attempts(monkeypatch):
    monkeypatch.setattr(ff, "free_route", lambda policy, p, m: {"ok": 1})
    responses = iter(
        [
            {
                "ok": True,
                "provider": "nous",
                "model": "m",
                "refusal_reason": None,
                "attempts": [
                    {
                        "provider": "nous",
                        "model": "m",
                        "extra": {
                            "status": "completed",
                            "http_status": 200,
                            "finish_reason": "stop",
                            "sidestep": False,
                        },
                    },
                    {"provider": "groq", "model": "m2"},
                ],
            },
            {"ok": False, "refusal_reason": "x", "attempts": None},
            {"ok": False, "refusal_reason": "x"},
        ]
    )
    monkeypatch.setattr(ff, "execute_plan", lambda *args, **kwargs: next(responses))

    assert ff._one(SLOT, {}, {}) == {
        "slot": 2,
        "ok": True,
        "provider": "nous",
        "model": "m",
        "refusal_reason": None,
        "attempts": [
            {
                "provider": "nous",
                "model": "m",
                "status": "completed",
                "http_status": 200,
                "finish": "stop",
                "sidestep": False,
            },
            {
                "provider": "groq",
                "model": "m2",
                "status": None,
                "http_status": None,
                "finish": None,
                "sidestep": None,
            },
        ],
    }
    for _ in range(2):
        row = ff._one(SLOT, {}, {})
        assert row["ok"] is False
        assert row["attempts"] == []
        assert row["refusal_reason"] == "x"


def test_one_maps_execute_plan_exception_to_class_name(monkeypatch):
    monkeypatch.setattr(ff, "free_route", lambda policy, p, m: {"ok": 1})

    def fail(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ff, "execute_plan", fail)
    assert ff._one(SLOT, {}, {}) == {
        "slot": 2,
        "ok": False,
        "provider": "nous",
        "error": "RuntimeError",
    }


def test_one_refuses_when_no_free_route_without_executing(monkeypatch):
    calls = []
    monkeypatch.setattr(ff, "free_route", lambda policy, p, m: None)

    def forbidden_execute(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("execute_plan called for an unvalidated route")

    monkeypatch.setattr(ff, "execute_plan", forbidden_execute)
    assert ff._one(SLOT, {}, {}) == {
        "slot": 2,
        "ok": False,
        "provider": "nous",
        "model": "m",
        "error": "no_validated_free_route",
    }
    assert calls == []


def test_run_report_fields(monkeypatch):
    plan = {
        "slots": [
            {"slot": 0, "provider": "nous", "model": "m"},
            {"slot": 1, "provider": "groq", "model": "m"},
        ]
    }
    requested = []
    policy, providers = {"pol": 1}, {"prov": 1}

    def fake_fanout(workers=5):
        requested.append(workers)
        return plan

    def fake_execute(task_args, received_policy, received_providers, candidate_plan, **kwargs):
        assert task_args["free_only"] is True
        assert received_policy is policy
        assert received_providers is providers
        assert kwargs["receipt_sink"] is ff.append_receipt
        provider = candidate_plan["candidates"][0]["provider"]
        if provider == "nous":
            return {"ok": True, "provider": "nous", "model": "m", "attempts": []}
        assert provider == "groq"
        return {"ok": False, "attempts": []}

    monkeypatch.setattr(ff, "configuration", lambda: (policy, providers))
    monkeypatch.setattr(ff, "fanout_plan", fake_fanout)
    monkeypatch.setattr(ff, "free_route", lambda policy, p, m: {"ok": 1})
    monkeypatch.setattr(ff, "execute_plan", fake_execute)

    report = ff.run(7)
    assert requested == [7]
    assert report == {
        "schema": "z0int.free_fanout_probe.v1",
        "requested_workers": 7,
        "working": 1,
        "cursor_used": False,
        "slots": [
            {
                "slot": 0,
                "ok": True,
                "provider": "nous",
                "model": "m",
                "refusal_reason": None,
                "attempts": [],
            },
            {
                "slot": 1,
                "ok": False,
                "provider": None,
                "model": None,
                "refusal_reason": None,
                "attempts": [],
            },
        ],
        "plan": plan,
    }
    assert report["plan"] is plan
    assert [slot["slot"] for slot in report["slots"]] == [0, 1]


def test_run_with_no_slots(monkeypatch):
    plan = {"slots": []}
    monkeypatch.setattr(ff, "fanout_plan", lambda workers=5: plan)

    report = ff.run(7)
    assert report["schema"] == "z0int.free_fanout_probe.v1"
    assert report["requested_workers"] == 7
    assert report["working"] == 0
    assert report["slots"] == []
    assert report["cursor_used"] is False
    assert report["plan"] is plan


def test_main_parses_workers_flag_and_exit_codes(monkeypatch, capsys):
    calls = []
    working = [1]

    def fake_run(workers=5):
        calls.append(workers)
        return {"working": working[0], "requested_workers": workers}

    monkeypatch.setattr(ff, "run", fake_run)

    assert ff.main(["--workers", "3"]) == 0
    assert calls == [3]
    assert json.loads(capsys.readouterr().out) == {"working": 1, "requested_workers": 3}

    assert ff.main([]) == 0
    assert calls == [3, 5]
    assert json.loads(capsys.readouterr().out) == {"working": 1, "requested_workers": 5}

    working[0] = 0
    assert ff.main(["--workers", "4"]) == 1
    assert calls == [3, 5, 4]
    assert json.loads(capsys.readouterr().out) == {"working": 0, "requested_workers": 4}

    working[0] = 1
    monkeypatch.setattr(sys, "argv", ["x", "--workers", "2"])
    assert ff.main() == 0
    assert calls == [3, 5, 4, 2]
    assert json.loads(capsys.readouterr().out) == {"working": 1, "requested_workers": 2}


def test_main_bad_workers_value(monkeypatch):
    calls = []

    def forbidden_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("run should not be reached")

    monkeypatch.setattr(ff, "run", forbidden_run)

    with pytest.raises(ValueError):
        ff.main(["--workers", "abc"])
    with pytest.raises(IndexError):
        ff.main(["--workers"])
    assert calls == []
