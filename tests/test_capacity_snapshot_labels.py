"""A path is never published as a label, whatever is written in front of it.

Every free-text field the snapshot publishes is judged the way an identifier is: without any
leading tern: prefixes, and by a rule that knows a relative, dotted or drive-rooted path from
an ordinary name. Synthetic fixtures only.
"""
from __future__ import annotations

import json

import pytest

from z0int import capacity_snapshot as cs
from z0int.capacity_snapshot import build_snapshot

NOW = 1_800_000_000.0

PREFIXES = ["", "tern:", "tern:tern:", "tern:tern:tern:"]
# Refused already when it leads the value; behind a prefix it was published.
ROOTED = ["/etc/shadow"]
# Published as text before this rule, with or without a prefix.
RELATIVE = [
    "etc/shadow", "./etc/shadow", "../../etc/shadow", "x:/etc/shadow", "C:/Users/x", ".ssh/id_rsa",
    "kev/.ssh/id_rsa", "a/../etc/shadow", "node /etc/shadow", "node ./etc/shadow", "node etc/shadow",
    "host:etc/shadow", "ETC/shadow", "Users/kev", "usr/bin/python", "var/log/syslog", "tmp/x",
    "proc/1/environ", "root/x", "home/kev", "sys/class", "mnt/data", "opt/app", "Volumes/x",
    "windows/system32", "C:/", "rack/tern:/b",
    "host:./etc/shadow", "host:.ssh/id_rsa", "a/etc/shadow", "backup/var/log",
    "node /data/x", "node ./data/x",
]
# Never a label: a backslash is outside the label alphabet.
BACKSLASH = ["C:\\Users\\x"]
ORDINARY = [
    "gpu-box-2", "task-refactor-parser-7", "build/arm64", "node 7", "a.b.c", "10.0.0.5:22", "k8s/ns/pod-1",
]
# Names that only resemble the refused shapes.
LOOKALIKES = [
    "free/model", "meta-llama/llama-4-scout-17b-16e-instruct", "models/gemini-1.5-pro", "etcd/member",
    "homelab/box", "my-etc/x", "a.b/c", "v1.2/rc", "cpu / gpu", "requests/min", ".hidden",
    "v1 .2", "a:b", "rack-2/etc",
    ".net build/arm64",
]

# Every free-text input key that reaches a label field, and the published field it lands in.
LABEL_KEYS = [
    "host.label", "host.name", "gpu.name", "session.name", "session.runtime", "session.program",
    "lease.selection_reason", "lease.policy_revision",
]
# Every free-text input key that is published as an identifier or a reference to one.
ID_KEYS = [
    "host.host_id", "host.name-as-id", "session.session_id", "session.host", "session.pane_id", "gpu.id",
    "offer.offer_id", "offer.provider", "offer.model", "offer.dimension", "lease.placement_id",
    "lease.demand_id", "lease.offer_id", "lease.selected_offer_id",
]


def _publish(key: str, raw: str):
    """(published value, whole snapshot) with `raw` planted in exactly one input key."""
    host = {"host_id": "h1", "status": "online", "observed_at": NOW - 2, "resources": {"gpus": [{"id": "g0"}]}}
    session = {"session_id": "s1", "host": "h1", "status": "locked", "observed_at": NOW - 2}
    entry = {"provider": "groq", "model": "m1", "offer_id": "o1", "health": "ok", "updated_at": NOW - 2,
             "quota": {"dimensions": {"rpm": {"limit": 1}}}}
    lease = {"placement_id": "p1", "status": "active"}
    kind, _, name = key.partition(".")
    if key == "host.name-as-id":
        host = {"name": raw, "status": "online", "observed_at": NOW - 2}
    elif kind == "host":
        host[name] = raw
    elif kind == "gpu":
        host["resources"]["gpus"][0][name] = raw
    elif kind == "session":
        session[name] = raw
    elif key == "offer.dimension":
        entry["quota"]["dimensions"] = {raw: {"limit": 1}}
    elif kind == "offer":
        entry[name] = raw
    else:
        lease[name] = raw
    snap = build_snapshot(
        tern={"observed_at": NOW - 1, "hosts": [host], "sessions": [session]},
        kerdoios={"generated_at": NOW - 1, "entries": [entry]},
        leases={"observed_at": NOW - 1, "leases": [lease]},
        include_local=False,
        now=NOW,
    )
    published = {
        "host.label": lambda: snap["hosts"][0]["label"],
        "host.name": lambda: snap["hosts"][0]["label"],
        "host.host_id": lambda: snap["hosts"][0]["host_id"],
        "host.name-as-id": lambda: snap["hosts"][0]["host_id"],
        "gpu.name": lambda: snap["hosts"][0]["resources"]["gpus"][0]["name"],
        "gpu.id": lambda: snap["hosts"][0]["resources"]["gpus"][0]["id"],
        "session.name": lambda: snap["sessions"][0]["name"],
        "session.runtime": lambda: snap["sessions"][0]["runtime"],
        "session.program": lambda: snap["sessions"][0]["runtime"],
        "session.session_id": lambda: snap["sessions"][0]["session_id"],
        "session.host": lambda: snap["sessions"][0]["host_id"],
        "session.pane_id": lambda: snap["sessions"][0]["pane_id"],
        "offer.offer_id": lambda: snap["offers"][0]["offer_id"],
        "offer.provider": lambda: snap["offers"][0]["provider"],
        "offer.model": lambda: snap["offers"][0]["model"],
        "offer.dimension": lambda: next(iter(snap["offers"][0]["quota"]["dimensions"])),
        "lease.placement_id": lambda: snap["leases"][0]["placement_id"],
        "lease.demand_id": lambda: snap["leases"][0]["demand_id"],
        "lease.offer_id": lambda: snap["leases"][0]["offer_id"],
        "lease.selected_offer_id": lambda: snap["leases"][0]["selected_offer_id"],
        "lease.selection_reason": lambda: snap["leases"][0]["selection_reason"],
        "lease.policy_revision": lambda: snap["leases"][0]["policy_revision"],
    }[key]()
    return published, snap


def test_the_enumerated_keys_are_every_label_and_identifier_the_normalizers_read():
    # A new free-text field must be added to the lists above, so it is judged by these tests too.
    assert cs._LEASE_LABEL_KEYS == ("selection_reason", "policy_revision")
    assert cs._LEASE_REF_KEYS == ("demand_id", "offer_id", "selected_offer_id")
    assert len(LABEL_KEYS) == 8 and len(ID_KEYS) == 14


@pytest.mark.parametrize("key", LABEL_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES[1:])
@pytest.mark.parametrize("path", ROOTED + RELATIVE + BACKSLASH)
def test_a_path_behind_any_number_of_tern_prefixes_is_not_published_as_a_label(key, prefix, path):
    published, snap = _publish(key, prefix + path)

    assert published is None
    assert path not in json.dumps(snap)


@pytest.mark.parametrize("key", LABEL_KEYS)
@pytest.mark.parametrize("path", ROOTED + RELATIVE + BACKSLASH)
def test_a_path_is_not_published_as_a_label(key, path):
    published, snap = _publish(key, path)

    assert published is None
    assert path not in json.dumps(snap)


@pytest.mark.parametrize("key", ID_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("path", ROOTED + RELATIVE + BACKSLASH)
def test_a_path_used_as_an_identifier_is_published_only_as_a_stand_in(key, prefix, path):
    published, snap = _publish(key, prefix + path)

    assert published.startswith("redacted-") and len(published) == len("redacted-") + 20
    assert path not in json.dumps(snap)


@pytest.mark.parametrize("key", LABEL_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("name", ORDINARY + LOOKALIKES)
def test_an_ordinary_name_stays_readable_as_a_label(key, prefix, name):
    assert _publish(key, prefix + name)[0] == prefix + name


@pytest.mark.parametrize("key", ID_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("name", ORDINARY + [n for n in LOOKALIKES if len(n) <= 30])
def test_an_ordinary_name_stays_readable_as_an_identifier(key, prefix, name):
    published, snap = _publish(key, prefix + name)

    added = "tern:" if key == "host.name-as-id" or (key == "session.host" and not prefix) else ""
    assert published == added + prefix + name
    assert "id_redacted" not in json.dumps(snap)


@pytest.mark.parametrize("key", LABEL_KEYS)
@pytest.mark.parametrize("raw", ["tern:", "tern:tern:", "tern: ", "tern: /etc/shadow", "tern: tern:/etc/shadow",
                                 " tern:/etc/shadow ", "tern:sk-live", "tern:token: x"])
def test_a_label_with_nothing_publishable_behind_its_prefixes_is_dropped(key, raw):
    assert _publish(key, raw)[0] is None


@pytest.mark.parametrize("key", LABEL_KEYS)
@pytest.mark.parametrize("raw,published", [
    ("tern:rocket", "tern:rocket"), ("tern: rocket", "tern: rocket"), ("  tern:rocket ", "tern:rocket"),
    ("tern", "tern"), ("terns:x", "terns:x"), ("lantern:x", "lantern:x"),
])
def test_a_prefixed_plain_label_is_published_as_written(key, raw, published):
    assert _publish(key, raw)[0] == published


def test_a_fallback_label_is_still_used_when_the_first_one_is_a_path():
    host = {"host_id": "h1", "label": "tern:/etc/shadow", "name": "rocket", "observed_at": NOW - 2}
    session = {"session_id": "s1", "runtime": "tern:etc/shadow", "program": "codex", "observed_at": NOW - 2}

    snap = build_snapshot(tern={"observed_at": NOW - 1, "hosts": [host], "sessions": [session]},
                          include_local=False, now=NOW)

    assert snap["hosts"][0]["label"] == "rocket"
    assert snap["sessions"][0]["runtime"] == "codex"
    assert "shadow" not in json.dumps(snap)


def test_a_redacted_host_row_carries_no_readable_copy_of_its_name():
    for prefix in PREFIXES:
        raw = prefix + "/etc/shadow"
        snap = build_snapshot(
            tern={"observed_at": NOW - 1,
                  "hosts": [{"host_id": raw, "name": raw, "label": raw, "observed_at": NOW - 2}],
                  "sessions": [{"session_id": "s1", "host": raw, "name": raw, "program": raw, "pane_id": raw,
                                "observed_at": NOW - 2}]},
            include_local=False, now=NOW,
        )
        host, session = snap["hosts"][0], snap["sessions"][0]

        assert host["id_redacted"] is True and host["label"] is None
        assert (session["host_id"] == host["host_id"]) is (prefix != "")  # "/x" is referenced as "tern:/x"
        assert (session["name"], session["runtime"]) == (None, None)
        assert session["pane_id"].startswith("redacted-")
        assert "shadow" not in json.dumps(snap)


@pytest.mark.parametrize("host_key", ["name", "host_id"])
@pytest.mark.parametrize("name", ROOTED + RELATIVE + ORDINARY)
def test_a_host_and_its_sessions_join_exactly_as_their_texts_say(host_key, name):
    spellings = [prefix + name for prefix in PREFIXES]
    for host_value in spellings:
        for session_host in spellings:
            snap = build_snapshot(
                tern={"observed_at": NOW - 1,
                      "hosts": [{host_key: host_value, "observed_at": NOW - 2}],
                      "sessions": [{"session_id": "s1", "host": session_host, "observed_at": NOW - 2}]},
                include_local=False, now=NOW,
            )
            host_text = host_value if host_key == "host_id" else "tern:" + host_value
            session_text = session_host if session_host.startswith("tern:") else "tern:" + session_host

            joined = snap["sessions"][0]["host_id"] == snap["hosts"][0]["host_id"]
            assert joined is (host_text == session_text), (host_value, session_host)


@pytest.mark.parametrize("text", ROOTED + RELATIVE + BACKSLASH + ["/", "a//b", "x/home/y", "x/Users/y", "a/.",
                                  "a/.."])
def test_the_label_rule_refuses_path_shapes(text):
    assert cs._label(text) is None


@pytest.mark.parametrize("text", ORDINARY + LOOKALIKES)
def test_the_label_rule_keeps_ordinary_names(text):
    assert cs._label(text) == text
