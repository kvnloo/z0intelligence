"""Install-readiness tests for the OMP z0int bridge (feat/omp-bridge-ready).

Covers the defects found by running the extension inside a real sandboxed
``omp`` (scripts/omp_bridge_e2e.py):

* generation pointers are per OMP process, so one session's hot reload no
  longer quarantines another session's writes;
* Kerdoios is opt-in and never spawned (or credited) when unconfigured;
* the installer manages exactly one symlink;
* the JS automatic client skips the Python spawn for disabled harnesses;
* the TS shim reads the OMP session id from ``ctx.sessionManager``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import omp_bridge_install as installer  # noqa: E402

from z0int.bridge import generation as gen  # noqa: E402
from z0int.bridge import runtime as rtmod  # noqa: E402
from z0int.bridge.runtime import BridgeRuntime  # noqa: E402


@contextmanager
def env(**values: str | None):
    prev = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


LOCAL_PREFLIGHT = {
    "capability_id": "coding.next_action",
    "route": "model",
    "work_requirement": {"mode": "balanced", "coding": 1.0, "reasoning": 0.5},
    "baseline_input_tokens": 10,
    "baseline_output_tokens": 5,
}


class PerProcessGenerationTests(unittest.TestCase):
    def test_other_process_pointer_does_not_govern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, env(Z0INT_HOME=tmp):
            gen.publish_current(generation=1, instance_id="b", build_id="x", omp_pid=200)
            gen.publish_current(generation=2, instance_id="a", build_id="x", omp_pid=100)
            # A's own old worker is stale; B at generation 1 is not.
            self.assertTrue(gen.is_stale(1, omp_pid=100))
            self.assertFalse(gen.is_stale(1, omp_pid=200))
            self.assertFalse(gen.is_stale(2, omp_pid=100))

    def test_global_pointer_only_trusted_for_same_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, env(Z0INT_HOME=tmp):
            # Legacy writer: global pointer only (as the pre-fix TS shim wrote it).
            gen.current_path().write_text(json.dumps({"generation": 5, "omp_pid": 100}))
            self.assertTrue(gen.is_stale(4, omp_pid=100))
            self.assertFalse(gen.is_stale(4, omp_pid=300))
            # No pid: legacy global semantics preserved.
            self.assertTrue(gen.is_stale(4))

    def test_second_session_turn_not_quarantined_after_first_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, env(Z0INT_HOME=tmp), mock.patch.object(
            rtmod, "preflight", lambda prompt: dict(LOCAL_PREFLIGHT)
        ), mock.patch.object(rtmod, "kerdoios_target", lambda: None):
            gen.publish_current(generation=1, instance_id="b1", build_id="x", omp_pid=200)
            gen.publish_current(generation=2, instance_id="a2", build_id="x", omp_pid=100)
            b = BridgeRuntime(generation=1, instance_id="b1", build_id="x")
            opened = b.turn_open(
                trace_id="tb", session_id="sb", prompt="p", omp_pid=200, writer_generation=1
            )
            self.assertTrue(opened.get("ok"), opened)
            closed = b.turn_close(
                trace_id="tb", session_id="sb", omp_pid=200, measured=3, writer_generation=1
            )
            self.assertTrue(closed.get("ok"), closed)
            self.assertFalse(gen.quarantine_path().exists())
            heart = (Path(tmp) / "stream" / "bridge_heart.jsonl").read_text()
            self.assertIn('"trace_id": "tb"', heart)


class KerdoiosOptInTests(unittest.TestCase):
    def test_unconfigured_is_none_and_never_spawns(self) -> None:
        with env(KERDOIOS_ROOT=None, Z0INT_KERDOIOS_ROOT=None, Z0INT_BRIDGE_KERDOIOS=None), mock.patch.object(
            rtmod.subprocess, "run", side_effect=AssertionError("spawned")
        ):
            self.assertIsNone(rtmod.kerdoios_target())
            self.assertIsNone(rtmod.kerdoios_plan("coding.x", {"mode": "balanced"}))
            rtmod.kerdoios_record(
                provider="p", model="m", capability_id="c", completed=False, input_tokens=1, output_tokens=1
            )

    def test_configured_root_and_kill_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "kerdoios").mkdir()
            with env(KERDOIOS_ROOT=tmp, KERDOIOS_PYTHON=None, EVOLUTION_LAB_PYTHON=None, Z0INT_BRIDGE_KERDOIOS=None):
                target = rtmod.kerdoios_target()
                self.assertEqual(target, (Path(tmp), sys.executable))
            with env(KERDOIOS_ROOT=tmp, Z0INT_BRIDGE_KERDOIOS="0"):
                self.assertIsNone(rtmod.kerdoios_target())
            with env(KERDOIOS_ROOT=str(Path(tmp) / "missing"), Z0INT_BRIDGE_KERDOIOS=None):
                self.assertIsNone(rtmod.kerdoios_target())

    def test_failed_plan_is_not_credited_to_kerdoios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, env(Z0INT_HOME=tmp), mock.patch.object(
            rtmod, "preflight", lambda prompt: dict(LOCAL_PREFLIGHT)
        ), mock.patch.object(rtmod, "kerdoios_plan", lambda *a, **k: {"ok": False, "error": "boom"}):
            rt = BridgeRuntime(generation=1, instance_id="i", build_id="b")
            out = rt.turn_open(trace_id="t1", session_id="s", prompt="p", omp_pid=1, writer_generation=1)
            self.assertEqual(out["receipt"]["provider"], "frontier")

    def test_successful_plan_placement_is_credited(self) -> None:
        plan = {"ok": True, "placements": [{"provider": "groq", "model": "llama"}]}
        with tempfile.TemporaryDirectory() as tmp, env(Z0INT_HOME=tmp), mock.patch.object(
            rtmod, "preflight", lambda prompt: dict(LOCAL_PREFLIGHT)
        ), mock.patch.object(rtmod, "kerdoios_plan", lambda *a, **k: plan):
            rt = BridgeRuntime(generation=1, instance_id="i", build_id="b")
            out = rt.turn_open(trace_id="t2", session_id="s", prompt="p", omp_pid=1, writer_generation=1)
            self.assertEqual((out["receipt"]["provider"], out["receipt"]["model"]), ("groq", "llama"))


class InstallerTests(unittest.TestCase):
    def test_install_status_uninstall_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Path(tmp) / "agent"
            self.assertFalse(installer.status(agent)["installed"])
            out = installer.install(agent)
            self.assertEqual(out["action"], "installed")
            link = agent / "extensions" / "z0int-bridge"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), (ROOT / "omp-extensions" / "z0int-bridge").resolve())
            # Sibling imports resolve through the link's real path.
            self.assertTrue((link.resolve().parent / "z0int-intelligence" / "index.ts").is_file())
            self.assertEqual(installer.install(agent)["action"], "already_installed")
            self.assertEqual(installer.uninstall(agent)["action"], "uninstalled")
            self.assertFalse(link.exists() or link.is_symlink())
            self.assertEqual(installer.uninstall(agent)["action"], "not_installed")
            # Nothing else created besides the (now empty) extensions dir.
            self.assertEqual(sorted(p.name for p in agent.iterdir()), ["extensions"])

    def test_refuses_foreign_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Path(tmp) / "agent"
            link = agent / "extensions" / "z0int-bridge"
            link.mkdir(parents=True)
            with self.assertRaises(SystemExit):
                installer.install(agent)
            with self.assertRaises(SystemExit):
                installer.uninstall(agent)
            self.assertTrue(link.is_dir())

    def test_cli_status_is_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "omp_bridge_install.py"), "status", "--agent-dir", tmp],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertTrue(json.loads(proc.stdout)["root_complete"])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class AutomaticClientTests(unittest.TestCase):
    def _enabled(self, z0home: str, extra_env: dict[str, str] | None = None) -> bool:
        script = (
            "const m=await import(process.argv[1]);"
            "console.log(JSON.stringify(m.automaticEnabled('omp',JSON.parse(process.argv[2]))));"
        )
        envmap = {"Z0INT_HOME": z0home, **(extra_env or {})}
        proc = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                (ROOT / "harness-adapters" / "automatic-client.mjs").as_uri(),
                json.dumps(envmap),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def test_disabled_without_config_and_by_kill_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(self._enabled(tmp))
            (Path(tmp) / "config").mkdir()
            (Path(tmp) / "config" / "automatic.json").write_text(json.dumps({"omp": {"enabled": True}}))
            self.assertTrue(self._enabled(tmp))
            self.assertFalse(self._enabled(tmp, {"Z0INT_AUTO_OMP": "0"}))

    def test_matches_python_settings(self) -> None:
        from z0int import automatic

        with tempfile.TemporaryDirectory() as tmp, env(Z0INT_HOME=tmp, Z0INT_AUTO_OMP=None):
            self.assertEqual(automatic.settings("omp")[0], self._enabled(tmp))
            (Path(tmp) / "config").mkdir()
            (Path(tmp) / "config" / "automatic.json").write_text(json.dumps({"omp": {"enabled": True}}))
            self.assertEqual(automatic.settings("omp")[0], self._enabled(tmp))


@unittest.skipUnless(shutil.which("bun"), "bun not installed")
class BridgeShimTests(unittest.TestCase):
    def _bun(self, expr: str) -> object:
        mod = (ROOT / "omp-extensions" / "z0int-bridge" / "index.ts").as_posix()
        script = f"const m = await import({json.dumps(mod)}); console.log(JSON.stringify({expr}));"
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                ["bun", "-e", script],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "Z0INT_HOME": tmp, "OMP_SESSION_ID": ""},
                cwd=tmp,
            )
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_session_id_from_session_manager(self) -> None:
        self.assertEqual(
            self._bun("m.resolveSessionId({sessionManager:{getSessionId:()=>'sess-123'}})"), "sess-123"
        )
        self.assertTrue(str(self._bun("m.resolveSessionId({})")).startswith("omp-"))

    def test_python_resolution_order(self) -> None:
        self.assertEqual(self._bun("m.resolvePython({Z0INT_PYTHON:'/x/py'}, '/r', '/z')"), "/x/py")
        self.assertEqual(self._bun("m.resolvePython({}, '/nonexistent-root', '/nonexistent-z0')"), "python3")


@unittest.skipUnless(os.environ.get("Z0INT_OMP_E2E") == "1" and shutil.which("omp"), "set Z0INT_OMP_E2E=1 (needs omp)")
class OmpSandboxEndToEnd(unittest.TestCase):
    def test_sandboxed_omp_bridge_e2e(self) -> None:
        sandbox = Path.home() / ".cache" / "omp-bridge-sandbox-test"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "omp_bridge_e2e.py"), "--runs", "2", "--sandbox", str(sandbox)],
            capture_output=True,
            text=True,
            timeout=540,
        )
        summary = json.loads((sandbox / "summary.json").read_text())
        self.assertTrue(summary["ok"], summary["checks"])
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
