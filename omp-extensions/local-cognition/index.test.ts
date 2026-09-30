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

test("payload keeps read-only authority and the example shadow models by default", async () => {
	process.env.OMP_Z0INT_COGNITION_TOOLS = "read,write";
	const { handler } = installed();
	handler(toolCall("write", { path: "x" }), fakeCtx());
	await flush();

	expect(sent[0].authority).toEqual(["read"]);
	expect(sent[0].shadows).toEqual(["nemotron_orchestrator_8b", "functiongemma_270m"]);
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
