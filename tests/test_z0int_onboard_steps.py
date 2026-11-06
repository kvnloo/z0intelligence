"""Selected onboarding, preview isolation and durable consent (synthetic only)."""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from z0int import onboard as ob


class SimulatedCrash(BaseException):
    """Emulate process loss without giving the runner a chance to save a result."""


class OnboardSteps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "private"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".gitignore").write_text(".z0int/\ndata/private/\n", encoding="utf-8")
        self.patch(mock.patch.dict(os.environ, {"Z0INT_HOME": str(self.home), "SKIP_EVOLUTION_LAB": "0"}))
        self.patch(mock.patch.object(ob, "_repo_root", return_value=self.repo))
        self.patch(mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden")))
        self.patch(mock.patch.object(subprocess, "run", side_effect=AssertionError("subprocess forbidden")))
        self.patch(mock.patch.object(subprocess, "check_output", side_effect=AssertionError("probe forbidden")))
        self.plan = {
            "schema": "z0int.models_plan.v1",
            "resident": ["fixture_model"],
            "recommendation": "synthetic resident plan",
        }
        self.manifest = {
            "models": {"fixture_model": {"hf": "fixture/model", "revision": "fixture-revision"}},
        }
        self.plan_models = self.patch(mock.patch.object(
            ob.models_mgmt, "plan_models", side_effect=lambda: copy.deepcopy(self.plan),
        ))
        self.load_manifest = self.patch(mock.patch.object(
            ob.models_mgmt, "load_manifest", side_effect=lambda: copy.deepcopy(self.manifest),
        ))
        self.sync_models = self.patch(mock.patch.object(
            ob.models_mgmt, "sync_models", return_value={"results": [{"status": "cached"}]},
        ))
        self.el = self.patch(mock.patch.object(
            ob.doctor, "discover_evolution_lab",
            return_value={"found": False, "path": None},
        ))
        self.data = self.patch(mock.patch.object(
            ob.doctor, "discover_data_sources", return_value={"synthetic_source": {"present": True}},
        ))
        self.omp = self.patch(mock.patch.object(
            ob.doctor, "discover_omp", return_value={"omp_home": None},
        ))
        self.doctor = self.patch(mock.patch.object(
            ob.doctor, "run_doctor", return_value=ob.doctor.DoctorReport(),
        ))

    def patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def state(self):
        return json.loads((self.home / "state" / "onboard.json").read_text(encoding="utf-8"))

    def snapshot(self):
        if not self.home.exists():
            return {}
        return {
            str(path.relative_to(self.home)): path.read_bytes() if path.is_file() else None
            for path in self.home.rglob("*")
        }

    def write_state(self, value):
        path = self.home / "state" / "onboard.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def interrupted_plan(self, *, force=False):
        calls = []

        def finish(state, ctx):
            calls.append("layout")
            ob._mark(state, "layout", "done")
            return {"ok": True}

        def crash(state, ctx):
            calls.append("privacy")
            raise SimulatedCrash()

        with mock.patch.object(ob, "STEPS", [("layout", finish), ("privacy", crash)]):
            with self.assertRaises(SimulatedCrash):
                ob.run_onboard(only=["privacy", "layout"], force=force)
        self.assertEqual(calls, ["layout", "privacy"])
        return self.state()

    def test_selected_steps_run_once_in_canonical_order(self):
        calls = []
        original = dict(ob.STEPS)

        def selected(name):
            def invoke(state, ctx):
                calls.append(name)
                return original[name](state, ctx)
            return invoke

        operations = [
            (name, selected(name) if name in {"data.discover", "env_hints"} else
             mock.Mock(side_effect=AssertionError("unselected step")))
            for name, _ in ob.STEPS
        ]
        with mock.patch.object(ob, "STEPS", operations):
            report = ob.run_onboard(only=["env_hints", "data.discover"])
        self.assertTrue(report["ok"], report)
        self.assertEqual(calls, ["data.discover", "env_hints"])
        self.assertEqual(set(self.state()["steps"]), {"data.discover", "env_hints"})
        self.assertEqual({p.name for p in self.home.iterdir()}, {"state", "sources", "config"})
        self.assertEqual(report["pending"], [])

    def test_invalid_empty_and_duplicate_selections_do_not_read_or_write_state(self):
        invalid = [[], ["no_such_step"], ["layout", "layout"], ["layout", ""], "layout"]
        with mock.patch.object(ob, "load_state", side_effect=AssertionError("state read")):
            for selection in invalid:
                with self.subTest(selection=selection):
                    report = ob.run_onboard(only=selection)
                    self.assertFalse(report["ok"])
                    self.assertEqual(report["error_code"], "invalid_steps")
        self.assertFalse(self.home.exists())

    def test_preview_calls_no_operations_and_creates_no_home(self):
        operations = [(name, mock.Mock(side_effect=AssertionError("step ran"))) for name, _ in ob.STEPS]
        with mock.patch.object(ob, "STEPS", operations), mock.patch.object(
            ob, "save_state", side_effect=AssertionError("checkpoint write"),
        ), mock.patch.object(ob.paths, "ensure_layout", side_effect=AssertionError("layout write")):
            report = ob.run_onboard(dry_run=True, force=True, sync_models=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["selected"], [name for name, _ in operations])
        self.assertTrue(all(result["would_run"] for result in report["results"].values()))
        for _, operation in operations:
            operation.assert_not_called()
        for operation in (self.doctor, self.el, self.data, self.omp, self.plan_models, self.sync_models, self.load_manifest):
            operation.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_preview_preserves_existing_files_and_does_not_complete_steps(self):
        first = ob.run_onboard(only=["layout"])
        self.assertTrue(first["ok"])
        before = self.snapshot()
        report = ob.run_onboard(only=["layout", "models.plan"], dry_run=True)
        self.assertTrue(report["ok"])
        self.assertFalse(report["results"]["layout"]["would_run"])
        self.assertTrue(report["results"]["models.plan"]["would_run"])
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("models.plan", self.state()["steps"])
        self.plan_models.assert_not_called()

    def test_preview_does_not_consume_future_execution(self):
        preview = ob.run_onboard(only=["models.plan"], dry_run=True)
        self.assertTrue(preview["ok"])
        self.assertFalse(self.home.exists())
        applied = ob.run_onboard(only=["models.plan"])
        self.assertTrue(applied["ok"], applied)
        self.plan_models.assert_called_once_with()
        config = json.loads((self.home / "config" / "z0int.json").read_text(encoding="utf-8"))
        self.assertEqual(config["models_plan"], self.plan)

    def test_each_write_step_works_without_layout(self):
        for name, directory in (("models.plan", "config"), ("data.discover", "sources"), ("env_hints", "config")):
            with self.subTest(step=name), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                home = Path(tmp) / "private"
                with mock.patch.dict(os.environ, {"Z0INT_HOME": str(home)}):
                    report = ob.run_onboard(only=[name])
                self.assertTrue(report["ok"], report)
                self.assertEqual({path.name for path in home.iterdir()}, {"state", directory})
                self.assertNotIn("layout", report["results"])

    def test_selected_doctor_creates_only_checkpoint_directory(self):
        report = ob.run_onboard(only=["doctor"])
        self.assertTrue(report["ok"], report)
        self.doctor.assert_called_once_with()
        self.assertEqual({path.name for path in self.home.iterdir()}, {"state"})
        self.assertEqual(set(self.state()["steps"]), {"doctor"})
        self.el.assert_not_called()
        self.sync_models.assert_not_called()

    def test_models_sync_requires_opt_in_even_when_selected(self):
        skipped = ob.run_onboard(only=["models.sync"])
        self.assertTrue(skipped["ok"], skipped)
        self.assertTrue(skipped["results"]["models.sync"]["skipped"])
        self.sync_models.assert_not_called()
        self.plan_models.assert_not_called()
        approved = ob.run_onboard(only=["models.sync"], sync_models=True)
        self.assertTrue(approved["ok"], approved)
        self.sync_models.assert_called_once_with(plan=self.plan, which="resident", dry_run=False)

    def test_missing_model_pin_is_failed_not_done(self):
        self.sync_models.return_value = {"results": [{"status": "missing_pin"}]}
        report = ob.run_onboard(only=["models.sync"], sync_models=True)
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed"], ["models.sync"])
        self.assertEqual(self.state()["steps"]["models.sync"]["status"], "error")
        self.assertEqual(self.state()["run"]["completed"], [])

    def test_checkpoint_precedes_operation_and_resume_does_not_widen_or_repeat(self):
        saved = self.interrupted_plan(force=True)
        self.assertEqual(saved["run"]["steps"], ["layout", "privacy"])
        self.assertEqual(saved["run"]["completed"], ["layout"])
        self.assertEqual(saved["run"]["in_flight"], "privacy")
        before = self.snapshot()
        denied = ob.run_onboard()
        self.assertEqual(denied["error_code"], "resume_required")
        self.assertEqual(self.snapshot(), before)
        preview = ob.run_onboard(resume=True, dry_run=True)
        self.assertEqual(preview["selected"], ["layout", "privacy"])
        self.assertFalse(preview["results"]["layout"]["would_run"])
        self.assertTrue(preview["results"]["privacy"]["would_run"])
        self.assertEqual(self.snapshot(), before)
        layout = mock.Mock(side_effect=AssertionError("completed step repeated"))
        with mock.patch.object(ob, "STEPS", [("layout", layout), ("privacy", ob.step_privacy)]):
            report = ob.run_onboard(resume=True, only=["privacy", "layout"])
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["completed"], ["layout", "privacy"])
        self.assertEqual(report["pending"], [])
        layout.assert_not_called()
        self.assertEqual(self.state()["run"]["status"], "done")
        self.sync_models.assert_not_called()
        self.el.assert_not_called()
        self.omp.assert_not_called()

    def test_resume_rejects_changed_steps_or_new_flags_without_writes(self):
        self.interrupted_plan()
        before = self.snapshot()
        for extra in (
            {"only": ["layout"]}, {"sync_models": True},
            {"skip_evolution_lab": True}, {"force": True},
        ):
            with self.subTest(extra=extra):
                report = ob.run_onboard(resume=True, **extra)
                self.assertEqual(report["error_code"], "resume_mismatch")
                self.assertEqual(self.snapshot(), before)

    def test_resume_requires_saved_consent(self):
        report = ob.run_onboard(resume=True)
        self.assertEqual(report["error_code"], "no_saved_run")
        self.assertFalse(self.home.exists())

    def test_failure_stops_remaining_steps_then_resumes(self):
        calls = []

        def fail(state, ctx):
            calls.append("doctor")
            ob._mark(state, "doctor", "warn")
            return {"ok": False, "unresolved": ["synthetic dependency"]}

        with mock.patch.object(ob, "STEPS", [("doctor", fail), ("privacy", mock.Mock(
            side_effect=AssertionError("ran after failure"),
        ))]):
            first = ob.run_onboard(only=["doctor", "privacy"])
        self.assertFalse(first["ok"])
        self.assertEqual(first["failed"], ["doctor"])
        self.assertEqual(first["pending"], ["doctor", "privacy"])
        self.assertEqual(self.state()["run"]["status"], "failed")
        resumed = ob.run_onboard(resume=True)
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["selected"], ["doctor", "privacy"])
        self.doctor.assert_called_once_with()

    def test_keyboard_interrupt_returns_structured_failure(self):
        self.doctor.side_effect = KeyboardInterrupt()
        report = ob.run_onboard(only=["doctor"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["results"]["doctor"]["error"], "KeyboardInterrupt")
        self.assertEqual(self.state()["run"]["in_flight"], "doctor")
        self.assertEqual(self.state()["run"]["completed"], [])

    def test_resume_reuses_saved_model_plan_after_downloader_crash(self):
        self.sync_models.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            ob.run_onboard(only=["models.plan", "models.sync"], sync_models=True)
        saved = self.state()["run"]
        self.assertEqual(saved["completed"], ["models.plan"])
        self.assertEqual(saved["context"]["models_plan"], self.plan)
        self.plan_models.side_effect = AssertionError("model plan recomputed")
        self.sync_models.side_effect = None
        report = ob.run_onboard(resume=True)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["options"]["sync_models"])
        self.sync_models.assert_called_with(plan=self.plan, which="resident", dry_run=False)
        self.assertEqual(self.plan_models.call_count, 1)

    def test_resume_rejects_model_manifest_drift_before_download(self):
        self.sync_models.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            ob.run_onboard(only=["models.sync"], sync_models=True)
        before = self.snapshot()
        self.manifest["models"]["fixture_model"]["revision"] = "changed-revision"
        report = ob.run_onboard(resume=True)
        self.assertEqual(report["error_code"], "resume_mismatch")
        self.assertEqual(self.sync_models.call_count, 1)
        self.assertEqual(self.snapshot(), before)

    def test_resume_freezes_evolution_lab_destination(self):
        first_destination = self.root / "first-lab"
        second_destination = self.root / "second-lab"
        self.el.return_value = {"found": True, "path": str(first_destination)}
        with mock.patch.object(ob, "_run", side_effect=SimulatedCrash()):
            with self.assertRaises(SimulatedCrash):
                ob.run_onboard(only=["evolution_lab"])
        self.assertEqual(self.state()["run"]["context"]["evolution_lab_path"], str(first_destination))
        self.el.return_value = {"found": True, "path": str(second_destination)}
        with mock.patch.object(ob, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")) as command:
            report = ob.run_onboard(resume=True)
        self.assertTrue(report["ok"], report)
        clone = command.call_args_list[0].args[0]
        install = command.call_args_list[-1].args[0]
        self.assertEqual(clone[-1], str(first_destination))
        self.assertEqual(install[-1], str(first_destination))
        self.el.assert_called_once_with()
        self.assertFalse(second_destination.exists())

    def test_resume_rejects_changed_evolution_source(self):
        with mock.patch.object(ob, "_run", side_effect=SimulatedCrash()):
            with self.assertRaises(SimulatedCrash):
                ob.run_onboard(only=["evolution_lab"])
        before = self.snapshot()
        with mock.patch.object(ob, "EVOLUTION_LAB_REF", "different-ref"):
            report = ob.run_onboard(resume=True)
        self.assertEqual(report["error_code"], "resume_mismatch")
        self.assertEqual(self.snapshot(), before)

    def test_resume_keeps_original_environment_skip(self):
        crash = mock.Mock(side_effect=SimulatedCrash())
        with mock.patch.dict(os.environ, {"SKIP_EVOLUTION_LAB": "1"}), mock.patch.object(
            ob, "STEPS", [("evolution_lab", crash)],
        ):
            with self.assertRaises(SimulatedCrash):
                ob.run_onboard(only=["evolution_lab"])
        report = ob.run_onboard(resume=True)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["results"]["evolution_lab"]["skipped"])
        self.el.assert_not_called()

    def test_resume_blocks_new_environment_skip(self):
        with mock.patch.object(ob, "_run", side_effect=SimulatedCrash()):
            with self.assertRaises(SimulatedCrash):
                ob.run_onboard(only=["evolution_lab"])
        before = self.snapshot()
        with mock.patch.dict(os.environ, {"SKIP_EVOLUTION_LAB": "1"}):
            report = ob.run_onboard(resume=True)
        self.assertEqual(report["error_code"], "resume_mismatch")
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_state_never_resets_or_executes(self):
        path = self.home / "state" / "onboard.json"
        path.parent.mkdir(parents=True)
        for content in ('{', '[]', '{"schema": "future", "steps": {}}',
                        '{"schema": "z0int.onboard.v1", "steps": []}',
                        '{"schema": "z0int.onboard.v1", "steps": {"layout": {"status": []}}}',
                        '{"schema": "z0int.onboard.v1", "steps": {}, "run": null}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                before = self.snapshot()
                report = ob.run_onboard(only=["doctor"])
                self.assertEqual(report["error_code"], "invalid_state")
                self.assertEqual(self.snapshot(), before)
        self.doctor.assert_not_called()

    def test_malformed_saved_plan_never_authorizes_operations(self):
        saved = self.interrupted_plan()
        changes = [
            {"schema": "future"}, {"steps": ["unknown"]},
            {"steps": ["privacy", "layout"]}, {"completed": ["privacy"]},
            {"in_flight": "layout"}, {"status": "done"}, {"status": []},
            {"options": {"force": 1, "sync_models": False, "skip_evolution_lab": False}},
            {"context": []}, {"context": {"models_plan": []}},
            {"context": {"evolution_lab_path": "relative/path"}}, {"inputs": []},
        ]
        for change in changes:
            with self.subTest(change=change):
                state = copy.deepcopy(saved)
                state["run"].update(change)
                self.write_state(state)
                before = self.snapshot()
                report = ob.run_onboard(resume=True)
                self.assertEqual(report["error_code"], "invalid_state")
                self.assertEqual(self.snapshot(), before)
        self.doctor.assert_not_called()
        self.el.assert_not_called()

    def test_atomic_replace_failure_keeps_previous_checkpoint(self):
        self.assertTrue(ob.run_onboard(only=["layout"])["ok"])
        before = self.snapshot()
        changed = self.state()
        changed["steps"]["layout"]["detail"] = "new value"
        with mock.patch.object(ob.os, "replace", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                ob.save_state(changed)
        self.assertEqual(self.snapshot(), before)

    def test_initial_checkpoint_failure_runs_no_step(self):
        with mock.patch.object(ob, "save_state", side_effect=OSError("synthetic disk error")):
            report = ob.run_onboard(only=["doctor"])
        self.assertEqual(report["error_code"], "checkpoint_error")
        self.doctor.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_result_checkpoint_failure_leaves_in_flight_step_for_resume(self):
        save = ob.save_state
        calls = []

        def failing_save(state, root=None):
            calls.append(copy.deepcopy(state))
            if state["run"]["status"] == "done":
                raise OSError("synthetic completion write failure")
            return save(state, root)

        with mock.patch.object(ob, "save_state", side_effect=failing_save):
            report = ob.run_onboard(only=["doctor"])
        self.assertEqual(report["error_code"], "checkpoint_error")
        self.assertEqual(report["completed"], [])
        self.assertEqual(report["pending"], ["doctor"])
        self.assertEqual(self.state()["run"]["in_flight"], "doctor")
        self.assertEqual(self.state()["run"]["completed"], [])
        self.assertTrue(ob.run_onboard(resume=True)["ok"])

    def test_force_only_reruns_the_selected_completed_steps(self):
        self.assertTrue(ob.run_onboard(only=["models.plan"])["ok"])
        self.assertTrue(ob.run_onboard(only=["models.plan"])["ok"])
        self.assertEqual(self.plan_models.call_count, 1)
        self.assertTrue(ob.run_onboard(only=["models.plan"], force=True)["ok"])
        self.assertEqual(self.plan_models.call_count, 2)
        self.el.assert_not_called()
        self.omp.assert_not_called()
        self.sync_models.assert_not_called()

    def test_cli_json_selection_validation_preview_and_resume(self):
        from z0int.cli import main

        for argv in (
            ["onboard", "--step", "privacy", "--dry-run", "--json"],
            ["--json", "onboard", "--step", "privacy", "--dry-run"],
        ):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(argv), 0)
                report = json.loads(output.getvalue())
            self.assertEqual(report["selected"], ["privacy"])
            self.assertTrue(report["dry_run"])
        self.assertFalse(self.home.exists())
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["onboard", "--step", "invalid", "--json"]), 1)
        self.assertEqual(json.loads(output.getvalue())["error_code"], "invalid_steps")
        self.interrupted_plan()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["onboard", "--resume", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["selected"], ["layout", "privacy"])


if __name__ == "__main__":
    unittest.main()
