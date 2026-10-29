from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class PathsLayout(unittest.TestCase):
    def test_ensure_layout(self):
        from z0int.paths import ensure_layout

        with tempfile.TemporaryDirectory() as tmp:
            layout = ensure_layout(Path(tmp))
            self.assertTrue((Path(tmp) / "state").is_dir())
            self.assertTrue((Path(tmp) / "episodes").is_dir())
            self.assertEqual(layout["root"], Path(tmp).resolve())


class ModelsPlan(unittest.TestCase):
    def test_decision_roster_five_post_models(self):
        from z0int.models_mgmt import decision_roster

        roster = decision_roster()
        for mid in ("laya_421m", "decider_2b", "nanojev_06b", "reflex", "system_one_4b"):
            self.assertIn(mid, roster["candidates"])
            self.assertEqual(roster["entries"][mid]["family"], "decision_backend")
        self.assertFalse(roster["entries"]["system_one_4b"].get("commercial_use", True))
        self.assertEqual(roster["entries"]["reflex"]["type"], "browser_app")

    def test_plan_twelve_gb(self):
        from z0int.models_mgmt import plan_models

        plan = plan_models(vram_gb=12.0)
        self.assertEqual(plan["policy"], "twelve_gb")
        # Laya is the preferred resident local fast path on 12GB.
        # NanoJev remains available only on demand for legacy benchmark/repro work.
        self.assertIn("laya_421m", plan["resident"])
        self.assertNotIn("nanojev_06b", plan["resident"])
        self.assertIn("nanojev_06b", plan["on_demand"])
        self.assertIn("local_mb", plan["resident"])
        self.assertNotIn("openjev_4b", plan["resident"])
        pairs = {tuple(x) for x in plan["never_coreside"]}
        self.assertIn(("nanojev_06b", "openjev_4b"), pairs)
        self.assertIn(("nanojev_06b", "openjev_06b"), pairs)

    def test_plan_cpu(self):
        from z0int import models_mgmt

        with mock.patch.object(models_mgmt, "detect_vram_gb", return_value=None):
            with mock.patch.object(models_mgmt, "detect_gpu_name", return_value=None):
                plan = models_mgmt.plan_models(vram_gb=None)
        # explicit None still re-detects inside plan_models — pass 0-sentinel via policy
        plan = models_mgmt.plan_models(vram_gb=0.0)
        self.assertEqual(plan["policy"], "cpu_only")
        self.assertIn("local_mb", plan["resident"])


class Doctor(unittest.TestCase):
    def test_doctor_json_shape(self):
        from z0int.doctor import run_doctor

        rep = run_doctor()
        d = rep.to_dict()
        self.assertEqual(d["schema"], "z0int.doctor.v1")
        self.assertIn("checks", d)
        self.assertIn("hardware", d)
        self.assertIn("models_plan", d)


class OnboardDryRun(unittest.TestCase):
    def test_dry_run_skip_el(self):
        from z0int.onboard import run_onboard
        from z0int import paths as paths_mod

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "z0"
            home.mkdir()
            with mock.patch.object(paths_mod, "home", return_value=home):
                # patch onboard module bindings
                import z0int.onboard as ob

                with mock.patch.object(ob.paths, "home", return_value=home):
                    with mock.patch.object(ob.paths, "ensure_layout", return_value={"root": home}):
                        with mock.patch.object(
                            ob.paths, "onboard_state_path", return_value=home / "state" / "onboard.json"
                        ):
                            with mock.patch.object(
                                ob.paths, "config_path", return_value=home / "config" / "z0int.json"
                            ):
                                (home / "state").mkdir(parents=True, exist_ok=True)
                                (home / "config").mkdir(parents=True, exist_ok=True)
                                (home / "sources").mkdir(parents=True, exist_ok=True)
                                report = run_onboard(
                                    dry_run=True,
                                    skip_evolution_lab=True,
                                    force=True,
                                )
        self.assertTrue(report["ok"], report)
        self.assertIn("layout", report["results"])
        self.assertIn("models.plan", report["results"])


class OnboardOmpOptIn(unittest.TestCase):
    def test_default_preserves_reset_and_previous_links(self):
        from z0int.onboard import run_onboard

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", {"HOME": tmp, "Z0INT_HOME": str(Path(tmp) / "state")}
        ):
            ext = Path(tmp) / ".omp" / "agent" / "extensions"
            ext.mkdir(parents=True)
            foreign = Path(tmp) / "foreign"
            foreign.mkdir()
            (ext / "openjev").symlink_to(foreign)
            # A previous run marked the plugin step done; reset must still stay reset.
            state = Path(tmp) / "state" / "state" / "onboard.json"
            state.parent.mkdir(parents=True)
            state.write_text('{"steps":{"omp_plugins":{"status":"done"}}}')
            for force in (False, True):
                report = run_onboard(only=["omp_plugins"], force=force)
                self.assertTrue(report["ok"])
                self.assertEqual(report["results"]["omp_plugins"]["reason"], "opt_in")
                self.assertIn("--omp-extension NAME", report["results"]["omp_plugins"]["opt_in"])
                self.assertEqual(list(ext.iterdir()), [ext / "openjev"])
                self.assertEqual((ext / "openjev").resolve(), foreign)

    def test_selection_idempotence_and_conflicts(self):
        from z0int.onboard import run_onboard, _repo_root

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", {"HOME": tmp, "Z0INT_HOME": str(Path(tmp) / "state")}
        ):
            ext = Path(tmp) / ".omp" / "agent" / "extensions"
            ext.mkdir(parents=True)
            foreign = Path(tmp) / "foreign"
            foreign.mkdir()
            (ext / "openjev").symlink_to(foreign)
            for _ in range(2):
                report = run_onboard(only=["omp_plugins"], omp_extensions=["z0int-bridge"])
                self.assertTrue(report["ok"], report)
                self.assertEqual((ext / "z0int-bridge").resolve(), _repo_root() / "omp-extensions" / "z0int-bridge")
            self.assertEqual((ext / "openjev").resolve(), foreign)
            (ext / "openjev-06b").mkdir()
            report = run_onboard(only=["omp_plugins"], omp_extensions=["flyforge-recovery", "openjev", "openjev-06b"])
            self.assertFalse(report["ok"])
            self.assertEqual(set(report["results"]["omp_plugins"]["conflicts"]), {"openjev", "openjev-06b"})
            self.assertFalse((ext / "flyforge-recovery").exists())
            self.assertEqual((ext / "openjev").resolve(), foreign)
            self.assertTrue((ext / "openjev-06b").is_dir())

    def test_invalid_name_fails_before_any_mutation(self):
        from z0int.onboard import run_onboard

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", {"HOME": tmp, "Z0INT_HOME": str(Path(tmp) / "state")}
        ):
            for name in ("../openjev", "/tmp/openjev", "missing"):
                report = run_onboard(omp_extensions=["z0int-bridge", name], skip_evolution_lab=True)
                self.assertFalse(report["ok"])
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_dry_run_does_not_write_anything(self):
        from z0int.onboard import run_onboard

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", {"HOME": tmp, "Z0INT_HOME": str(Path(tmp) / "state")}
        ):
            omp = Path(tmp) / ".omp"
            omp.mkdir()
            report = run_onboard(dry_run=True, skip_evolution_lab=True, sync_models=True,
                                 omp_extensions=["z0int-bridge"])
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["results"]["omp_plugins"]["selected"], ["z0int-bridge"])
            self.assertEqual(list(omp.iterdir()), [])
            self.assertFalse((Path(tmp) / "state").exists())

class CliSmoke(unittest.TestCase):
    def test_module_main_doctor(self):
        from z0int.cli import main

        rc = main(["doctor", "--json"])
        self.assertIn(rc, (0, 1))


if __name__ == "__main__":
    unittest.main()
