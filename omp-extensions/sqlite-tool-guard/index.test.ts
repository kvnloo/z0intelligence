import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { afterAll, describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, linkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import guard, { sqliteToolDenial } from "./index";

const temp = mkdtempSync(join(tmpdir(), "sqlite-tool-guard-"));
const agent = join(temp, "agent");
mkdirSync(join(agent, "sessions"), { recursive: true });
writeFileSync(join(agent, "agent.db"), "synthetic-not-a-real-db");
writeFileSync(join(agent, "agent.db-shm"), "synthetic");
symlinkSync(agent, join(temp, "alias"));
linkSync(join(agent, "agent.db-shm"), join(temp, "hardlink"));
afterAll(() => rmSync(temp, { recursive: true, force: true }));
const denied = (tool: string, path?: string, cwd = temp) => sqliteToolDenial(tool, path === undefined ? {} : { path }, cwd, agent);

describe("agent SQLite pre-tool guard", () => {
	for (const tool of ["read", "grep", "find", "glob"]) {
		for (const path of [agent, temp, "/", join(agent, "agent.db"), join(agent, "agent.db-wal"), join(agent, "agent.db.corrupt-fixture"), `${agent}/agent.db:1-5:raw`, `${temp}/**/*.ts`, `${agent}/**`, `${temp}/alias/agent.db-shm`, join(temp, "hardlink"), `file://${agent}/agent.db`, `${temp}/safe;${agent}`]) {
			test(`${tool} denies ${path}`, () => expect(denied(tool, path)).toBeString());
		}
		test(`${tool} denies implicit ancestor cwd`, () => expect(denied(tool)).toBeString());
		for (const path of [join(agent, "sessions"), `${agent}/sessions/**/*.jsonl`, join(temp, "safe"), `${agent}/session-not-agent.db`, "skill://ctx"]) {
			test(`${tool} allows ${path}`, () => expect(denied(tool, path)).toBeUndefined());
		}
		test(`${tool} allows implicit safe cwd`, () => expect(denied(tool, undefined, join(agent, "sessions"))).toBeUndefined());
	}
	test("external bash is not intercepted", () => expect(denied("bash", agent)).toBeUndefined());
	test("real extension callback blocks before a tool executes without UI", async () => {
		type Callback = (event: { toolName: string; input: Record<string, unknown> }, ctx: { cwd: string; hasUI: boolean }) => { block: true; reason: string } | undefined;
		let callback: Callback | undefined;
		// The fixture supplies only the registration method used by this extension.
		const api = { on: (event: string, handler: Callback) => { expect(event).toBe("tool_call"); callback = handler; } } as unknown as ExtensionAPI;
		guard(api);
		if (!callback) throw new Error("tool_call was not registered");
		const previous = process.env.PI_CODING_AGENT_DIR;
		process.env.PI_CODING_AGENT_DIR = agent;
		try {
			const result = await callback({ toolName: "grep", input: { path: agent } }, { cwd: temp, hasUI: false });
			expect(result?.block).toBe(true);
			expect(result?.reason).toContain("SQLite lock safety");
			expect(await callback({ toolName: "read", input: { path: join(agent, "sessions") } }, { cwd: temp, hasUI: false })).toBeUndefined();
		} finally {
			if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR;
			else process.env.PI_CODING_AGENT_DIR = previous;
		}
	});
});
