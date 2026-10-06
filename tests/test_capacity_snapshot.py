from __future__ import annotations

import json
import stat

from z0int.capacity_snapshot import SCHEMA, build_snapshot, write_snapshot
from z0int.cli import main as cli_main


def test_tern_sessions_are_sticky_and_not_implicitly_migratable():
    snap = build_snapshot(
        include_local=False,
        now=10.0,
        tern={
            "observed_at": 9.0,
            "hosts": [
                {
                    "host_id": "tern:groot",
                    "label": "groot",
                    "status": "online",
                    "rtt_ms": 4.2,
                }
            ],
            "sessions": [
                {
                    "session_id": "omp:abc",
                    "host_id": "tern:groot",
                    "runtime": "omp",
                    "status": "running",
                    "migration_allowed": True,
                }
            ],
        },
    )
    assert snap["schema"] == SCHEMA
    assert snap["hosts"][0]["host_id"] == "tern:groot"
    session = snap["sessions"][0]
    assert session["session_sticky"] is True
    assert session["migration_allowed"] is False


def test_kerdoios_unknown_is_not_zero_and_secrets_are_not_projected():
    snap = build_snapshot(
        include_local=False,
        now=20.0,
        kerdoios={
            "saved_at": 19.0,
            "entries": [
                {
                    "provider": "openrouter",
                    "model": "free/model",
                    "api_key": "must-not-leak",
                    "quota": {
                        "dimensions": {
                            "rpd": {
                                "limit": 50,
                                "remaining": None,
                                "reset_at": 100.0,
                                "source": "provider",
                                "secret": "nope",
                            }
                        }
                    },
                }
            ],
        },
    )
    offer = snap["offers"][0]
    assert offer["quota"]["dimensions"]["rpd"]["remaining"] is None
    encoded = json.dumps(snap)
    assert "must-not-leak" not in encoded
    assert '"secret"' not in encoded


def test_provider_offer_identity_is_stable_for_same_provider_and_model():
    raw = {"entries": [{"provider": "groq", "model": "m", "quota": {}}]}
    a = build_snapshot(include_local=False, now=1.0, kerdoios=raw)
    b = build_snapshot(include_local=False, now=2.0, kerdoios=raw)
    assert a["offers"][0]["offer_id"] == b["offers"][0]["offer_id"]


def test_atomic_private_write(tmp_path):
    snap = build_snapshot(include_local=False, now=1.0)
    path = tmp_path / "capacity_snapshot.json"
    written = write_snapshot(snap, path)
    assert written == path
    assert json.loads(path.read_text())["schema"] == SCHEMA
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o077 == 0


def test_root_cli_emits_snapshot(tmp_path, monkeypatch, capsys):
    for name in (
        "Z0INT_TERN_CAPACITY_SNAPSHOT",
        "Z0INT_KERDOIOS_CAPACITY_SNAPSHOT",
        "Z0INT_PLACEMENT_LEASES_SNAPSHOT",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "cli-capacity.json"
    rc = cli_main([
        "capacity",
        "snapshot",
        "--no-local",
        "--output",
        str(path),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == SCHEMA
    assert payload["path"] == str(path)
    assert json.loads(path.read_text())["schema"] == SCHEMA


def test_native_tern_session_shape_is_normalized():
    snap = build_snapshot(
        include_local=False,
        now=30.0,
        tern={
            "hosts": [{"name": "groot", "state": "connected", "rtt_ms": 3.5}],
            "sessions": [{
                "id": 42,
                "name": "omp-work",
                "host": "groot",
                "current": True,
                "locked": False,
                "tabs": 3,
            }],
        },
    )
    host = snap["hosts"][0]
    session = snap["sessions"][0]
    assert host["label"] == "groot"
    assert session["name"] == "omp-work"
    assert session["host_id"] == "groot"
    assert session["status"] == "current"
    assert session["tab_count"] == 3
    assert session["migration_allowed"] is False
