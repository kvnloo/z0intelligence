"""Tests for the experimental Bend AODL structural gate adapter (z0int#48).

The encoder / fail-closed tests need no Bend install.  The kernel parity tests
run only when the compiled kernel binary exists (``bend/aodl_gate/build/gate``
or ``$Z0INT_BEND_GATE_BIN``); build it with ``z0int.bend_gate.build_kernel()``.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

from z0int import bend_gate as bg

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "third_party" / "aodl_a848270"
# Load the frozen validator under a distinct name without changing import
# resolution for the installed aodl_contract package.
_spec = importlib.util.spec_from_file_location("bend_aodl_reference", REF / "aodl_contract" / "validator.py")
_reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reference)
validate = _reference.validate

CATALOG = bg.load_catalog(REF / "harnesses" / "catalog.json")
FIXTURES = sorted((REF / "examples").rglob("*.json"))
HAVE_KERNEL = bg.default_binary().exists()


def load(name: str) -> dict:
    return json.loads((REF / "examples" / name).read_text())


def spawn(**kw) -> bg.SpawnProposal:
    base = dict(contract_revision=3, request_revision=3, dynamic_allowed=True, max_children=4, max_depth=2,
                live_children=1, parent_depth=0, budgets={"tokens": 1000}, observed={"tokens": 400},
                proposed={"tokens": 100}, ceiling=["execute", "verify"], requested=["execute"])
    base.update(kw)
    return bg.SpawnProposal(**base)


def fake_kernel(tmp: Path, body: str) -> Path:
    path = tmp / "fake-gate"
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class ReferenceIsolationTests(unittest.TestCase):
    def test_reference_and_canonical_imports_are_order_independent(self) -> None:
        # Fresh interpreters reproduce pytest collection before any other
        # module can cache the installed package.
        code = """
import importlib
import runpy
import sys
from pathlib import Path

reference_first, script, reference = sys.argv[1:]
before = sys.path[:]
if reference_first == "0":
    canonical = importlib.import_module("aodl_contract")
namespace = runpy.run_path(script, run_name="bend_reference_collection")
expected = before if Path(script).name == "test_bend_gate.py" else [str(Path(script).resolve().parents[1] / "src"), *before]
assert sys.path == expected
assert str(Path(reference).resolve().parents[1]) not in sys.path
if reference_first == "1":
    canonical = importlib.import_module("aodl_contract")
assert canonical is sys.modules["aodl_contract"]
assert canonical.CANON_VERSION == "aodl-canon-1"
assert callable(canonical.validate) and callable(canonical.semantic_fingerprint)
assert Path(namespace["validate"].__code__.co_filename).resolve() == Path(reference).resolve()
assert namespace["validate"]({})  # frozen validator still rejects an invalid document
"""
        reference = REF / "aodl_contract" / "validator.py"
        for script in (Path(__file__), ROOT / "benchmarks" / "bend_gate_parity.py"):
            for reference_first in ("0", "1"):
                with self.subTest(script=script.name, reference_first=reference_first):
                    subprocess.run(
                        [sys.executable, "-c", code, reference_first, str(script), str(reference)],
                        check=True, cwd=ROOT,
                    )


class EncoderTests(unittest.TestCase):
    def test_interning_follows_python_equality(self) -> None:
        tab = bg._Table()
        self.assertEqual(tab.ref(1), tab.ref(1.0))
        self.assertEqual(tab.ref(1), tab.ref(True))
        self.assertNotEqual(tab.ref(1), tab.ref("1"))
        self.assertEqual(tab.ref(0), tab.ref(False))
        self.assertEqual(tab.ref([1, "a"]), tab.ref([1.0, "a"]))
        self.assertNotEqual(tab.ref(None), tab.ref("None"))

    def test_value_tags(self) -> None:
        tab = bg._Table()
        tab.ref("ab"), tab.ref(None), tab.ref(0.0), tab.ref([0])
        self.assertEqual(tab.tokens(), [4, 0, 2, 97, 98, 1, 2, 3])

    def test_shape_failures_are_not_encoded(self) -> None:
        doc = load("valid/pipeline.json")
        del doc["constraints"]
        enc = bg.encode_document(doc, CATALOG)
        self.assertFalse(enc.representable)
        self.assertIn("required", {c for c, _ in enc.shape})
        self.assertTrue(validate(doc))

    def test_hotl_01_is_unsupported_not_guessed(self) -> None:
        enc = bg.encode_document(load("valid/hotl-0.1-fanin.json"), CATALOG)
        self.assertFalse(enc.representable)
        self.assertIsNotNone(enc.unsupported)

    def test_every_shape_clean_fixture_encodes_to_u32_tokens(self) -> None:
        for path in FIXTURES:
            enc = bg.encode_document(json.loads(path.read_text()), CATALOG)
            if enc.representable:
                self.assertEqual(enc.tokens[0], bg.MODE_DOCUMENT)
                self.assertTrue(all(isinstance(t, int) and 0 <= t <= bg.U32_MAX for t in enc.tokens), path)

    def test_transition_refuses_unrepresentable_values(self) -> None:
        for bad in (1.5, -1, True, 2**47, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bg.encode_transition(spawn(budgets={"tokens": bad}) if bad is not None
                                         else spawn(live_children=None))

    def test_nats_travel_as_hi_lo(self) -> None:
        toks = bg.encode_transition(spawn(contract_revision=2**40 + 5, request_revision=2**40 + 5))
        self.assertEqual(toks[:5], [bg.MODE_TRANSITION, 256, 5, 256, 5])

    def test_reference_transition(self) -> None:
        self.assertEqual(bg.reference_transition(spawn()), [])
        self.assertEqual(bg.reference_transition(spawn(request_revision=4)), [101])
        self.assertEqual(bg.reference_transition(spawn(live_children=4)), [103])
        self.assertEqual(bg.reference_transition(spawn(proposed={"tokens": 601})), [105])
        self.assertEqual(bg.reference_transition(spawn(requested=["deploy"])), [106])

    def test_parse_reply(self) -> None:
        self.assertEqual(bg.parse_reply("ALLOW\n"), (True, ()))
        self.assertEqual(bg.parse_reply("DENY 3 10"), (False, (3, 10)))
        for bad in ("", "OK", "DENY", "ALLOW 1", "DENY x"):
            with self.assertRaises(ValueError):
                bg.parse_reply(bad)


class FailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.doc = load("valid/pipeline.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, gate: bg.BendGate) -> bg.GateDecision:
        with gate:
            return gate.check_document(self.doc, CATALOG)

    def test_missing_binary_denies(self) -> None:
        d = self.check(bg.BendGate(self.tmp / "nope"))
        self.assertFalse(d.allowed)
        self.assertEqual(d.source, "fail-closed")

    def test_garbage_reply_denies(self) -> None:
        d = self.check(bg.BendGate(fake_kernel(self.tmp, "read line; echo OK"), threads=None))
        self.assertFalse(d.allowed)
        self.assertEqual(d.source, "fail-closed")

    def test_exit_without_reply_denies(self) -> None:
        d = self.check(bg.BendGate(fake_kernel(self.tmp, "exit 3"), threads=None))
        self.assertFalse(d.allowed)

    def test_timeout_denies(self) -> None:
        d = self.check(bg.BendGate(fake_kernel(self.tmp, "sleep 5"), threads=None, timeout_s=0.3))
        self.assertFalse(d.allowed)
        self.assertIn("timed out", d.detail)

    def test_canonical_disagreement_denies(self) -> None:
        gate = bg.BendGate(fake_kernel(self.tmp, "while read line; do echo ALLOW; done"), threads=None)
        with gate:
            d = gate.check_document(self.doc, CATALOG, canonical=lambda doc: ["x"])
            self.assertFalse(d.allowed)
            self.assertEqual(d.source, "canonical-mismatch")
            ok = gate.check_document(self.doc, CATALOG, canonical=lambda doc: [])
            self.assertTrue(ok.allowed)

    def test_unbound_executable_cannot_claim_current_source_revision(self) -> None:
        path = fake_kernel(self.tmp, "while read line; do echo ALLOW; done")
        with bg.BendGate(path, threads=None) as gate:
            decision = gate.check_spawn(spawn())
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.kernel_revision)
        self.assertEqual(decision.build_provenance, "unverified")
        self.assertEqual(decision.executable_sha256, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_missing_executable_has_no_runtime_identity(self) -> None:
        decision = self.check(bg.BendGate(self.tmp / "nope"))
        self.assertFalse(decision.allowed)
        self.assertIsNone(decision.kernel_revision)
        self.assertIsNone(decision.executable_sha256)

    def test_restart_cannot_silently_switch_executable_identity(self) -> None:
        path = fake_kernel(self.tmp, "while read line; do echo ALLOW; done")
        with bg.BendGate(path, threads=None) as gate:
            first = gate.check_spawn(spawn())
            self.assertTrue(first.allowed)
            gate.close()
            fake_kernel(self.tmp, "while read line; do echo DENY 101; done")
            second = gate.check_spawn(spawn())
        self.assertFalse(second.allowed)
        self.assertEqual(second.source, "fail-closed")
        self.assertIn("executable changed", second.detail)


@unittest.skipUnless(HAVE_KERNEL, "Bend gate kernel not built (z0int.bend_gate.build_kernel())")
class KernelParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = bg.BendGate()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.gate.close()

    def test_fixture_verdicts_and_codes_match_canonical(self) -> None:
        for path in FIXTURES:
            doc = json.loads(path.read_text())
            issues = validate(doc)
            enc = bg.encode_document(doc, CATALOG)
            with self.subTest(fixture=path.name):
                if enc.unsupported:
                    self.assertEqual(path.name, "hotl-0.1-fanin.json")
                    continue
                if not enc.representable:
                    self.assertTrue(issues)
                    continue
                ok, codes = self.gate.raw(enc.tokens)
                self.assertEqual(ok, not issues)
                self.assertEqual(Counter(bg.RULE_CODES[c] for c in codes), Counter(i.code for i in issues))

    def test_quirks_match_canonical(self) -> None:
        base = load("valid/market.json")
        cases = []
        for pay in (0, 0.0, False, None, True, "escrow"):
            d = copy.deepcopy(base)
            d["policies"]["auction"]["payment"] = pay
            cases.append(d)
        d = copy.deepcopy(base)
        n = d["intentGraph"]["nodes"][0]
        n["capabilities"] = n["capabilities"] + ["Wallet"]
        n["authorityCeiling"] = n.get("authorityCeiling", []) + ["wallet"]
        cases.append(d)
        for doc in cases:
            issues = validate(doc)
            ok, codes = self.gate.raw(bg.encode_document(doc, CATALOG).tokens)
            self.assertEqual(ok, not issues)
            self.assertEqual(Counter(bg.RULE_CODES[c] for c in codes), Counter(i.code for i in issues))

    def test_garbage_lines_fail_closed(self) -> None:
        for toks in ([], [9], [1, 5], [2, 0], ["x"], [1] + [0] * 10 + [7]):
            with self.subTest(toks=toks):
                ok, codes = self.gate.raw(toks) if toks else (False, (0,))
                self.assertFalse(ok)
                self.assertEqual(codes, (0,))

    def test_transitions_match_reference(self) -> None:
        cases = [spawn(), spawn(request_revision=2), spawn(dynamic_allowed=False), spawn(live_children=3),
                 spawn(live_children=4), spawn(parent_depth=1), spawn(parent_depth=2),
                 spawn(proposed={"tokens": 600}), spawn(proposed={"tokens": 601}),
                 spawn(requested=["execute", "merge"]), spawn(requested=[]),
                 spawn(budgets={"tokens": 2**46}, observed={"tokens": 2**45}, proposed={"tokens": 2**45}),
                 spawn(budgets={"tokens": 2**46}, observed={"tokens": 2**45}, proposed={"tokens": 2**45 + 1}),
                 spawn(max_children="4"), spawn(max_children=2**40, live_children=2**33)]
        for p in cases:
            with self.subTest(p=p):
                d = self.gate.check_spawn(p)
                ref = bg.reference_transition(p)
                self.assertEqual(d.allowed, not ref)
                self.assertEqual(sorted(d.codes), sorted(bg.TRANSITION_CODES[c] for c in ref))

    def test_unrepresentable_spawn_denied(self) -> None:
        d = self.gate.check_spawn(spawn(proposed={"tokens": 0.5}))
        self.assertFalse(d.allowed)
        self.assertEqual(d.source, "host-shape")


if __name__ == "__main__":
    unittest.main()
