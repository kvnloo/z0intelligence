/**
 * `bun test` for the z0int-bridge capture handlers (oh-my-pi#109).
 *
 * A fake ExtensionAPI and, for the worker, either an interpreter that does not exist (fail-open) or a tiny
 * fake JSONL worker. No OMP runtime, no z0int Python, no model server, no network.
 */
import { afterAll, beforeAll, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const home = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "z0int-bridge-test-"));
process.env.Z0INT_HOME = home;
process.env.Z0INT_PYTHON = join(home, "no-such-python");
process.env.OMP_SESSION_ID = "";

const m = await import("./index.ts");

type AnyFn = (...args: unknown[]) => unknown;

function fakePi() {
	const handlers = new Map<string, AnyFn[]>();
	return {
		handlers,
		events: {},
		on: (event: string, handler: AnyFn) => {
			handlers.set(event, [...(handlers.get(event) ?? []), handler]);
		},
		setLabel: () => undefined,
		registerCommand: () => undefined,
		registerTool: () => undefined,
	};
}

function ctx(over: Record<string, unknown> = {}) {
	return {
		cwd: "/work/repo",
		sessionManager: { getSessionId: () => "sess-1" },
		agent: { kind: "main", id: "Main", name: "main", depth: 0 },
		model: { provider: "stub", id: "model-a" },
		ui: { notify: () => undefined },
		...over,
	};
}

const rejections: unknown[] = [];
const onRejection = (reason: unknown) => rejections.push(reason);
const errors: unknown[] = [];
const onError = (error: unknown) => errors.push(error);

beforeAll(() => {
	process.on("unhandledRejection", onRejection);
	process.on("uncaughtException", onError);
});

afterAll(() => {
	process.off("unhandledRejection", onRejection);
	process.off("uncaughtException", onError);
	rmSync(home, { recursive: true, force: true });
});

function settle(ms = 300): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}

function drops(harness: string): Array<Record<string, unknown>> {
	const path = join(home, "state", harness, "drops.jsonl");
	if (!existsSync(path)) return [];
	return readFileSync(path, "utf8")
		.split("\n")
		.filter(Boolean)
		.map(line => JSON.parse(line));
}

test("the extension loads and registers with a missing interpreter", async () => {
	const pi = fakePi();
	expect(() => m.default(pi as never)).not.toThrow();
	for (const event of ["input", "before_agent_start", "turn_end", "agent_end"]) {
		expect(pi.handlers.get(event)?.length).toBeGreaterThan(0);
	}
	await settle();
	expect(rejections).toEqual([]);
	expect(errors).toEqual([]);
});

test("capture handlers return undefined and fail open, counted, when the worker cannot start", async () => {
	const pi = fakePi();
	expect(m.registerBridgeCapture(pi as never, { harness: "omp" }).status).toBe("SUPPORTED");
	const before = m.captureStatus().counters.worker_unavailable ?? 0;
	const [open] = pi.handlers.get("before_agent_start")!;
	const [turnEnd] = pi.handlers.get("turn_end")!;
	const [agentEnd] = pi.handlers.get("agent_end")!;
	expect(await open({ type: "before_agent_start", prompt: "fix the parser" }, ctx())).toBeUndefined();
	expect(await turnEnd({ type: "turn_end", message: { role: "assistant" } }, ctx())).toBeUndefined();
	expect(await agentEnd({ type: "agent_end", messages: [] }, ctx())).toBeUndefined();
	await settle();
	expect(m.captureStatus().counters.worker_unavailable).toBe(before + 1);
	const rows = drops("omp").filter(r => r.reason === "worker_unavailable");
	expect(rows.length).toBe(before + 1);
	expect(rows[0].schema).toBe("z0int.omp.drop.v0");
	expect(JSON.stringify(rows)).not.toContain("fix the parser");
	expect(rejections).toEqual([]);
	expect(errors).toEqual([]);
});

test("thrown errors inside a handler are caught: no throw, no unhandledRejection", async () => {
	const pi = fakePi();
	m.registerBridgeCapture(pi as never, { harness: "omp" });
	const hostile = {
		get cwd(): string {
			throw new Error("cwd exploded");
		},
		sessionManager: {
			getSessionId: () => {
				throw new Error("session exploded");
			},
		},
	};
	for (const [event, payload] of [
		["input", { type: "input", text: "/reload-plugins" }],
		["before_agent_start", { type: "before_agent_start", prompt: "hello" }],
		["turn_end", null],
		["agent_end", { type: "agent_end", messages: null }],
	] as const) {
		for (const handler of pi.handlers.get(event)!) {
			expect(await handler(payload, hostile)).toBeUndefined();
			expect(await handler(payload, undefined)).toBeUndefined();
		}
	}
	await settle();
	expect(rejections).toEqual([]);
	expect(errors).toEqual([]);
});

test("the turn_open frame carries the task cwd, harness, subagent parent and model", () => {
	const info = m.turnContext(ctx({ agent: { kind: "sub", id: "0-Task", name: "task", depth: 1, parentId: "Main" } }));
	expect(info).toEqual({ cwd: "/work/repo", agentKind: "sub", parentId: "Main", model: "stub/model-a" });
	const frame = m.turnOpenFrame({ traceId: "t1", sessionId: "sess-1", pid: 9 }, "fix it", info, "omp");
	expect(frame.op).toBe("turn_open");
	expect(frame.payload).toMatchObject({ cwd: "/work/repo", harness: "omp", parent_id: "Main", agent_kind: "sub" });
	const close = m.turnCloseFrame({ traceId: "t1", sessionId: "sess-1", pid: 9 }, "agent_end", { execution_completed: false }, "omo");
	expect(close).toMatchObject({ op: "agent_end", trace_id: "t1", payload: { harness: "omo", execution_completed: false } });
	expect(m.turnContext(undefined)).toEqual({ cwd: null, agentKind: null, parentId: null, model: null });
});

test("the session id comes from ctx.sessionManager", () => {
	expect(m.resolveSessionId({ sessionManager: { getSessionId: () => "sess-123" } })).toBe("sess-123");
	expect(String(m.resolveSessionId({}))).toStartWith("omp-");
});

test("interpreter resolution: Z0INT_PYTHON, then the checkout venv, then the z0int runtime, then python3", () => {
	expect(m.resolvePython({ Z0INT_PYTHON: "/x/py" }, "/r", "/z")).toBe("/x/py");
	expect(m.resolvePython({}, "/nonexistent-root", "/nonexistent-z0")).toBe("python3");
});

// --- a fake JSONL worker: the frames reach it, one turn per session ------------------------------------

test("frames reach the worker and a turn per session closes on its own agent_end", async () => {
	const log = join(home, "worker-frames.jsonl");
	const worker = join(home, "fake-worker.py");
	writeFileSync(
		worker,
		[
			"#!/usr/bin/env python3",
			"import json, sys",
			`log = open(${JSON.stringify(log)}, "a")`,
			"for line in sys.stdin:",
			"    req = json.loads(line)",
			"    log.write(line); log.flush()",
			"    out = {'id': req.get('id'), 'ok': True, 'protocol': 'z0int.bridge.v2', 'generation': 1,",
			"           'instance_id': 'fake', 'build_id': 'fake'}",
			"    sys.stdout.write(json.dumps(out) + '\\n'); sys.stdout.flush()",
			"    if req.get('op') == 'shutdown':",
			"        break",
		].join("\n"),
	);
	chmodSync(worker, 0o755);
	process.env.Z0INT_PYTHON = worker;
	try {
		const pi = fakePi();
		m.registerBridgeCapture(pi as never, { harness: "omp" });
		const [open] = pi.handlers.get("before_agent_start")!;
		const [agentEnd] = pi.handlers.get("agent_end")!;
		const main = ctx();
		const sub = ctx({
			sessionManager: { getSessionId: () => "sub-1" },
			agent: { kind: "sub", id: "0-Task", name: "task", depth: 1, parentId: "Main" },
		});
		expect(await open({ prompt: "main prompt" }, main)).toBeUndefined();
		expect(await open({ prompt: "sub prompt" }, sub)).toBeUndefined();
		expect(await agentEnd({ messages: [], willRetry: true }, sub)).toBeUndefined(); // not the end yet
		expect(await agentEnd({ messages: [], aborted: true }, sub)).toBeUndefined();
		expect(await agentEnd({ messages: [] }, main)).toBeUndefined();
		const frames = readFileSync(log, "utf8")
			.split("\n")
			.filter(Boolean)
			.map(line => JSON.parse(line));
		const opens = frames.filter(f => f.op === "turn_open");
		const closes = frames.filter(f => f.op === "agent_end");
		expect(opens.map(f => f.session_id).sort()).toEqual(["sess-1", "sub-1"]);
		expect(closes.map(f => f.session_id)).toEqual(["sub-1", "sess-1"]);
		const subOpen = opens.find(f => f.session_id === "sub-1");
		expect(subOpen.payload).toMatchObject({ cwd: "/work/repo", parent_id: "Main", agent_kind: "sub", harness: "omp" });
		const byTrace = Object.fromEntries(opens.map(f => [f.session_id, f.trace_id]));
		expect(closes[0].trace_id).toBe(byTrace["sub-1"]);
		expect(closes[0].payload.execution_completed).toBe(false);
		expect(closes[1].trace_id).toBe(byTrace["sess-1"]);
		expect(closes[1].payload.execution_completed).toBe(true);
		await m.stopWorker();
	} finally {
		process.env.Z0INT_PYTHON = join(home, "no-such-python");
	}
	expect(rejections).toEqual([]);
	expect(errors).toEqual([]);
});
