"""Every limit of the label rule, the numeric bounds, references and tern: prefixes, held at the
limit, one below and one above, through the helper and through build_snapshot.

Limits these tests pin (the last value published, the first value refused):

    limit                                  published        refused / stand-in
    label or id text length                128              129
    quota dimension name length            48               49
    named-host / session-host name         123 (+ "tern:")  124
    alphanumeric run                       20               21
    word with an uppercase letter          32               33
    word without one                       64               65
    the same, as a named host's name       27 and 59        28 and 60  ("tern:" is part of the word)
    UUID inside a word                     counts as 1 character
    slash-joined lowercase (redactor)      47               48
    integer label (JSON number)            2**63 - 1        2**63  (either sign)
    integer identifier                     judged by its text: 20 digits published, 21 a stand-in
    number (_num)                          2**63 - 1        2**63  (either sign), NaN, Infinity, bool
    count (_count)                         0 .. 2**63 - 1   -1, 2**63, a fraction, bool
    GPU rows per host                      64               the 65th is cut
    reference                              any scalar       None, a container, a blank string -> null

A refused label is null. A refused identifier is a stand-in with id_redacted on its row.
Synthetic fixtures only.
"""
from __future__ import annotations

import hashlib
import math

import pytest

from test_capacity_snapshot_labels import ID_KEYS, LABEL_KEYS, _publish
from z0int import capacity_snapshot as cs
from z0int.capacity_snapshot import build_snapshot

NOW = 1_800_000_000.0
BOUND = 2**63
UUID = "123e4567-e89b-12d3-a456-426614174000"
# The largest and smallest doubles inside the bound: float(2**63 - 1) rounds up to 2**63 itself.
FLOAT_BELOW_BOUND = math.nextafter(float(BOUND), 0.0)


def _stand_in(text: str) -> str:
    return "redacted-" + hashlib.sha256(text.encode()).hexdigest()[:20]


def _word(n: int, piece: str = "abcdefghij") -> str:
    """One space-free word of exactly n characters whose alphanumeric runs are at most len(piece)."""
    stream = (piece + "-") * (n // (len(piece) + 1) + 2)
    return stream[1 : n + 1] if stream[n - 1] == "-" else stream[:n]


def _phrase(n: int) -> str:
    """Exactly n characters of short words."""
    return ("abcdefgh " * (n // 9 + 1))[: n - 1] + "z"


def _slashed(n: int) -> str:
    """Exactly n characters of lowercase letters and slashes: no dash, no space, no system root."""
    return ("abcdefgh/" * (n // 9 + 1))[: n - 1] + "z"


def _id_text(key: str, raw: object) -> str:
    """The text an identifier key publishes for `raw` when that text is a label."""
    text = str(raw)
    if key == "host.name-as-id" or (key == "session.host" and not text.startswith("tern:")):
        return "tern:" + text
    return text


PREFIXED_KEYS = ("host.name-as-id", "session.host")  # published with "tern:" in front of the name


def _tern(hosts=(), sessions=()):
    return build_snapshot(tern={"observed_at": NOW - 1, "hosts": list(hosts), "sessions": list(sessions)},
                          include_local=False, now=NOW)


def _kerdoios(*entries):
    return build_snapshot(kerdoios={"generated_at": NOW - 1, "entries": list(entries)}, include_local=False, now=NOW)


def _leases(*rows):
    return build_snapshot(leases={"observed_at": NOW - 1, "leases": list(rows)}, include_local=False, now=NOW)


def _assert_label(raw: object, published: bool) -> None:
    expected = (raw.strip() if isinstance(raw, str) else str(raw)) if published else None
    assert cs._label(raw) == expected
    for key in LABEL_KEYS:
        assert _publish(key, raw)[0] == expected, key


def _assert_id(raw: object, published: bool, keys=ID_KEYS) -> None:
    text = str(raw)
    assert cs._ident(raw) == ((text, False) if published else (_stand_in(text), True))
    assert cs._ref(raw) == (text if published else _stand_in(text))
    for key in keys:
        value, snap = _publish(key, raw)
        expected = _id_text(key, raw)
        assert value == (expected if published else _stand_in(expected)), key
        flagged = [row for group in ("hosts", "sessions", "offers", "leases") for row in snap[group]
                   if row.get("id_redacted")]
        flagged += [d for offer in snap["offers"] for d in offer["quota"].get("dimensions", {}).values()
                    if d.get("id_redacted")]
        own_id = key in ("host.host_id", "host.name-as-id", "session.session_id", "offer.offer_id",
                         "offer.dimension", "lease.placement_id")
        assert len(flagged) == (0 if published or not own_id else 1), key


def test_the_limits_are_the_documented_ones():
    assert (cs._MAX_TEXT, cs._MAX_DIMENSION_NAME, cs._MAX_RUN, cs._MAX_WORD, cs._MAX_LOWERCASE_WORD) == (
        128, 48, 20, 32, 64)
    assert (cs._MAX_NUMBER, cs._MAX_GPUS) == (2**63, 64)


def test_the_builders_make_what_they_say():
    for n in (31, 32, 33, 63, 64, 65, 66):
        for piece in ("abcdefghij", "Abcdefghij", "abcdefghiJ", "0123456789"):
            word = _word(n, piece)
            assert len(word) == n and " " not in word and max(map(len, cs._ALNUM_RUN.findall(word))) <= 10
            assert (word == word.lower()) is (piece == piece.lower())
    for n in (47, 48, 123, 124, 127, 128, 129):
        assert len(_phrase(n)) == n and _phrase(n) == _phrase(n).strip()
        assert len(_slashed(n)) == n and set(_slashed(n)) <= set("abcdefghz/")


# --------------------------------------------------------------------------- (1) alphanumeric run


RUNS = ["a", "Z", "7", "aB3"]


@pytest.mark.parametrize("unit", RUNS)
@pytest.mark.parametrize("n", [1, 19, 20])
def test_a_run_up_to_the_cap_is_published(unit, n):
    run = (unit * 21)[:n]
    _assert_label(run, True)
    _assert_id(run, True)


@pytest.mark.parametrize("unit", RUNS)
@pytest.mark.parametrize("n", [21, 22])
def test_a_run_over_the_cap_is_refused(unit, n):
    run = (unit * 22)[:n]
    _assert_label(run, False)
    _assert_id(run, False)


@pytest.mark.parametrize("unit", RUNS)
@pytest.mark.parametrize("frame", ["box {} up", "x-{}-y", "{}.local", "a/{}", "v_{}_w", "ok {}", "{} ok", "n:{}"])
def test_the_run_cap_holds_wherever_the_run_sits(unit, frame):
    at, over = frame.format((unit * 21)[:20]), frame.format((unit * 21)[:21])
    assert cs._label(at) == at and cs._ident(at) == (at, False)
    assert cs._label(over) is None and cs._ident(over) == (_stand_in(over), True)
    assert _publish("host.label", at)[0] == at and _publish("host.label", over)[0] is None
    assert _publish("lease.demand_id", at)[0] == at and _publish("lease.demand_id", over)[0] == _stand_in(over)


@pytest.mark.parametrize("separator", list("._:/- "))
def test_any_separator_ends_a_run(separator):
    text = "a" * 20 + separator + "b" * 20
    assert cs._label(text) == text and cs._ident(text) == (text, False)
    assert _publish("session.name", text)[0] == text and _publish("session.session_id", text)[0] == text


def test_one_long_run_is_refused_however_many_short_ones_surround_it():
    assert cs._label("ok fine " + "a" * 21 + " ok") is None
    assert cs._label("a" * 21 + " ok fine") is None
    assert cs._label("ok fine ok") == "ok fine ok"


def test_a_uuid_is_not_a_run_but_hex_touching_one_is():
    assert cs._label(UUID) == UUID and cs._label(UUID.upper()) == UUID.upper()
    for glued in ("a" * 13 + UUID, UUID + "a" * 9):  # 13 + 8 and 12 + 9 characters in one run
        assert cs._label(glued) is None and cs._ident(glued) == (_stand_in(glued), True)
        assert _publish("gpu.name", glued)[0] is None and _publish("gpu.id", glued)[0] == _stand_in(glued)
    for glued in ("a" * 12 + UUID, UUID + "a" * 8):  # 20 characters in one run
        assert cs._label(glued) == glued and _publish("gpu.name", glued)[0] == glued


# --------------------------------------------------------------------------- (2) word length


@pytest.mark.parametrize("piece", ["abcdefghij", "0123456789", "a1b2c3d4e5"])
@pytest.mark.parametrize("n", [63, 64])
def test_a_word_without_uppercase_up_to_64_characters_is_published(piece, n):
    word = _word(n, piece)
    _assert_label(word, True)
    _assert_id(word, True, [key for key in ID_KEYS if key not in ("offer.dimension", *PREFIXED_KEYS)])


@pytest.mark.parametrize("piece", ["abcdefghij", "0123456789", "a1b2c3d4e5"])
@pytest.mark.parametrize("n", [65, 66])
def test_a_word_without_uppercase_over_64_characters_is_refused(piece, n):
    word = _word(n, piece)
    _assert_label(word, False)
    _assert_id(word, False)


@pytest.mark.parametrize("piece", ["Abcdefghij", "ABCDEFGHIJ", "abcdefghiJ", "a1B2c3d4e5"])
@pytest.mark.parametrize("n", [31, 32])
def test_a_word_with_uppercase_up_to_32_characters_is_published(piece, n):
    word = _word(n, piece)
    _assert_label(word, True)
    _assert_id(word, True, [key for key in ID_KEYS if key not in PREFIXED_KEYS])


@pytest.mark.parametrize("piece", ["Abcdefghij", "ABCDEFGHIJ", "abcdefghiJ", "a1B2c3d4e5"])
@pytest.mark.parametrize("n", [33, 34, 64])
def test_a_word_with_uppercase_over_32_characters_is_refused(piece, n):
    word = _word(n, piece)
    _assert_label(word, False)
    _assert_id(word, False)


@pytest.mark.parametrize("n", [33, 48, 64])
def test_one_uppercase_letter_is_what_halves_the_cap(n):
    lower = _word(n)
    for upper in ("Z" + lower[1:], lower[:-1] + "Z", lower[: n // 2] + "Z" + lower[n // 2 + 1:]):
        assert len(upper) == n and cs._label(lower) == lower and cs._label(upper) is None
        assert _publish("host.label", lower)[0] == lower and _publish("host.label", upper)[0] is None
        assert _publish("gpu.id", lower)[0] == lower and _publish("gpu.id", upper)[0] == _stand_in(upper)


def test_the_word_cap_is_per_word_and_a_word_ends_only_at_a_space():
    mixed = _word(32, "Abcdefghij")
    three = f"{mixed} {mixed} {mixed}"
    assert cs._label(three) == three and _publish("session.name", three)[0] == three
    for separator in "._:/-":
        joined = mixed + separator + mixed
        assert cs._label(joined) is None, separator
    lower = _word(64)
    assert cs._label(lower + " " + _word(60)) == lower + " " + _word(60)
    assert cs._label(lower + "-" + "b") is None


def test_one_long_word_is_refused_however_many_short_ones_surround_it():
    assert cs._label("ok " + _word(65)) is None and cs._label(_word(65) + " ok") is None
    assert cs._label("ok " + _word(33, "Abcdefghij") + " ok") is None
    assert _publish("host.label", "ok " + _word(65))[0] is None
    assert _publish("lease.demand_id", "ok " + _word(65))[0] == _stand_in("ok " + _word(65))


@pytest.mark.parametrize("piece,n,published", [("abcdefghij", 58, True), ("abcdefghij", 59, True),
                                               ("abcdefghij", 60, False), ("abcdefghij", 61, False),
                                               ("Abcdefghij", 26, True), ("Abcdefghij", 27, True),
                                               ("Abcdefghij", 28, False), ("Abcdefghij", 29, False)])
def test_the_prefix_a_named_host_is_given_counts_toward_its_word(piece, n, published):
    # The published id is judged whole, and "tern:" joins the word it leads: a colon does not end a word.
    name = _word(n, piece)
    text = "tern:" + name
    assert cs._label(text) == (text if published else None)
    assert cs._ident(name, "tern:") == cs._ident(text) == ((text, False) if published else (_stand_in(text), True))
    snap = _tern([{"name": name}, {"host_id": text, "label": "explicit"}],
                 [{"session_id": "s1", "host": name}, {"session_id": "s2", "host": text}])
    assert [host["host_id"] for host in snap["hosts"]] == [text if published else _stand_in(text)]
    assert snap["hosts"][0].get("id_redacted") is (None if published else True)
    assert [session["host_id"] for session in snap["sessions"]] == [snap["hosts"][0]["host_id"]] * 2
    assert cs._label(name) == name  # the name alone is within the word cap


def test_a_uuid_counts_as_one_character_of_its_word():
    at, over = _word(62) + "-" + UUID, _word(63) + "-" + UUID  # 62 + 1 + 1 and 63 + 1 + 1 characters
    assert cs._label(at) == at and cs._ident(at) == (at, False)
    assert cs._label(over) is None and cs._ident(over) == (_stand_in(over), True)
    assert _publish("host.label", at)[0] == at and _publish("host.label", over)[0] is None
    assert _publish("offer.model", at)[0] == at and _publish("offer.model", over)[0] == _stand_in(over)
    mixed_at, mixed_over = _word(30, "Abcdefghij") + "-" + UUID, _word(31, "Abcdefghij") + "-" + UUID
    assert cs._label(mixed_at) == mixed_at and cs._label(mixed_over) is None
    assert cs._label(f"{UUID}/{UUID}/{UUID}") == f"{UUID}/{UUID}/{UUID}"  # 110 characters, 5 counted


# --------------------------------------------------------------------------- (3) text length


@pytest.mark.parametrize("n", [1, 2, 127, 128])
def test_text_up_to_128_characters_is_published(n):
    text = _phrase(n)
    _assert_label(text, True)
    _assert_id(text, True, [key for key in ID_KEYS if key not in ("offer.dimension", "host.name-as-id",
                                                                  "session.host")])


@pytest.mark.parametrize("n", [129, 130])
def test_text_over_128_characters_is_refused(n):
    text = _phrase(n)
    _assert_label(text, False)
    _assert_id(text, False)


def test_a_label_is_measured_after_its_padding_is_removed_and_an_identifier_is_not():
    padded = "  " + _phrase(128) + " \t"
    assert cs._label(padded) == _phrase(128) and _publish("host.label", padded)[0] == _phrase(128)
    assert cs._label("  " + _phrase(129) + " ") is None
    assert cs._ident(padded) == (_stand_in(padded), True)


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t\r\n "])
def test_a_blank_label_is_null(blank):
    _assert_label(blank, False)


@pytest.mark.parametrize("n,published", [(1, True), (47, True), (48, True), (49, False), (50, False), (128, False)])
def test_a_quota_dimension_name_is_published_up_to_48_characters(n, published):
    name = _phrase(n)
    assert cs._ident(name, limit=cs._MAX_DIMENSION_NAME) == ((name, False) if published else (_stand_in(name), True))
    snap = _kerdoios({"provider": "p", "offer_id": "o", "quota": {"dimensions": {name: {"limit": 1}}}})
    dimensions = snap["offers"][0]["quota"]["dimensions"]
    assert dimensions == ({name: {"limit": 1}} if published else {_stand_in(name): {"limit": 1, "id_redacted": True}})


@pytest.mark.parametrize("n,published", [(1, True), (122, True), (123, True), (124, False), (125, False), (128, False)])
def test_a_named_host_is_published_while_its_whole_id_fits_and_its_sessions_follow_it(n, published):
    name = _phrase(n)
    snap = _tern([{"name": name}], [{"session_id": "s1", "host": name}, {"session_id": "s2", "host_id": name}])
    host = snap["hosts"][0]
    assert cs._ident(name, "tern:") == (("tern:" + name, False) if published else (_stand_in("tern:" + name), True))
    assert host["host_id"] == ("tern:" + name if published else _stand_in("tern:" + name))
    assert host.get("id_redacted") is (None if published else True)
    assert host["label"] == name  # the name itself is a label up to 128 characters
    assert [session["host_id"] for session in snap["sessions"]] == [host["host_id"]] * 2


@pytest.mark.parametrize("n,published", [(127, True), (128, True), (129, False)])
def test_an_id_that_already_carries_its_prefix_is_measured_whole(n, published):
    text = "tern:" + _phrase(n - 5)
    assert len(text) == n
    assert cs._ident(text) == ((text, False) if published else (_stand_in(text), True))
    assert cs._label(text) == (text if published else None)
    snap = _tern([{"host_id": text}], [{"session_id": "s1", "host": text}])
    assert snap["hosts"][0]["host_id"] == snap["sessions"][0]["host_id"] == (text if published else _stand_in(text))


# --------------------------------------------------------------------------- (4) the redactor's own limit


@pytest.mark.parametrize("n,published", [(46, True), (47, True), (48, False), (49, False), (64, False)])
def test_slash_joined_lowercase_is_published_up_to_47_characters(n, published):
    # No run over 8 and one lowercase word of at most 64: only the repo redactor refuses these.
    text = _slashed(n)
    _assert_label(text, published)
    _assert_id(text, published, [key for key in ID_KEYS if key != "offer.dimension"] if n > 43 else ID_KEYS)


def test_inner_spacing_is_published_as_written():
    for text in ("a  b", "gpu   box", "a \t b".replace("\t", " ")):
        assert cs._label(text) == text and cs._ident(text) == (text, False)
        assert _publish("host.label", text)[0] == text and _publish("lease.demand_id", text)[0] == text


@pytest.mark.parametrize("text", ["a@b", "kev@box", "a+b", "a=b", "a,b", "a;b", "a~b", "a\\b", "a\tb", "a\nb",
                                  "café", "a b", "(a)", "a|b", "a'b", '"a"', "a*", "a?", "#a", "a%20b"])
def test_a_character_outside_the_label_alphabet_is_refused(text):
    _assert_label(text, False)
    assert cs._ident(text) == (_stand_in(text), True)
    assert _publish("lease.demand_id", text)[0] == _stand_in(text)


@pytest.mark.parametrize("text", ["a.b", "a_b", "a:b", "a/b", "a-b", "a b", "A9", ".a", "_a", "-a", ":a", "a.", "a:"])
def test_every_character_of_the_label_alphabet_is_published(text):
    _assert_label(text, True)
    _assert_id(text, True)


@pytest.mark.parametrize("text", ["box sk-1", "box ghp_1", "x-ghp_1", "box xoxb-1", "a.AKIA1", "box AIza1", "a eyJ1",
                                  "n/gho_x", "v:ghs_x"])
def test_a_credential_prefix_is_refused_anywhere_in_the_text(text):
    _assert_label(text, False)
    assert cs._ident(text) == (_stand_in(text), True)


@pytest.mark.parametrize("text", ["task-1", "desk-2", "risk-level", "ask-me"])
def test_sk_dash_inside_a_word_is_not_a_key_prefix(text):
    _assert_label(text, True)
    _assert_id(text, True)


# --------------------------------------------------------------------------- (5) integers and numbers


INSIDE = [0, 1, -1, BOUND - 2, BOUND - 1, -(BOUND - 2), -(BOUND - 1)]
OUTSIDE = [BOUND, BOUND + 1, -BOUND, -(BOUND + 1), 2**64, -(2**64), 10**30]


@pytest.mark.parametrize("value", INSIDE)
def test_an_integer_label_inside_the_bound_is_published_as_its_digits(value):
    _assert_label(value, True)


@pytest.mark.parametrize("value", OUTSIDE)
def test_an_integer_label_at_or_past_the_bound_is_null(value):
    _assert_label(value, False)


@pytest.mark.parametrize("value", [True, False, 1.0, 0.0, 1.5, float(BOUND), float("nan"), float("inf")])
def test_a_bool_or_float_is_never_a_label(value):
    _assert_label(value, False)


INT_ID_KEYS = [key for key in ID_KEYS if key != "offer.dimension"]  # a JSON object key is always a string


@pytest.mark.parametrize("value", [1, -1, BOUND - 1, BOUND, -BOUND, BOUND + 1, 10**19, 10**20 - 1, -(10**20 - 1)])
def test_an_integer_identifier_is_published_by_its_text_up_to_20_digits(value):
    # An identifier maps by its text alone, so the same id spelled as a JSON string joins it.
    _assert_id(value, True, INT_ID_KEYS)
    assert cs._ident(value) == cs._ident(str(value))


@pytest.mark.parametrize("value", [10**20, -(10**20), 10**20 + 1, 10**30])
def test_an_integer_identifier_of_21_digits_is_a_stand_in(value):
    _assert_id(value, False, INT_ID_KEYS)
    assert cs._ident(value) == cs._ident(str(value))


@pytest.mark.parametrize("value", [0, 1, -1, 0.0, -0.0, 0.5, -0.5, BOUND - 1, -(BOUND - 1), FLOAT_BELOW_BOUND,
                                   -FLOAT_BELOW_BOUND, 1e-300])
def test_a_number_inside_the_bound_is_published(value):
    out = cs._num(value)
    assert out == value and type(out) is type(value)


@pytest.mark.parametrize("value", [BOUND, -BOUND, BOUND + 1, -(BOUND + 1), float(BOUND), -float(BOUND), 1e300, -1e300,
                                   float("inf"), float("-inf"), float("nan"), True, False, "1", None, [1], {}])
def test_a_number_at_or_past_the_bound_or_not_a_number_is_null(value):
    assert cs._num(value) is None


def test_the_float_just_inside_the_bound_is_the_last_one_published():
    assert FLOAT_BELOW_BOUND < BOUND == float(BOUND - 1)
    assert cs._num(FLOAT_BELOW_BOUND) == FLOAT_BELOW_BOUND and cs._num(float(BOUND - 1)) is None
    assert cs._count(FLOAT_BELOW_BOUND) == int(FLOAT_BELOW_BOUND) == 2**63 - 1024


@pytest.mark.parametrize("value,count", [(0, 0), (1, 1), (BOUND - 1, BOUND - 1), (0.0, 0), (-0.0, 0), (3.0, 3),
                                         (1e15, 10**15)])
def test_a_whole_number_from_zero_up_is_a_count_and_always_an_int(value, count):
    out = cs._count(value)
    assert out == count and type(out) is int


@pytest.mark.parametrize("value", [-1, -2, -(BOUND - 1), -1.0, -0.5, 0.5, 1.5, 2.000001, BOUND, BOUND + 1,
                                   float(BOUND), float("inf"), float("nan"), True, False, "3", None, [3]])
def test_a_negative_fractional_oversized_or_non_numeric_value_is_not_a_count(value):
    assert cs._count(value) is None


NUMBERS = [(0, 0), (-1, -1), (0.5, 0.5), (BOUND - 1, BOUND - 1), (-(BOUND - 1), -(BOUND - 1)),
           (FLOAT_BELOW_BOUND, FLOAT_BELOW_BOUND), (BOUND, None), (-BOUND, None), (float(BOUND), None),
           (float("inf"), None), (float("nan"), None), (True, None), ("7", None)]
COUNTS = [(0, 0), (1, 1), (3.0, 3), (BOUND - 1, BOUND - 1), (-1, None), (0.5, None), (BOUND, None),
          (float(BOUND), None), (True, None), ("7", None)]


@pytest.mark.parametrize("value,published", NUMBERS)
def test_every_number_field_of_the_snapshot_holds_the_numeric_bound(value, published):
    snap = build_snapshot(
        tern={"observed_at": NOW - 1,
              "hosts": [{"host_id": "h1", "rtt_ms": value,
                         "resources": {"gpus": [{"id": "g0", "utilization_pct": value}]}}],
              "sessions": [{"session_id": "s1", "rtt_ms": value}]},
        kerdoios={"generated_at": NOW - 1, "entries": [{
            "provider": "p", "offer_id": "o", "price": value, "predicted_cost": value, "burn_rate": value,
            "time_to_exhaustion": value, "time_to_reset": value,
            "quota": {"remaining_free_quota": value, "remaining_tokens": value, "remaining": value,
                      "day_tokens_remaining": value, "limit": value, "daily_limit": value,
                      "day_tokens_limit": value, "dimensions": {"rpm": {"limit": value, "remaining": value}}}}]},
        include_local=False, now=NOW)
    host, session, offer = snap["hosts"][0], snap["sessions"][0], snap["offers"][0]
    seen = [host["rtt_ms"], host["resources"]["gpus"][0]["utilization_pct"], session["rtt_ms"]]
    seen += [offer[key] for key in ("price", "predicted_cost", "burn_rate", "time_to_exhaustion", "time_to_reset")]
    seen += [offer["quota"][key] for key in cs._QUOTA_REMAINING_KEYS + cs._QUOTA_LIMIT_KEYS]
    seen += [offer["quota"]["dimensions"]["rpm"][key] for key in ("limit", "remaining")]
    assert len(seen) == 17
    for out in seen:
        assert out == published and type(out) is type(published)


@pytest.mark.parametrize("value,published", COUNTS)
def test_every_count_field_of_the_snapshot_holds_the_count_bounds(value, published):
    resources = {key: value for key in cs._RESOURCE_INT_KEYS}
    resources.update(gpu_count=value, gpus=[{"id": "g0", "memory_total_bytes": value, "memory_free_bytes": value}])
    snap = _tern([{"host_id": "h1", "resources": resources}], [{"session_id": "s1", "tabs": value}])
    out = snap["hosts"][0]["resources"]
    gpu = out["gpus"][0]
    seen = [out[key] for key in cs._RESOURCE_INT_KEYS] + [gpu["memory_total_bytes"], gpu["memory_free_bytes"]]
    seen.append(snap["sessions"][0]["tab_count"])
    assert len(seen) == 8
    for count in seen:
        assert count == published and type(count) is type(published)
    # A gpu_count that is not a count falls back to the number of GPU rows observed.
    assert out["gpu_count"] == (1 if published is None else published) and type(out["gpu_count"]) is int


def test_a_gpu_count_is_the_reported_one_else_the_rows_observed_else_unknown():
    def resources(value):
        return _tern([{"host_id": "h1", "resources": value}])["hosts"][0]["resources"]

    assert resources({"gpu_count": 2}) == {"gpus": None, "gpu_count": 2}
    assert resources({"gpu_count": 0}) == {"gpus": None, "gpu_count": 0}
    assert resources({"gpu_count": -1}) == {"gpus": None, "gpu_count": None}
    assert resources({"gpus": [{"id": "a"}, {"id": "b"}]})["gpu_count"] == 2
    assert resources({"gpus": [{"id": "a"}, {"id": "b"}], "gpu_count": 3})["gpu_count"] == 3
    assert resources({"gpus": []}) == {"gpus": [], "gpu_count": 0}
    assert resources({"gpus": "two"}) == {"gpus": None, "gpu_count": None}
    assert resources({}) == {}


@pytest.mark.parametrize("n,kept", [(1, 1), (63, 63), (64, 64), (65, 64), (66, 64)])
def test_at_most_64_gpu_rows_are_published_and_they_are_the_first_ones(n, kept):
    gpus = [{"id": f"g{i}"} for i in range(n)]
    out = _tern([{"host_id": "h1", "resources": {"gpus": gpus}}])["hosts"][0]["resources"]["gpus"]
    assert [gpu["id"] for gpu in out] == [f"g{i}" for i in range(kept)]


# --------------------------------------------------------------------------- (6) references


BLANKS = ["", " ", "   ", "\t", "\n", " \t\r\n "]
NOT_SCALAR = [None, [], ["g0"], {}, {"id": "g0"}]


@pytest.mark.parametrize("value", BLANKS + NOT_SCALAR)
def test_a_blank_or_non_scalar_reference_is_null_never_a_stand_in(value):
    assert cs._ref(value) is None and cs._ref(value, "tern:") is None
    snap = build_snapshot(
        tern={"observed_at": NOW - 1,
              "hosts": [{"host_id": "h1", "resources": {"gpus": [{"id": value}]}}],
              "sessions": [{"session_id": "s1", "pane_id": value}]},
        kerdoios={"generated_at": NOW - 1, "entries": [{"provider": "p", "offer_id": "o", "model": value}]},
        leases={"observed_at": NOW - 1, "leases": [{"placement_id": "p1", "demand_id": value, "offer_id": value,
                                                    "selected_offer_id": value}]},
        include_local=False, now=NOW)
    assert snap["hosts"][0]["resources"]["gpus"][0]["id"] is None
    assert snap["sessions"][0]["pane_id"] is None
    assert snap["offers"][0]["model"] is None and snap["offers"][0]["actor_id"] is None
    assert snap["leases"] == [{"placement_id": "p1", "demand_id": None, "offer_id": None, "selected_offer_id": None}]
    assert "redacted-" not in repr(snap) and "id_redacted" not in repr(snap)
    assert all(source["status"] == "ok" for source in snap["sources"].values())


@pytest.mark.parametrize("value,published", [("x", "x"), (0, "0"), (7, "7"), (-7, "-7"), (0.0, "0.0"), (1.5, "1.5"),
                                             ("0", "0"), ("a b", "a b")])
def test_a_scalar_reference_that_is_a_label_is_published_as_its_text(value, published):
    assert cs._ref(value) == published == cs._ident(value)[0]
    for key in ("gpu.id", "session.pane_id", "offer.model", "lease.demand_id", "lease.offer_id",
                "lease.selected_offer_id"):
        assert _publish(key, value)[0] == published, key


@pytest.mark.parametrize("value", [" x", "x ", " x ", "\tx", "x\n", " 0 "])
def test_a_padded_reference_is_not_trimmed_into_another_rows_id(value):
    # An id maps by its text alone: " x" is not "x", and it is not blank, so it gets its own stand-in.
    assert cs._ref(value) == _stand_in(value) != cs._ref(value.strip())
    for key in ("gpu.id", "session.pane_id", "offer.model", "lease.demand_id"):
        assert _publish(key, value)[0] == _stand_in(value), key


def test_a_reference_is_mapped_with_the_prefix_it_is_given():
    assert cs._ref("h1", "tern:") == "tern:h1" == cs._ident("h1", "tern:")[0]
    assert cs._ref("etc/shadow", "tern:") == _stand_in("tern:etc/shadow") == cs._ident("etc/shadow", "tern:")[0]
    assert cs._ref("h1") == cs._ref("h1", "") == "h1"
    assert cs._ref(" ", "tern:") is None


def test_a_lease_publishes_only_the_keys_its_row_carries():
    assert _leases({"placement_id": "p1"})["leases"] == [{"placement_id": "p1"}]
    assert _leases({"placement_id": "p1", "offer_id": "o1"})["leases"] == [{"placement_id": "p1", "offer_id": "o1"}]


def test_a_lease_row_without_a_placement_id_or_that_is_not_an_object_is_skipped_not_counted():
    snap = _leases("junk", 7, None, ["p0"], {"demand_id": "d1"}, {"placement_id": ""}, {"placement_id": "p1"})
    assert snap["leases"] == [{"placement_id": "p1"}]
    assert snap["sources"]["leases"] == {"status": "ok", "observed_at": NOW - 1, "age_s": 1.0}


# --------------------------------------------------------------------------- (7) tern: prefixes


PADDED_HOSTS = [" tern:h1", " tern:h1 ", "\ttern:h1", "  tern:tern:h1", " h1", "h1 ", " Tern:h1"]


@pytest.mark.parametrize("name", PADDED_HOSTS)
@pytest.mark.parametrize("host_key", ["name", "id", "label", "slot"])
@pytest.mark.parametrize("session_key", ["host", "host_id"])
def test_a_padded_host_name_and_the_session_naming_it_get_the_same_stand_in(name, host_key, session_key):
    # The prefix test reads the text as written: a name that only starts with tern: after trimming is
    # a name, so host and session both prefix it and both publish the stand-in of the same text.
    snap = _tern([{host_key: name}], [{"session_id": "s1", session_key: name}])
    host, session = snap["hosts"][0], snap["sessions"][0]
    assert host["host_id"] == session["host_id"] == _stand_in("tern:" + name)
    assert host["id_redacted"] is True and name not in repr(snap["sessions"])


@pytest.mark.parametrize("name", PADDED_HOSTS + ["tern:h1 "])
def test_a_padded_explicit_host_id_is_a_stand_in_of_its_own_text(name):
    snap = _tern([{"host_id": name}])
    assert snap["hosts"][0]["host_id"] == _stand_in(name) and snap["hosts"][0]["id_redacted"] is True


@pytest.mark.parametrize("ref,published", [("tern:h1", "tern:h1"), ("h1", "tern:h1"), ("tern:tern:h1", "tern:tern:h1"),
                                           ("Tern:h1", "tern:Tern:h1"), ("TERN:h1", "tern:TERN:h1"),
                                           ("tern", "tern:tern"), ("tern-h1", "tern:tern-h1"),
                                           ("x tern:h1", "tern:x tern:h1"), ("tern:", None), ("tern:h1 ", None),
                                           (7, "tern:7"), (0, "tern:0"), (1.5, "tern:1.5")])
def test_a_session_host_gets_the_prefix_only_when_its_text_does_not_start_with_it(ref, published):
    session = _tern([], [{"session_id": "s1", "host": ref}])["sessions"][0]
    expected = published if published is not None else _stand_in(str(ref))
    assert session["host_id"] == expected
    assert "id_redacted" not in session  # the flag marks a row's own id, never a reference


@pytest.mark.parametrize("prefix", ["tern:", "tern:tern:", "tern:tern:tern:tern:"])
@pytest.mark.parametrize("n,published", [(20, True), (21, False)])
def test_the_run_cap_is_applied_behind_any_number_of_prefixes(prefix, n, published):
    text = prefix + "a" * n
    assert cs._label(text) == (text if published else None)
    assert cs._ident(text) == ((text, False) if published else (_stand_in(text), True))
    assert cs._ident("a" * n, prefix) == cs._ident(text)
    assert _publish("host.label", text)[0] == (text if published else None)
    assert _publish("host.host_id", text)[0] == (text if published else _stand_in(text))


@pytest.mark.parametrize("text", ["Tern:" + "a" * 20, "TERN:x", "xtern:y", "tern", "tern-x", "a tern:b"])
def test_only_a_leading_lowercase_tern_colon_is_a_prefix(text):
    assert cs._label(text) == text and cs._ident(text) == (text, False)


# --------------------------------------------------------------------------- (8) rows around the helpers


def test_a_host_label_wins_over_its_name_and_the_name_is_used_when_the_label_is_refused():
    def label(host):
        return _tern([{"host_id": "h1", **host}])["hosts"][0]["label"]

    assert label({"label": "front", "name": "back"}) == "front"
    assert label({"label": "a" * 21, "name": "back"}) == "back"
    assert label({"label": "front", "name": "a" * 21}) == "front"
    assert label({"label": "a" * 21, "name": "b" * 21}) is None
    assert label({"label": BOUND, "name": BOUND - 1}) == str(BOUND - 1)


def test_a_session_runtime_wins_over_its_program():
    def runtime(session):
        return _tern([], [{"session_id": "s1", **session}])["sessions"][0]["runtime"]

    assert runtime({"runtime": "claude", "program": "zsh"}) == "claude"
    assert runtime({"runtime": "a" * 21, "program": "zsh"}) == "zsh"
    assert runtime({"runtime": "a" * 21, "program": "b" * 21}) is None


def test_a_tern_export_with_only_hosts_or_only_sessions_is_not_empty():
    ok = {"status": "ok", "observed_at": NOW - 1, "age_s": 1.0}
    assert _tern([{"host_id": "h1"}])["sources"]["tern"] == ok
    assert _tern([], [{"session_id": "s1"}])["sources"]["tern"] == ok
    assert _tern()["sources"]["tern"] == {"status": "degraded", "observed_at": NOW - 1, "age_s": 1.0,
                                          "reason": "empty_tern_export"}


def test_a_kerdoios_source_names_its_worst_problem_first():
    stale = {"provider": "p", "offer_id": "stale", "quota": {"remaining": 1, "reset_at": NOW - 10}}
    dropped = {"provider": ["p"], "offer_id": "dropped"}
    cut = {"provider": "p", "offer_id": "cut",
           "quota": {"dimensions": {f"d{i}": {"limit": 1} for i in range(cs._MAX_DIMENSIONS + 1)}}}

    def reason(*entries):
        return _kerdoios(*entries)["sources"]["kerdoios"].get("reason")

    assert reason(stale, dropped, cut) == reason(stale, dropped) == reason(stale, cut) == reason(stale)
    assert reason(stale) == "observation_predates_reset"
    assert reason(dropped, cut) == reason(dropped) == "row_id_not_scalar"
    assert reason(cut) == "dimensions_truncated"
    assert reason({"provider": "p", "offer_id": "fine"}) is None
    both = _kerdoios(stale, dropped, cut)["sources"]["kerdoios"]
    assert both["rows_dropped"] == 1 and both["dimensions_truncated"] == 1
