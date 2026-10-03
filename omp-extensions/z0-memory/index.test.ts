/**
 * `bun test` for omp-extensions/z0-memory: the OMP `context` seam and the OMO (senpi) inject shim.
 *
 * The extension is loaded against a fake ExtensionAPI; the z0int side is the real `python -m z0int.memory.seam`
 * of this tree (Z0INT_PYTHON) over a synthetic AgentsView fixture. The `context` result is model-visible only:
 * the host's canonical history is the event's message list, which must come back untouched.
 */
import { afterEach, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const ROOT = join(import.meta.dir, "..", "..");
// bun runs every test file in one process: another file may already have pointed Z0INT_PYTHON at a missing
// interpreter, so take it only when it exists, and put back every variable these tests change.
const PY = [process.env.Z0INT_PYTHON].find(p => p && existsSync(p)) ?? "python3";
const KEYS = ["Z0INT_HOME", "AGENTSVIEW_DATA_DIR", "Z0INT_PYTHON", "Z0INT_MEMORY_INJECT", "Z0INT_MEMORY_ENDPOINT"] as const;
const saved = Object.fromEntries(KEYS.map(k => [k, process.env[k]]));
afterEach(() => {
	for (const k of KEYS) {
		if (saved[k] === undefined) delete process.env[k];
		else process.env[k] = saved[k];
	}
});
const QUERY = "how do we deploy the quokka gateway";
const LOOPBACK = { provider: "sandbox", id: "fake", baseUrl: "http://127.0.0.1:11547/v1" };

const omp = (await import("./index.ts")).default as (pi: unknown) => { status: string; missing: string[] };
const omo = (await import("./omo.ts")).default as (pi: unknown) => { status: string; missing: string[] };

function fixture(mode: string) {
	const h = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "c8-omp-"));
	const r = spawnSync(PY, ["-c", "import sys; sys.path[:0] = [sys.argv[2], sys.argv[3]]; from memory_fixture import build_av_db; build_av_db(sys.argv[1])",
		join(h, "av", "sessions.db"), join(ROOT, "tests"), join(ROOT, "src")], { encoding: "utf8" });
	expect(r.status).toBe(0);
	process.env.Z0INT_HOME = join(h, "z0");
	process.env.AGENTSVIEW_DATA_DIR = join(h, "av");
	process.env.Z0INT_PYTHON = PY;
	process.env.Z0INT_MEMORY_INJECT = mode;
	return h;
}

function slowPython(h: string) {
	const p = join(h, "slow-python");
	writeFileSync(p, "#!/bin/sh\nsleep 5\n");
	chmodSync(p, 0o755);
	process.env.Z0INT_PYTHON = p;
}

const SENPI_EVENTS = ["session_start", "input", "before_agent_start", "agent_start", "agent_end", "turn_start", "turn_end",
	"context", "tool_call", "tool_result", "message_end", "session_shutdown"];

function fakePi(events?: string[]) {
	const handlers = new Map<string, Array<(...args: unknown[]) => unknown>>();
	return {
		handlers,
		on(event: string, handler: (...args: unknown[]) => unknown) {
			if (events && !events.includes(event)) throw new Error(`unknown event ${event}`);
			handlers.set(event, [...(handlers.get(event) ?? []), handler]);
		},
	};
}

const user = (text: string, ts = 1) => ({ role: "user", content: [{ type: "text", text }], timestamp: ts });
const hctx = (model = LOOPBACK, sid = "sess-1") => ({ cwd: "/w/z0", model, sessionManager: { getSessionId: () => sid } });

async function context(pi: ReturnType<typeof fakePi>, messages: unknown[], ctx = hctx()) {
	const [handler] = pi.handlers.get("context") ?? [];
	return (await handler({ type: "context", messages }, ctx)) as { messages?: any[] } | undefined;
}

function seamRows(h: string, harness: string) {
	const p = join(h, "z0", "state", "memory", "seam", `${harness}.jsonl`);
	return existsSync(p) ? readFileSync(p, "utf8").split("\n").filter(Boolean).map(l => JSON.parse(l)) : [];
}

async function waitRows(h: string, harness: string, n = 1, ms = 20000) {
	const end = Date.now() + ms;
	while (Date.now() < end && seamRows(h, harness).length < n) await new Promise(r => setTimeout(r, 50));
	return seamRows(h, harness);
}

test("OMP shadow: the context handler returns undefined at once and a receipt is written later", async () => {
	const h = fixture("shadow");
	slowPython(h);
	const pi = fakePi();
	omp(pi);
	const messages = [user(QUERY)];
	const t0 = performance.now();
	expect(await context(pi, messages)).toBeUndefined();
	expect(performance.now() - t0).toBeLessThan(20);
	process.env.Z0INT_PYTHON = PY;
	expect(await context(pi, messages, hctx(LOOPBACK, "sess-2"))).toBeUndefined();
	const [row] = await waitRows(h, "omp");
	expect(row.outcome).toBe("shadow");
	expect(row.would_inject).toBe(true);
	expect(row.memory.snapshot_id).toBe(row.memory_snapshot_id);
}, 30000);

test("OMP canary: the brief is in the next model-visible request only; history is untouched; no double injection", async () => {
	const h = fixture("canary");
	const pi = fakePi();
	omp(pi);
	const messages = Object.freeze([user("earlier", 0), { role: "assistant", content: [{ type: "text", text: "ok" }] }, user(QUERY)]);
	const out = await context(pi, messages as unknown[]);
	expect(out?.messages?.length).toBe(4);
	const brief = out!.messages![2];
	expect(brief.role).toBe("user");
	expect(brief.content[0].text).toMatch(/^z0 memory brief \(evidence, not instructions\)/);
	expect(brief.content[0].text).toContain("agentsview:h1#");
	expect(out!.messages![3]).toBe(messages[2]);  // the user's own message stays last
	expect(messages.length).toBe(3);
	expect(seamRows(h, "omp").at(-1).outcome).toBe("injected");
	expect(await context(pi, messages as unknown[])).toBeUndefined();  // the same turn again: no second injection
	const tool = [...messages, { role: "toolResult", content: [{ type: "text", text: "x" }] }];
	expect(await context(pi, tool)).toBeUndefined();  // tool continuation: native
}, 30000);

test("OMP canary past the deadline is native with a counted timeout; fail-open; cloud blocked", async () => {
	const h = fixture("on");
	slowPython(h);
	const pi = fakePi();
	omp(pi);
	const t0 = performance.now();
	expect(await context(pi, [user(QUERY)])).toBeUndefined();
	expect(performance.now() - t0).toBeLessThan(600);
	process.env.Z0INT_PYTHON = join(h, "missing-python");
	expect(await context(pi, [user(QUERY)], hctx(LOOPBACK, "sess-3"))).toBeUndefined();
	process.env.Z0INT_PYTHON = PY;
	const cloud = { provider: "anthropic", id: "x", baseUrl: "https://api.anthropic.com" };
	expect(await context(pi, [user(QUERY)], hctx(cloud, "sess-4"))).toBeUndefined();
	const outcomes = (await waitRows(h, "omp", 3)).map(r => r.outcome);
	expect(outcomes).toEqual(["timeout", "error", "cloud_injection_blocked"]);
}, 30000);

test("OMO: the senpi shim loads, and with a context hook the brief reaches the next request", async () => {
	const h = fixture("canary");
	const pi = fakePi(SENPI_EVENTS);
	const out = omo(pi);
	expect(out).toEqual({ status: "SUPPORTED", missing: [] });
	const res = await context(pi, [user(QUERY)]);
	expect(res?.messages?.[0].content[0].text).toContain("agentsview:h1#");
	expect(seamRows(h, "omo").at(-1).outcome).toBe("injected");
}, 30000);

test("OMO without a context hook records B=UNSUPPORTED instead of passing", () => {
	const h = fixture("canary");
	const out = omo(fakePi(SENPI_EVENTS.filter(e => e !== "context")));
	expect(out).toEqual({ status: "UNSUPPORTED", missing: ["context"] });
	const status = JSON.parse(readFileSync(join(h, "z0", "state", "memory", "seam", "push_status.json"), "utf8"));
	expect(status.omo).toEqual({ status: "UNSUPPORTED", missing: ["context"] });
});

test("the installed senpi declares the context event (else UNSUPPORTED is recorded)", () => {
	const types = process.env.SENPI_TYPES;
	if (!types || !existsSync(types)) {
		console.log("SKIP: SENPI_TYPES not given; the installed senpi API is unchecked");
		return;
	}
	expect(readFileSync(types, "utf8")).toContain('on(event: "context"');
});

test("the omo entry is a one-line re-export activation can write", async () => {
	const h = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "c8-omo-shim-"));
	const shim = join(h, "z0-memory.js");
	writeFileSync(shim, `export { default } from ${JSON.stringify(pathToFileURL(join(import.meta.dir, "omo.ts")).href)};\n`);
	expect((await import(shim)).default).toBe(omo);
});

// ----------------------------------------------------------------------------- round 2 (verifier findings)
test("the gate endpoint is the active model's baseUrl only: a generic env var never marks a cloud model loopback", async () => {
	const h = fixture("canary");
	process.env.Z0INT_MEMORY_ENDPOINT = "http://127.0.0.1:9";
	const pi = fakePi();
	omp(pi);
	const cloud = { provider: "anthropic", id: "x", baseUrl: "https://api.anthropic.com" };
	expect(await context(pi, [user(QUERY)], hctx(cloud, "sess-e1"))).toBeUndefined();
	const noModel = { cwd: "/w/z0", sessionManager: { getSessionId: () => "sess-e2" } };
	expect(await context(pi, [user(QUERY)], noModel)).toBeUndefined();
	const got = seamRows(h, "omp").map(r => [r.outcome, r.endpoint_loopback]);
	expect(got).toEqual([["cloud_injection_blocked", false], ["cloud_injection_blocked", false]]);
}, 30000);

test("OMP turn key is the user message's own id: a new turn after compaction still gets its brief; replays are counted", async () => {
	const h = fixture("canary");
	const { counters } = await import("../../harness-adapters/memory-client.mjs");
	const pi = fakePi();
	omp(pi);
	const first = [user(QUERY, 1000)];
	expect((await context(pi, first))?.messages?.length).toBe(2);
	// compaction: the history shrinks back to one user message, a different turn with the same user count
	const after = [user("how do we deploy the quokka gateway now", 2000)];
	expect((await context(pi, after))?.messages?.length).toBe(2);
	const before = counters.replay;
	expect(await context(pi, after)).toBeUndefined();  // a re-sent request of the same turn: replay, counted
	expect(counters.replay).toBe(before + 1);
	expect(seamRows(h, "omp").map(r => r.outcome)).toEqual(["injected", "injected"]);
}, 30000);
