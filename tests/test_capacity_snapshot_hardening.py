"""Freshness, bounds and fail-soft behaviour of the read-only capacity snapshot.

Synthetic fixtures only. A socket guard fails any non-loopback connection.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import stat

import pytest

from z0int import capacity_snapshot as cs
from z0int import posture
from z0int.capacity_snapshot import build_snapshot, write_snapshot

NOW = 1_800_000_000.0
DAY = 86_400.0
MARK = "ZQCANARY"


@pytest.fixture(autouse=True)
def _socket_guard(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if str(host) not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError(f"non-loopback connection attempted: {address!r}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    for name in (
        "Z0INT_TERN_CAPACITY_SNAPSHOT",
        "Z0INT_KERDOIOS_CAPACITY_SNAPSHOT",
        "Z0INT_PLACEMENT_LEASES_SNAPSHOT",
    ):
        monkeypatch.delenv(name, raising=False)


def _tern():
    return {
        "observed_at": NOW - 2,
        "generated_at": NOW - 2,
        "hosts": [
            {
                "host_id": "tern:groot",
                "label": "groot",
                "mode": "remote",
                "status": "online",
                "rtt_ms": 4.2,
                "observed_at": NOW - 3,
                "resources": {
                    "cpu_logical": 16,
                    "ram_total_bytes": 64,
                    "ram_available_bytes": 32,
                    "storage_total_bytes": 1000,
                    "storage_free_bytes": 500,
                    "gpu_count": 1,
                    "gpus": [
                        {
                            "id": "GPU-1",
                            "name": "RTX",
                            "memory_total_bytes": 24,
                            "memory_free_bytes": 12,
                            "utilization_pct": 5.0,
                        }
                    ],
                },
            },
            {"id": "h2", "name": "rocket", "slot": 2, "state": "connected"},
        ],
        "sessions": [
            {
                "session_id": "omp:abc",
                "name": "work",
                "host_id": "tern:groot",
                "runtime": "omp",
                "status": "running",
                "current": True,
                "locked": False,
                "tab_count": 3,
                "rtt_ms": 1.5,
                "pane_id": "p1",
                "observed_at": NOW - 3,
                "migration_allowed": True,
            },
            {"id": 42, "host": "rocket", "program": "codex", "state": "idle", "tabs": 2},
        ],
    }


def _kerdoios():
    return {
        "generated_at": NOW - 5,
        "saved_at": NOW - 5,
        "observed_at": NOW - 5,
        "entries": [
            {
                "offer_id": "offer-1",
                "provider": "groq",
                "model": "free/model",
                "health": "ok",
                "status": "ok",
                "quota": {
                    "remaining_free_quota": 10,
                    "remaining_tokens": 1000,
                    "remaining": 7,
                    "day_tokens_remaining": 900,
                    "limit": 50,
                    "daily_limit": 50,
                    "day_tokens_limit": 5000,
                    "reset_at": NOW + DAY,
                    "dimensions": {
                        "rpd": {
                            "limit": 50,
                            "remaining": 7,
                            "reset_at": NOW + DAY,
                            "source": "provider",
                        }
                    },
                },
                "price": 0.0,
                "predicted_cost": 0.0,
                "burn_rate": 3.0,
                "time_to_exhaustion": 100.0,
                "time_to_reset": DAY,
                "reset_at": NOW + DAY,
                "updated_at": NOW - 6,
                "observed_at": NOW - 6,
            }
        ],
    }


def _leases():
    return {
        "observed_at": NOW - 1,
        "leases": [
            {
                "placement_id": "pl-1",
                "demand_id": "d-1",
                "offer_id": "offer-1",
                "status": "active",
                "lease_expires_at": NOW + 60,
                "selected_offer_id": "offer-1",
                "selection_reason": "cheapest free offer",
                "policy_revision": "r7",
            }
        ],
    }


def _build(tern=None, kerdoios=None, leases=None, **kw):
    return build_snapshot(include_local=False, now=NOW, tern=tern, kerdoios=kerdoios, leases=leases, **kw)


# --------------------------------------------------------------------------- (1) stale quota


def test_stale_rule_is_the_posture_rule_not_a_second_implementation():
    assert cs.observation_predates_reset is posture.observation_predates_reset
    assert cs.OBSERVATION_PREDATES_RESET == posture.OBSERVATION_PREDATES_RESET == "observation_predates_reset"


def test_quota_observed_before_its_reset_is_unknown_with_reason():
    raw = _kerdoios()
    entry = raw["entries"][0]
    entry["updated_at"] = entry["observed_at"] = NOW - 10 * DAY
    entry["reset_at"] = NOW - 9 * DAY
    entry["time_to_reset"] = DAY
    entry["quota"]["reset_at"] = NOW - 9 * DAY
    entry["quota"]["dimensions"]["rpd"]["reset_at"] = NOW - 9 * DAY

    snap = _build(kerdoios=raw)
    offer = snap["offers"][0]
    quota = offer["quota"]
    for key in ("remaining_free_quota", "remaining_tokens", "remaining", "day_tokens_remaining"):
        assert quota[key] is None, key
    assert quota["dimensions"]["rpd"]["remaining"] is None
    assert quota["dimensions"]["rpd"]["stale_reason"] == "observation_predates_reset"
    assert quota["limit"] == 50
    assert offer["burn_rate"] is None
    assert offer["time_to_exhaustion"] is None
    assert offer["time_to_reset"] is None
    assert offer["stale_reason"] == "observation_predates_reset"
    assert snap["sources"]["kerdoios"]["status"] != "ok"
    assert snap["sources"]["kerdoios"]["reason"] == "observation_predates_reset"
    assert snap["status"] != "ok"


def test_quota_with_future_reset_is_kept():
    snap = _build(kerdoios=_kerdoios())
    offer = snap["offers"][0]
    assert offer["quota"]["remaining"] == 7
    assert offer["quota"]["dimensions"]["rpd"]["remaining"] == 7
    assert offer["burn_rate"] == 3.0
    assert offer["time_to_exhaustion"] == 100.0
    assert "stale_reason" not in offer
    assert snap["sources"]["kerdoios"]["status"] == "ok"


def test_only_the_dimension_past_its_reset_goes_unknown():
    raw = _kerdoios()
    dims = raw["entries"][0]["quota"]["dimensions"]
    dims["rpm"] = {"limit": 30, "remaining": 29, "reset_at": NOW + 30, "source": "provider"}
    dims["rpd"]["reset_at"] = NOW - 60
    offer = _build(kerdoios=raw)["offers"][0]
    assert offer["quota"]["dimensions"]["rpd"]["remaining"] is None
    assert offer["quota"]["dimensions"]["rpm"]["remaining"] == 29
    assert offer["burn_rate"] is None
    assert offer["stale_reason"] == "observation_predates_reset"


# --------------------------------------------------------------------------- (2) freshness


def test_missing_tern_timestamp_is_unknown_age_and_not_ok():
    raw = _tern()
    for key in ("observed_at", "generated_at"):
        raw.pop(key)
    for row in raw["hosts"] + raw["sessions"]:
        row.pop("observed_at", None)
    snap = _build(tern=raw)
    source = snap["sources"]["tern"]
    assert source["observed_at"] is None
    assert source["age_s"] is None
    assert source["status"] != "ok"
    assert source["reason"] == "missing_observed_at"
    assert snap["status"] != "ok"
    assert all(host["observed_at"] is None for host in snap["hosts"])
    assert all(session["observed_at"] is None for session in snap["sessions"])


def test_missing_kerdoios_and_lease_timestamps_are_not_forged():
    kerdoios = _kerdoios()
    for key in ("generated_at", "saved_at", "observed_at"):
        kerdoios.pop(key)
    for key in ("updated_at", "observed_at"):
        kerdoios["entries"][0].pop(key)
    snap = _build(kerdoios=kerdoios, leases=_leases()["leases"])
    for name in ("kerdoios", "leases"):
        assert snap["sources"][name]["observed_at"] is None, name
        assert snap["sources"][name]["age_s"] is None, name
        assert snap["sources"][name]["status"] != "ok", name
        assert snap["sources"][name]["reason"] == "missing_observed_at", name
    assert snap["offers"][0]["observed_at"] is None


def test_valid_timestamp_reports_real_age():
    snap = _build(tern=_tern(), kerdoios=_kerdoios(), leases=_leases())
    assert snap["sources"]["tern"] == {"status": "ok", "observed_at": NOW - 2, "age_s": 2.0}
    assert snap["sources"]["kerdoios"]["age_s"] == 5.0
    assert snap["sources"]["leases"]["age_s"] == 1.0
    assert snap["status"] == "ok"


@pytest.mark.parametrize("bad", [NOW + 3600, -5.0, 0, True, "tomorrow", [NOW], float("nan"), float("inf")])
def test_future_negative_or_malformed_timestamps_are_invalid(bad):
    tern = _tern()
    tern["observed_at"] = bad
    tern.pop("generated_at")
    tern["hosts"][0]["observed_at"] = bad
    kerdoios = _kerdoios()
    kerdoios["generated_at"] = bad
    kerdoios["entries"][0]["updated_at"] = bad
    snap = _build(tern=tern, kerdoios=kerdoios)
    for name in ("tern", "kerdoios"):
        source = snap["sources"][name]
        assert source["observed_at"] is None, name
        assert source["age_s"] is None, name
        assert source["status"] != "ok", name
        assert source["reason"] == "invalid_observed_at", name
    assert snap["hosts"][0]["observed_at"] is None
    assert snap["offers"][0]["observed_at"] is None
    json.dumps(snap, allow_nan=False)


def test_old_tern_file_without_timestamp_reports_mtime_only_as_mtime(tmp_path, capsys):
    raw = _tern()
    for key in ("observed_at", "generated_at"):
        raw.pop(key)
    tern_path = tmp_path / "tern.json"
    tern_path.write_text(json.dumps(raw))
    old = os.stat(tern_path).st_mtime - 30 * DAY
    os.utime(tern_path, (old, old))
    out = tmp_path / "snap.json"
    rc = cs.main(["--no-local", "--tern", str(tern_path), "--kerdoios", str(tmp_path / "absent.json"),
                  "--output", str(out)])
    assert rc == 0
    source = json.loads(out.read_text())["sources"]["tern"]
    assert source["status"] != "ok"
    assert source["observed_at"] is None
    assert source["age_s"] is None
    assert source["file_mtime"] == pytest.approx(old, abs=1.0)
    assert source["file_age_s"] == pytest.approx(30 * DAY, abs=120.0)


# --------------------------------------------------------------------------- (3) bounds / canaries


def _shapes(tag: str) -> dict[str, object]:
    token = f"{MARK}{tag}"
    return {
        "nested": {"k": token, "deep": {"k": [token]}},
        "list": [token, {"k": token}],
        "long": token + "x" * 400,
        "secret": f"sk-{token}abcdefghijklmnopqrstuvwx",
        "email": f"{token}@example.com",
        "path": f"/home/{token}/.ssh/id_rsa",
        "control": f"{token}\n\x00\x1b[31m",
    }


# Shapes that are only a canary where the field is *not* a free-text label.
_PLAIN = "plain"


def _positions(node, path=()):
    """Every container, leaf and extra-key position in a fixture document."""
    yield path
    if isinstance(node, dict):
        yield path + ("zq_unlisted_key",)
        for key, value in node.items():
            yield from _positions(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _positions(value, path + (index,))


def _plant(doc, path, value):
    node = doc
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value


_TEXT_FIELDS = {
    "host_id", "id", "name", "label", "slot", "session_id", "host", "runtime", "program", "pane_id",
    "offer_id", "provider", "model", "placement_id", "demand_id", "selected_offer_id",
    "selection_reason", "policy_revision",
}


def _leaks():
    docs = {"tern": _tern(), "kerdoios": _kerdoios(), "leases": _leases()}
    leaks, planted = [], 0
    for source, base in docs.items():
        for path in _positions(base):
            if not path:
                continue
            shapes = _shapes(f"{len(leaks)}n{planted}")
            if path[-1] not in _TEXT_FIELDS:
                shapes[_PLAIN] = f"{MARK}plain{planted}"
            for shape, value in shapes.items():
                planted += 1
                mutated = {name: copy.deepcopy(doc) for name, doc in docs.items()}
                _plant(mutated[source], path, value)
                encoded = json.dumps(_build(**mutated), allow_nan=False)
                if MARK in encoded:
                    leaks.append((source, path, shape))
    return leaks, planted


def test_no_canary_reaches_the_snapshot_from_any_input_position():
    leaks, planted = _leaks()
    assert planted > 500
    assert leaks == [], f"{len(leaks)} of {planted} canaries leaked, e.g. {leaks[:8]}"


def test_canaries_as_mapping_keys_do_not_reach_the_snapshot():
    kerdoios = _kerdoios()
    dims = kerdoios["entries"][0]["quota"]["dimensions"]
    for shape, value in _shapes("key").items():
        if isinstance(value, str):
            dims[value] = {"limit": 1, "remaining": 1}
    kerdoios["entries"] = {f"{MARK}entrykey": kerdoios["entries"][0]}
    snap = _build(kerdoios=kerdoios)
    assert MARK not in json.dumps(snap)
    dimensions = snap["offers"][0]["quota"]["dimensions"]
    assert len(dimensions) == 1 + sum(isinstance(value, str) for value in _shapes("key").values())
    for name, row in dimensions.items():
        assert name == "rpd" or (name.startswith("redacted-") and row["id_redacted"]), name


def test_all_positions_planted_at_once_do_not_leak():
    docs = {"tern": _tern(), "kerdoios": _kerdoios(), "leases": _leases()}
    for name, doc in docs.items():
        for index, path in enumerate(sorted((p for p in _positions(doc) if p), key=len, reverse=True)):
            shapes = list(_shapes(f"all{name}{index}").values())
            _plant(doc, path, shapes[index % len(shapes)])
    assert MARK not in json.dumps(_build(**docs), allow_nan=False)


def test_plain_short_labels_and_typed_values_survive_sanitising():
    snap = _build(tern=_tern(), kerdoios=_kerdoios(), leases=_leases())
    host = next(h for h in snap["hosts"] if h["host_id"] == "tern:groot")
    assert host["label"] == "groot" and host["status"] == "online" and host["mode"] == "remote"
    assert host["resources"]["gpus"][0] == {
        "id": "GPU-1", "name": "RTX", "memory_total_bytes": 24, "memory_free_bytes": 12, "utilization_pct": 5.0,
    }
    assert host["resources"]["gpu_count"] == 1
    offer = snap["offers"][0]
    assert (offer["provider"], offer["model"], offer["health"]) == ("groq", "free/model", "ok")
    assert offer["quota"]["dimensions"]["rpd"] == {
        "limit": 50, "remaining": 7, "reset_at": NOW + DAY, "source": "provider",
    }
    assert snap["leases"][0]["selection_reason"] == "cheapest free offer"
    assert snap["leases"][0]["status"] == "active"
    session = next(s for s in snap["sessions"] if s["session_id"] == "42")
    assert (session["host_id"], session["runtime"], session["tab_count"]) == ("tern:rocket", "codex", 2)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 10**40, True, "12", None, [1], {"a": 1}])
def test_numeric_fields_must_be_finite_bounded_numbers(bad):
    tern = _tern()
    tern["hosts"][0]["rtt_ms"] = bad
    tern["hosts"][0]["resources"]["ram_total_bytes"] = bad
    kerdoios = _kerdoios()
    kerdoios["entries"][0]["price"] = bad
    kerdoios["entries"][0]["quota"]["limit"] = bad
    snap = _build(tern=tern, kerdoios=kerdoios)
    host = next(h for h in snap["hosts"] if h["host_id"] == "tern:groot")
    assert host["rtt_ms"] is None
    assert host["resources"]["ram_total_bytes"] is None
    assert snap["offers"][0]["price"] is None
    assert snap["offers"][0]["quota"]["limit"] is None
    json.dumps(snap, allow_nan=False)


def test_enumerations_come_from_a_fixed_set():
    tern = _tern()
    tern["hosts"][0]["status"] = "pwned"
    tern["hosts"][0]["mode"] = "pwned"
    tern["sessions"][0]["status"] = "pwned"
    kerdoios = _kerdoios()
    kerdoios["entries"][0]["health"] = "pwned"
    kerdoios["entries"][0]["quota"]["dimensions"]["rpd"]["source"] = "pwned"
    leases = _leases()
    leases["leases"][0]["status"] = "pwned"
    snap = _build(tern=tern, kerdoios=kerdoios, leases=leases)
    assert "pwned" not in json.dumps(snap)
    host = next(h for h in snap["hosts"] if h["host_id"] == "tern:groot")
    assert (host["status"], host["mode"]) == ("unknown", "remote")
    assert snap["offers"][0]["health"] == "unknown"
    assert snap["leases"][0]["status"] == "unknown"


# --------------------------------------------------------------------------- (4) temp file


def test_preplanted_symlink_at_predictable_temp_name_does_not_redirect_write(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    dest_dir = tmp_path / "state"
    dest_dir.mkdir()
    dest = dest_dir / "capacity_snapshot.json"
    planted = dest_dir / f".{dest.name}.{os.getpid()}.tmp"
    planted.symlink_to(victim)

    write_snapshot(_build(), dest)

    assert victim.read_text() == "untouched"
    assert not dest.is_symlink()
    assert json.loads(dest.read_text())["schema"] == cs.SCHEMA
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600


def test_temp_file_is_exclusive_nofollow_private_and_cleaned_up(tmp_path, monkeypatch):
    dest = tmp_path / "state" / "capacity_snapshot.json"
    calls = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, **kwargs):
        calls.append((str(path), flags, mode))
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    old_umask = os.umask(0)
    try:
        write_snapshot(_build(), dest)
    finally:
        os.umask(old_umask)

    created = [c for c in calls if c[1] & os.O_CREAT]
    assert len(created) == 1
    path, flags, mode = created[0]
    assert os.path.dirname(path) == str(dest.parent)
    assert flags & os.O_EXCL and flags & os.O_NOFOLLOW
    assert mode == 0o600
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert [p.name for p in dest.parent.iterdir()] == [dest.name]


def test_failed_write_leaves_previous_snapshot_and_no_temp_file(tmp_path, monkeypatch):
    dest = tmp_path / "capacity_snapshot.json"
    write_snapshot(_build(), dest)
    before = dest.read_text()

    def boom(src, dst):
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_snapshot(_build(), dest)
    assert dest.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == [dest.name]


# --------------------------------------------------------------------------- (5) malformed input

_CONTAINER_ERRORS = {
    "tern": [b"[1, 2]", b'"text"', b"7", b'{"hosts": 5}', b'{"hosts": {"a": 1}}', b'{"sessions": "x"}'],
    "kerdoios": [b"[1, 2]", b'"text"', b"7", b'{"entries": 5}', b'{"entries": "x"}'],
    "leases": [b'"text"', b"7", b'{"leases": 5}', b'{"leases": {"a": 1}}', b"{}"],
}
_UNPARSEABLE = [
    b"\xff\xfe\x00{not utf8",
    b'{"observed_at": NaN}',
    b'{"observed_at": Infinity}',
    b'{"observed_at": -Infinity}',
    b"{truncated",
    b"",
    b"[" * 200_000,
]
_MALFORMED = [(name, body) for name, bodies in _CONTAINER_ERRORS.items() for body in bodies + _UNPARSEABLE]


@pytest.mark.parametrize("bad_source,body", _MALFORMED, ids=[f"{n}-{i}" for i, (n, _) in enumerate(_MALFORMED)])
def test_malformed_source_is_marked_error_and_the_rest_is_still_written(tmp_path, capsys, bad_source, body):
    wall = cs.time.time()
    files = {
        "tern": {**_tern(), "observed_at": wall - 1, "generated_at": wall - 1},
        "kerdoios": {**_kerdoios(), "generated_at": wall - 1},
        "leases": {**_leases(), "observed_at": wall - 1},
    }
    argv = ["--no-local"]
    for name, doc in files.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(body if name == bad_source else json.dumps(doc).encode())
        argv += [f"--{name}", str(path)]
    out = tmp_path / "snap.json"
    out.write_text(json.dumps({"schema": cs.SCHEMA, "generated_at": 1.0, "status": "ok"}))

    rc = cs.main(argv + ["--output", str(out)])

    assert rc == 0
    snap = json.loads(out.read_text())
    assert snap["generated_at"] >= wall
    assert snap["sources"][bad_source]["status"] == "error"
    assert snap["sources"][bad_source]["reason"]
    assert snap["status"] != "ok"
    for name in files:
        if name != bad_source:
            assert snap["sources"][name]["status"] == "ok", name
    if bad_source != "kerdoios":
        assert snap["offers"][0]["provider"] == "groq"
    if bad_source != "tern":
        assert len(snap["hosts"]) == 2


def test_wrong_typed_fields_inside_rows_do_not_crash():
    tern = _tern()
    tern["hosts"][0].update(mode=[], status={}, label=["x"], resources="nope")
    tern["hosts"].append("not-a-row")
    tern["sessions"][0].update(status=[], host_id={"a": 1}, current="yes")
    kerdoios = _kerdoios()
    kerdoios["entries"][0].update(quota=[1], health=[], provider="groq")
    kerdoios["entries"].append(5)
    snap = _build(tern=tern, kerdoios=kerdoios, leases=[{"placement_id": ["x"]}, 3])
    json.dumps(snap, allow_nan=False)
    assert snap["offers"][0]["quota"] == {}
    assert snap["leases"] == []


def test_normaliser_exception_marks_only_that_source_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cs, "_normalize_host", boom)
    snap = _build(tern=_tern(), kerdoios=_kerdoios())
    assert snap["sources"]["tern"]["status"] == "error"
    assert snap["sources"]["kerdoios"]["status"] == "ok"
    assert snap["offers"]


# --------------------------------------------------------------------------- (6) gpu unknown


def test_gpu_count_is_unknown_when_nvidia_smi_is_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    snap = build_snapshot(include_local=True, now=NOW)
    host = snap["hosts"][0]
    assert host["resources"]["gpu_count"] is None
    assert host["resources"]["gpus"] is None
    local_offer = next(o for o in snap["offers"] if o["origin"] == "host")
    assert local_offer["resources"]["gpu_count"] is None
    assert local_offer["resources"]["gpus"] is None


def test_gpu_count_is_unknown_when_nvidia_smi_fails(monkeypatch):
    class Failed:
        returncode = 9
        stdout = ""

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: Failed())
    assert cs.probe_local_host(now=NOW)["resources"]["gpu_count"] is None


def test_gpu_count_reflects_a_successful_probe(monkeypatch):
    class Ok:
        returncode = 0
        stdout = "GPU-aaaa, RTX 4090, 24564, 1000, 7\n"

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: Ok())
    resources = cs.probe_local_host(now=NOW)["resources"]
    assert resources["gpu_count"] == 1
    assert resources["gpus"][0]["name"] == "RTX 4090"


def test_remote_host_without_gpu_facts_does_not_report_zero():
    tern = _tern()
    tern["hosts"][0]["resources"] = {"cpu_logical": 8, "gpu_count": "many"}
    host = next(h for h in _build(tern=tern)["hosts"] if h["host_id"] == "tern:groot")
    assert host["resources"]["gpu_count"] is None


def test_an_identifier_with_a_lone_surrogate_costs_no_other_row():
    tern = _tern()
    tern["hosts"].append({"host_id": "tern:gpu-box-2", "label": "gpu-box-2", "status": "offline",
                          "observed_at": NOW - 3})
    tern["sessions"] = [
        {"session_id": "s1", "host_id": "tern:groot", "status": "locked", "observed_at": NOW - 3},
        {"session_id": "bad\ud83d", "host_id": "tern:groot", "status": "locked", "observed_at": NOW - 3},
    ]

    snap = _build(tern=tern)

    assert snap["sources"]["tern"]["status"] != "error"
    assert {h["host_id"] for h in snap["hosts"]} >= {"tern:groot", "tern:gpu-box-2"}
    sessions = {s["session_id"]: s for s in snap["sessions"]}
    assert "s1" in sessions
    stand_ins = [s for s in snap["sessions"] if s.get("id_redacted")]
    assert len(stand_ins) == 1 and stand_ins[0]["session_id"].startswith("redacted-")
    # The published snapshot is still plain UTF-8 JSON
    json.dumps(snap, allow_nan=False).encode("utf-8")


@pytest.mark.parametrize("raw", ["/etc/shadow", "/abs/path/x", "~/secrets", "~root"])
def test_a_path_shaped_host_name_is_never_published_behind_the_tern_prefix(raw):
    tern = _tern()
    tern["hosts"].append({"name": raw, "status": "offline", "observed_at": NOW - 3})
    tern["sessions"] = [{"session_id": "s9", "host": raw, "status": "locked", "observed_at": NOW - 3}]

    text = json.dumps(_build(tern=tern))

    assert raw not in text
    assert f"tern:{raw}" not in text


@pytest.mark.parametrize("host_key,host_value,session_host", [
    ("name", "/etc/shadow", "tern:/etc/shadow"),
    ("host_id", "tern:/etc/shadow", "/etc/shadow"),
    ("name", "/etc/shadow", "/etc/shadow"),
    ("host_id", "tern:/etc/shadow", "tern:/etc/shadow"),
    ("name", "gpu-box-2", "tern:gpu-box-2"),
    ("host_id", "tern:gpu-box-2", "gpu-box-2"),
])
def test_a_host_and_its_sessions_join_whichever_spelling_each_side_uses(host_key, host_value, session_host):
    tern = _tern()
    tern["hosts"] = [{host_key: host_value, "status": "offline", "observed_at": NOW - 3}]
    tern["sessions"] = [{"session_id": "s1", "host": session_host, "status": "locked", "observed_at": NOW - 3}]

    snap = _build(tern=tern)

    host_ids = {h["host_id"] for h in snap["hosts"]}
    session = next(s for s in snap["sessions"] if s["session_id"] == "s1")
    assert session["host_id"] in host_ids
    assert "/etc/shadow" not in json.dumps(snap)
