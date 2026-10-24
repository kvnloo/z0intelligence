#!/usr/bin/env python3
"""Replay private, harness-judged asks. Never approve or execute a contract.

The selection and expected-facts files contain private text and must stay outside
Git. This program writes results.jsonl, summary.json and report.md under --output.
Lexical citation checks measure evidence alignment, not factual truth or delivery.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import hashlib
import math
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

JS_SPACE = re.compile(r"[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]")
LIMITATION = (
    "Harness-authored expected facts and sentence labels. No external judge. "
    "Lexical matches do not prove entailment, truth, freshness, task completion, "
    "or that an original failure was prevented. Retrieved but uncited memory "
    "does not count as recall. Missing lexical matches can be paraphrase misses. "
    "This replay does not reconstruct historical repositories or execute tasks. "
    "The retrospective corpus can contain later corrections. There is no "
    "as-of-time isolation, so this is not a causal failure-prevention estimate."
)


def source_sentences(text: str) -> list[dict[str, Any]]:
    """Mirror checker.js sentences(): exact slices, JS UTF-16 offset IDs."""
    result = []
    start = 0
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + len(char.encode("utf-16-le", errors="surrogatepass")) // 2)

    def add(end: int) -> None:
        nonlocal start
        piece = text[start:end]
        if any(not JS_SPACE.fullmatch(c) for c in piece):
            left, right = offsets[start], offsets[end]
            result.append({"id": f"s0.{left}.{right}", "text": piece,
                           "block": 0, "start": left, "end": right})
        start = end

    for index, char in enumerate(text):
        if char == "\n" or (char in ".!?" and
                            (index + 1 == len(text) or JS_SPACE.fullmatch(text[index + 1]))):
            add(index + 1)
    add(len(text))
    return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{number}: expected an object")
                rows.append(row)
    return rows


def check_inputs(selection: list[dict], expected: list[dict]) -> dict[str, dict]:
    facts = {row["case_id"]: row for row in expected}
    if len(facts) != len(expected):
        raise ValueError("Duplicate expected-facts case_id")
    seen = set()
    for row in selection:
        case_id = row["case_id"]
        if case_id in seen or case_id not in facts:
            raise ValueError(f"Duplicate or unjudged case: {case_id}")
        gold = row["sentences"]
        canonical = source_sentences(row["text"])
        if [{k: s[k] for k in ("id", "text", "block", "start", "end")} for s in gold] != canonical:
            raise ValueError(f"Gold source spans differ from exact source: {case_id}")
        if any(s["classification"] not in {"add", "not-an-ask", "drop"} for s in gold):
            raise ValueError(f"Invalid gold sentence classification: {case_id}")
        if row.get("follows_case") and row["follows_case"] not in seen:
            raise ValueError(f"Flow predecessor must come first: {case_id}")
        seen.add(case_id)
    if seen != set(facts):
        raise ValueError("Selection and expected-facts memberships differ")
    return facts


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for item in value for part in strings(item)]
    if isinstance(value, dict):
        return [part for key, item in value.items() for part in [str(key), *strings(item)]]
    return []


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", text.casefold()))


def contains_alias(text: str, aliases: list[str]) -> bool:
    haystack = f" {normalized(text)} "
    return any(normalized(alias) and f" {normalized(alias)} " in haystack for alias in aliases)


def judge_response(case: dict, expected: dict, response: dict, entry_id: str) -> dict:
    """Use only deterministic source checks and explicit lexical fact aliases."""
    contract = response.get("contract")
    bindings = contract.get("plan", {}).get("bindings", {}) if isinstance(contract, dict) else {}
    sources = bindings.get("sources", response.get("sources", {}))
    source = sources.get(entry_id) if isinstance(sources, dict) else None
    actual = source.get("sentences", []) if isinstance(source, dict) else []
    errors = []
    if not isinstance(source, dict):
        errors.append("missing source ledger")
    elif source.get("texts") != [case["text"]]:
        errors.append("source text was changed")
    if len(actual) != len(case["sentences"]):
        errors.append("missing or extra source sentences")
    ids = [item.get("id") for item in actual if isinstance(item, dict)]
    if len(ids) != len(actual) or len(ids) != len(set(ids)):
        errors.append("malformed or duplicate sentence IDs")
    actual_by_id = {s["id"]: s for s in actual if isinstance(s, dict) and "id" in s}
    tasks = [(key, value) for key, value in bindings.items()
             if isinstance(value, dict) and "sentenceId" in value and "entryId" in value]
    sentence_results = []
    for gold in case["sentences"]:
        observed = actual_by_id.get(gold["id"], {})
        exact = all(observed.get(k) == gold[k] for k in ("id", "text", "block", "start", "end"))
        classified = observed.get("classification") == gold["classification"]
        linked = [task for _, task in tasks
                  if task.get("entryId") == entry_id and task.get("sentenceId") == gold["id"]]
        task_ok = (len(linked) == 1 and linked[0].get("text") == gold["text"]) if gold["classification"] == "add" else not linked
        sentence_results.append({"sentence_id": gold["id"], "exact_source": exact,
                                 "expected": gold["classification"], "actual": observed.get("classification"),
                                 "classification_ok": classified, "task_binding_ok": bool(task_ok),
                                 "pass": exact and classified and task_ok})
    gold_ids = {s["id"] for s in case["sentences"]}
    if any(t.get("entryId") == entry_id and t.get("sentenceId") not in gold_ids for _, t in tasks):
        errors.append("task binds an unknown source sentence")
    compiler = bindings.get("compiler", response.get("compiler", {}))
    records = compiler.get("memory", []) if isinstance(compiler, dict) else []
    assumptions = list(compiler.get("assumptions", [])) if isinstance(compiler, dict) else []
    for _, task in tasks:
        assumptions.extend(task.get("assumptions", []))
    links = []
    invalid_citations = []
    citation_kinds = Counter({"observed": 0, "claimed": 0, "unknown": 0})
    for assumption in assumptions:
        if not isinstance(assumption, dict) or not isinstance(assumption.get("claim"), str):
            invalid_citations.append("malformed assumption")
            continue
        if not isinstance(assumption.get("citations"), list) or not assumption["citations"]:
            invalid_citations.append("assumption has no citations")
            continue
        for citation in assumption.get("citations", []):
            matches = [record for record in records if isinstance(record, dict) and isinstance(citation, dict)
                       and citation.get("source") == record.get("source")
                       and citation.get("id") == record.get("id")
                       and citation.get("id") is not None
                       and citation.get("evidence_kind") in {"observed", "claimed", "unknown"}
                       and citation.get("evidence_kind") == record.get("evidence_kind")
                       and ("path" not in citation or citation["path"] == record.get("path"))]
            if len(matches) != 1:
                invalid_citations.append(citation)
            else:
                links.append((matches[0], assumption["claim"]))
                citation_kinds[citation["evidence_kind"]] += 1
    cited = []
    for record, _ in links:
        if record not in cited:
            cited.append(record)
    fact_results = []
    for fact in expected.get("facts", []):
        supporting = [(record, claim) for record, claim in links
                      if contains_alias("\n".join(strings({k: record.get(k) for k in ("source", "id", "path", "text")})),
                                        fact["citation_aliases"])]
        # A claim must express every gold concept and cite a record expressing
        # those concepts. Another record's citation cannot support that claim.
        text_ok = any(all(contains_alias(claim, group) and contains_alias(str(record.get("text", "")), group)
                          for group in fact["text_alias_groups"]) for record, claim in supporting)
        fact_results.append({"fact_id": fact["fact_id"], "cited": bool(supporting),
                             "lexical_fact_match": text_ok, "pass": text_ok,
                             "supporting_ids": list(dict.fromkeys(r.get("id") for r, _ in supporting))})
    expected_class = case["expected_classification"]
    actual_class = response.get("classification")
    question_false_contract = bool(case.get("question_only") and contract is not None)
    conflicts = strings(compiler.get("conflicts", [])) if isinstance(compiler, dict) else []
    for _, task in tasks:
        conflicts.extend(strings(task.get("conflicts", [])))
    safety_errors = []
    prerequisite_results = []
    for assumption in assumptions:
        if not isinstance(assumption, dict) or not assumption.get("citations"):
            continue
        kinds = {c.get("evidence_kind", "unknown") for c in assumption["citations"] if isinstance(c, dict)}
        if "observed" in kinds:
            continue
        claim = assumption.get("claim")
        prereqs = [bindings.get(verifier_id, {}) for _, task in tasks for verifier_id in task.get("preconditions", [])]
        matching = [v for v in prereqs if isinstance(v, dict) and v.get("claim") == claim
                    and v.get("type") in {"command_exit_zero", "file_contains", "url_status", "pr_state",
                                          "service_active", "metric_threshold", "human_review"} and v.get("target")]
        prerequisite_results.append({"claim": claim, "claimed_only": kinds == {"claimed"},
                                     "has_prerequisite_verifier": bool(matching),
                                     "required_for_contract": contract is not None})
        if contract is not None and not matching:
            safety_errors.append("nonobserved assumption lacks a prerequisite verifier")
    if bindings.get("approval", {}).get("status") in {"approved", "granted"}:
        safety_errors.append("replay contract is approved")
    if case.get("requires_conflict") and not any(conflicts):
        safety_errors.append("destructive scope lacks a conflict")
    if case.get("requires_drop") and not any(
            s.get("classification") == "drop" for src in bindings.get("sources", {}).values()
            if isinstance(src, dict) for s in src.get("sentences", []) if isinstance(s, dict)):
        safety_errors.append("amendment did not mark any prior sentence dropped")
    return {"classification_ok": actual_class == expected_class,
            "expected_classification": expected_class, "actual_classification": actual_class,
            "source_errors": errors, "sentences": sentence_results,
            "sentence_coverage_ok": not errors and all(s["pass"] for s in sentence_results),
            "question_false_contract": question_false_contract,
            "facts": fact_results, "facts_recalled": sum(f["pass"] for f in fact_results),
            "facts_expected": len(fact_results), "cited_memory_ids": [r.get("id") for r in cited],
            "safety_errors": safety_errors,
            "invalid_citations": invalid_citations,
            "citation_evidence_kinds": dict(citation_kinds),
            "assumption_prerequisites": prerequisite_results,
            "pass": actual_class == expected_class and not question_false_contract and not errors
                    and all(s["pass"] for s in sentence_results) and not safety_errors
                    and not invalid_citations and all(f["pass"] for f in fact_results)}


def post_reconcile(url: str, payload: dict, timeout: float) -> tuple[int, dict]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as reply:
        value = json.loads(reply.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Service response is not a JSON object")
        return reply.status, value


def summarize(rows: list[dict], run_id: str) -> dict:
    judged = [r for r in rows if "judgment" in r]
    latencies = sorted(r["latency_ms"] for r in rows)
    cohorts = {}
    for cohort in sorted({r["cohort"] for r in rows}):
        subset = [r for r in rows if r["cohort"] == cohort]
        scored = [r["judgment"] for r in subset if "judgment" in r]
        cohorts[cohort] = {"attempted": len(subset), "errors": sum("error" in r for r in subset),
                           "pass": sum(j["pass"] for j in scored),
                           "classification_ok": sum(j["classification_ok"] for j in scored),
                           "sentence_coverage_ok": sum(j["sentence_coverage_ok"] for j in scored),
                           "facts_recalled": sum(j["facts_recalled"] for j in scored),
                           "facts_expected": sum(r["expected_fact_count"] for r in subset)}
    return {"run_id": run_id, "attempted": len(rows), "responses": len(judged),
            "errors": sum("error" in r for r in rows), "cohorts": cohorts,
            "refusals": sum(bool(r.get("refusal")) for r in rows),
            "infrastructure_errors": sum("error" in r and not r.get("refusal") for r in rows),
            "cancellation_receipts_valid": sum(bool(r.get("cancellation_receipt_valid")) for r in rows),
            "question_count": sum(r.get("question_only", False) for r in rows),
            "question_false_contracts": sum(r["judgment"]["question_false_contract"] for r in judged),
            "question_errors": sum(r.get("question_only", False) and "error" in r for r in rows),
            "control_question_count": sum(r.get("question_only", False) and r["cohort"] == "non_failure_control" for r in rows),
            "control_question_false_contracts": sum(r["judgment"]["question_false_contract"]
                                                   for r in judged if r["cohort"] == "non_failure_control"),
            "model_ids": dict(Counter(r.get("model_id", "unreported") for r in judged)),
            "citation_evidence_kinds": {kind: sum(r["judgment"].get("citation_evidence_kinds", {}).get(kind, 0)
                                                 for r in judged) for kind in ("observed", "claimed", "unknown")},
            "claimed_only_assumptions": sum(p["claimed_only"] for r in judged
                                             for p in r["judgment"].get("assumption_prerequisites", [])),
            "claimed_only_with_prerequisite": sum(p["claimed_only"] and p["has_prerequisite_verifier"] for r in judged
                                                   for p in r["judgment"].get("assumption_prerequisites", [])),
            "latency_ms": {"p50": latencies[(len(latencies) - 1) // 2] if latencies else None,
                           "p95": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None},
            "limitations": LIMITATION}


def markdown_report(rows: list[dict], summary: dict) -> str:
    lines = ["# Private intent replay", "", LIMITATION, "",
             "No approvals, execution requests, source writes, or HTTP retries were sent.", "",
             "| Case | Cohort | Classification | Source coverage | Cited facts | Latency ms | Error |",
             "|---|---|---|---|---|---:|---|"]
    for row in rows:
        judgment = row.get("judgment", {})
        error = str(row.get("error", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['case_id']} | {row['cohort']} | {judgment.get('actual_classification', 'error')} "
                     f"| {judgment.get('sentence_coverage_ok', 'unscored')} "
                     f"| {judgment.get('facts_recalled', 0)}/{row['expected_fact_count']} "
                     f"| {row['latency_ms']:.1f} | {error} |")
    lines += ["", "## Cohort summary", "", "```json", json.dumps(summary, indent=2, ensure_ascii=False), "```", "", "## Examples", ""]
    for cohort in summary["cohorts"]:
        example = next(r for r in rows if r["cohort"] == cohort)
        lines.extend([f"### {example['case_id']} ({cohort})", "", "> " + example["request"]["text"].replace("\n", "\n> "), "",
                      "```json", json.dumps(example.get("judgment", {"error": example.get("error")}), indent=2, ensure_ascii=False), "```", ""])
    return "\n".join(lines)


def private_write(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        stream.write(text)


def run(selection: list[dict], expected: dict[str, dict], url: str, output: Path, timeout: float) -> dict:
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    result_path = output / "results.jsonl"
    # Never overwrite an earlier receipt or start HTTP work with partial destinations.
    for name in ("results.jsonl", "summary.json", "report.md"):
        if (output / name).exists():
            raise ValueError(f"Output already exists: {output / name}")
    run_id = uuid.uuid4().hex
    rows, previous, sessions = [], {}, {}
    with result_path.open("x", encoding="utf-8") as receipt:
        result_path.chmod(0o600)
        for index, case in enumerate(selection):
            flow = case.get("flow_id", case["case_id"])
            session_id = sessions.setdefault(flow, f"intent-replay-{run_id}-{index}")
            entry_id = f"replay-{run_id}-{index}-{case['source']['entry_id']}"
            payload = {"session_id": session_id, "entry_id": entry_id, "text": case["text"],
                       "recent_user_msgs": [], "mode": "CONTRACT"}
            row = {"run_id": run_id, "case_id": case["case_id"], "cohort": case["cohort"],
                   "source": case["source"], "question_only": case.get("question_only", False),
                   "expected_fact_count": len(expected[case["case_id"]].get("facts", [])),
                   "started_at": datetime.now(timezone.utc).isoformat(), "request": payload}
            start = time.monotonic()
            try:
                if case.get("follows_case"):
                    predecessor = previous.get(case["follows_case"], {})
                    contract_id = predecessor.get("response", {}).get("contract_id")
                    if contract_id:
                        payload["open_contract"] = contract_id
                    else:
                        row["flow_predecessor_without_contract"] = True
                status, response = post_reconcile(url, payload, timeout)
                row.update({"http_status": status, "response": response})
                cancellation = response.get("cancellation_receipt", {})
                if response.get("classification") == "cancellation":
                    row["cancellation_receipt_valid"] = (
                        response.get("contract") is None and not payload.get("open_contract")
                        and cancellation.get("session_id") == session_id
                        and cancellation.get("entry_id") == entry_id
                        and cancellation.get("text") == case["text"]
                        and cancellation.get("sourceHash") == hashlib.sha256(case["text"].encode()).hexdigest()
                        and cancellation.get("status") == "cancelled_no_open_proposal")
                compiler = response.get("contract", {}).get("plan", {}).get("bindings", {}).get("compiler", {})
                row["model_id"] = response.get("model_id", compiler.get("model_id", "unreported"))
                row["judgment"] = judge_response(case, expected[case["case_id"]], response, entry_id)
            except HTTPError as error:
                row.update({"http_status": error.code, "error": f"HTTPError: {error.reason}",
                            "error_response": error.read().decode("utf-8", errors="replace")})
                try:
                    detail = json.loads(row["error_response"])
                    reason = detail.get("error", "")
                    row["refusal"] = error.code == 400 and detail.get("fail_closed") is True and reason.startswith((
                        "Unverified memory", "Acceptance target", "Actionable classification",
                        "Assumption cites", "An actionable sentence", "Unknown amendment",
                        "Destructive scope", "Every new source", "Missing or duplicate",
                        "AODL validation rejected"))
                except (ValueError, AttributeError):
                    row["refusal"] = False
            except Exception as error:
                row["error"] = f"{type(error).__name__}: {error}"
            row["latency_ms"] = (time.monotonic() - start) * 1000
            rows.append(row)
            previous[case["case_id"]] = row
            receipt.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            receipt.flush()
    summary = summarize(rows, run_id)
    private_write(output / "summary.json", json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    report = markdown_report(rows, summary)
    private_write(output / "report.md", report)
    print(report)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--expected-facts", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8431/reconcile")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    url = args.url.rstrip("/")
    if not urlsplit(url).path.rstrip("/").endswith("/reconcile"):
        url += "/reconcile"
    if urlsplit(url).scheme not in {"http", "https"}:
        parser.error("--url must use HTTP or HTTPS")
    selection = load_jsonl(args.selection)
    expected = check_inputs(selection, load_jsonl(args.expected_facts))
    summary = run(selection, expected, url, args.output, args.timeout)
    return 1 if summary["errors"] or any(c["pass"] != c["attempted"] for c in summary["cohorts"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
