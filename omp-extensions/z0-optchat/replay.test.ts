import { expect, test, setDefaultTimeout } from "bun:test";

// The replay spawns bun and drives 3 modes x 11 calls; allow for loaded hosts.
setDefaultTimeout(60_000);
import { comparison, longestCommonPrefix } from "./replay/replay.ts";

test("synthetic ten-round replay proves preservation, cache reuse, zoom cap and off behavior", async () => {
	const [old, fixed, off] = await comparison();
	if (!old || !fixed || !off) throw new Error("missing replay result");
	expect(fixed.calls).toBe(11);
	expect(fixed.prefixStablePercent).toBe(100);
	expect(fixed.inTurnDropped).toBe(0);
	expect(fixed.zoomHints).toBe(0);
	expect(fixed.zoomExecuted).toBe(2);
	expect(old.inTurnDropped).toBe(110);
	expect(old.zoomHints).toBe(11);
	expect(old.zoomExecuted).toBe(10);
	expect(old.cacheRead).toBeLessThan(fixed.cacheRead);
	expect(old.cacheWrite).toBeGreaterThan(fixed.cacheWrite);
	expect(off.inTurnDropped).toBe(0);
	expect(off.zoomExecuted).toBe(0);
	expect(off.zoomRequests).toBe(0);
	expect(longestCommonPrefix("abc", "abd")).toBe(2);
});

test("standalone harness prints both table and parseable JSON", async () => {
	const executable = process.execPath;
	const result = Bun.spawnSync([executable, "run", `${import.meta.dir}/replay/replay.ts`], {
		env: { ...process.env, BUN_BE_BUN: "1" }, stdout: "pipe", stderr: "pipe",
	});
	expect(result.exitCode).toBe(0);
	const text = result.stdout.toString();
	expect(text).toContain("prefixStablePercent");
	const jsonStart = text.indexOf("[\n");
	expect(jsonStart).toBeGreaterThan(0);
	const rows = JSON.parse(text.slice(jsonStart));
	expect(rows.map((row: { mode: string }) => row.mode)).toEqual(["legacy", "new", "off"]);
});
