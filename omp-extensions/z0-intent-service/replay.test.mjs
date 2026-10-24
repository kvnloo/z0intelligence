import test from "node:test";
import assert from "node:assert/strict";
import { calibrate, metrics, replayCases, splitFor } from "../../scripts/replay-intent-triage.mjs";

const row = (entry_id, text, session = "session-a") => ({ harness: "omp", session, entry_id, text });

test("stable thirty-percent hash split groups sessions independent of text and labels", () => {
  const a = row("one", "first"), b = row("two", "other");
  assert.equal(splitFor(a), splitFor(b));
  assert.equal(splitFor(a), splitFor({ ...a, text: "changed", label: "failure" }));
  const partitions = Array.from({ length: 1000 }, (_, i) => splitFor(row("x", "x", `s-${i}`)));
  assert.ok(partitions.filter(split => split === "holdout").length > 200);
  assert.ok(partitions.filter(split => split === "holdout").length < 400);
});

test("replay includes failure asks, reviewed controls and unreviewed population without false negatives", () => {
  const messages = [row("ask", "Fix typo"), row("correction", "Not what I asked"), row("control", "hello"), row("unknown", "unreviewed")];
  const labels = [{ ...messages[1], outcome: "scope-miss", evidence: [{ ...messages[0], kind: "ask", quote: "Fix typo" }] }, { ...messages[2], outcome: "not-a-miss" }];
  const cases = replayCases(labels, messages);
  assert.equal(cases.find(value => value.entry_id === "ask").role, "original-ask");
  assert.equal(cases.find(value => value.entry_id === "ask").label, "failure");
  assert.equal(cases.find(value => value.entry_id === "unknown").label, "unreviewed");
  assert.equal(cases.find(value => value.entry_id === "control").label, "control");
});

test("confusion reports actual reviewed binary labels against all three routed tiers", () => {
  const report = metrics([
    { ...row("a", "a"), label: "failure", role: "original-ask", tier: "FAST", features: { chars: 1 }, latency_ms: 1 },
    { ...row("b", "b"), label: "failure", tier: "CHECK", latency_ms: 2 },
    { ...row("c", "c"), label: "control", tier: "CONTRACT", latency_ms: 3 },
    { ...row("d", "d"), label: "unreviewed", tier: "FAST", latency_ms: 4 },
  ]);
  assert.equal(report.failure_recall_check_contract, 0.5);
  assert.equal(report.fast_share, 0.5);
  assert.equal(report.reviewed, 3);
  assert.deepEqual(report.confusion.failure, { FAST: 1, CHECK: 1, CONTRACT: 0 });
  assert.equal(report.worst_misses.length, 1);
  assert.equal(report.original_ask_failure_recall, 0);
  assert.equal(report.triage_p95_ms, 4);
});

test("calibration consumes training only and never tunes on holdout", () => {
  const train = [
    { ...row("one", "Fix " + "x".repeat(100)), split: "train", label: "failure", role: "original-ask" },
    { ...row("two", "hello"), split: "train", label: "control" },
  ];
  const first = calibrate(train, []);
  const second = calibrate([...train, { ...row("three", "What is a closure?"), split: "holdout", label: "failure" }], []);
  assert.deepEqual(first.selected, second.selected);
  assert.equal(first.training_only, true);
  assert.equal(first.target_met, true);
  assert.throws(() => calibrate([], []));
});
