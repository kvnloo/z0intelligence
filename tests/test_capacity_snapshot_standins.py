"""An id the snapshot makes up is never taken from input.

A stand-in ("redacted-<20 hex>"), the local host id ("host-<20 hex>") and a synthesized offer id
("offer-<20 hex>") are made by the module. An input value of the same shape gets a stand-in of its
own, so two different raw values never publish one id. Also here: the system roots a relative path
may start with, and the conditions of the label rule and prefix handling no other test held.
Synthetic fixtures only.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil

import pytest

from test_capacity_snapshot_labels import ID_KEYS, LABEL_KEYS, PREFIXES, _publish
from z0int import capacity_snapshot as cs
from z0int.capacity_snapshot import build_snapshot

NOW = 1_800_000_000.0
MADE_UP = re.compile(r"(redacted|host|offer)-[0-9a-f]{20}")


def _made(kind: str, *parts: str) -> str:
    return f"{kind}-{hashlib.sha256(chr(0).join(parts).encode()).hexdigest()[:20]}"


def _stand_in(text: str) -> str:
    return _made("redacted", text)


def _tern(hosts, sessions=()):
    return build_snapshot(tern={"observed_at": NOW - 1, "hosts": list(hosts), "sessions": list(sessions)},
                          include_local=False, now=NOW)


STAND_IN_SHAPED = [_stand_in("tern:etc/shadow"), _stand_in("etc/shadow"), _stand_in("h1"), "redacted-" + "0" * 20,
                   "redacted-0123456789abcdef0123"]
ADDED_ROOTS = ["bin", "sbin", "lib", "lib64", "dev", "run", "boot", "srv", "snap", "private", "library",
               "applications", "system", "programdata"]
OLDER_ROOTS = ["etc", "home", "root", "usr", "var", "tmp", "proc", "sys", "mnt", "opt", "users", "volumes", "windows"]
UNCOVERED_PATHS = ["dev/sda", "bin/bash", "lib/systemd/system", "run/secrets/db"]
MUST_STAY_READABLE = ["build/arm64", "k8s/ns/pod-1", "lib-a", "dev-box", "run 7", "system-a", "bin", "dev",
                      "release/1.2", "team/system-x"]


# --------------------------------------------------------------------------- (1) made-up ids


def test_a_host_id_that_is_another_hosts_stand_in_does_not_merge_the_two_rows():
    twin = _stand_in("tern:etc/shadow")
    snap = _tern([{"host_id": twin, "label": "impostor"}, {"name": "etc/shadow", "label": "real"}],
                 [{"session_id": "s1", "host": "etc/shadow"}])

    by_label = {host["label"]: host for host in snap["hosts"]}
    assert set(by_label) == {"impostor", "real"}
    assert by_label["real"]["host_id"] == twin
    assert by_label["impostor"]["host_id"] == _stand_in(twin) and by_label["impostor"]["id_redacted"] is True
    assert snap["sessions"][0]["host_id"] == twin


@pytest.mark.parametrize("key", ID_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("raw", STAND_IN_SHAPED)
def test_a_value_shaped_like_a_stand_in_is_published_only_as_a_stand_in_of_its_own(key, prefix, raw):
    published, snap = _publish(key, prefix + raw)

    added = "tern:" if key == "host.name-as-id" or (key == "session.host" and not prefix) else ""
    assert published == _stand_in(added + prefix + raw)
    if key == "host.name-as-id":  # a host's name is also its label, and a label is not an id
        snap["hosts"][0]["label"] = None
    assert raw not in json.dumps(snap)


@pytest.mark.parametrize("raw", ["redacted-" + "0" * 19, "redacted-" + "0" * 20 + "-1", "redacted-" + "A" * 20,
                                 "redacted-" + "g" * 20, "redacted_" + "0" * 20, "redacted-box-2", "redacted",
                                 "x-redacted-" + "0" * 20, "redacted-" + "0" * 20 + ".1"])
def test_a_name_that_only_resembles_a_stand_in_stays_readable(raw):
    assert cs._ident(raw) == (raw, False)
    assert cs._ident(raw, own=True) == (raw, False)


def _generated() -> list[str]:
    names = ["gpu-box-2", "build/arm64", "node 7", "h1", "x", "etc/shadow", "/etc/shadow", "dev/sda", "a b",
             "C:\\Users\\x", "sk-live-1", "0", "tern", "redacted", "host", "offer", "Z" * 40, ""]
    level = [prefix + name for prefix in PREFIXES for name in names]
    out = list(level)
    for _ in range(3):  # ids made from the values so far, and ids made from those
        level = [_made(kind, value) for kind in ("redacted", "host", "offer") for value in level[:60]]
        out += [prefix + value for prefix in PREFIXES for value in level]
        out += [value.upper() for value in level] + [value[:-1] for value in level] + [value + "0" for value in level]
    return sorted(set(out))


@pytest.mark.parametrize("prefix", ["", "tern:"])
@pytest.mark.parametrize("own", [False, True])
def test_two_different_raw_values_never_publish_the_same_id(prefix, own):
    values = _generated()
    published = {value: cs._ident(value, prefix, own=own) for value in values}

    assert len(values) > 1500
    assert len({text for text, _ in published.values()}) == len(values)
    for value, (text, stand_in) in published.items():
        assert stand_in is (text != prefix + value)
        assert stand_in is bool(re.fullmatch(r"redacted-[0-9a-f]{20}", text)), value
        if own and not stand_in:
            assert not MADE_UP.fullmatch(text), value


def test_hosts_and_sessions_join_only_through_the_one_text_they_share():
    values = [value for value in _generated() if value.strip()]
    hosts = [{"host_id": value} for value in values] + [{"name": value} for value in values]
    sessions = [{"session_id": f"s{index}", "host": value} for index, value in enumerate(values)]
    snap = _tern(hosts, sessions)

    host_texts = set(values) | {"tern:" + value for value in values}
    assert len(snap["hosts"]) == len(host_texts)  # only "tern:x" by id and "x" by name are one host
    assert len({host["host_id"] for host in snap["hosts"]}) == len(host_texts)
    published = {text: cs._ident(text)[0] for text in host_texts}
    by_id = {cs._ident(value, own=True)[0] for value in values}
    assert {host["host_id"] for host in snap["hosts"]} == by_id | {published["tern:" + value] for value in values}
    for value, session in zip(values, sorted(snap["sessions"], key=lambda row: int(row["session_id"][1:]))):
        assert session["host_id"] == published[value if value.startswith("tern:") else "tern:" + value]


def test_an_exported_host_cannot_take_the_place_of_the_local_host(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(cs, "_machine_seed", lambda: "synthetic-seed")
    local_id = _made("host", "synthetic-seed")
    snap = build_snapshot(
        tern={"observed_at": NOW - 1,
              "hosts": [{"host_id": local_id, "label": "impostor", "status": "offline", "mode": "remote"}]},
        include_local=True, now=NOW)

    local = next(host for host in snap["hosts"] if host["host_id"] == local_id)
    other = next(host for host in snap["hosts"] if host["host_id"] != local_id)
    assert len(snap["hosts"]) == 2
    assert (local["mode"], local["status"]) == ("direct", "online") and local["label"] != "impostor"
    assert other["host_id"] == _stand_in(local_id) and other["id_redacted"] is True and other["label"] == "impostor"


def test_an_exported_offer_cannot_take_the_id_of_a_synthesized_offer(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(cs, "_machine_seed", lambda: "synthetic-seed")
    provider_offer = _made("offer", "provider", "groq", "m1")
    local_offer = _made("offer", "host", _made("host", "synthetic-seed"), "local.compute")
    snap = build_snapshot(
        kerdoios={"generated_at": NOW - 1, "entries": [
            {"provider": "groq", "model": "m1", "updated_at": NOW - 2},
            {"provider": "impostor-a", "offer_id": provider_offer, "updated_at": NOW - 2},
            {"provider": "impostor-b", "offer_id": local_offer, "updated_at": NOW - 2},
        ]},
        include_local=True, now=NOW)

    by_id = {offer["offer_id"]: offer for offer in snap["offers"]}
    assert len(by_id) == len(snap["offers"]) == 4
    assert by_id[provider_offer]["provider"] == "groq" and by_id[local_offer]["origin"] == "host"
    assert by_id[_stand_in(provider_offer)]["provider"] == "impostor-a"
    assert by_id[_stand_in(local_offer)]["provider"] == "impostor-b"
    assert by_id[_stand_in(local_offer)]["id_redacted"] is True


@pytest.mark.parametrize("kind", ["host", "offer"])
def test_a_row_id_shaped_like_any_synthesized_id_is_published_only_as_a_stand_in(kind):
    raw = _made(kind, "anything")

    assert _publish("host.host_id", raw)[0] == _stand_in(raw)
    assert _publish("offer.offer_id", raw)[0] == _stand_in(raw)
    for near in (raw[:-1], raw + "-0", raw.upper(), "tern:" + raw, raw.replace("-", "_"), "x-" + raw,
                 kind + "-0123456789ABCDEF0123", kind + "-0123456789abcdef012g"):
        assert cs._ident(near, own=True) == (near, False)


@pytest.mark.parametrize("key", [key for key in ID_KEYS if key not in ("host.host_id", "offer.offer_id")])
@pytest.mark.parametrize("kind", ["host", "offer"])
def test_a_synthesized_id_shape_is_refused_only_as_a_host_or_offer_row_id(key, kind):
    raw = _made(kind, "anything")
    published, snap = _publish(key, raw)

    assert published == ("tern:" + raw if key in ("host.name-as-id", "session.host") else raw)
    assert "id_redacted" not in json.dumps(snap)


def test_a_reference_to_a_synthesized_id_still_reaches_the_row_that_carries_it(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(cs, "_machine_seed", lambda: "synthetic-seed")
    local_id = _made("host", "synthetic-seed")
    provider_offer = _made("offer", "provider", "groq", "m1")
    local_offer = _made("offer", "host", local_id, "local.compute")
    snap = build_snapshot(
        tern={"observed_at": NOW - 1, "hosts": [{"name": local_id}],
              "sessions": [{"session_id": "s1", "host": local_id}]},
        kerdoios={"generated_at": NOW - 1, "entries": [{"provider": "groq", "model": "m1", "updated_at": NOW - 2}]},
        leases=[{"placement_id": "p1", "offer_id": provider_offer, "selected_offer_id": local_offer}],
        include_local=True, now=NOW)

    offer_ids = {offer["offer_id"] for offer in snap["offers"]}
    assert snap["leases"][0]["offer_id"] == provider_offer and provider_offer in offer_ids
    assert snap["leases"][0]["selected_offer_id"] == local_offer and local_offer in offer_ids
    # A session's host is a Tern name: it never reaches the local host, at this head or the one before.
    assert snap["sessions"][0]["host_id"] == "tern:" + local_id
    assert {host["host_id"] for host in snap["hosts"]} == {local_id, "tern:" + local_id}


def test_a_dimension_named_like_another_dimensions_stand_in_does_not_replace_it():
    twin = _stand_in("etc/shadow")
    entry = {"provider": "groq", "model": "m1", "updated_at": NOW - 2,
             "quota": {"dimensions": {twin: {"limit": 1}, "etc/shadow": {"limit": 2}}}}
    snap = build_snapshot(kerdoios={"generated_at": NOW - 1, "entries": [entry]}, include_local=False, now=NOW)

    dimensions = snap["offers"][0]["quota"]["dimensions"]
    assert {name: row["limit"] for name, row in dimensions.items()} == {_stand_in(twin): 1, twin: 2}


# --------------------------------------------------------------------------- (3) system roots


@pytest.mark.parametrize("key", LABEL_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("path", UNCOVERED_PATHS)
def test_a_relative_system_path_is_not_published_as_a_label(key, prefix, path):
    published, snap = _publish(key, prefix + path)

    assert published is None
    assert path not in json.dumps(snap)


@pytest.mark.parametrize("key", ID_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("path", UNCOVERED_PATHS)
def test_a_relative_system_path_used_as_an_identifier_is_published_only_as_a_stand_in(key, prefix, path):
    published, snap = _publish(key, prefix + path)

    assert re.fullmatch(r"redacted-[0-9a-f]{20}", published)
    assert path not in json.dumps(snap)


@pytest.mark.parametrize("root", ADDED_ROOTS)
@pytest.mark.parametrize("before", ["", "a/", "node ", "host:", "tern:"])
@pytest.mark.parametrize("after", ["x", "x/y", ""])
def test_each_added_system_root_makes_a_path_wherever_a_segment_may_start(root, before, after):
    for word in (root, root.upper(), root.title()):
        assert cs._label(f"{before}{word}/{after}") is None


@pytest.mark.parametrize("key", LABEL_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("name", MUST_STAY_READABLE)
def test_a_name_that_holds_a_root_word_stays_readable_as_a_label(key, prefix, name):
    assert _publish(key, prefix + name)[0] == prefix + name


@pytest.mark.parametrize("key", ID_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("name", MUST_STAY_READABLE)
def test_a_name_that_holds_a_root_word_stays_readable_as_an_identifier(key, prefix, name):
    published, snap = _publish(key, prefix + name)

    added = "tern:" if key == "host.name-as-id" or (key == "session.host" and not prefix) else ""
    assert published == added + prefix + name
    assert "id_redacted" not in json.dumps(snap)


@pytest.mark.parametrize("root", ADDED_ROOTS)
def test_a_root_word_is_refused_only_as_a_segment_with_more_path_after_it(root):
    for name in (root, f"{root}-a", f"{root} 7", f"{root}.1", f"{root}_a", f"team/{root}", f"team/{root}-x",
                 f"my-{root}/x", f"x{root}/a", f"{root}x/a", f"a.{root}/b", f"a_{root}/b"):
        assert cs._label(name) == name
        assert cs._ident(name) == (name, False)


@pytest.mark.parametrize("name", ["data/set-1", "media/x", "nix/store", "libs/x", "models/x", "workspace/a",
                                  "documents/x", "cygdrive/c", "devs/x", "runs/7", "systems/a", "mylib/x",
                                  "snapshot/1", "bootstrap/x", "library-2/x", "rsbin/x", "lib32/x"])
def test_a_directory_word_that_is_not_a_listed_system_root_stays_a_name(name):
    assert cs._label(name) == name
    assert cs._ident(name) == (name, False)


# --------------------------------------------------------------------------- (2) conditions no test held


@pytest.mark.parametrize("root", OLDER_ROOTS + ADDED_ROOTS)
def test_a_root_directory_with_nothing_after_its_slash_is_still_a_path(root):
    for text in (f"{root}/", f"{root}/ x", f"a {root}/", f"a/{root}/ b"):
        assert cs._label(text) is None
        assert cs._ident(text)[1] is True


def test_a_slash_that_stands_alone_between_words_is_not_a_path():
    for text in ("a:/ b", "cpu:/ gpu", "a / b"):
        assert cs._label(text) == text
    for text in ("a:/b", "a /b", "a:/"):
        assert cs._label(text) is None


@pytest.mark.parametrize("text", ["TERN:", "Tern:", "TERN: / a", "Tern:tern:"])
def test_only_the_lowercase_prefix_is_the_tern_prefix(text):
    assert cs._label(text) == text
    assert cs._label(text.lower()) is None


@pytest.mark.parametrize("name", ["net", "tern", "r", "enter", "rent:e"])
def test_a_name_spelled_with_the_letters_of_the_prefix_is_still_a_name(name):
    assert cs._label("tern:" + name) == "tern:" + name
    assert _tern([{"name": name}])["hosts"][0]["host_id"] == "tern:" + name


@pytest.mark.parametrize("key", ID_KEYS)
@pytest.mark.parametrize("prefix", PREFIXES[1:])
def test_an_identifier_with_a_space_behind_its_prefixes_is_published_only_as_a_stand_in(key, prefix):
    published, snap = _publish(key, prefix + " rocket")

    assert re.fullmatch(r"redacted-[0-9a-f]{20}", published)
    assert "id_redacted" in json.dumps(snap) or key not in ("host.host_id", "session.session_id")


@pytest.mark.parametrize("name", ["tern-", "tern.", "tern_1", "terna/b", "ternary", "tern/ x", "terns:x"])
def test_a_name_that_only_starts_like_the_prefix_is_judged_whole(name):
    assert cs._ident(name) == (name, False)


@pytest.mark.parametrize("name", ["TERN:h1", "Tern:h1", "ternary", "tern/c", "tern", "terns:x"])
def test_a_session_host_without_the_exact_prefix_is_a_tern_name(name):
    snap = _tern([{"name": name}, {"host_id": name}], [{"session_id": "s1", "host": name}])

    assert snap["sessions"][0]["host_id"] == "tern:" + name
    assert sorted(host["host_id"] for host in snap["hosts"]) == sorted([name, "tern:" + name])
