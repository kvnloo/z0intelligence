"""The Evolution Lab is a research dependency, not a production one.

Before this, seven modules each carried their own absolute path to an Evolution
Lab checkout and **three of them disagreed**:

    /workspace/evolution-lab          2c95b59
    /home/kvn/tmp/evolution-lab       5cdb8e1   <- mushroom.py hardcoded this
    /home/kvn/tmp/evolution-lab-agy   a2d2485   <- pipeline.py hardcoded this

Different production call sites therefore bound to different revisions of the
same dependency, by accident. `src/z0int/train.py` even hardcoded a fourth
location that did not exist on this machine at all.

These tests pin the resolution rules and, more importantly, guard the boundary:
a hardcoded absolute lab or repo path reappearing in production code fails the
suite. That is the regression this file exists to prevent.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from z0int import paths

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "z0int"


def _isolated_env(base: Path) -> dict:
    """Environment with no lab configured, so the real machine cannot leak in.

    Without this the resolver reads the real ~/.z0int/config/env_hints.json and
    every test that expects "not found" instead finds the developer's checkout.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("EVOLUTION_LAB") and k != "Z0INT_HOME"}
    env["Z0INT_HOME"] = str(_make_home(base, {}))
    return env


def _make_lab(base: Path, name: str = "evolution-lab") -> Path:
    lab = base / name
    (lab / "evolution_lab").mkdir(parents=True, exist_ok=True)
    (lab / "pyproject.toml").write_text("[project]\nname='evolution-lab'\n")
    return lab


def _make_home(base: Path, hints: dict) -> Path:
    home = base / "z0int-home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "env_hints.json").write_text(json.dumps(hints))
    return home


class ResolutionTests(unittest.TestCase):
    def test_explicit_wins_over_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            explicit = _make_lab(base, "explicit-lab")
            env_lab = _make_lab(base, "env-lab")
            with mock.patch.dict(os.environ, {"EVOLUTION_LAB_ROOT": str(env_lab)}, clear=False):
                self.assertEqual(paths.evolution_lab_root(explicit=explicit), explicit.resolve())

    def test_env_wins_over_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env_lab = _make_lab(base, "env-lab")
            hint_lab = _make_lab(base, "hint-lab")
            home = _make_home(base, {"EVOLUTION_LAB_ROOT": str(hint_lab)})
            with mock.patch.dict(
                os.environ,
                {"EVOLUTION_LAB_ROOT": str(env_lab), "Z0INT_HOME": str(home)},
                clear=False,
            ):
                self.assertEqual(paths.evolution_lab_root(), env_lab.resolve())

    def test_hints_are_used_when_no_env_is_set(self) -> None:
        """The onboard-authored hint is configuration, and it must be honoured."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            hint_lab = _make_lab(base, "hint-lab")
            home = _make_home(base, {"EVOLUTION_LAB_ROOT": str(hint_lab)})
            env = {k: v for k, v in os.environ.items()
                   if k not in ("EVOLUTION_LAB_ROOT", "EVOLUTION_LAB_DIR")}
            env["Z0INT_HOME"] = str(home)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(paths.evolution_lab_root(), hint_lab.resolve())

    def test_a_configured_path_that_is_not_a_checkout_is_skipped(self) -> None:
        """A configured path is intent, not truth. It must not win by existing."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bogus = base / "not-a-lab"
            bogus.mkdir()
            real = _make_lab(base, "real-lab")
            env = {"Z0INT_HOME": str(_make_home(base, {}))}
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(paths, "LEGACY_EVOLUTION_LAB_ROOTS", ((real, "test"),)):
                    self.assertEqual(paths.evolution_lab_root(explicit=bogus), real.resolve())

    def test_alias_variable_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lab = _make_lab(base, "alias-lab")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("EVOLUTION_LAB_ROOT", "EVOLUTION_LAB_DIR")}
            env["EVOLUTION_LAB_DIR"] = str(lab)
            env["Z0INT_HOME"] = str(_make_home(base, {}))
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(paths.evolution_lab_root(), lab.resolve())

    def test_returns_none_when_no_checkout_exists(self) -> None:
        """Production must be able to say 'the lab is absent'."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = {"Z0INT_HOME": str(_make_home(base, {}))}
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(paths, "LEGACY_EVOLUTION_LAB_ROOTS", ()):
                    self.assertIsNone(paths.evolution_lab_root())

    def test_python_falls_back_to_the_running_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = {"Z0INT_HOME": str(_make_home(base, {}))}
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(paths, "LEGACY_EVOLUTION_LAB_ROOTS", ()):
                    import sys

                    self.assertEqual(paths.evolution_lab_python(), sys.executable)


class StatusTests(unittest.TestCase):
    def test_status_reports_every_existing_checkout_and_the_ambiguity(self) -> None:
        """Three checkouts at three revisions is the finding; it must be visible."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _make_lab(base, "lab-a")
            b = _make_lab(base, "lab-b")
            with mock.patch.dict(os.environ, _isolated_env(base), clear=True):
                with mock.patch.object(
                    paths, "LEGACY_EVOLUTION_LAB_ROOTS", ((a, "legacy-a"), (b, "legacy-b"))
                ):
                    status = paths.evolution_lab_status()
            self.assertTrue(status["found"])
            self.assertTrue(status["ambiguous"])
            self.assertEqual(len(status["existing_checkouts"]), 2)

    def test_status_is_unambiguous_with_one_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            only = _make_lab(base, "only-lab")
            with mock.patch.dict(os.environ, _isolated_env(base), clear=True):
                with mock.patch.object(
                    paths, "LEGACY_EVOLUTION_LAB_ROOTS", ((only, "legacy-only"),)
                ):
                    status = paths.evolution_lab_status()
            self.assertFalse(status["ambiguous"])
            self.assertEqual(status["source"], "legacy-only")

    def test_status_lists_missing_candidates_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            missing = base / "gone"
            with mock.patch.dict(os.environ, _isolated_env(base), clear=True):
                with mock.patch.object(paths, "LEGACY_EVOLUTION_LAB_ROOTS", ((missing, "gone"),)):
                    status = paths.evolution_lab_status()
            self.assertFalse(status["found"])
            self.assertTrue(any(c["path"] == str(missing) for c in status["missing_candidates"]))


class ArtifactTests(unittest.TestCase):
    def test_artifact_is_resolved_across_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lab = _make_lab(base, "art-lab")
            target = lab / "data" / "p0"
            target.mkdir(parents=True)
            (target / "recovery_student.npz").write_bytes(b"x")
            with mock.patch.dict(os.environ, _isolated_env(base), clear=True):
                with mock.patch.object(paths, "LEGACY_EVOLUTION_LAB_ROOTS", ((lab, "test"),)):
                    found = paths.evolution_lab_artifact("data/p0/recovery_student.npz")
            self.assertEqual(found, (target / "recovery_student.npz").resolve())

    def test_missing_artifact_is_none_not_a_guess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lab = _make_lab(base, "art-lab")
            with mock.patch.dict(os.environ, _isolated_env(base), clear=True):
                with mock.patch.object(paths, "LEGACY_EVOLUTION_LAB_ROOTS", ((lab, "test"),)):
                    self.assertIsNone(paths.evolution_lab_artifact("data/p0/nope.npz"))


class BoundaryGuardTests(unittest.TestCase):
    """The actual regression: production must not name a lab or repo path."""

    # paths.py is the ONE place a legacy path may live; it is documented there.
    ALLOWED = {SRC / "paths.py"}

    def test_no_production_module_hardcodes_a_lab_or_repo_path(self) -> None:
        offenders: list[str] = []
        needles = ("/workspace/evolution-lab", "/home/kvn/tmp/evolution-lab", "/home/kvn/tmp/openjev")
        for path in sorted(SRC.rglob("*.py")):
            if path in self.ALLOWED or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.relative_to(REPO)}: {needle}")
        self.assertEqual(
            offenders,
            [],
            "production modules must resolve the lab through paths.*, not by "
            "naming one machine's checkout:\n  " + "\n  ".join(offenders),
        )

    def test_paths_module_is_the_single_shim(self) -> None:
        text = (SRC / "paths.py").read_text(encoding="utf-8")
        self.assertIn("LEGACY_EVOLUTION_LAB_ROOTS", text)
        # If the shim is ever removed, remove this test with it.
        self.assertIn("Delete once EVOLUTION_LAB_ROOT is configured everywhere", text)


if __name__ == "__main__":
    unittest.main()
