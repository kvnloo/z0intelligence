import { describe, expect, test, vi } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import governor, { statePacket } from "./index.ts";
import { chooseArm, config, defaults, probes, recall, shouldCompact, visibleContext } from "./core.ts";
import type { Ctx, Event } from "./index.ts";
import type { Entry } from "./core.ts";

describe("experimental policy", () => {
 test("defaults only measure; malformed settings cannot activate", () => { expect(config({ mode: "yes" }).mode).toBe("measure"); expect(config({ ceilingPercent: -1 }).ceilingPercent).toBeGreaterThan(config({}).floorPercent); });
 test("low floor and cooldown guard requests and ceiling", () => { expect(shouldCompact(defaults, "25", 9, true, true, Infinity)).toBeUndefined(); expect(shouldCompact(defaults, "25", 90, true, true, 0)).toBeUndefined(); });
 test("arms do not claim universal 25 percent optimum", () => { expect(shouldCompact(defaults, "25", 30, true, false, Infinity)).toBe("natural_boundary"); expect(shouldCompact(defaults, "50", 30, true, false, Infinity)).toBeUndefined(); expect(shouldCompact(defaults, "native", 65, true, false, Infinity)).toBeUndefined(); });
 test("ceiling backstops native while request requires boundary", () => { expect(shouldCompact(defaults, "native", 71, false, false, Infinity)).toBe("hard_ceiling"); expect(shouldCompact(defaults, "native", 20, true, true, Infinity)).toBe("model_boundary_request"); expect(shouldCompact(defaults, "25", 30, false, true, Infinity)).toBeUndefined(); });
 test("balanced exploration requires five measured observations", () => { expect(chooseArm([], 0).arm).toBe("25"); expect(chooseArm([{ arm: "25", recall_score: 1, status: "compacted", policy_evidence: true }], 1).arm).not.toBe("25"); });
 test("degraded arm evolves away after qualification", () => {
  const rows = ["25", "50", "native"].flatMap(arm => Array.from({ length: 5 }, () => ({ arm: arm as "25" | "50" | "native", recall_score: arm === "25" ? 0.9 : 1, status: "compacted", policy_evidence: true })));
  rows.push({ arm: "25", recall_score: 0.3, status: "compacted", policy_evidence: true });
  expect(chooseArm(rows, 16).reason).toBe("recall_degradation_switch"); expect(chooseArm(rows, 16).arm).not.toBe("25");
 });
 test("manual and model-request overrides cannot promote a threshold arm", () => { expect(chooseArm([{ arm: "25", recall_score: 1, status: "compacted", policy_evidence: false }], 0).arm).toBe("25"); });
});
describe("retention measurement", () => {
 test("extracts four categories without secret lines or hidden thinking", () => {
  const set = probes([{ content: [{ type: "text", text: "Use /workspace/project/src/main.ts\nTASK-172 abcdef1234\nDecided to keep GPU inference remote.\nBlocker: awaiting owner approval.\npassword=/secret/hidden/file" }, { type: "thinking", thinking: "/hidden/reasoning/path" }] }]);
  expect(new Set(set.map(p => p.kind)).size).toBe(4); expect(JSON.stringify(set)).not.toContain("secret"); expect(JSON.stringify(set)).not.toContain("reasoning");
 });
 test("missing probes are unknown, not perfect recall", () => { expect(recall([], "anything").recall_score).toBeNull(); });
 test("exact recall measures missing literal", () => { expect(recall([{ kind: "path", literal: "/a/b" }, { kind: "id", literal: "TASK-17" }], "keep /a/b").recall_score).toBe(0.5); });
 test("old branch and preserveData cannot leak into post score", () => {
  const post = visibleContext([{ type: "message", id: "old", message: { content: "forgotten TASK-172" } }, { type: "message", id: "kept", message: { content: "tail" } }, { type: "compaction", id: "c", firstKeptEntryId: "kept", summary: "new summary" }]);
  expect(post).toBe("new summary\ntail"); expect(post).not.toContain("TASK-172");
 });
});
test("state packet unavailable fails open", async () => {
 const old = process.env.Z0INT_STATEPACK_PATH; process.env.Z0INT_STATEPACK_PATH = "/no/such/statepack.py";
 try { expect((await statePacket("unit fixture", 100)).status).toBe("unavailable"); } finally { if (old === undefined) delete process.env.Z0INT_STATEPACK_PATH; else process.env.Z0INT_STATEPACK_PATH = old; }
});
test("extension inert without flag, then measures native compaction without old-context leakage", async () => {
 const home = mkdtempSync(join(tmpdir(), "compact-governor-unit-")); const previous = process.env.Z0INT_HOME; process.env.Z0INT_HOME = home;
 const events = new Map<string, (event: Event, ctx: Ctx) => unknown>(); const tools: { name: string }[] = [];
 const branch: Entry[] = [{ type: "message", id: "old", message: { content: "TASK-172 /a/b" } }]; let calls = 0;
 const ctx: Ctx = { getContextUsage: () => ({ tokens: 500, contextWindow: 1000, percent: 50 }), isIdle: () => true, hasPendingMessages: () => false, compact: async () => { calls++; }, sessionManager: { getSessionId: () => "test-session", getBranch: () => branch } };
 try {
  governor({ on: (n, h) => { events.set(n, h); }, registerTool: t => { tools.push(t); } });
  await events.get("agent_end")!({}, ctx); expect(existsSync(join(home, "compaction"))).toBe(false); expect(calls).toBe(0);
  mkdirSync(join(home, "compaction")); writeFileSync(join(home, "compaction", "enabled"), "on");
  await events.get("agent_end")!({}, ctx); expect(calls).toBe(0);
  expect(JSON.parse(readFileSync(join(home, "compaction", "receipts.jsonl"), "utf8").trim()).status).toBe("would_compact");
  await events.get("session_before_compact")!({}, ctx);
  branch.push({ type: "compaction", id: "c", firstKeptEntryId: "missing", summary: "retained /a/b" });
  await events.get("session_compact")!({ compactionEntry: { summary: "retained /a/b", tokensBefore: 500, tokensAfter: 100 } }, ctx);
  const row = JSON.parse(readFileSync(join(home, "compaction", "receipts.jsonl"), "utf8").trim().split("\n").at(-1)!);
  expect(row.recall_score).toBe(0.5); expect(row.tokens_after).toBe(100);
  expect(tools[0].name).toBe("compact_boundary");
  vi.useFakeTimers();
  writeFileSync(join(home, "compaction", "policy.json"), JSON.stringify({ mode: "active", arm: "25", cooldownMs: 0 }));
  await events.get("agent_end")!({}, ctx);
  writeFileSync(join(home, "compaction", "enabled"), "off");
  vi.advanceTimersByTime(1); expect(calls).toBe(0);
  writeFileSync(join(home, "compaction", "enabled"), "on");
  await events.get("agent_end")!({}, ctx);
  vi.advanceTimersByTime(1); expect(calls).toBe(1);
 } finally { vi.useRealTimers(); if (previous === undefined) delete process.env.Z0INT_HOME; else process.env.Z0INT_HOME = previous; rmSync(home, { recursive: true, force: true }); }
});
