import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { appendReceipt, DEFAULT_THRESHOLDS, projectsFromPacket, sessionDirectory, sourceHash, stripOverride, triage } from "./triage.js";

test("overrides preserve raw hash and strip only the leading agent prefix", () => {
  const raw = "  /aodl  push prod\n";
  const result = triage(raw);
  assert.equal(result.tier, "CONTRACT");
  assert.equal(result.override, "/aodl");
  assert.equal(result.text, "push prod\n");
  assert.equal(result.sourceHash, sourceHash(raw));
  assert.notEqual(result.sourceHash, sourceHash(result.text));
  assert.equal(triage("quick: deploy services on phones", { openContract: true }).tier, "FAST");
  assert.equal(stripOverride("mention quick: in a paragraph").override, null);
  assert.equal(stripOverride("/aodlish x").override, null);
});

test("bounded simple inputs are FAST, uncertain input escalates", () => {
  for (const text of ["What is a closure?", "Fix the typo", "thanks"]) assert.equal(triage(text).tier, "FAST", text);
  for (const text of ["continue", "yes", "that one", "", "hmm"]) assert.equal(triage(text).tier, "CHECK", text);
  assert.equal(triage("What is a closure?", { packetAvailable: false }).tier, "CHECK");
  assert.equal(triage("What is this?", { hasImages: true }).tier, "CHECK");
});

test("prior references, risk, scope, corrections and length are explicit", () => {
  for (const text of ["again", "already", "like before", "we decided", "last time", "ln"]) assert.ok(triage(text).features.prior_references.length, text);
  for (const text of ["deploy live", "merge it", "delete this", "push it", "update phones", "restart services", "edit configs", "replace keys"]) assert.equal(triage(text).tier, "CONTRACT", text);
  for (const text of ["you ignored the request", "not what I asked", "why did you guess", "still broken"]) assert.equal(triage(text).tier, "CONTRACT", text);
  assert.equal(triage("Implement workers in parallel").tier, "CONTRACT");
  assert.equal(triage("Change both repos").tier, "CONTRACT");
  assert.equal(triage("Change Hermes and OMP").features.multi_system, true);
  assert.equal(triage("a".repeat(1100)).tier, "CONTRACT");
  assert.ok(triage("Read this.\nFix that.\nBuild it.\nShip it.").features.sentence_count >= 4);
  assert.ok(triage("Read this. Fix that. Build it.").features.imperative_count >= 3);
  assert.equal(triage("What is a closure?", { openContract: true }).tier, "CONTRACT");
});

test("destructive scope always requires a contract before any model call", () => {
  const fixtures = [
    "nuke", "wipe", "delete", "reset", "drop", "force", "rm",
    "all repos", "all commits", "all repositories", "all repos/commits",
    "NUKE", "rm -rf .", "git reset --hard", "git push --force",
    "nuking", "wiping", "deleted", "resetting", "dropped", "forcing",
    "What does reset mean?", "Why drop?", "Can you explain delete?",
    "Show all commits?", "Read all repos", "Read the reset instructions",
    "I just want to vent: nuke it all, this is exhausting",
    "dont actually delete anything btw",
    "don't actually delete anything btw",
  ];
  for (const text of fixtures) {
    for (const prefix of ["", "quick: ", "/aodl "]) {
      const raw = prefix + text;
      const result = triage(raw);
      assert.equal(result.tier, "CONTRACT", raw);
      assert.equal(result.features.destructive_scope, true, raw);
      assert.ok(result.rule_ids.includes("destructive-scope"), raw);
      assert.ok(!result.rule_ids.includes("override-quick"), raw);
      assert.equal(result.override, prefix ? prefix.trim() : null, raw);
      assert.equal(result.sourceHash, sourceHash(raw), raw);
      assert.equal(result.slm_used, false, raw);
    }
  }
  assert.equal(triage("quick: reset", { thresholds: { ...DEFAULT_THRESHOLDS, contractScore: 100 } }).tier, "CONTRACT");
});

test("destructive scope keeps unrelated routing and word boundaries unchanged", () => {
  for (const text of ["What is a preset?", "What is a droplet?", "What is a forceful argument?", "What is an arm?", "What is an alkali?"]) {
    const result = triage(text);
    assert.equal(result.tier, "FAST", text);
    assert.equal(result.features.destructive_scope, false, text);
    assert.ok(!result.rule_ids.includes("destructive-scope"), text);
  }
  assert.equal(triage("continue").tier, "CHECK");
  assert.equal(triage("What are multiple repos?").tier, "CHECK");
  assert.equal(triage("quick: deploy services on phones", { openContract: true }).tier, "FAST");
});

test("packet headings never become a guessed project registry", () => {
  const names = projectsFromPacket("## In-flight PRs\n# Heading\nhttps://github.com/owner/actual-repo/pull/1\n`/workspace/products/Twilight/README.md`\n");
  assert.deepEqual(names, ["actual-repo", "twilight"]);
  assert.equal(triage("Read actual-repo", { projects: names }).tier, "CHECK");
  assert.equal(triage("Change actual-repo and Twilight", { projects: names }).tier, "CONTRACT");
  assert.deepEqual(triage("Read actual-repository", { projects: names }).features.named_projects, []);
});

test("every receipt is an exact durable append and lock contention fails closed", async () => {
  const home = await mkdtemp(join(tmpdir(), "intent-triage-"));
  try {
    const result = triage("quick: hi\n");
    await appendReceipt("session-1", "entry-1", result, { home, now: () => "2026-10-24T00:00:00.000Z" });
    await appendReceipt("session-1", "entry-2", triage("again"), { home });
    const rows = (await readFile(join(home, "intent/session-1/triage.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
    assert.equal(rows.length, 2);
    assert.equal(rows[0].sourceHash, sourceHash("quick: hi\n"));
    assert.equal(rows[0].override, "quick:");
    assert.equal(rows[0].slm_used, false);
    assert.equal(rows[0].ts, "2026-10-24T00:00:00.000Z");
    await mkdir(join(home, "intent/session-1/triage.lock"));
    await assert.rejects(appendReceipt("session-1", "entry-3", result, { home }));
    assert.equal((await readFile(join(home, "intent/session-1/triage.jsonl"), "utf8")).trim().split("\n").length, 2);
    assert.throws(() => sessionDirectory("../escape", home));
  } finally { await rm(home, { recursive: true, force: true }); }
});
