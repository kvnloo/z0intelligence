"""The linear baseline, and the host-vs-service parity claim.

Two things are pinned here.

**The cheap transferred baseline is servable.** A ridge fit was the strongest
cheap baseline the stack had, but it was not a backend: it could not be served,
compared against a model, or placed. It is now an ordinary `DecisionBackend`.

**Host and service agree.** The architecture claims that the same
`DecisionRequest` produces the same `DecisionResult` whether served by an
in-process host backend or by a worker process, with only transport and
placement differing. That was declared but never proven -- the existing worker
test spawns the process and never sends a `decision` op. This does, with the
SAME fitted weights on both sides, and asserts the answers are identical.

Determinism is load-bearing for that claim, so it is tested directly: feature
hashing uses blake2b rather than `hash()`, because Python's string hash is
salted per process and would make two correct implementations disagree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from z0int.backends.base import request_from_mapping, result_to_dict
from z0int.backends.linear import (
    ABSTAIN,
    DEFAULT_DIMS,
    LinearDecisionBackend,
    _bucket,
    candidates,
    features,
)

ROOT = Path(__file__).resolve().parents[1]


def _request() -> dict:
    return {
        "state": {"repo": "kvnloo/z0intelligence", "task": "worker needed for repo audit"},
        "questions": [
            {"id": "q_worker", "type": "boolean", "instructions": "Should a worker be used?"},
            {
                "id": "q_route",
                "type": "choice",
                "instructions": "Which route?",
                "options": [
                    {"id": "local", "description": "local"},
                    {"id": "remote_free", "description": "remote free"},
                    {"id": "paid", "description": "paid"},
                ],
            },
        ],
    }


def _examples() -> list[dict]:
    """A trivially separable rule: 'audit' implies worker=true and remote_free."""
    rows: list[dict] = []
    for i in range(24):
        audit = i % 2 == 0
        state = {"task": "repo audit" if audit else "format a string", "n": i}
        rows.append({"state": state, "question_id": "q_worker", "answer": "true" if audit else "false"})
        rows.append(
            {"state": state, "question_id": "q_route", "answer": "remote_free" if audit else "local"}
        )
    return rows


def _fitted() -> LinearDecisionBackend:
    return LinearDecisionBackend(kind="ridge").fit(_examples())


class FeatureDeterminismTests(unittest.TestCase):
    def test_bucket_is_stable_across_processes(self) -> None:
        """blake2b, not hash(): a salted hash would differ per process.

        This is what makes a host-vs-service comparison meaningful -- if the
        feature map changed between processes the two sides would be answering
        different questions and any difference would be unattributable.
        """
        token = "task=repo audit"
        here = _bucket(token, DEFAULT_DIMS)
        script = (
            "import sys; sys.path.insert(0, %r);"
            "from z0int.backends.linear import _bucket, DEFAULT_DIMS;"
            "print(_bucket(%r, DEFAULT_DIMS))" % (str(ROOT / "src"), token)
        )
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(int(out.stdout.strip()), here)
        self.assertNotEqual(here, hash(token) % DEFAULT_DIMS)

    def test_features_are_normalised_and_stable(self) -> None:
        a = features({"a": 1, "b": [2, 3]}, "q", 64)
        b = features({"b": [2, 3], "a": 1}, "q", 64)
        np.testing.assert_allclose(a, b, atol=1e-12)
        self.assertAlmostEqual(float(np.linalg.norm(a)), 1.0, places=9)

    def test_empty_state_yields_zero_vector(self) -> None:
        vector = features({}, "", 32)
        self.assertEqual(float(np.linalg.norm(vector)), 0.0)

    def test_candidates_are_stable_and_ordered(self) -> None:
        from z0int.backends.base import DecisionQuestion, DecisionOption

        self.assertEqual(candidates(DecisionQuestion(id="q", type="boolean", instructions="x")), ["false", "true"])
        q = DecisionQuestion(
            id="q", type="choice", instructions="x",
            options=(DecisionOption(id="b", description="b"), DecisionOption(id="a", description="a")),
        )
        self.assertEqual(candidates(q), ["b", "a"])


class LinearBackendTests(unittest.TestCase):
    def test_untrained_is_uniform_and_says_so(self) -> None:
        backend = LinearDecisionBackend(kind="ridge")
        result = backend.evaluate(request_from_mapping(_request()))
        self.assertEqual(result.revision, "untrained")
        answer = result.answers[0]
        self.assertAlmostEqual(answer.probabilities["true"], 0.5, places=9)
        self.assertFalse(backend.health().loaded)

    def test_fit_learns_a_separable_rule(self) -> None:
        backend = _fitted()
        result = backend.evaluate(request_from_mapping(_request()))
        self.assertEqual(result.answers[0].value, True)
        self.assertEqual(result.answers[1].value, "remote_free")
        self.assertGreater(result.answers[0].confidence, 0.5)

    def test_diagnostics_carry_no_process_local_state(self) -> None:
        """Parity compares results; a timestamp here would break comparability."""
        backend = _fitted()
        first = backend.evaluate(request_from_mapping(_request()))
        second = backend.evaluate(request_from_mapping(_request()))
        self.assertEqual(first.diagnostics, second.diagnostics)
        self.assertNotIn("latency", first.diagnostics)
        self.assertNotIn("time", first.diagnostics)

    def test_tie_break_is_deterministic(self) -> None:
        backend = LinearDecisionBackend(kind="ridge")
        values = {backend.evaluate(request_from_mapping(_request())).answers[1].value for _ in range(5)}
        self.assertEqual(len(values), 1)

    def test_save_load_round_trip_preserves_digest_and_answer(self) -> None:
        backend = _fitted()
        request = request_from_mapping(_request())
        before = backend.evaluate(request)
        with tempfile.TemporaryDirectory() as tmp:
            path = backend.save(Path(tmp) / "w.json")
            restored = LinearDecisionBackend.load(path)
        self.assertEqual(restored.digest(), backend.digest())
        after = restored.evaluate(request)
        self.assertEqual(before.answers, after.answers)

    def test_load_rejects_tampered_weights(self) -> None:
        backend = _fitted()
        with tempfile.TemporaryDirectory() as tmp:
            path = backend.save(Path(tmp) / "w.json")
            payload = json.loads(Path(path).read_text())
            label = sorted(payload["weights"])[0]
            payload["weights"][label][0] += 1.0
            Path(path).write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                LinearDecisionBackend.load(path)

    def test_digest_changes_when_weights_change(self) -> None:
        a = _fitted()
        b = LinearDecisionBackend(kind="ridge").fit(_examples()[:10])
        self.assertNotEqual(a.digest(), b.digest())

    def test_abstains_below_the_confidence_threshold(self) -> None:
        from z0int.backends.base import DecisionOption, DecisionQuestion, DecisionRequest

        question = DecisionQuestion(
            id="q", type="choice", instructions="pick",
            options=(
                DecisionOption(id="a", description="a"),
                DecisionOption(id="b", description="b"),
                DecisionOption(id=ABSTAIN, description="no confident answer"),
            ),
        )
        request = DecisionRequest(state={"x": 1}, questions=(question,))
        # Untrained => uniform 1/3 for each of three options.
        strict = LinearDecisionBackend(kind="ridge", confidence_threshold=0.99)
        self.assertEqual(strict.evaluate(request).answers[0].value, ABSTAIN)
        lenient = LinearDecisionBackend(kind="ridge", confidence_threshold=0.0)
        self.assertNotEqual(lenient.evaluate(request).answers[0].value, ABSTAIN)

    def test_logistic_kind_also_fits(self) -> None:
        backend = LinearDecisionBackend(kind="logistic").fit(_examples())
        result = backend.evaluate(request_from_mapping(_request()))
        self.assertEqual(result.answers[0].value, True)

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LinearDecisionBackend(kind="perceptron")


class HostServiceParityTests(unittest.TestCase):
    """Same request, same weights, two processes: the results must be identical."""

    def test_worker_process_agrees_with_the_host(self) -> None:
        backend = _fitted()
        request_mapping = _request()
        host = backend.evaluate(request_from_mapping(request_mapping))

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["Z0INT_ROOT"] = str(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            env["Z0INT_HOME"] = tmp
            # Same fitted weights in both processes. Without this the worker
            # would build an untrained backend and the comparison would be
            # between two different models.
            env["Z0INT_LINEAR_WEIGHTS"] = str(backend.save(Path(tmp) / "linear.json"))
            py = os.environ.get("Z0INT_PYTHON") or sys.executable
            proc = subprocess.Popen(
                [py, "-u", "-m", "z0int.bridge.worker", "--generation", "1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, cwd=str(ROOT),
            )
            assert proc.stdin and proc.stdout

            def rpc(obj: dict) -> dict:
                proc.stdin.write(json.dumps(obj) + "\n")
                proc.stdin.flush()
                return json.loads(proc.stdout.readline())

            try:
                self.assertTrue(rpc({"id": "1", "op": "hello"})["ok"])
                warm = rpc(
                    {"id": "2", "op": "decision_warm", "payload": {"backend": "linear"}}
                )
                self.assertTrue(warm["ok"], warm)
                # Warmup is asynchronous by design: a decision taken while the
                # backend loads returns `status: warming` rather than blocking.
                # Poll until it is genuinely ready, then assert on the result.
                deadline = time.time() + 30.0
                out: dict = {}
                attempt = 3
                while time.time() < deadline:
                    attempt += 1
                    out = rpc(
                        {
                            "id": str(attempt),
                            "op": "decision",
                            "payload": {
                                "backend": "linear",
                                "capability_id": "rlm.worker_needed",
                                "request": request_mapping,
                            },
                        }
                    )
                    if out.get("ok"):
                        break
                    if out.get("status") != "warming":
                        break
                    time.sleep(0.05)
                self.assertTrue(out.get("ok"), out)
            finally:
                rpc({"id": "9", "op": "shutdown"})
                proc.wait(timeout=10)

        served = out["result"]
        # Identical answers: value, probabilities and confidence, per question.
        self.assertEqual(len(served["answers"]), len(host.answers))
        for got, expected in zip(served["answers"], host.answers):
            self.assertEqual(got["question_id"], expected.question_id)
            self.assertEqual(got["type"], expected.type)
            self.assertEqual(got["value"], expected.value)
            self.assertAlmostEqual(got["confidence"], expected.confidence, places=9)
            self.assertEqual(sorted(got["probabilities"]), sorted(expected.probabilities))
            for key, probability in expected.probabilities.items():
                self.assertAlmostEqual(got["probabilities"][key], probability, places=9)

    def test_the_two_processes_report_the_same_model_revision(self) -> None:
        """Only transport may differ -- not the model identity."""
        backend = _fitted()
        with tempfile.TemporaryDirectory() as tmp:
            path = backend.save(Path(tmp) / "linear.json")
            os.environ["Z0INT_LINEAR_WEIGHTS"] = str(path)
            try:
                from z0int.backends.registry import create_backend

                loaded = create_backend("linear")
            finally:
                os.environ.pop("Z0INT_LINEAR_WEIGHTS", None)
        self.assertEqual(loaded.digest(), backend.digest())
        self.assertEqual(loaded.revision, backend.revision)


if __name__ == "__main__":
    unittest.main()
