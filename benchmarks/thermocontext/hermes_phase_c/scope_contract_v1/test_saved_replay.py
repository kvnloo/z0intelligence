"""Saved artifact/reception tests using native offline preparation only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qualify


@unittest.skipUnless(os.environ.get("HERMES_SOURCE_TREE"), "native preparation needs HERMES_SOURCE_TREE")
class SavedReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="scope-contract-replay-")
        cls.saved = Path(cls.temp.name) / "saved"
        qualify.qualify(cls.saved, Path(os.environ["HERMES_SOURCE_TREE"]))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def copy(self, root):
        return Path(shutil.copytree(self.saved, Path(root) / "copy"))

    def test_saved_replay_and_create_only(self):
        self.assertEqual(qualify.replay(self.saved)["arms"], 14)
        with self.assertRaises(FileExistsError):
            qualify.qualify(self.saved, Path(os.environ["HERMES_SOURCE_TREE"]))

    def test_summary_tampering_rejected(self):
        for key, value in (("status", "ALL_MODELS_PASS"), ("arms_passed", 99),
                           ("old_files_unchanged", 999), ("provider_calls", 1),
                           ("actual_provider_cost_usd", 0.2)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                copied = self.copy(root)
                result = qualify.read(copied / "RESULT.json")
                result[key] = value
                (copied / "RESULT.json").write_text(json.dumps(result))
                with self.assertRaisesRegex(ValueError, "summary"):
                    qualify.replay(copied)
        with tempfile.TemporaryDirectory() as root:
            copied = self.copy(root)
            result = qualify.read(copied / "RESULT.json")
            result["cases"]["full-matching-positive"]["context_utf8_bytes"] = 1
            (copied / "RESULT.json").write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "summary"):
                qualify.replay(copied)

    def test_manifest_membership_and_wire_tampering_rejected(self):
        for mutation in ("missing_source", "missing_artifact", "extra_artifact", "wire"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                copied = self.copy(root)
                result = qualify.read(copied / "RESULT.json")
                if mutation == "missing_source":
                    result["source_bindings"].pop(next(iter(result["source_bindings"])))
                elif mutation == "missing_artifact":
                    relative = next(iter(result["artifact_bindings"]))
                    result["artifact_bindings"].pop(relative)
                    (copied / relative).unlink()
                elif mutation == "extra_artifact":
                    (copied / "invented.json").write_text("{}")
                else:
                    path = copied / "full-matching-positive/request.json"
                    request = qualify.read(path)
                    request["messages"][0]["content"] += " altered system"
                    path.write_text(json.dumps(request))
                    # Even an updated manifest cannot conceal a non-context wire change.
                    result["artifact_bindings"][str(path.relative_to(copied))] = qualify.digest(path)
                (copied / "RESULT.json").write_text(json.dumps(result))
                with self.assertRaises(ValueError):
                    qualify.replay(copied)

    def test_relocated_sparse_checkout_replays_without_original_paths_or_git(self):
        with tempfile.TemporaryDirectory() as root:
            checkout = Path(root) / "relocated"
            bindings = qualify.read(qualify.HERE / "baseline-bindings.json")["files"]
            bindings = set(bindings) | set(qualify.source_bindings())
            for relative in bindings:
                target = checkout / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(qualify.ROOT / relative, target)
            # Ordinary package dependencies, not a new environment or Git checkout.
            shutil.copytree(qualify.ROOT / "src", checkout / "src", dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            saved = Path(shutil.copytree(self.saved, Path(root) / "relocated-artifacts"))
            relative_script = str((qualify.HERE / "qualify.py").relative_to(qualify.ROOT))
            wrapper = '''import runpy, sys
original = sys.argv.pop(1)
def reject_original(event, args):
    if event == "open" and args and isinstance(args[0], str) and args[0].startswith(original + "/"):
        raise AssertionError("replay read original checkout: " + args[0])
sys.addaudithook(reject_original)
script = sys.argv.pop(1)
sys.argv[0] = script
runpy.run_path(script, run_name="__main__")
'''
            environment = dict(os.environ, PYTHONPATH=str(checkout / "src"), PYTHONDONTWRITEBYTECODE="1")
            environment.pop("HERMES_SOURCE_TREE", None)
            proc = subprocess.run([sys.executable, "-c", wrapper, str(qualify.ROOT),
                                   str(checkout / relative_script), "replay", "--out", str(saved)],
                                  cwd=checkout, env=environment, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('"status": "REPLAY_PASS"', proc.stdout)

    def test_common_system_change_with_consistent_manifest_and_witnesses_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            copied = self.copy(root)
            result = qualify.read(copied / "RESULT.json")
            for key in result["cases"]:
                directory = copied / key
                request = qualify.read(directory / "request.json")
                request["messages"][0]["content"] += " common altered system"
                (directory / "request.json").write_text(json.dumps(request))
                selected = qualify.read(directory / "selection.json")
                witness = qualify.read(directory / "witness.json")
                witness.update(qualify.boundary.witness(selected, request))
                (directory / "witness.json").write_text(json.dumps(witness))
                result["cases"][key]["offline_serialized_request_bytes"] = len(json.dumps(request, ensure_ascii=False).encode())
                normalized = qualify.normalized_wire(json.dumps(request).encode(), selected["context"],
                                                     (qualify.HERE / "prompt.txt").read_text())
                result["normalized_offline_wire_sha256"] = hashlib.sha256(qualify.canonical(normalized).encode()).hexdigest()
            result["artifact_bindings"] = qualify.artifact_bindings(copied, qualify.read(qualify.HERE / "fixtures.json"))
            (copied / "RESULT.json").write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "bound reference"):
                qualify.replay(copied)


if __name__ == "__main__":
    unittest.main()
