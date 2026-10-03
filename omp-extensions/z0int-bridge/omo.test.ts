/**
 * `bun test` for the OMO (senpi) capture shim: the one-line re-export that activation writes to
 * ~/.omo/agent/extensions/z0-capture.js, loaded against a fake senpi API.
 *
 * The capture needs four senpi events: input, before_agent_start, turn_end and agent_end. Senpi's `on()`
 * (dist/core/extensions/loader.js) accepts any event name and exposes no event list at runtime, so divergence
 * is caught two ways: at test time against the installed senpi types (an explicit skip with a reason when they
 * are absent, never a pass), and at run time by counting `unclosed_turn` when a turn opens in a session whose
 * last turn never saw agent_end. A host that does reject an event (or has no `on`) is recorded UNSUPPORTED.
 */
import { afterAll, expect, test } from "bun:test";
import { createRequire } from "node:module";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
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

/** The installed senpi types: SENPI_TYPES, else module resolution, else bun's global install ($BUN_INSTALL). */
function senpiTypes(): string | null {
	const rel = "dist/core/extensions/types.d.ts";
	const candidates: string[] = [];
	if (process.env.SENPI_TYPES) candidates.push(process.env.SENPI_TYPES);
	try {
		const pkg = createRequire(import.meta.path).resolve("@code-yeongyu/senpi/package.json");
		candidates.push(join(pkg, "..", rel));
	} catch {
		/* not resolvable from this checkout */
	}
	const bunInstall = process.env.BUN_INSTALL || join(homedir(), ".bun");
	candidates.push(join(bunInstall, "install", "global", "node_modules", "@code-yeongyu", "senpi", rel));
	return candidates.find(p => existsSync(p)) ?? null;
}

test("a host whose on() accepts any event but never delivers agent_end is caught at run time", async () => {
	// Like the real senpi loader: on() stores any name. A diverged API registers "fine" and never closes a turn.
	const handlers = new Map<string, Array<(...args: unknown[]) => unknown>>();
	const pi = { on: (e: string, h: (...args: unknown[]) => unknown) => handlers.set(e, [...(handlers.get(e) ?? []), h]) };
	const fake = join(home, "fake-worker.py");
	writeFileSync(fake, [
		"#!/usr/bin/env python3",
		"import json, sys",
		"for line in sys.stdin:",
		"    req = json.loads(line)",
		"    sys.stdout.write(json.dumps({'id': req.get('id'), 'ok': True, 'protocol': 'z0int.bridge.v2',",
		"                                 'generation': 1, 'instance_id': 'f', 'build_id': 'f'}) + '\\n')",
		"    sys.stdout.flush()",
		"    if req.get('op') == 'shutdown':",
		"        break",
	].join("\n"));
	chmodSync(fake, 0o755);
	process.env.Z0INT_PYTHON = fake;
	try {
		expect(omoCapture(pi).status).toBe("SUPPORTED"); // nothing at registration time can tell
		const [open] = handlers.get("before_agent_start")!;
		const c = { cwd: "/w", sessionManager: { getSessionId: () => "omo-1" } };
		await open({ prompt: "one" }, c);
		await open({ prompt: "two" }, c); // agent_end for "one" never arrived
		expect(dropReasons()).toContain("unclosed_turn");
		await bridge.stopWorker();
	} finally {
		process.env.Z0INT_PYTHON = join(home, "no-such-python");
	}
});

const TYPES = senpiTypes();
test.skipIf(!TYPES)(
	`the installed senpi declares the events the shim needs${TYPES ? "" : " [SKIPPED: senpi types.d.ts not found (set SENPI_TYPES); OMO API status UNVERIFIED]"}`,
	() => {
		const text = readFileSync(TYPES!, "utf8");
		const missing = ["input", "before_agent_start", "turn_end", "agent_end"].filter(
			e => !text.includes(`on(event: "${e}"`),
		);
		const status = missing.length ? "UNSUPPORTED" : "SUPPORTED";
		console.log(`OMO senpi API (${TYPES}): ${status}${missing.length ? ` missing=${missing.join(",")}` : ""}`);
		expect(text).toContain("cwd: string;");
		expect(text).toContain("sessionManager: ReadonlySessionManager;");
		expect(status).toBe("SUPPORTED");
	},
);
