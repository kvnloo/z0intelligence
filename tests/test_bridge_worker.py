from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from z0int.bridge.generation import is_stale, publish_current, quarantine, read_current
from z0int.bridge.protocol import BRIDGE_PROTOCOL, compute_build_id
from z0int.bridge.runtime import BridgeRuntime, session_open_path


@contextmanager
def z0home(tmp: str):
    prev = os.environ.get("Z0INT_HOME")
    os.environ["Z0INT_HOME"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("Z0INT_HOME", None)
        else:
            os.environ["Z0INT_HOME"] = prev


class BridgeProtocolTests(unittest.TestCase):
    def test_build_id_stable_shape(self) -> None:
        a = compute_build_id()
        b = compute_build_id()
        self.assertEqual(len(a), 16)
        self.assertEqual(a, b)

    def test_generation_publish_and_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            publish_current(generation=3, instance_id="abc", build_id="deadbeef")
            cur = read_current()
            assert cur is not None
            self.assertEqual(cur["generation"], 3)
            self.assertEqual(cur["protocol"], BRIDGE_PROTOCOL)
            self.assertFalse(is_stale(3))
            self.assertFalse(is_stale(4))
            self.assertTrue(is_stale(2))
            quarantine({"trace_id": "t1"}, reason="test")
            q = Path(tmp) / "stream" / "bridge_quarantine.jsonl"
            self.assertTrue(q.is_file())
            row = json.loads(q.read_text().splitlines()[-1])
            self.assertEqual(row["quarantine_reason"], "test")


class BridgeRuntimeTests(unittest.TestCase):
    def test_session_open_is_per_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            p1 = session_open_path("sess-a", 111)
            p2 = session_open_path("sess-b", 111)
            p3 = session_open_path("sess-a", 222)
            self.assertNotEqual(p1, p2)
            self.assertNotEqual(p1, p3)
            self.assertIn("sessions", str(p1))

    def test_turn_open_close_explicit_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            import z0int.bridge.runtime as rtmod

            rtmod.preflight = lambda prompt: {  # type: ignore[assignment]
                "capability_id": "coding.next_action",
                "route": "local",
                "label": "VERIFY",
                "p": 0.9,
                "baseline_input_tokens": 100,
                "baseline_output_tokens": 50,
                "estimated_frontier_tokens_avoided": 0,
            }
            rtmod.kerdoios_plan = lambda *a, **k: None  # type: ignore[assignment]
            recorded: dict = {}
            rtmod.kerdoios_record = lambda **k: recorded.update(k)  # type: ignore[assignment]

            rt = BridgeRuntime(generation=1, instance_id="test", build_id="buildtest")
            opened = rt.turn_open(
                trace_id="abc123def456",
                session_id="s1",
                prompt="hello world",
                omp_pid=4242,
                writer_generation=1,
            )
            self.assertTrue(opened.get("ok"))
            self.assertEqual(opened["bridge_generation"], 1)
            open_path = session_open_path("s1", 4242)
            self.assertTrue(open_path.is_file())
            closed = rt.turn_close(
                trace_id="abc123def456",
                session_id="s1",
                omp_pid=4242,
                measured=40,
                input_tokens=10,
                output_tokens=30,
                execution_completed=True,
                verified_success=None,
                source="bridge_turn_end",
                measurement_state="partial",
                state_reason="char_count_proxy",
                writer_generation=1,
            )
            self.assertTrue(closed.get("ok"))
            self.assertEqual(
                closed.get("closed", {}).get("receipt", {}).get("measurement_state"),
                "partial",
            )
            self.assertEqual(recorded.get("measurement_state"), "partial")
            self.assertEqual(recorded.get("state_reason"), "char_count_proxy")
            heart = (Path(tmp) / "stream" / "bridge_heart.jsonl").read_text()
            self.assertIn("abc123def456", heart)
            self.assertIn('"measurement_state": "partial"', heart)

    def test_stale_generation_open_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            import z0int.bridge.runtime as rtmod

            rtmod.preflight = lambda prompt: {"route": "local", "capability_id": "x"}  # type: ignore
            rt = BridgeRuntime(generation=5, instance_id="t", build_id="b")
            out = rt.turn_open(
                trace_id="t",
                session_id="s",
                prompt="p",
                omp_pid=1,
                writer_generation=4,
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("error"), "generation_mismatch")


class BridgeWorkerProcessTests(unittest.TestCase):
    def test_worker_hello_self_check_reload_fail_closed(self) -> None:
        env = os.environ.copy()
        root = Path(__file__).resolve().parents[1]
        env["PYTHONPATH"] = str(root / "src")
        env["Z0INT_ROOT"] = str(root)
        with tempfile.TemporaryDirectory() as tmp:
            env["Z0INT_HOME"] = tmp
            py = os.environ.get("Z0INT_PYTHON") or "/home/kvn/tmp/openjev/.venv/bin/python"
            proc = subprocess.Popen(
                [py, "-u", "-m", "z0int.bridge.worker", "--generation", "7"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(root),
            )
            assert proc.stdin and proc.stdout

            def rpc(obj: dict) -> dict:
                proc.stdin.write(json.dumps(obj) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                return json.loads(line)

            hello = rpc({"id": "1", "op": "hello"})
            self.assertTrue(hello["ok"])
            self.assertEqual(hello["protocol"], BRIDGE_PROTOCOL)
            self.assertEqual(hello["generation"], 7)
            check = rpc({"id": "2", "op": "self_check"})
            self.assertTrue(check["ok"])
            bad = rpc({"id": "3", "op": "not_a_real_op"})
            self.assertFalse(bad["ok"])
            st = rpc({"id": "4", "op": "status"})
            self.assertTrue(st["ok"])
            rpc({"id": "5", "op": "shutdown"})
            proc.wait(timeout=5)
            self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
