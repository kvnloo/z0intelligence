/**
 * `bun test` for the OMO (senpi) capture shim: the one-line re-export that activation writes to
 * ~/.omo/agent/extensions/z0-capture.js, loaded against a fake senpi API.
 *
 * The capture needs four senpi events: input, before_agent_start, turn_end and agent_end. When the API
 * diverges the shim records UNSUPPORTED (status + a counted drop row) instead of pretending to capture.
 */
import { afterAll, expect, test } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const home = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "z0int-omo-test-"));
process.env.Z0INT_HOME = home;
process.env.Z0INT_PYTHON = join(home, "no-such-python");

// Exactly the file activation writes (README "OMO"): a one-line re-export of the pinned checkout's entry.
const shim = join(home, "z0-capture.js");
writeFileSync(shim, `export { default } from ${JSON.stringify(pathToFileURL(join(import.meta.dir, "omo.ts")).href)};\n`);
const omoCapture = (await import(shim)).default as (pi: unknown) => { status: string; missing: string[] };
const bridge = await import("./index.ts");

const SENPI_EVENTS = ["session_start", "input", "before_agent_start", "agent_start", "agent_end", "turn_start", "turn_end",
	"tool_call", "tool_result", "message_end", "session_shutdown"];

afterAll(() => rmSync(home, { recursive: true, force: true }));

/** A fake senpi ExtensionAPI: `on` accepts only the events senpi declares, like a host that validates names. */
function fakeSenpi(events: string[]) {
	const handlers = new Map<string, Array<(...args: unknown[]) => unknown>>();
	return {
		handlers,
		on(event: string, handler: (...args: unknown[]) => unknown) {
			if (!events.includes(event)) throw new Error(`unknown event ${event}`);
			handlers.set(event, [...(handlers.get(event) ?? []), handler]);
		},
	};
}

function dropReasons(): string[] {
	const path = join(home, "state", "omo", "drops.jsonl");
	return existsSync(path) ? readFileSync(path, "utf8").split("\n").filter(Boolean).map(l => JSON.parse(l).reason) : [];
}

test("the senpi re-export loads, registers capture only and stays inert", async () => {
	const pi = fakeSenpi(SENPI_EVENTS);
	const out = omoCapture(pi);
	expect(out.status).toBe("SUPPORTED");
	expect([...pi.handlers.keys()].sort()).toEqual(["agent_end", "before_agent_start", "input", "turn_end"]);
	// no routing: the OMO shim never registers the z0int-intelligence route_worker tool (G-#95)
	expect("registerTool" in pi).toBe(false);
	for (const [event, handlers] of pi.handlers) {
		for (const handler of handlers) {
			expect(await handler({ type: event, prompt: "hi", text: "hi", messages: [] }, { cwd: "/w" })).toBeUndefined();
		}
	}
	expect(bridge.captureStatus().harnesses.omo).toEqual({ status: "SUPPORTED", missing: [] });
	expect(dropReasons()).toContain("worker_unavailable"); // fail-open, counted under omo
});

test("a diverged senpi API is recorded UNSUPPORTED, never a silent pass", () => {
	const pi = fakeSenpi(SENPI_EVENTS.filter(e => e !== "before_agent_start" && e !== "agent_end"));
	const out = omoCapture(pi);
	expect(out.status).toBe("UNSUPPORTED");
	expect(out.missing.sort()).toEqual(["agent_end", "before_agent_start"]);
	expect(dropReasons()).toContain("unsupported_api");
	expect(omoCapture({}).status).toBe("UNSUPPORTED"); // no `on` at all
});

test("the installed senpi declares the events the shim needs (else UNSUPPORTED is recorded)", () => {
	const candidates = [
		process.env.SENPI_TYPES,
		"/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@code-yeongyu/senpi/dist/core/extensions/types.d.ts",
	].filter((p): p is string => Boolean(p));
	const types = candidates.find(p => existsSync(p));
	if (!types) {
		console.log("OMO senpi API: UNSUPPORTED-UNVERIFIED (senpi types.d.ts not found on this host)");
		return;
	}
	const text = readFileSync(types, "utf8");
	const missing = ["input", "before_agent_start", "turn_end", "agent_end"].filter(
		e => !text.includes(`on(event: "${e}"`),
	);
	const status = missing.length ? "UNSUPPORTED" : "SUPPORTED";
	console.log(`OMO senpi API (${types}): ${status}${missing.length ? ` missing=${missing.join(",")}` : ""}`);
	expect(text).toContain("cwd: string;");
	expect(text).toContain("sessionManager: ReadonlySessionManager;");
	expect(status).toBe("SUPPORTED");
});
