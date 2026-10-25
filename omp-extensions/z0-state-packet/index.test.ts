import { test, expect } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import register from "./index";

test("before_agent_start delivers fresh evidence and only a stale warning", () => {
	const dir = mkdtempSync(join(tmpdir(), "packet-"));
	const previous = process.env.Z0INT_STATE_PACKET;
	const previousPython = process.env.Z0INT_STATE_PACKET_PYTHON;
	try {
		const path = join(dir, "STATE-PACKET.md");
		process.env.Z0INT_STATE_PACKET = path;
		process.env.Z0INT_STATE_PACKET_PYTHON = "python3";
		let handler: ((event: { systemPrompt: string[] }) => { systemPrompt: string[] } | undefined) | undefined;
		register({ on(event, callback) { expect(event).toBe("before_agent_start"); handler = callback; } });
		const packet = (date: Date) => `\`/generator.py\` → generated_at: ${date.toISOString()}\n\`/generator.py\` → generator_sha256: ${"a".repeat(64)}\n\`/report\` → measured evidence\n`;
		writeFileSync(path, packet(new Date()));
		const event = { systemPrompt: ["original"] };
		const fresh = handler?.(event);
		expect(fresh?.systemPrompt[0]).toBe("original");
		expect(fresh?.systemPrompt[1]).toContain("measured evidence");
		expect(event.systemPrompt).toEqual(["original"]);
		writeFileSync(path, packet(new Date(Date.now() - 10800000)));
		const stale = handler?.(event)?.systemPrompt[1];
		expect(stale).toStartWith("packet stale since ");
		expect(stale).not.toContain("measured evidence");
		rmSync(path);
		expect(handler?.(event)).toBeUndefined();
	} finally {
		if (previous === undefined) delete process.env.Z0INT_STATE_PACKET;
		else process.env.Z0INT_STATE_PACKET = previous;
		if (previousPython === undefined) delete process.env.Z0INT_STATE_PACKET_PYTHON;
		else process.env.Z0INT_STATE_PACKET_PYTHON = previousPython;
		rmSync(dir, { recursive: true });
	}
});
