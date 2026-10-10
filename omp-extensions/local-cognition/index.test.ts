/**
 * `bun test` for the OMP local-cognition shadow lane.
 *
 * Uses a fake ExtensionAPI and a fake bridge transport: no OMP runtime, no
 * Python, no model server, no network.
 */
import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import localCognition, {
	__resetLocalCognitionForTest,
	buildShadowPayload,
	classifyShadowResponse,
	coverage,
	cognitionTimeoutMs,
	loadCognitionSettings,
	receiptPath,
	resolveAvailableTools,
	shadowEnabled,
	statusRing,
} from "./index.ts";

type AnyFn = (...args: unknown[]) => unknown;

function makePi() {
	const handlers = new Map<string, AnyFn>();
	const commands = new Map<string, { description?: string; handler: (args: string, ctx: unknown) => unknown }>();
	const pi = {
		handlers,
		commands,
		setLabel: () => undefined,
		on: (event: string, handler: AnyFn) => {
			handlers.set(event, handler);
		},
		registerCommand: (name: string, definition: { description?: string; handler: AnyFn }) => {
			commands.set(name, definition as { description?: string; handler: AnyFn });
		},
	};
	return pi;
}

function fakeCtx(over: Record<string, unknown> = {}) {
	const notifications: Array<{ message: string; level?: string }> = [];
	return {
		notifications,
		ui: { notify: (message: string, level?: string) => notifications.push({ message, level }) },
		...over,
	};
}

function toolCall(toolName = "read", input: unknown = { path: "AGENTS.md" }) {
	return { type: "tool_call", toolCallId: "call-1", toolName, input };
}

const globalHolder = globalThis as { __omp_z0int_bridge_transport__?: unknown };
const originalTransport = globalHolder.__omp_z0int_bridge_transport__;
let sent: Array<Record<string, unknown>> = [];

const ENV_KEYS = [
	"OMP_Z0INT_COGNITION_SHADOW",
	"OMP_Z0INT_COGNITION_TOOLS",
	"OMP_Z0INT_COGNITION_MODELS",
	"OMP_Z0INT_COGNITION_AUTHORITY",
	"OMP_Z0INT_COGNITION_TIMEOUT_MS",
	"OMP_Z0INT_COGNITION_BRIDGE_FALLBACK",
	"Z0INT_HOME",
];

function flush(ms = 30): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}

beforeEach(() => {
	__resetLocalCognitionForTest();
	sent = [];
	for (const key of ENV_KEYS) delete process.env[key];
	globalHolder.__omp_z0int_bridge_transport__ = {
		kind: "z0int-bridge",
		generation: 3,
		buildId: "build-1",
		instanceId: "instance-1",
		request: async (body: Record<string, unknown>) => {
			sent.push(body);
			return { ok: true };
		},
		warm: async () => ({ ok: true }),
	};
});

afterEach(() => {
	if (originalTransport === undefined) delete globalHolder.__omp_z0int_bridge_transport__;
	else globalHolder.__omp_z0int_bridge_transport__ = originalTransport;
	for (const key of ENV_KEYS) delete process.env[key];
});

function installed() {
	const pi = makePi();
	localCognition(pi);
	const handler = pi.handlers.get("tool_call");
	if (!handler) throw new Error("tool_call handler not registered");
	return { pi, handler };
}

// --- never blocks -------------------------------------------------------

test("tool_call handler returns undefined and never blocks", () => {
	const { handler } = installed();
	const result = handler(toolCall(), fakeCtx());
	expect(result).toBeUndefined();
});

test("a rejected send does not throw into the tool_call handler", async () => {
	globalHolder.__omp_z0int_bridge_transport__ = {
		kind: "z0int-bridge",
		request: async () => {
			throw new Error("worker exploded");
		},
	};
	const { handler } = installed();
	expect(() => handler(toolCall(), fakeCtx())).not.toThrow();
	await flush();
	// Failure is recorded in memory, never surfaced as a tool failure.
	const row = statusRing[statusRing.length - 1];
	expect(row?.ok).toBe(false);
	expect(String(row?.error)).toContain("worker exploded");
});

// --- sends the shadow request ------------------------------------------

test("sends a cognition_shadow request through the bridge transport", async () => {
	process.env.OMP_Z0INT_COGNITION_TOOLS = "read,grep";
	const { handler } = installed();
	handler(toolCall(), fakeCtx());
	await flush();

	expect(sent).toHaveLength(1);
	const request = sent[0];
	expect(request.op).toBe("cognition_shadow");
	expect(Array.isArray(request.actions)).toBe(true);
	expect(request.state).toContain("OMP tool_call: read");
	expect((request.facts as Record<string, unknown>).tool_name).toBe("read");
});

test("payload actions contain only the available tool names", async () => {
	process.env.OMP_Z0INT_COGNITION_TOOLS = "read,grep";
	const { handler } = installed();
	handler(toolCall("read"), fakeCtx());
	await flush();

	const actions = sent[0].actions as Array<Record<string, unknown>>;
	const tools = actions.map(action => action.tool);
	expect(new Set(tools)).toEqual(new Set(["read", "grep"]));
	for (const tool of tools) {
		expect(["read", "grep"]).toContain(String(tool));
	}
	for (const action of actions) {
		expect(action.kind).toBe("tool");
		expect(["read", "write"]).toContain(String(action.risk_class));
	}
});

test("payload keeps read-only authority and asks one model by default", async () => {
	process.env.OMP_Z0INT_COGNITION_TOOLS = "read,write";
	const { handler } = installed();
	handler(toolCall("write", { path: "x" }), fakeCtx());
	await flush();

	expect(sent[0].authority).toEqual(["read"]);
	// One resident model is what the local supervisor holds; asking two at once never answers.
	expect(sent[0].shadows).toEqual(["functiongemma_270m"]);
	expect(sent[0].risk_class).toBe("write");
	const actions = sent[0].actions as Array<Record<string, unknown>>;
	const writeAction = actions.find(action => action.tool === "write");
	expect(writeAction?.risk_class).toBe("write");
});

// --- kill switch --------------------------------------------------------

test("env kill-switch disables the lane", async () => {
	process.env.OMP_Z0INT_COGNITION_SHADOW = "0";
	expect(shadowEnabled()).toBe(false);

	const { handler } = installed();
	handler(toolCall(), fakeCtx());
	await flush();
	expect(sent).toHaveLength(0);
});

test("env kill-switch accepts off/false and explicit on", () => {
	for (const value of ["0", "false", "off", "no"]) {
		process.env.OMP_Z0INT_COGNITION_SHADOW = value;
		expect(shadowEnabled()).toBe(false);
	}
	for (const value of ["1", "true", "on"]) {
		process.env.OMP_Z0INT_COGNITION_SHADOW = value;
		expect(shadowEnabled()).toBe(true);
	}
	delete process.env.OMP_Z0INT_COGNITION_SHADOW;
	expect(shadowEnabled()).toBe(true);
});

test("cognition.json setting gates the lane when env is unset", () => {
	const home = mkdtempSync(join(tmpdir(), "local-cognition-"));
	try {
		mkdirSync(join(home, "config"), { recursive: true });
		writeFileSync(join(home, "config", "cognition.json"), JSON.stringify({ shadow: false }));
		process.env.Z0INT_HOME = home;
		__resetLocalCognitionForTest();

		expect(receiptPath()).toBe(join(home, "shadow", "cognition-shadow.jsonl"));
		expect(loadCognitionSettings().shadow).toBe(false);
		expect(shadowEnabled()).toBe(false);

		process.env.OMP_Z0INT_COGNITION_SHADOW = "1";
		expect(shadowEnabled()).toBe(true);
	} finally {
		rmSync(home, { recursive: true, force: true });
	}
});

// --- misc ---------------------------------------------------------------

test("timeout comes from the env override", () => {
	process.env.OMP_Z0INT_COGNITION_TIMEOUT_MS = "1234";
	expect(cognitionTimeoutMs()).toBe(1234);
	delete process.env.OMP_Z0INT_COGNITION_TIMEOUT_MS;
	__resetLocalCognitionForTest();
	expect(cognitionTimeoutMs()).toBe(4000);
});

test("resolveAvailableTools prefers ctx.getTools and always includes the observed tool", () => {
	const names = resolveAvailableTools(
		{},
		{ getTools: () => [{ name: "bash" }, { name: "read" }] },
		"glob",
	);
	expect(new Set(names)).toEqual(new Set(["bash", "read", "glob"]));
});

test("buildShadowPayload is self-contained and schema-shaped", () => {
	const payload = buildShadowPayload(toolCall("grep", { pattern: "x" }), ["grep"]);
	expect(payload.op).toBe("cognition_shadow");
	expect(typeof payload.trace_id).toBe("string");
	expect((payload.trace_id as string).length).toBeGreaterThan(8);
	expect(payload.granted_capabilities).toEqual(["grep"]);
	expect(payload.satisfied).toEqual([]);
	expect(payload.objective).toBeNull();
});

test("registers z0int-cognition-status and the command never throws", async () => {
	const home = mkdtempSync(join(tmpdir(), "local-cognition-status-"));
	process.env.Z0INT_HOME = home;
	__resetLocalCognitionForTest();
	try {
		const { pi } = installed();
		const command = pi.commands.get("z0int-cognition-status");
		expect(command).toBeDefined();

		const ctx = fakeCtx();
		await command?.handler("5", ctx);
		expect(ctx.notifications).toHaveLength(1);
		expect(ctx.notifications[0].message).toContain("no shadow rows yet");
	} finally {
		rmSync(home, { recursive: true, force: true });
	}
});


// --- correlated, classified observations --------------------------------

function observingPi() {
	const pi = makePi();
	const entries: Array<{ customType: string; data: Record<string, unknown> }> = [];
	const messages: unknown[] = [];
	Object.assign(pi, {
		appendEntry: (customType: string, data: Record<string, unknown>) => entries.push({ customType, data }),
		sendMessage: (message: unknown) => messages.push(message),
	});
	return { pi, entries, messages };
}

function sessionCtx(sessionId = "omp-session-7") {
	return fakeCtx({ sessionManager: { getSessionId: () => sessionId } });
}

function respondWith(response: unknown | (() => Promise<unknown>)) {
	globalHolder.__omp_z0int_bridge_transport__ = {
		kind: "z0int-bridge",
		generation: 3,
		buildId: "build-x",
		request: async (body: Record<string, unknown>) => {
			sent.push(body);
			return typeof response === "function" ? (response as () => Promise<unknown>)() : response;
		},
	};
}

const RAN = { label: "a", backend: "vllm", model: "m-a", abstained: false, invalid_call: false, latency_ms: 41 };

test("an observation carries the live OMP session and tool-call identities, not an environment guess", async () => {
	process.env.OMP_SESSION_ID = "stale-env-session";
	const { pi } = observingPi();
	respondWith({ ok: true, trace_id: "t", legal_ids: ["read"], shadow: [] });
	localCognition(pi as never);
	pi.handlers.get("tool_call")?.({ type: "tool_call", toolCallId: "call-42", toolName: "read", input: {} }, sessionCtx());
	await flush();
	expect(sent[0].session_id).toBe("omp-session-7");
	expect((sent[0].facts as Record<string, unknown>).tool_call_id).toBe("call-42");
	delete process.env.OMP_SESSION_ID;
});

test("worker responses are classified by what actually ran", () => {
	const cases: Array<[unknown, string, string[]]> = [
		[{ ok: true, legal_ids: ["read"], shadow: [{ ...RAN, selected_action: "read" }] }, "slm_executed", ["selected"]],
		[{ ok: true, legal_ids: ["read"], shadow: [{ ...RAN, selected_action: null, abstained: true }] }, "slm_executed", ["abstained"]],
		[{ ok: true, legal_ids: ["read"], shadow: [{ ...RAN, selected_action: null, abstained: true, invalid_call: true, attempted_action: "rm" }] }, "slm_executed", ["invalid_call"]],
		[{ ok: true, legal_ids: ["read"], shadow: [{ label: "a", backend: null, model: "m-a", abstained: true, error: "shadow_timeout" }] }, "timeout", ["timeout"]],
		[{ ok: true, legal_ids: ["read"], shadow: [{ label: "a", backend: null, model: "m-a", abstained: true, error: "ConnectionError: refused" }] }, "unavailable", ["unavailable"]],
		[{ ok: true, legal_ids: ["read"], shadow: [] }, "compiler_only", []],
		[{ ok: false, reason: "compile_failed: X" }, "error", []],
		[
			{ ok: true, legal_ids: ["read"], shadow: [
				{ ...RAN, selected_action: "read" },
				{ label: "b", backend: null, model: "m-b", abstained: true, error: "shadow_timeout" },
			] },
			"slm_executed", ["selected", "timeout"],
		],
	];
	for (const [response, outcome, statuses] of cases) {
		const classified = classifyShadowResponse(response, "read");
		expect(classified.outcome).toBe(outcome);
		expect(classified.models.map(model => model.status)).toEqual(statuses);
	}
	// A model that never ran is never reported as having abstained.
	const down = classifyShadowResponse({ ok: true, shadow: [{ label: "a", backend: null, abstained: true, error: "unavailable" }] }, "read");
	expect(down.models[0].status).toBe("unavailable");
	expect(down.models[0].ran).toBe(false);
	const agreed = classifyShadowResponse({ ok: true, shadow: [{ ...RAN, selected_action: "read" }, { ...RAN, label: "b", selected_action: "grep" }] }, "read");
	expect(agreed.models.map(model => model.agrees_with_observed)).toEqual([true, false]);
});

test("each observation is appended to the OMP session once, correlated, and nothing is sent to the model", async () => {
	const { pi, entries, messages } = observingPi();
	respondWith({ ok: true, trace_id: "ignored", legal_ids: ["read", "grep"], shadow: [{ ...RAN, selected_action: "grep" }] });
	localCognition(pi as never);
	const result = pi.handlers.get("tool_call")?.({ type: "tool_call", toolCallId: "call-9", toolName: "read", input: {} }, sessionCtx("s-1"));
	expect(result).toBeUndefined();
	await flush();
	expect(entries).toHaveLength(1);
	expect(entries[0].customType).toBe("z0int-cognition-shadow");
	const data = entries[0].data;
	expect(data.session_id).toBe("s-1");
	expect(data.tool_call_id).toBe("call-9");
	expect(data.tool).toBe("read");
	expect(data.trace_id).toBe(sent[0].trace_id);
	expect(data.outcome).toBe("slm_executed");
	expect(data.legal_count).toBe(2);
	expect(data.worker).toEqual({ kind: "z0int-bridge", generation: 3, build_id: "build-x" });
	expect((data.models as Array<Record<string, unknown>>)[0]).toMatchObject({ label: "a", status: "selected", selected_action: "grep", agrees_with_observed: false, latency_ms: 41 });
	// The tool input and the compiled state stay out of the session entry.
	expect(JSON.stringify(data)).not.toContain("input");
	expect(messages).toHaveLength(0);
});

test("a transport timeout, a failed send and a missing transport are recorded as such, never as success", async () => {
	process.env.OMP_Z0INT_COGNITION_TIMEOUT_MS = "20";
	process.env.OMP_Z0INT_COGNITION_BRIDGE_FALLBACK = "0";
	const { pi, entries } = observingPi();
	localCognition(pi as never);
	const fire = (id: string) => pi.handlers.get("tool_call")?.({ type: "tool_call", toolCallId: id, toolName: "read", input: {} }, sessionCtx());

	respondWith(() => new Promise(resolve => setTimeout(() => resolve({ ok: true, shadow: [] }), 200)));
	fire("slow");
	await flush(80);
	respondWith(() => Promise.reject(new Error("worker gone")));
	fire("broken");
	await flush();
	delete globalHolder.__omp_z0int_bridge_transport__;
	fire("alone");
	await flush();

	expect(entries.map(entry => [entry.data.tool_call_id, entry.data.outcome])).toEqual([
		["slow", "timeout"], ["broken", "error"], ["alone", "no_transport"],
	]);
	expect(statusRing.map(row => row.ok)).toEqual([false, false, false]);
});

test("coverage counts every observed tool call against the observations that settled", async () => {
	const { pi } = observingPi();
	respondWith({ ok: true, legal_ids: ["read"], shadow: [] });
	localCognition(pi as never);
	for (const id of ["a", "b", "c"]) {
		pi.handlers.get("tool_call")?.({ type: "tool_call", toolCallId: id, toolName: "read", input: {} }, sessionCtx());
	}
	expect(coverage().tool_calls).toBe(3);
	await flush();
	expect(coverage()).toMatchObject({ tool_calls: 3, settled: 3, by_outcome: { compiler_only: 3 } });
});

test("a session without appendEntry still observes and never throws", async () => {
	const pi = makePi();
	respondWith({ ok: true, legal_ids: ["read"], shadow: [] });
	localCognition(pi as never);
	expect(pi.handlers.get("tool_call")?.(toolCall(), sessionCtx())).toBeUndefined();
	await flush();
	expect(statusRing.at(-1)?.outcome).toBe("compiler_only");
});

test("the status command reports coverage by outcome", async () => {
	const { pi } = observingPi();
	respondWith({ ok: true, legal_ids: ["read"], shadow: [{ label: "a", backend: null, abstained: true, error: "unavailable" }] });
	localCognition(pi as never);
	pi.handlers.get("tool_call")?.(toolCall(), sessionCtx());
	await flush();
	const ctx = sessionCtx();
	await pi.commands.get("z0int-cognition-status")?.handler("", ctx);
	expect(ctx.notifications[0].message).toContain('observed 1 tool calls, 1 settled {"unavailable":1}');
});

test("a result that arrives after the session changed is not written into the new session", async () => {
	const { pi, entries } = observingPi();
	let current = "session-a";
	const ctx = fakeCtx({ sessionManager: { getSessionId: () => current } });
	respondWith(() => new Promise(resolve => setTimeout(() => resolve({ ok: true, legal_ids: ["read"], shadow: [] }), 40)));
	localCognition(pi as never);
	pi.handlers.get("tool_call")?.({ type: "tool_call", toolCallId: "late", toolName: "read", input: {} }, ctx);
	current = "session-b"; // the user switched sessions before the worker answered
	await flush(120);
	expect(entries).toHaveLength(0);
	expect(statusRing.at(-1)).toMatchObject({ outcome: "compiler_only", session_id: "session-a", recorded: "session_changed" });
	// The same observation in an unchanged session is recorded.
	pi.handlers.get("tool_call")?.({ type: "tool_call", toolCallId: "on-time", toolName: "read", input: {} }, ctx);
	await flush(120);
	expect(entries.map(entry => [entry.data.session_id, entry.data.tool_call_id])).toEqual([["session-b", "on-time"]]);
	expect(statusRing.at(-1)?.recorded).toBe("session_entry");
});
