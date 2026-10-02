"""Create-only offline qualification and portable saved-artifact replay.

No execute/live subcommand exists. Native checks collect and compose context but
never send a request. The independently frozen fixture oracle stays host-side.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PHASE_C = HERE.parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(HERE), str(PHASE_C), str(PHASE_C / "causal_context"),
               str(PHASE_C / "verified_context_cohort")]
import discriminator
import selection_boundary as boundary
import z0int.context_resolve as resolver
from run_cohort import normalized_wire
from contract import canonical, report, validate_report
from check_fixture import grade

FREEZE_SHA256 = "354533b1812105db27e7c5274a38c359d41201b85e8f2ba0752491615ad8400e"
REFERENCE = PHASE_C / "results/verified-context-cohort-20261002/runs/full-complete/native-request.bin"
NATIVE_FILES = {"agent/turn_context.py": "b838c191eb44e8fd99a768bb782438b2b8751c775df67599dbc4b5892ea344ce",
                "tools/hook_output_spill.py": "3e2ab6e5f088920065c22549f34d072526aa793246096cef8656d8810f6012a0"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def validate_freeze():
    if digest(HERE / "freeze.json") != FREEZE_SHA256:
        raise ValueError("preimplementation freeze changed")
    freeze = read(HERE / "freeze.json")
    for relative, expected in freeze["frozen_inputs"].items():
        if digest(HERE / relative) != expected:
            raise ValueError("frozen input changed: " + relative)
    bindings = read(HERE / "baseline-bindings.json")["files"]
    for relative, expected in bindings.items():
        if digest(ROOT / relative) != expected:
            raise ValueError("predecessor artifact changed: " + relative)
    if digest(Path(resolver.__file__)) != bindings["src/z0int/context_resolve.py"]:
        raise ValueError("actual imported ContextPacket source changed")
    return len(bindings)


def render(case, arm):
    if arm not in ("full", "minimal"):
        raise ValueError("unknown arm")
    packet = discriminator.packet_from(case["packet"])
    chosen = case["legal_optional_ids"] if arm == "full" else []
    return boundary.materialize(packet, chosen, pinned_ids=case["pinned_ids"],
        legal_optional_ids=case["legal_optional_ids"],
        expected_pool_digest=boundary.freeze_digest(packet), budget=20000,
        count_tokens=lambda text: len(text.encode()),
        tokenizer_identity="fixture_utf8_bytes_not_provider_tokens")


def validate_preservation(case, selection):
    visible = json.loads(selection["context"])
    if set(visible) != {"evidence", "contradictions", "unresolved_gaps"}:
        raise ValueError("non-evidence model-visible fields")
    expected = {ref["source_id"]: ref for ref in case["packet"]["evidence"]}
    selected = [ref["source_id"] for ref in visible["evidence"]]
    if not set(case["pinned_ids"]) <= set(selected):
        raise ValueError("required raw evidence omitted")
    if any(ref != expected.get(ref["source_id"]) for ref in visible["evidence"]):
        raise ValueError("raw evidence or provenance changed")
    for key in ("contradictions", "unresolved_gaps"):
        if visible[key] != case["packet"][key]:
            raise ValueError("raw " + key + " changed")


def mutant_failures(oracle, fixtures):
    failures = {name: [] for name in oracle["mutants_required_to_fail"]}
    for case in fixtures["cases"]:
        key = case["case_id"]
        expected = oracle["expected"][key]
        visible = [ref["source_id"] for ref in case["packet"]["evidence"]]
        for name in failures:
            answer = copy.deepcopy(expected)
            if name == "always_false_scope":
                answer["scope"] = {"status": "declared", "value": False,
                                   "citations": expected["scope"]["citations"]}
            elif name == "always_unknown_scope":
                answer["scope"] = {"status": "unknown", "value": None, "citations": []}
            elif name == "copy_other_subject_positive" and key == "wrong-subject-positive":
                answer["scope"] = {"status": "declared", "value": True, "citations": ["record-2"]}
            elif name == "couple_scope_to_source_missingness" and key == "missing-source-known-negative":
                answer["scope"] = {"status": "unknown", "value": None, "citations": []}
            elif name == "select_one_conflicting_scope_declaration" and key == "conflicting-scope":
                answer["scope"] = {"status": "declared", "value": True, "citations": ["record-2"]}
            if not grade(answer, expected, visible)["passed"]:
                failures[name].append(key)
    if not all(failures.values()):
        raise ValueError("an invalid constant/coupled/scope-transfer reporter survived")
    return failures


def source_bindings():
    paths = [path for path in HERE.iterdir() if path.is_file()]
    paths += [PHASE_C / "verified_context_cohort/run_cohort.py", REFERENCE,
              PHASE_C / "causal_context/fixtures/sources.json"]
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths)}


def artifact_bindings(out, fixtures):
    names = ("selection.json", "declarations.json", "check.json", "request.json", "witness.json", "context.txt")
    expected = {arm + "-" + case["case_id"] + "/" + name
                for case in fixtures["cases"] for arm in ("full", "minimal") for name in names}
    actual = {str(path.relative_to(out)) for path in out.rglob("*")
              if path.is_file() and path != out / "RESULT.json"}
    if actual != expected:
        raise ValueError("saved artifact membership changed")
    return {relative: digest(out / relative) for relative in sorted(expected)}


def summary(rows, mutants, sources, artifacts, template, old_count):
    pins = read(PHASE_C / "causal_context/fixtures/sources.json")
    return {"schema": "scope_contract.offline_qualification.v1", "study_id": "state-packet-scope-contract",
            "status": "OFFLINE_CONTRACT_QUALIFIED", "provider_calls": 0, "actual_provider_tokens": None,
            "actual_provider_cost_usd": 0, "old_files_unchanged": old_count, "arms_passed": len(rows),
            "cases": rows, "mutants_rejected": mutants,
            "source_bindings": sources, "artifact_bindings": artifacts,
            "preimplementation_freeze_sha256": FREEZE_SHA256,
            "normalized_offline_wire_sha256": hashlib.sha256(canonical(template).encode()).hexdigest(),
            "native_hermes_revision": pins["hermes_revision"],
            "native_composition_sha256": pins["hermes_composition_source_sha256"],
            "native_source_files": NATIVE_FILES,
            "prospective_live": "NOT_EXECUTED; finalize execution freeze and root bounded execution decision",
            "scope": "Synthetic exact source reporting and byte preservation; no real performance or model claim"}


def qualify(out, hermes_repo):
    old_count = validate_freeze()
    pins = read(PHASE_C / "causal_context/fixtures/sources.json")
    revision = subprocess.check_output(["git", "-C", str(hermes_repo), "rev-parse", "HEAD"], text=True).strip()
    composition = hermes_repo / pins["hermes_source_file"]
    if revision != pins["hermes_revision"] or digest(composition) != pins["hermes_composition_source_sha256"]:
        raise ValueError("native Hermes composition pin changed")
    if any(digest(hermes_repo / path) != expected for path, expected in NATIVE_FILES.items()):
        raise ValueError("native Hermes hook source pin changed")
    fixtures, oracle = read(HERE / "fixtures.json"), read(HERE / "oracle.json")
    prompt = (HERE / "prompt.txt").read_text()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(hermes_repo))
    from agent.turn_context import compose_user_api_content, substitute_api_content
    rows, template = {}, None
    for case in fixtures["cases"]:
        pair = []
        for arm in ("full", "minimal"):
            key = arm + "-" + case["case_id"]
            directory = out / key
            directory.mkdir()
            selected = render(case, arm)
            validate_preservation(case, selected)
            extracted = report(selected["packet"], fixtures["target"])
            validate_report(extracted["answer"], selected["packet"], fixtures["target"])
            checked = grade(extracted["answer"], oracle["expected"][case["case_id"]], selected["selected_ids"])
            if not checked["passed"]:
                raise ValueError("independent fixture mismatch: " + key)
            with patch.object(discriminator, "PROMPT", prompt):
                context, admission = discriminator.native_hook_admission(selected["context"], hermes_repo, 20000)
            if context != selected["context"]:
                raise ValueError("native hook truncated context")
            request = read(REFERENCE)
            user = {"role": "user", "content": prompt,
                    "api_content": compose_user_api_content(prompt, "", context)}
            substitute_api_content(user)
            request["messages"][1] = user
            raw = json.dumps(request, ensure_ascii=False).encode()
            normalized = normalized_wire(raw, context, prompt)
            if template is not None and normalized != template:
                raise ValueError("non-context native wire changed")
            template = normalized
            witness = boundary.witness(selected, request)
            if not witness["request_contains_exact_context"] or len(raw) > 20000:
                raise ValueError("offline wire occurrence or byte cap failed")
            witness.update(request_origin="offline native hook/composition with prior observed system; not transport",
                           transmitted=False, native_admission=admission)
            for name, value in (("selection.json", selected), ("declarations.json", extracted),
                                ("check.json", checked), ("request.json", request), ("witness.json", witness)):
                save(directory / name, value)
            (directory / "context.txt").write_text(context)
            rows[key] = {"passed": True, "context_utf8_bytes": len(context.encode()),
                         "offline_serialized_request_bytes": len(raw), "within_byte_cap": True}
            pair.append(extracted["answer"])
        if pair[0] != pair[1]:
            raise ValueError("membership change altered declaration report")
    result = summary(rows, mutant_failures(oracle, fixtures), source_bindings(),
                     artifact_bindings(out, fixtures), template, old_count)
    save(out / "RESULT.json", result)
    return result


def replay(out):
    old_count = validate_freeze()
    result = read(out / "RESULT.json")
    fixtures, oracle = read(HERE / "fixtures.json"), read(HERE / "oracle.json")
    sources, artifacts = source_bindings(), artifact_bindings(out, fixtures)
    if sources != result["source_bindings"] or artifacts != result["artifact_bindings"]:
        raise ValueError("saved source/artifact bindings changed")
    prompt = (HERE / "prompt.txt").read_text()
    rows, template = {}, None
    for case in fixtures["cases"]:
        for arm in ("full", "minimal"):
            key = arm + "-" + case["case_id"]
            directory = out / key
            selection = read(directory / "selection.json")
            if selection != render(case, arm):
                raise ValueError("saved renderer result changed")
            validate_preservation(case, selection)
            extracted = report(selection["packet"], fixtures["target"])
            if extracted != read(directory / "declarations.json"):
                raise ValueError("saved declaration result changed")
            checked = grade(extracted["answer"], oracle["expected"][case["case_id"]], selection["selected_ids"])
            if not checked["passed"] or checked != read(directory / "check.json"):
                raise ValueError("independent oracle replay failed")
            if (directory / "context.txt").read_text() != selection["context"]:
                raise ValueError("saved context text changed")
            request = read(directory / "request.json")
            reference = read(REFERENCE)
            reference["messages"][1] = {"role": "user", "content": prompt + "\n\n" + selection["context"]}
            if canonical(request) != canonical(reference):
                raise ValueError("saved request differs from bound reference outside prompt/context")
            raw = json.dumps(request, ensure_ascii=False).encode()
            normalized = normalized_wire(raw, selection["context"], prompt)
            if template is not None and normalized != template:
                raise ValueError("saved native composition changed outside context")
            template = normalized
            witness = boundary.witness(selection, request)
            witness.update(request_origin="offline native hook/composition with prior observed system; not transport",
                           transmitted=False, native_admission={"max_chars": 20000,
                           "exact_context_preserved": True, "spill_notice_present": False,
                           "input_context_chars": len(selection["context"]),
                           "scope": "real native collection/spill; controlled hook return; no transport"})
            if witness != read(directory / "witness.json") or len(raw) > 20000:
                raise ValueError("saved native witness or byte cap changed")
            rows[key] = {"passed": True, "context_utf8_bytes": len(selection["context"].encode()),
                         "offline_serialized_request_bytes": len(raw), "within_byte_cap": True}
    expected = summary(rows, mutant_failures(oracle, fixtures), sources, artifacts, template, old_count)
    if canonical(result) != canonical(expected):
        raise ValueError("saved receipt summary differs from replay")
    return {"status": "REPLAY_PASS", "arms": len(rows), "old_files_unchanged": old_count,
            "scope": "Portable relative-path replay; native source need not be installed; no provider calls"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualify", "replay"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--hermes-repo", type=Path)
    args = parser.parse_args()
    if args.mode == "qualify" and args.hermes_repo is None:
        parser.error("qualify requires --hermes-repo for offline native composition")
    value = qualify(args.out, args.hermes_repo) if args.mode == "qualify" else replay(args.out)
    print(json.dumps({key: value[key] for key in ("status", "old_files_unchanged")}, sort_keys=True))
