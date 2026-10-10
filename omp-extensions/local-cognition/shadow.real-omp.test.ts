/**
 * Live qualification of the shadow lane in an actual OMP session.
 *
 * Real: OMP's session and extension runtime, this extension, the z0int bridge
 * worker, and a locally served model. Scripted: only the parent model, so no
 * provider or approval is involved.
 *
 * Skipped unless Z0INT_COGNITION_LIVE_SLM names a served model, so the default
 * `bun test` stays hermetic. Needs the coherent OMP fixture on the module path.
 */
import { expect, test } from "bun:test";
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import localCognition, { __resetLocalCognitionForTest, coverage } from "./index.ts";

const LIVE_MODEL = process.env.Z0INT_COGNITION_LIVE_SLM ?? "";
const live = LIVE_MODEL ? test : test.skip;

type Entry = { type?: string; customType?: string; data?: Record<string, unknown>; message?: { role?: string; content?: unknown } };

live("every tool call in a real OMP session yields one correlated observation from the served model", async () => {
	// Imported here so a tree without the OMP packages can still load this file and skip.
	const { createMockModel, registerMockApi } = await import("@oh-my-pi/pi-ai/providers/mock");
	const { createAgentSession, SessionManager, Settings } = await import("@oh-my-pi/pi-coding-agent");
	const { initializeExtensions } = await import("@oh-my-pi/pi-coding-agent/modes/runtime-init");
	const root = await mkdtemp(join(tmpdir(), "z0int-cognition-live-"));
	const cwd = join(root, "project");
	const home = join(root, "z0int-home");
	await mkdir(join(home, "config"), { recursive: true });
	await mkdir(cwd, { recursive: true });
	await writeFile(join(cwd, "notes.txt"), "alpha\nbeta\n", "utf8");
	// The worker reads the operator's real serving map; its receipts go to the temporary home.
	await cp(join(process.env.Z0INT_LIVE_SERVING_HOME ?? join(homedir(), ".z0int"), "config", "serving.json"), join(home, "config", "serving.json"));
	const saved = { ...process.env };
	Object.assign(process.env, {
		Z0INT_HOME: home,
		OMP_Z0INT_COGNITION_SHADOW: "1",
		OMP_Z0INT_COGNITION_MODELS: LIVE_MODEL,
		OMP_Z0INT_COGNITION_TIMEOUT_MS: "30000",
	});
	delete process.env.OMP_SESSION_ID;
	delete (globalThis as { __omp_z0int_bridge_transport__?: unknown }).__omp_z0int_bridge_transport__;
	__resetLocalCognitionForTest();
	registerMockApi("z0int-cognition-live-shadow");
	const model = createMockModel({
		id: "z0int-cognition-live-parent",
		provider: "ollama",
		responses: [
			{ content: [{ type: "toolCall", name: "read", arguments: { path: join(cwd, "notes.txt") } }] },
			{ content: [{ type: "toolCall", name: "grep", arguments: { pattern: "alpha", path: cwd } }] },
			{ content: [{ type: "toolCall", name: "read", arguments: { path: join(cwd, "notes.txt") } }] },
			{ content: ["done"] },
		],
	});
	const agentDir = join(cwd, ".agent-state");
	await mkdir(agentDir, { recursive: true });
	const settings = await Settings.loadIsolated({ cwd, agentDir, inMemory: true });
	const manager = SessionManager.inMemory(cwd);
	const extension = (pi: unknown) => localCognition(pi as never);
	const { session } = await createAgentSession({
		cwd, agentDir, settings, sessionManager: manager, model,
		autoApprove: true, hasUI: true, cacheWarming: false, disableExtensionDiscovery: true,
		enableMCP: false, enableLsp: false, skipPythonPreflight: true, skills: [], rules: [], contextFiles: [],
		extensions: [extension],
	});
	// Bind the extension runtime the way OMP's own non-interactive modes do, so
	// pi.appendEntry writes to the real session instead of throwing uninitialized.
	const runtimeErrors: string[] = [];
	await initializeExtensions(session, {
		reportSendError: (_action: string, error: Error) => runtimeErrors.push(error.message),
		reportRuntimeError: (error: { error?: unknown }) => runtimeErrors.push(String(error?.error ?? error)),
	} as never);
	try {
		await session.prompt("Look at the notes.");
		for (let i = 0; i < 400 && coverage().settled < coverage().tool_calls; i += 1) {
			await new Promise(resolve => setTimeout(resolve, 100));
		}
		const entries = manager.getEntries() as Entry[];
		const toolCallIds = entries
			.filter(entry => entry.type === "message" && entry.message?.role === "assistant")
			.flatMap(entry => (Array.isArray(entry.message?.content) ? (entry.message?.content as Array<Record<string, unknown>>) : []))
			.filter(block => block.type === "toolCall")
			.map(block => String(block.id));
		const observations = entries.filter(entry => entry.customType === "z0int-cognition-shadow").map(entry => entry.data ?? {});
		const seen = coverage();
		const receiptText = await readFile(join(home, "shadow", "cognition-shadow.jsonl"), "utf8").catch(() => "");
		const receiptRows = receiptText.split("\n").filter(Boolean).map(line => JSON.parse(line) as Record<string, unknown>);
		const report = {
			model: LIVE_MODEL,
			session_id: manager.getSessionId(),
			tool_calls: toolCallIds.length,
			coverage: seen,
			observations: observations.map(row => ({
				tool_call_id: row.tool_call_id, tool: row.tool, outcome: row.outcome, via: row.via, round_trip_ms: row.round_trip_ms,
				models: row.models, trace_id: row.trace_id, worker: row.worker, error: row.error,
			})),
			receipt_trace_ids: receiptRows.map(row => row.trace_id),
			receipt_session_ids: [...new Set(receiptRows.map(row => row.session_id))],
			model_facing_custom_messages: entries.filter(entry => entry.type === "custom_message").length,
		};
		if (process.env.Z0INT_COGNITION_LIVE_REPORT) {
			await writeFile(process.env.Z0INT_COGNITION_LIVE_REPORT, JSON.stringify(report, null, 1), "utf8");
		}

		// Coverage: one settled observation per tool call OMP actually ran.
		expect(toolCallIds.length).toBe(3);
		expect(seen.tool_calls).toBe(3);
		expect(seen.settled).toBe(3);
		expect(observations).toHaveLength(3);
		// Identity: the live session and the exact tool calls, in order.
		expect(observations.map(row => row.session_id)).toEqual([manager.getSessionId(), manager.getSessionId(), manager.getSessionId()]);
		expect(observations.map(row => row.tool_call_id).sort()).toEqual([...toolCallIds].sort());
		// Execution: the served model answered, it was not a compiler-only pass.
		for (const row of observations) {
			expect(row.outcome).toBe("slm_executed");
			const models = row.models as Array<Record<string, unknown>>;
			expect(models).toHaveLength(1);
			expect(models[0].ran).toBe(true);
			expect(typeof models[0].latency_ms).toBe("number");
		}
		// Linkage: each session entry's trace is the worker's receipt for the same session.
		expect(observations.map(row => row.trace_id).sort()).toEqual([...report.receipt_trace_ids].sort());
		expect(report.receipt_session_ids).toEqual([manager.getSessionId()]);
		// Passive: nothing was added to what the parent model reads.
		expect(report.model_facing_custom_messages).toBe(0);
		expect(runtimeErrors).toEqual([]);
	} finally {
		await session.dispose();
		await manager.close();
		for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
		Object.assign(process.env, saved);
		await rm(root, { recursive: true, force: true });
	}
}, 120_000);
