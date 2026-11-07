import os
import subprocess
import time

import pytest

from z0int.autoresearch import resources as res


def test_load_avg_ratio_divides_by_cpu_count(monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (4.0, 0.0, 0.0), raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert res._load_avg_ratio() == 0.5

    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert res._load_avg_ratio() == 4.0


def test_load_avg_ratio_returns_zero_on_error(monkeypatch):
    def unavailable():
        raise OSError("load average unavailable")

    monkeypatch.setattr(os, "getloadavg", unavailable, raising=False)
    assert res._load_avg_ratio() == 0.0


def test_gpu_stats_parses_first_line(monkeypatch):
    calls = []

    def gpu_output(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return "42, 8000\n17, 1\n"

    monkeypatch.setattr(subprocess, "check_output", gpu_output)
    assert res._gpu_stats() == (42.0, 8000.0)
    assert calls == [(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.free",
            "--format=csv,noheader,nounits",
        ],
        {"text": True, "timeout": 2},
    )]

    def missing_gpu(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi not installed")

    monkeypatch.setattr(subprocess, "check_output", missing_gpu)
    assert res._gpu_stats() == (None, None)

    for bad_output in ("garbage", "", "not-a-number, 8000"):
        monkeypatch.setattr(
            subprocess, "check_output", lambda *args, value=bad_output, **kwargs: value
        )
        assert res._gpu_stats() == (None, None)


def test_interactive_recent_marker_fresh_and_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)

    marker = tmp_path / "runtime" / "interactive.touch"
    marker.parent.mkdir()
    marker.touch()
    now = time.time()
    os.utime(marker, (now, now))

    assert res.interactive_recent() is True
    old = now - 100
    os.utime(marker, (old, old))
    assert res.interactive_recent() is False
    assert res.interactive_recent(grace=1000) is True


def test_interactive_recent_falls_back_to_bridge_heart(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)

    assert res.interactive_recent() is False

    heart = tmp_path / "stream" / "bridge_heart.jsonl"
    heart.parent.mkdir()
    heart.write_text("{}\n")
    now = time.time()
    os.utime(heart, (now, now))
    assert res.interactive_recent() is True

    old = now - 100
    os.utime(heart, (old, old))
    assert res.interactive_recent() is False

    marker = tmp_path / "runtime" / "interactive.touch"
    marker.parent.mkdir()
    marker.touch()
    os.utime(marker, (old, old))
    os.utime(heart, (now, now))
    assert res.interactive_recent() is False


def test_can_run_research_classes_ignore_gpu_contention(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)
    monkeypatch.setattr(res, "_load_avg_ratio", lambda: 0.99)
    monkeypatch.setattr(res, "_gpu_stats", lambda: (99.0, 10.0))
    monkeypatch.setattr(res, "interactive_recent", lambda grace=None: True)

    for cls, name in (
        (res.ResourceClass.RESEARCH_REMOTE, "research_remote"),
        ("cpu_light", "cpu_light"),
    ):
        assert res.can_run(cls) == {
            "pause": False,
            "reasons": [],
            "resource_class": name,
            "cpu_load_ratio": 0.99,
            "gpu_util": 99.0,
            "gpu_free_mb": 10.0,
            "interactive": True,
        }


def test_can_run_research_env_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)
    monkeypatch.setattr(res, "_load_avg_ratio", lambda: 0.0)
    monkeypatch.setattr(res, "_gpu_stats", lambda: (0.0, 5000.0))
    monkeypatch.setattr(res, "interactive_recent", lambda grace=None: False)

    monkeypatch.setenv("Z0INT_PAUSE_RESEARCH", "1")
    for cls in (res.ResourceClass.RESEARCH_REMOTE, "cpu_light"):
        result = res.can_run(cls)
        assert result["pause"] is True
        assert result["reasons"] == ["env:Z0INT_PAUSE_RESEARCH"]

    monkeypatch.setenv("Z0INT_PAUSE_RESEARCH", "0")
    for cls in (res.ResourceClass.RESEARCH_REMOTE, "cpu_light"):
        result = res.can_run(cls)
        assert result["pause"] is False
        assert result["reasons"] == []

    monkeypatch.setenv("Z0INT_PAUSE_RESEARCH", "1")
    for cls in (res.ResourceClass.GPU_TRAIN, res.ResourceClass.GPU_BENCHMARK):
        result = res.can_run(cls)
        assert result["pause"] is False
        assert result["reasons"] == []


def test_can_run_gpu_class_reasons(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)
    monkeypatch.setattr(res, "_load_avg_ratio", lambda: 0.9)
    monkeypatch.setattr(res, "_gpu_stats", lambda: (50.0, 1000.0))
    monkeypatch.setattr(res, "interactive_recent", lambda grace=None: True)

    assert res.can_run(res.ResourceClass.GPU_TRAIN) == {
        "pause": True,
        "reasons": [
            "interactive_traffic",
            "cpu_load:0.90",
            "gpu_util:50.0",
            "gpu_mem_free:1000.0",
        ],
        "resource_class": "gpu_train",
        "cpu_load_ratio": 0.9,
        "gpu_util": 50.0,
        "gpu_free_mb": 1000.0,
        "interactive": True,
    }


def test_can_run_gpu_thresholds_are_strict(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)
    monkeypatch.setattr(res, "_load_avg_ratio", lambda: 0.70)
    monkeypatch.setattr(res, "_gpu_stats", lambda: (35.0, 2500.0))
    monkeypatch.setattr(res, "interactive_recent", lambda grace=None: False)

    at_boundary = res.can_run(res.ResourceClass.GPU_TRAIN)
    assert at_boundary["pause"] is False
    assert at_boundary["reasons"] == []

    monkeypatch.setattr(res, "_gpu_stats", lambda: (None, None))
    unavailable = res.can_run(res.ResourceClass.GPU_TRAIN)
    assert unavailable["pause"] is False
    assert unavailable["reasons"] == []
    assert unavailable["gpu_util"] is None
    assert unavailable["gpu_free_mb"] is None

    monkeypatch.setattr(res, "_load_avg_ratio", lambda: 0.2)
    custom = res.can_run(
        res.ResourceClass.GPU_TRAIN,
        limits=res.ResourceLimits(cpu_load_pause_above=0.1),
    )
    assert custom["pause"] is True
    assert custom["reasons"] == ["cpu_load:0.20"]


def test_should_pause_equals_gpu_benchmark(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)
    monkeypatch.setattr(res, "_load_avg_ratio", lambda: 0.75)
    monkeypatch.setattr(res, "_gpu_stats", lambda: (42.0, 4000.0))
    monkeypatch.setattr(res, "interactive_recent", lambda grace=None: False)

    legacy = res.should_pause()
    assert legacy == res.can_run(res.ResourceClass.GPU_BENCHMARK)
    assert legacy["resource_class"] == "gpu_benchmark"
    assert legacy["pause"] is True
    assert legacy["reasons"] == ["cpu_load:0.75", "gpu_util:42.0"]


def test_can_run_rejects_unknown_class(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_PAUSE_RESEARCH", raising=False)
    monkeypatch.setattr(res, "_load_avg_ratio", lambda: 0.0)
    monkeypatch.setattr(res, "_gpu_stats", lambda: (None, None))
    monkeypatch.setattr(res, "interactive_recent", lambda grace=None: False)

    with pytest.raises(ValueError, match="bogus"):
        res.can_run("bogus")
