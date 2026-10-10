"""The hardened capacity snapshot must never publish less than the PR head did.

A row is never dropped for the shape of its identifier: it is kept under a stable
stand-in. Synthetic fixtures only. A socket guard fails any non-loopback connection.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

from z0int import capacity_snapshot as cs
from z0int.capacity_snapshot import build_snapshot

NOW = 1_800_000_000.0
DAY = 86_400.0

# tests/fixtures/capacity_snapshot_pr_head.py is src/z0int/capacity_snapshot.py at the PR head
# (46a50108f569e67a2d8b56b9b35d982f94d701cc), byte for byte. This is its git blob id.
PR_HEAD_FIXTURE = Path(__file__).parent / "fixtures" / "capacity_snapshot_pr_head.py"
PR_HEAD_BLOB = "03d79604078f9a4082917bd860c1dc719ade2c69"

AWS_STYLE = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_STYLE_PLAIN = "wJalrXUtnFEMIzK7MDENGzbPxRfiCYEXAMPLEKEY"
HEX32 = "0123456789abcdef0123456789abcdef"
GOOGLE_STYLE = "AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY"
SECRET_SHAPED = [
    AWS_STYLE,
    AWS_STYLE_PLAIN,
    HEX32,
    GOOGLE_STYLE,
    "password=hunter2",
    "https://user:hunter2@example.test/x",
    "https://example.test/x?token=abc123",
    "password: hunter2",
    "api_key:9f8e7d6c5b4a39281706",
    "Bearer abc123def456",
]
ORDINARY = ["build", "gpu-box-2", "task-refactor-parser-7", "desk-workstation-01", "disk-usage-monitor-1",
            "risk-engine-batch-3"]
# Identifiers the PR head published and the previous hardening silently dropped.
EMBEDDED_SK = ["task-refactor-parser-7", "desk-workstation-01", "disk-usage-monitor-1", "risk-engine-batch-3"]
AWKWARD_IDS = [
    "a" * 20 + "0123456789abcdef0123" + "f" * 8,  # 48 hex characters
    "Zq9" * 16,  # 48 alphanumerics
    "someone@example.test",
    "/srv/pool/alpha",
    "~alpha",
    "n" * 129,
    1.5,
    True,
    *SECRET_SHAPED,
]


@pytest.fixture(autouse=True)
def _socket_guard(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if str(host) not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError(f"non-loopback connection attempted: {address!r}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)


def _stand_in(raw: object) -> str:
    return cs._stable_id("redacted", str(raw))


def _build(tern=None, kerdoios=None, leases=None):
    return build_snapshot(include_local=False, now=NOW, tern=tern, kerdoios=kerdoios, leases=leases)


def _tern(hosts=(), sessions=()):
    return {"observed_at": NOW - 2, "hosts": list(hosts), "sessions": list(sessions)}


def _kerdoios(entries):
    return {"generated_at": NOW - 5, "entries": list(entries)}


def _entry(**extra):
    return {"provider": "groq", "model": "free/model", "health": "ok", "updated_at": NOW - 6, **extra}


def _leases(rows):
    return {"observed_at": NOW - 1, "leases": list(rows)}


# --------------------------------------------------------------------------- (1) rows are never dropped


def test_ordinary_names_containing_sk_dash_are_published_verbatim():
    for raw in EMBEDDED_SK:
        snap = _build(
            tern=_tern(
                hosts=[{"host_id": raw, "status": "offline", "label": raw}],
                sessions=[{"session_id": raw, "name": raw, "host_id": raw, "status": "locked", "locked": True}],
            ),
            kerdoios=_kerdoios([_entry(offer_id=raw, provider=raw, model=raw, health="exhausted")]),
            leases=_leases([{"placement_id": raw, "offer_id": raw, "status": "active", "selection_reason": raw}]),
        )
        host = snap["hosts"][0]
        assert (host["host_id"], host["label"], host["status"]) == (raw, raw, "offline")
        session = snap["sessions"][0]
        assert (session["session_id"], session["name"]) == (raw, raw)
        assert (session["status"], session["locked"]) == ("locked", True)
        assert session["host_id"] == f"tern:{raw}"
        offer = snap["offers"][0]
        assert (offer["offer_id"], offer["provider"], offer["model"], offer["health"]) == (raw, raw, raw, "exhausted")
        lease = snap["leases"][0]
        assert (lease["placement_id"], lease["offer_id"], lease["status"], lease["selection_reason"]) == (
            raw, raw, "active", raw)
        assert "id_redacted" not in json.dumps(snap), raw
        assert snap["status"] == "ok", raw


def test_awkward_identifier_keeps_the_row_under_a_stand_in():
    for raw in AWKWARD_IDS:
        snap = _build(
            tern=_tern(
                hosts=[{"host_id": raw, "status": "offline", "label": "box"}],
                sessions=[{"session_id": raw, "name": "work", "status": "locked", "locked": True, "tab_count": 2}],
            ),
            kerdoios=_kerdoios([_entry(offer_id=raw, health="exhausted")]),
            leases=_leases([{"placement_id": raw, "status": "active", "policy_revision": "r7"}]),
        )
        stand_in = _stand_in(raw)
        assert stand_in.startswith("redacted-") and len(stand_in) == len("redacted-") + 20

        assert [(h["host_id"], h["status"], h["label"], h.get("id_redacted")) for h in snap["hosts"]] == [
            (stand_in, "offline", "box", True)], raw
        assert [(s["session_id"], s["status"], s["locked"], s["name"], s["tab_count"], s.get("id_redacted"))
                for s in snap["sessions"]] == [(stand_in, "locked", True, "work", 2, True)], raw
        assert [(o["offer_id"], o["health"], o["provider"], o["model"], o.get("id_redacted"))
                for o in snap["offers"]] == [(stand_in, "exhausted", "groq", "free/model", True)], raw
        assert [(row["placement_id"], row["status"], row["policy_revision"], row.get("id_redacted"))
                for row in snap["leases"]] == [(stand_in, "active", "r7", True)], raw

        for name in ("tern", "kerdoios", "leases"):
            assert snap["sources"][name]["status"] == "ok", (raw, name)
            assert "rows_dropped" not in snap["sources"][name], (raw, name)
        if isinstance(raw, str):
            assert raw not in json.dumps(snap), raw


def test_provider_and_model_that_fail_the_label_policy_keep_the_offer():
    for raw in AWKWARD_IDS:
        if not isinstance(raw, str):
            continue
        snap = _build(kerdoios=_kerdoios([_entry(provider=raw, model=raw, health="exhausted")]))
        assert len(snap["offers"]) == 1, raw
        offer = snap["offers"][0]
        assert offer["health"] == "exhausted"
        assert offer["provider"] == offer["model"] == offer["actor_id"] == _stand_in(raw), raw
        assert raw not in json.dumps(snap), raw
        assert snap["sources"]["kerdoios"]["status"] == "ok"


def test_references_between_rows_use_the_same_stand_in():
    host_raw, offer_raw = "ops@example.test", HEX32 + HEX32
    snap = _build(
        tern=_tern(
            hosts=[{"name": host_raw, "state": "connected"}],
            sessions=[{"id": 7, "host": host_raw, "pane_id": "%3"}, {"id": 8, "host_id": f"tern:{host_raw}"}],
        ),
        kerdoios=_kerdoios([_entry(offer_id=offer_raw)]),
        leases=_leases([{"placement_id": "pl-1", "demand_id": offer_raw, "offer_id": offer_raw,
                         "selected_offer_id": offer_raw, "status": "active"}]),
    )
    host_id = _stand_in(f"tern:{host_raw}")
    assert [host["host_id"] for host in snap["hosts"]] == [host_id]
    assert [session["host_id"] for session in snap["sessions"]] == [host_id, host_id]
    assert all("id_redacted" not in session for session in snap["sessions"])
    assert snap["sessions"][0]["pane_id"] == _stand_in("%3")
    lease = snap["leases"][0]
    assert snap["offers"][0]["offer_id"] == _stand_in(offer_raw)
    assert lease["offer_id"] == lease["selected_offer_id"] == lease["demand_id"] == _stand_in(offer_raw)
    assert "id_redacted" not in lease
    assert host_raw not in json.dumps(snap) and offer_raw not in json.dumps(snap)


@pytest.mark.parametrize("bad", [{"k": "v"}, ["v"]])
def test_container_typed_id_is_the_only_dropped_row_and_degrades_the_source(bad):
    snap = _build(
        tern=_tern(
            hosts=[{"host_id": bad, "status": "offline"}, {"host_id": "tern:groot", "status": "online"}],
            sessions=[{"session_id": bad, "status": "locked"}, {"session_id": "s1", "status": "idle"}],
        ),
        kerdoios=_kerdoios([_entry(offer_id=bad), _entry(provider=bad), _entry(offer_id="offer-1")]),
        leases=_leases([{"placement_id": bad, "status": "active"}, {"placement_id": "pl-1", "status": "active"}]),
    )
    assert [host["host_id"] for host in snap["hosts"]] == ["tern:groot"]
    assert [session["session_id"] for session in snap["sessions"]] == ["s1"]
    assert [offer["offer_id"] for offer in snap["offers"]] == ["offer-1"]
    assert [lease["placement_id"] for lease in snap["leases"]] == ["pl-1"]
    for name, dropped in (("tern", 2), ("kerdoios", 2), ("leases", 1)):
        source = snap["sources"][name]
        assert source["status"] == "degraded", name
        assert source["rows_dropped"] == dropped, name
        assert source["reason"] == "row_id_not_scalar", name
    assert snap["status"] == "degraded"


# --------------------------------------------------------------------------- (2) quota dimensions


def test_awkward_dimension_names_are_kept_under_a_stand_in():
    names = [name for name in AWKWARD_IDS if isinstance(name, str)] + ["x" * 49]
    dimensions = {name: {"limit": 10, "remaining": 0, "source": "provider"} for name in names}
    dimensions["rpd"] = {"limit": 50, "remaining": 7}
    dimensions["task-refactor-parser-7"] = {"limit": 5, "remaining": 0}
    snap = _build(kerdoios=_kerdoios([_entry(quota={"dimensions": dimensions})]))
    out = snap["offers"][0]["quota"]["dimensions"]
    assert out["rpd"] == {"limit": 50, "remaining": 7}
    assert out["task-refactor-parser-7"] == {"limit": 5, "remaining": 0}
    for name in names:
        assert out[_stand_in(name)] == {"limit": 10, "remaining": 0, "source": "provider", "id_redacted": True}, name
        assert name not in json.dumps(snap), name
    assert len(out) == len(dimensions)
    assert snap["sources"]["kerdoios"]["status"] == "ok"


def test_dimension_cap_keeps_exhausted_dimensions_and_degrades_the_source():
    dimensions = {f"dim-{index:02d}": {"limit": 10, "remaining": 5} for index in range(38)}
    dimensions["zz-exhausted"] = {"limit": 10, "remaining": 0}
    dimensions["zz-overdrawn"] = {"limit": 10, "remaining": -3}
    snap = _build(kerdoios=_kerdoios([_entry(quota={"dimensions": dimensions}), _entry(model="other", quota={
        "dimensions": {f"d{index}": {"remaining": 1} for index in range(33)}})]))
    out = next(o for o in snap["offers"] if o["model"] == "free/model")["quota"]["dimensions"]
    assert len(out) == 32
    assert out["zz-exhausted"]["remaining"] == 0
    assert out["zz-overdrawn"]["remaining"] == -3
    assert "dim-00" in out and "dim-37" not in out
    source = snap["sources"]["kerdoios"]
    assert source["status"] == "degraded"
    assert source["dimensions_truncated"] == 8 + 1
    assert source["reason"] == "dimensions_truncated"
    assert snap["status"] == "degraded"

    # Exactly at the cap nothing is cut and nothing is degraded.
    dimensions = {f"dim-{index:02d}": {"limit": 10, "remaining": 0} for index in range(32)}
    snap = _build(kerdoios=_kerdoios([_entry(quota={"dimensions": dimensions})]))
    assert len(snap["offers"][0]["quota"]["dimensions"]) == 32
    assert snap["sources"]["kerdoios"] == {"status": "ok", "observed_at": NOW - 5, "age_s": 5.0}


# --------------------------------------------------------------------------- (3) falsy-skipping enumerations


def test_empty_status_falls_through_to_state_as_at_the_pr_head():
    snap = _build(
        tern=_tern(
            hosts=[{"host_id": "tern:a", "status": "", "state": "offline"}],
            sessions=[
                {"session_id": "s1", "status": "", "state": "locked"},
                {"session_id": "s2", "status": 0, "state": "locked"},
                {"session_id": "s3", "status": "", "locked": True},
                {"session_id": "s4", "status": "", "state": ""},
            ],
        ),
        kerdoios=_kerdoios([
            _entry(model="m1", health="", status="exhausted"),
            _entry(model="m2", health=0, status="exhausted"),
        ]),
    )
    assert snap["hosts"][0]["status"] == "offline"
    statuses = {session["session_id"]: session["status"] for session in snap["sessions"]}
    assert statuses == {"s1": "locked", "s2": "locked", "s3": "locked", "s4": "unknown"}
    assert [offer["health"] for offer in snap["offers"]] == ["exhausted", "exhausted"]


def test_tab_count_matches_the_pr_head_exactly():
    # The PR head read `tab_count or tabs`: a zero tab_count falls through to tabs (and to None without one).
    cases = [{"tab_count": 0}, {"tab_count": 0, "tabs": 5}, {"tab_count": 0, "tabs": 0}, {"tabs": 0},
             {"tab_count": 4, "tabs": 9}, {"tabs": 2}, {}]
    seen = []
    for fields in cases:
        tern = _tern(sessions=[{"session_id": "s1", **fields}])
        expected = _pr_head().build_snapshot(include_local=False, now=NOW, tern=tern)["sessions"][0]["tab_count"]
        assert _build(tern=tern)["sessions"][0]["tab_count"] == expected, fields
        seen.append(expected)
    assert seen == [None, 5, 0, 0, 4, 2, None]


# --------------------------------------------------------------------------- (4) label policy


def test_secret_shaped_strings_do_not_pass_free_text_labels():
    for secret in SECRET_SHAPED:
        snap = _build(
            tern=_tern(
                hosts=[{"host_id": "tern:a", "label": secret, "name": secret, "status": "online",
                        "resources": {"gpus": [{"id": "GPU-1", "name": secret}]}}],
                sessions=[{"session_id": "s1", "name": secret, "runtime": secret, "program": secret,
                           "status": "locked"}],
            ),
            kerdoios=_kerdoios([_entry(offer_id="offer-1", health="exhausted")]),
            leases=_leases([{"placement_id": "pl-1", "status": "active", "selection_reason": secret,
                             "policy_revision": secret}]),
        )
        assert secret not in json.dumps(snap), secret
        assert snap["hosts"][0]["label"] is None and snap["hosts"][0]["status"] == "online"
        assert snap["hosts"][0]["resources"]["gpus"][0] == {
            "id": "GPU-1", "name": None, "memory_total_bytes": None, "memory_free_bytes": None,
            "utilization_pct": None}
        session = snap["sessions"][0]
        assert (session["name"], session["runtime"], session["status"]) == (None, None, "locked")
        lease = snap["leases"][0]
        assert (lease["selection_reason"], lease["policy_revision"], lease["status"]) == (None, None, "active")


def _published_name(value):
    return _build(tern=_tern(sessions=[{"session_id": "s1", "name": value}]))["sessions"][0]["name"]


def test_label_policy_keeps_ordinary_names_and_refuses_credential_shapes():
    kept = ORDINARY + [
        "cheapest free offer", "RTX 4090", "free/model", "omp:abc", "tern:groot", "GPU-1", "v1.2_rc",
        "llama-3.3-70b-versatile", "meta-llama/llama-4-scout-17b-16e-instruct",
        "GPU-8f3c1a2b-1234-5678-9abc-def012345678", "omp:8F3C1A2B-1234-5678-9ABC-DEF012345678",
    ]
    for name in kept:
        assert _published_name(name) == name, name
    assert _published_name(42) == "42"
    refused = SECRET_SHAPED + [
        "sk-abcdefghijkl", "key sk-abcdefghijkl", "ghp_abcdefghijklmnopqrst", "xoxb-1234567890",
        "AKIAABCDEFGHIJKLMNOP", "a" * 40, "eyJhbGciOiJIUzI1NiIsInR5.abc", "b@example.test", "/home/alpha/x",
        "~/x", "x" * 129, "line\nbreak", "a//b", "tab\there", "", "   ", "Meta-Llama/Llama-4-Scout-17B-16E-Instr",
        ["build"], {"name": "build"}, 1.5, True,
    ]
    for value in refused:
        assert _published_name(value) is None, value


# --------------------------------------------------------------------------- differential against the PR head


def _pr_head():
    name = "z0int._capacity_snapshot_pr_head"
    if name not in sys.modules:
        data = PR_HEAD_FIXTURE.read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        assert blob == PR_HEAD_BLOB, "the PR-head oracle fixture must stay byte-identical to the PR head"
        spec = importlib.util.spec_from_file_location(name, PR_HEAD_FIXTURE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _broad_input():
    ids = [*ORDINARY, *AWKWARD_IDS, "plain", 42, 0.25, "omp:abc", "a b", " padded ", "ünï", "%3", "x=y",
           "8f3c1a2b-1234-5678-9abc-def012345678", "sk-abcdefghijklmnop", "gpu-box-2.lan:22"]
    host_states = [{"status": "offline"}, {"status": "", "state": "offline"}, {"state": "connected"},
                   {"status": "degraded"}, {"status": "online"}, {}]
    session_states = [
        {"status": "locked", "locked": True}, {"status": "", "state": "locked"}, {"locked": True},
        {"current": True, "locked": True}, {"current": True}, {"status": "running", "current": False},
        {"status": 0, "state": "current_locked"}, {},
    ]
    health_states = [{"health": "exhausted"}, {"health": "", "status": "exhausted"},
                     {"health": 0, "status": "rate_limited"}, {"status": "cooldown"}, {"health": "ok"}, {}]
    lease_states = ["active", "leased", "expired", "revoked"]

    hosts, sessions, entries, leases = [], [], [], []
    for index, raw in enumerate(ids):
        key = ("host_id", "id", "name", "label")[index % 4]
        # Booleans and floats are only identifiers where the PR head stringified them.
        host_key = key if isinstance(raw, str) else "host_id"
        hosts.append({host_key: raw, **host_states[index % len(host_states)], "observed_at": NOW - 3})
        sessions.append({
            ("session_id", "id")[index % 2]: raw,
            ("host_id", "host")[index % 2]: raw,
            "name": raw if isinstance(raw, str) else "work",
            **session_states[index % len(session_states)],
            "tab_count": index % 3,
            "tabs": index % 2,
            "observed_at": NOW - 3,
        })
        dimensions = {
            str(other): {"limit": 10, "remaining": 0 if (index + position) % 3 == 0 else 4, "reset_at": NOW + DAY}
            for position, other in enumerate(ids[: 6 + index % 5])
        }
        entry = {
            "provider": raw if index % 2 and isinstance(raw, str) else "groq",
            "model": raw if isinstance(raw, str) else f"model-{index}",
            **health_states[index % len(health_states)],
            "quota": {"remaining": 0 if index % 4 == 0 else 9, "limit": 10, "reset_at": NOW + DAY,
                      "dimensions": dimensions},
            "reset_at": NOW + DAY,
            "updated_at": NOW - 6,
        }
        if index % 3:
            entry["offer_id"] = raw
        entries.append(entry)
        leases.append({
            "placement_id": raw, "demand_id": raw, "offer_id": raw, "selected_offer_id": raw,
            "status": lease_states[index % len(lease_states)], "lease_expires_at": NOW + 60,
        })
    return {
        "tern": {"observed_at": NOW - 2, "hosts": hosts, "sessions": sessions},
        "kerdoios": {"generated_at": NOW - 5, "entries": entries},
        "leases": {"observed_at": NOW - 1, "leases": leases},
    }


def _find(rows, key, old_id):
    """The row published for a PR-head id: under the id itself, or under its stand-in."""
    old_id = str(old_id)  # the PR head published lease ids with their JSON type; ids are strings now
    matches = [row for row in rows if row[key] in {old_id, _stand_in(old_id)}]
    assert len(matches) == 1, f"{key}={old_id!r}: {len(matches)} rows in the hardened snapshot"
    row = matches[0]
    assert row.get("id_redacted", False) is (row[key] != old_id), old_id
    return row


def _same_ref(new, old):
    assert new in ({None} if old is None else {str(old), _stand_in(old)}), (new, old)


def test_nothing_the_pr_head_published_is_missing_from_the_hardened_snapshot():
    docs = _broad_input()
    old = _pr_head().build_snapshot(include_local=False, now=NOW, **json.loads(json.dumps(docs)))
    new = build_snapshot(include_local=False, now=NOW, **docs)
    json.dumps(new, allow_nan=False)

    assert len(old["hosts"]) > 20 and len(old["sessions"]) > 20 and len(old["offers"]) > 20 and len(old["leases"]) > 20
    for kind in ("hosts", "sessions", "offers", "leases"):
        assert len(new[kind]) == len(old[kind]), kind
    assert new["summary"] == old["summary"]
    for name in ("tern", "kerdoios", "leases"):
        assert new["sources"][name]["status"] == "ok", (name, new["sources"][name])
    assert new["status"] == old["status"] == "ok"

    facts = 0
    for host in old["hosts"]:
        row = _find(new["hosts"], "host_id", host["host_id"])
        assert row["status"] == host["status"], host
        assert row["mode"] == host["mode"]
        facts += 1
    for session in old["sessions"]:
        row = _find(new["sessions"], "session_id", session["session_id"])
        for key in ("status", "current", "locked", "tab_count", "session_sticky", "migration_allowed"):
            assert row[key] == session[key], (key, session)
            facts += 1
        _same_ref(row["host_id"], session["host_id"])
        assert any(host["host_id"] == row["host_id"] for host in new["hosts"]) == any(
            host["host_id"] == session["host_id"] for host in old["hosts"]), session
    for offer in old["offers"]:
        row = _find(new["offers"], "offer_id", offer["offer_id"])
        assert row["health"] == offer["health"], offer
        _same_ref(row["provider"], offer["provider"])
        _same_ref(row["model"], offer["model"])
        assert row["quota"]["remaining"] == offer["quota"]["remaining"]
        assert len(row["quota"]["dimensions"]) == len(offer["quota"]["dimensions"]), offer["offer_id"]
        for name, dimension in offer["quota"]["dimensions"].items():
            kept = row["quota"]["dimensions"].get(name, row["quota"]["dimensions"].get(_stand_in(name)))
            assert kept is not None, (offer["offer_id"], name)
            assert kept["remaining"] == dimension["remaining"] and kept["limit"] == dimension["limit"], name
            facts += 1
    for lease in old["leases"]:
        row = _find(new["leases"], "placement_id", lease["placement_id"])
        assert row["status"] == lease["status"], lease
        for key in ("demand_id", "offer_id", "selected_offer_id"):
            _same_ref(row[key], lease[key])
        assert row["lease_expires_at"] == lease["lease_expires_at"]
        facts += 1
    assert facts > 400

    exhausted_old = sum(offer["health"] == "exhausted" for offer in old["offers"])
    locked_old = sum(session["status"] in {"locked", "current_locked"} for session in old["sessions"])
    offline_old = sum(host["status"] == "offline" for host in old["hosts"])
    assert exhausted_old > 5 and locked_old > 5 and offline_old > 5
    assert sum(offer["health"] == "exhausted" for offer in new["offers"]) == exhausted_old
    assert sum(session["status"] in {"locked", "current_locked"} for session in new["sessions"]) == locked_old
    assert sum(host["status"] == "offline" for host in new["hosts"]) == offline_old

    encoded = json.dumps(new)
    for secret in SECRET_SHAPED + ["someone@example.test", "sk-abcdefghijklmnop"]:
        assert secret not in encoded, secret
