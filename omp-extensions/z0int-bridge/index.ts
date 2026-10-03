/**
 * z0int bridge — immutable OMP transport shim (v2).
 *
 * This file is intentionally dumb:
 *   OMP events → stable serialization → trace lifecycle → resident worker IPC
 *
 * Experimental logic lives in `python -u -m z0int.bridge.worker` and is
 * hot-swapped on `/reload-plugins` without restarting the OMP session.
 *
 * ONE restart is required after installing this shim. After that, bridge
 * Python edits take effect via /reload-plugins (transactional generation swap).
 *
 * execution remains log_only until host consume is authorized.
 *
 * Capture (oh-my-pi#109): turn_open carries the task cwd, the harness, the subagent parent and the model so
 * the worker can write the z0int#62 record family; every capture handler returns undefined and fails open
 * (a worker that cannot start is a counted drop in $Z0INT_HOME/state/<harness>/drops.jsonl).
 * `registerBridgeCapture` is the capture alone (the OMO/senpi entry `omo.ts` uses only that); the default
 * export adds the canonical z0int-intelligence routing and the bridge commands for OMP.
 */
import registerIntelligence from "../z0int-intelligence/index.ts";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { createInterface, type Interface } from "node:readline";
import { appendFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const BRIDGE_PROTOCOL = "z0int.bridge.v2";
// Read per use, not at import, so the host env at spawn time decides (and tests can point it elsewhere).
function z0Home(env: NodeJS.ProcessEnv = process.env): string {
	return env.Z0INT_HOME || join(homedir(), ".z0int");
}
const Z0_ROOT =
	process.env.Z0INT_ROOT ||
	// Prefer the tree that owns this extension when possible.
	process.env.Z0INT_BRIDGE_ROOT ||
	fileURLToPath(new URL("../../", import.meta.url));

/** Z0INT_PYTHON, else the owning checkout's .venv, else the z0int runtime, else python3 (salvaged, 815c680). */
export function resolvePython(env: NodeJS.ProcessEnv = process.env, root = Z0_ROOT, z0 = z0Home(env)): string {
	if (env.Z0INT_PYTHON) return env.Z0INT_PYTHON;
	for (const candidate of [join(root, ".venv", "bin", "python"), join(z0, "bin", "python")]) {
		if (existsSync(candidate)) return candidate;
	}
	return "python3";
}

const WORKER_TIMEOUT_MS = Number(process.env.Z0INT_BRIDGE_TIMEOUT_MS || 45_000);
const HANDSHAKE_TIMEOUT_MS = 20_000;

type Jsonish = Record<string, unknown>;

type WorkerHello = {
	ok: boolean;
	protocol?: string;
	generation?: number;
	instance_id?: string;
	build_id?: string;
	error?: string;
};

type Pending = {
	id: string;
	resolve: (value: Jsonish) => void;
	reject: (error: Error) => void;
	timer: ReturnType<typeof setTimeout>;
};

type ActiveTurn = {
	traceId: string;
	sessionId: string;
	harness: string;
	openedGeneration: number;
	turnsSeen: number;
	turnsWithProviderUsage: number;
	inputTokens: number;
	outputTokens: number;
	provider: string | null;
	model: string | null;
};

type WorkerHandle = {
	child: ChildProcess;
	lines: Interface;
	generation: number;
	instanceId: string;
	buildId: string;
	pending: Map<string, Pending>;
};

let current: WorkerHandle | null = null;
let starting: Promise<WorkerHandle> | null = null;
let generation = 0;
let reloadPromise: Promise<Jsonish> | null = null;
// One open turn per session: an in-process subagent's turn never closes (or replaces) its parent's.
const activeTurns = new Map<string, ActiveTurn>();

// --- capture accounting (content-free) -----------------------------------------------------------------
const CAPTURE_EVENTS = ["input", "before_agent_start", "turn_end", "agent_end"] as const;
const counters: Record<string, number> = {};
const harnessStatus: Record<string, { status: "SUPPORTED" | "UNSUPPORTED"; missing: string[] }> = {};

export function captureStatus(): { counters: Record<string, number>; harnesses: typeof harnessStatus } {
	return { counters: { ...counters }, harnesses: { ...harnessStatus } };
}

/** Count a lost capture and persist it in the shared drop file (same row shape as harness_capture). */
function recordDrop(harness: string, reason: string): void {
	counters[reason] = (counters[reason] ?? 0) + 1;
	try {
		const dir = join(z0Home(), "state", harness);
		mkdirSync(dir, { recursive: true });
		const row = {
			schema: `z0int.${harness.replaceAll("-", "_")}.drop.v0`,
			harness,
			kind: "opportunity_record",
			reason,
			count: 1,
			recorded_at: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
		};
		appendFileSync(join(dir, "drops.jsonl"), JSON.stringify(row) + "\n", "utf8");
	} catch {
		/* a lost drop marker never affects the host */
	}
}

function ensureDir(path: string): void {
	mkdirSync(path, { recursive: true });
}

export function publishCurrent(h: Pick<WorkerHandle, "generation" | "instanceId" | "buildId">): void {
	const runtime = join(z0Home(), "runtime");
	ensureDir(runtime);
	const blob = {
		protocol: BRIDGE_PROTOCOL,
		generation: h.generation,
		instance_id: h.instanceId,
		build_id: h.buildId,
		activated_at: Date.now() / 1000,
		omp_pid: process.pid,
	};
	writeFileSync(join(runtime, "bridge-current.json"), JSON.stringify(blob, null, 2) + "\n", "utf8");
}

function failAll(h: WorkerHandle, err: Error): void {
	for (const p of h.pending.values()) {
		clearTimeout(p.timer);
		p.reject(err);
	}
	h.pending.clear();
}

function spawnWorker(nextGen: number): Promise<WorkerHandle> {
	return new Promise((resolve, reject) => {
		const env = {
			...process.env,
			Z0INT_ROOT: Z0_ROOT,
			PYTHONPATH: join(Z0_ROOT, "src") + (process.env.PYTHONPATH ? `:${process.env.PYTHONPATH}` : ""),
			Z0INT_BRIDGE_GENERATION: String(nextGen),
		};
		let child: ChildProcess;
		try {
			child = spawn(resolvePython(), ["-u", "-m", "z0int.bridge.worker", "--generation", String(nextGen)], {
				cwd: Z0_ROOT,
				env,
				stdio: ["pipe", "pipe", "pipe"],
			});
		} catch (e) {
			reject(e instanceof Error ? e : new Error(String(e)));
			return;
		}
		// A worker that never started (missing interpreter) or died must not raise into the host.
		child.stdin?.on("error", () => {
			/* fail-open: the pending requests are failed by exit/error below */
		});
		const pending = new Map<string, Pending>();
		const lines = createInterface({ input: child.stdout! });
		const handle: WorkerHandle = {
			child,
			lines,
			generation: nextGen,
			instanceId: "pending",
			buildId: "pending",
			pending,
		};

		lines.on("line", line => {
			let msg: Jsonish;
			try {
				msg = JSON.parse(line) as Jsonish;
			} catch (e) {
				return;
			}
			const id = typeof msg.id === "string" ? msg.id : "";
			const p = id ? pending.get(id) : undefined;
			if (!p) return;
			pending.delete(id);
			clearTimeout(p.timer);
			p.resolve(msg);
		});

		child.stderr?.on("data", () => {
			/* keep resident quiet unless debugging */
		});

		child.on("exit", code => {
			failAll(handle, new Error(`z0int bridge worker exited ${code}`));
			if (current === handle) current = null;
		});

		child.on("error", err => {
			failAll(handle, err instanceof Error ? err : new Error(String(err)));
			reject(err instanceof Error ? err : new Error(String(err)));
		});

		// handshake
		const helloId = randomUUID();
		const timer = setTimeout(() => {
			try {
				child.kill("SIGKILL");
			} catch {
				/* */
			}
			reject(new Error("bridge worker hello timeout"));
		}, HANDSHAKE_TIMEOUT_MS);

		pending.set(helloId, {
			id: helloId,
			timer,
			resolve: msg => {
				if (msg.ok !== true) {
					reject(new Error(String(msg.error || "hello_failed")));
					return;
				}
				if (msg.protocol !== BRIDGE_PROTOCOL) {
					try {
						child.kill("SIGKILL");
					} catch {
						/* */
					}
					reject(new Error(`protocol_mismatch:${msg.protocol}`));
					return;
				}
				handle.instanceId = String(msg.instance_id || "unknown");
				handle.buildId = String(msg.build_id || "unknown");
				if (typeof msg.generation === "number") handle.generation = msg.generation;
				resolve(handle);
			},
			reject: err => reject(err),
		});

		try {
			child.stdin!.write(
				JSON.stringify({ id: helloId, op: "hello", bridge_generation: nextGen }) + "\n",
			);
		} catch (e) {
			clearTimeout(timer);
			pending.delete(helloId);
			reject(e instanceof Error ? e : new Error(String(e)));
		}
	});
}

async function request(h: WorkerHandle, body: Jsonish, timeoutMs = WORKER_TIMEOUT_MS): Promise<Jsonish> {
	const id = randomUUID();
	const { promise, resolve, reject } = Promise.withResolvers<Jsonish>();
	const timer = setTimeout(() => {
		h.pending.delete(id);
		reject(new Error(`bridge worker timeout op=${body.op}`));
	}, timeoutMs);
	h.pending.set(id, { id, resolve, reject, timer });
	const line = JSON.stringify({ id, bridge_generation: h.generation, ...body }) + "\n";
	if (!h.child.stdin || h.child.killed) {
		h.pending.delete(id);
		clearTimeout(timer);
		return Promise.reject(new Error("bridge worker stdin closed"));
	}
	h.child.stdin.write(line);
	return promise;
}

async function selfCheck(h: WorkerHandle): Promise<Jsonish> {
	return request(h, { op: "self_check" }, HANDSHAKE_TIMEOUT_MS);
}

async function drainAndStop(h: WorkerHandle): Promise<void> {
	try {
		await request(h, { op: "drain" }, 3000);
	} catch {
		/* */
	}
	try {
		await request(h, { op: "shutdown" }, 3000);
	} catch {
		/* */
	}
	try {
		h.child.kill("SIGTERM");
	} catch {
		/* */
	}
	setTimeout(() => {
		try {
			if (h.child.exitCode === null) h.child.kill("SIGKILL");
		} catch {
			/* */
		}
	}, 2000);
}

async function ensureWorker(): Promise<WorkerHandle> {
	if (current && current.child.exitCode === null) return current;
	// One start at a time: the eager start and a first turn must not spawn two workers.
	if (starting) return starting;
	starting = (async () => {
		const nextGen = generation + 1 || 1;
		const h = await spawnWorker(nextGen);
		const check = await selfCheck(h);
		if (check.ok !== true) {
			await drainAndStop(h);
			throw new Error(`self_check failed: ${JSON.stringify(check.errors || check.error)}`);
		}
		current = h;
		generation = h.generation;
		publishCurrent(h);
		publishShadowTransport(h);
		maybePrewarmDecider(h);
		return h;
	})().finally(() => {
		starting = null;
	});
	return starting;
}

/** Stop the resident worker (tests, and hosts that unload the extension). */
async function stopWorker(): Promise<void> {
	const h = current;
	current = null;
	publishShadowTransport(null);
	if (h) await drainAndStop(h);
}

async function reload(reason: string): Promise<Jsonish> {
	if (reloadPromise) return reloadPromise;
	reloadPromise = (async () => {
		if (activeTurns.size) {
			return {
				ok: false,
				error: "turn_in_flight",
				deferred: true,
				generation,
				reason,
			};
		}
		const nextGen = generation + 1;
		let next: WorkerHandle;
		try {
			next = await spawnWorker(nextGen);
			const check = await selfCheck(next);
			if (check.ok !== true) {
				await drainAndStop(next);
				return {
					ok: false,
					error: "self_check_failed",
					detail: check,
					generation,
					reason,
				};
			}
		} catch (e) {
			return {
				ok: false,
				error: e instanceof Error ? e.message : String(e),
				generation,
				reason,
			};
		}
		const previous = current;
		current = next;
		generation = next.generation;
		publishCurrent(next);
		publishShadowTransport(next);
		maybePrewarmDecider(next);
		if (previous) void drainAndStop(previous);
		return {
			ok: true,
			generation: next.generation,
			build_id: next.buildId,
			instance_id: next.instanceId,
			reason,
		};
	})().finally(() => {
		reloadPromise = null;
	});
	return reloadPromise;
}

/** The host session id: OMP and senpi expose it through ctx.sessionManager.getSessionId() (salvaged, 815c680). */
export function resolveSessionId(ctx: unknown): string {
	try {
		if (ctx && typeof ctx === "object") {
			const sm = (ctx as { sessionManager?: { getSessionId?: () => unknown } }).sessionManager;
			const id = typeof sm?.getSessionId === "function" ? sm.getSessionId() : undefined;
			if (typeof id === "string" && id) return id;
			const s = (ctx as { sessionId?: unknown }).sessionId;
			if (typeof s === "string" && s) return s;
		}
	} catch {
		/* fall through */
	}
	return process.env.OMP_SESSION_ID || `omp-${process.pid}`;
}

export type TurnInfo = { cwd: string | null; agentKind: string | null; parentId: string | null; model: string | null };

/** What the capture needs from the handler ctx: the task cwd, the agent identity and the model. Never throws. */
export function turnContext(ctx: unknown): TurnInfo {
	const c = (ctx && typeof ctx === "object" ? ctx : {}) as Record<string, unknown>;
	const read = (key: string): unknown => {
		try {
			return c[key];
		} catch {
			return undefined;
		}
	};
	const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
	const agent = (read("agent") ?? {}) as { kind?: unknown; parentId?: unknown };
	const model = (read("model") ?? {}) as { provider?: unknown; id?: unknown };
	const id = text(model?.id);
	return {
		cwd: text(read("cwd")),
		agentKind: text(agent?.kind),
		parentId: text(agent?.parentId),
		model: id && text(model?.provider) ? `${model.provider}/${id}` : id,
	};
}

type FrameTurn = { traceId: string; sessionId: string; pid: number };

export function turnOpenFrame(turn: FrameTurn, prompt: string, info: Partial<TurnInfo>, harness: string): Jsonish {
	return {
		op: "turn_open",
		trace_id: turn.traceId,
		session_id: turn.sessionId,
		omp_pid: turn.pid,
		payload: {
			prompt,
			session_id: turn.sessionId,
			omp_pid: turn.pid,
			harness,
			cwd: info.cwd ?? null,
			parent_id: info.parentId ?? null,
			agent_kind: info.agentKind ?? null,
			model: info.model ?? null,
		},
	};
}

export function turnCloseFrame(turn: FrameTurn, op: string, fields: Jsonish, harness: string): Jsonish {
	return {
		op,
		trace_id: turn.traceId,
		session_id: turn.sessionId,
		omp_pid: turn.pid,
		payload: { trace_id: turn.traceId, session_id: turn.sessionId, omp_pid: turn.pid, harness, ...fields },
	};
}

function estimateMeasuredFromMessages(messages: unknown[]): {
	input_tokens: number;
	output_tokens: number;
	measured: number;
	provider: string | null;
	model: string | null;
	usage_source: "provider_usage" | "char_proxy";
} {
	// Prefer provider usage when the close event exposes it. A turn_end may only
	// carry the final assistant message, so this remains partial coverage unless
	// the host later supplies a true turn/session aggregate.
	let provider: string | null = null;
	let model: string | null = null;
	let usageIn: number | null = null;
	let usageOut: number | null = null;
	for (let i = messages.length - 1; i >= 0; i--) {
		const m = messages[i];
		if (!m || typeof m !== "object") continue;
		const o = m as Record<string, unknown>;
		const role = String(o.role || "");
		if (role && role !== "assistant") continue;
		if (typeof o.provider === "string") provider = o.provider;
		if (typeof o.model === "string") model = o.model;
		const usage = o.usage;
		if (usage && typeof usage === "object") {
			const u = usage as Record<string, unknown>;
			const inn = u.input ?? u.input_tokens;
			const out = u.output ?? u.output_tokens;
			if (typeof inn === "number") usageIn = inn;
			if (typeof out === "number") usageOut = out;
			if (usageIn != null || usageOut != null) break;
		}
		const nested = o.message;
		if (nested && typeof nested === "object") {
			const nm = nested as Record<string, unknown>;
			if (typeof nm.provider === "string") provider = nm.provider;
			if (typeof nm.model === "string") model = nm.model;
			const nestedUsage = nm.usage;
			if (nestedUsage && typeof nestedUsage === "object") {
				const u = nestedUsage as Record<string, unknown>;
				const inn = u.input ?? u.input_tokens;
				const out = u.output ?? u.output_tokens;
				if (typeof inn === "number") usageIn = inn;
				if (typeof out === "number") usageOut = out;
				if (usageIn != null || usageOut != null) break;
			}
		}
	}
	if (usageIn != null || usageOut != null) {
		const input_tokens = Math.max(0, Math.round(usageIn || 0));
		const output_tokens = Math.max(0, Math.round(usageOut || 0));
		return {
			input_tokens,
			output_tokens,
			measured: input_tokens + output_tokens,
			provider,
			model,
			usage_source: "provider_usage",
		};
	}

	// Fallback estimate only. Never label this measured coverage complete.
	let userChars = 0;
	let assistantChars = 0;
	const walkContent = (content: unknown): string => {
		if (typeof content === "string") return content;
		if (!Array.isArray(content)) return "";
		return content
			.map(item =>
				item && typeof item === "object" && "text" in item
					? String((item as { text?: unknown }).text || "")
					: "",
			)
			.join("");
	};
	for (const m of messages) {
		if (!m || typeof m !== "object") continue;
		const o = m as Record<string, unknown>;
		const role = String(o.role || "");
		const text = walkContent(o.content);
		if (role === "user") userChars += text.length;
		else if (role === "assistant") assistantChars += text.length;
		else if (!role) assistantChars += text.length;
		if (typeof o.provider === "string" && !provider) provider = o.provider;
		if (typeof o.model === "string" && !model) model = o.model;
	}
	const input_tokens = Math.max(0, Math.round(userChars / 4));
	const output_tokens = Math.max(1, Math.round(assistantChars / 4));
	return {
		input_tokens,
		output_tokens,
		measured: input_tokens + output_tokens,
		provider,
		model,
		usage_source: "char_proxy",
	};
}

function providerUsageFromMessage(message: unknown): {
	input_tokens: number;
	output_tokens: number;
	provider: string | null;
	model: string | null;
} | null {
	if (!message || typeof message !== "object") return null;
	const o = message as Record<string, unknown>;
	const role = String(o.role || "");
	if (role && role !== "assistant") return null;
	let provider = typeof o.provider === "string" ? o.provider : null;
	let model = typeof o.model === "string" ? o.model : null;
	let usage = o.usage;
	if ((!usage || typeof usage !== "object") && o.message && typeof o.message === "object") {
		const nested = o.message as Record<string, unknown>;
		if (typeof nested.provider === "string") provider = nested.provider;
		if (typeof nested.model === "string") model = nested.model;
		usage = nested.usage;
	}
	if (!usage || typeof usage !== "object") return null;
	const u = usage as Record<string, unknown>;
	const rawIn = u.input ?? u.input_tokens;
	const rawOut = u.output ?? u.output_tokens;
	if (typeof rawIn !== "number" && typeof rawOut !== "number") return null;
	return {
		input_tokens: Math.max(0, Math.round(typeof rawIn === "number" ? rawIn : 0)),
		output_tokens: Math.max(0, Math.round(typeof rawOut === "number" ? rawOut : 0)),
		provider,
		model,
	};
}

function accumulateTurnUsage(turn: ActiveTurn, message: unknown): void {
	turn.turnsSeen += 1;
	const usage = providerUsageFromMessage(message);
	if (!usage) return;
	turn.turnsWithProviderUsage += 1;
	turn.inputTokens += usage.input_tokens;
	turn.outputTokens += usage.output_tokens;
	if (usage.provider) turn.provider = usage.provider;
	if (usage.model) turn.model = usage.model;
}

type ShadowTransport = {
	kind: "z0int-bridge";
	request: (body: Jsonish, timeoutMs?: number) => Promise<Jsonish>;
	warm: (backend: string) => Promise<Jsonish>;
	generation?: number;
	buildId?: string;
	instanceId?: string;
};

function publishShadowTransport(h: WorkerHandle | null): void {
	const g = globalThis as { __omp_z0int_bridge_transport__?: ShadowTransport };
	if (!h) {
		delete g.__omp_z0int_bridge_transport__;
		return;
	}
	g.__omp_z0int_bridge_transport__ = {
		kind: "z0int-bridge",
		generation: h.generation,
		buildId: h.buildId,
		instanceId: h.instanceId,
		request: (body, timeoutMs) => request(h, body, timeoutMs),
		warm: (backend) =>
			request(h, { op: "decision_warm", payload: { backend } }, 5_000),
	};
}

function maybePrewarmDecider(h: WorkerHandle): void {
	const env = String(process.env.OMP_SHADOW_WORKER_NEEDED || "").toLowerCase();
	const enabled = env === "1" || env === "true" || env === "on";
	if (!enabled) return;
	// Non-blocking — never await during OMP startup / handshake.
	void request(h, { op: "decision_warm", payload: { backend: "decider_2b" } }, 5_000).catch(() => {
		/* fail-open */
	});
}

function eventField(event: unknown, key: string): unknown {
	return event && typeof event === "object" && key in event ? (event as Record<string, unknown>)[key] : undefined;
}

async function closeActive(sessionId: string, messages: unknown[], source: string, completed: boolean): Promise<void> {
	const turn = activeTurns.get(sessionId);
	if (!turn) return;
	activeTurns.delete(sessionId);

	let inputTokens = turn.inputTokens;
	let outputTokens = turn.outputTokens;
	let provider = turn.provider;
	let model = turn.model;
	let measurementState: "complete" | "partial";
	let stateReason: string;

	if (turn.turnsSeen > 0) {
		measurementState =
			turn.turnsWithProviderUsage === turn.turnsSeen ? "complete" : "partial";
		stateReason =
			measurementState === "complete"
				? "all_turns_provider_usage"
				: "missing_turn_provider_usage";
	} else {
		const fallback = estimateMeasuredFromMessages(messages);
		inputTokens = fallback.input_tokens;
		outputTokens = fallback.output_tokens;
		provider = fallback.provider;
		model = fallback.model;
		measurementState = "partial";
		stateReason =
			fallback.usage_source === "provider_usage"
				? "agent_end_last_message_provider_usage"
				: "agent_end_char_count_proxy";
	}

	try {
		const h = await ensureWorker();
		await request(
			h,
			turnCloseFrame(
				{ traceId: turn.traceId, sessionId: turn.sessionId, pid: process.pid },
				source === "bridge_agent_end" ? "agent_end" : "turn_close",
				{
					measured: inputTokens + outputTokens,
					input_tokens: inputTokens,
					output_tokens: outputTokens,
					execution_completed: completed,
					// verified_success stays null until async join
					source,
					provider,
					model,
					measurement_state: measurementState,
					state_reason: stateReason,
					turns_seen: turn.turnsSeen,
					turns_with_provider_usage: turn.turnsWithProviderUsage,
					opened_generation: turn.openedGeneration,
					close_generation: h.generation,
				},
				turn.harness,
			),
		);
	} catch {
		/* fail-open */
	}
}

/**
 * The capture handlers alone, for any pi-family host (OMP, OMO/senpi). Every handler returns undefined and
 * never throws. A host whose API lacks a needed event is recorded UNSUPPORTED (status + a counted drop row)
 * instead of half-capturing.
 */
export function registerBridgeCapture(
	pi: ExtensionAPI,
	opts: { harness?: string } = {},
): { status: "SUPPORTED" | "UNSUPPORTED"; missing: string[] } {
	const harness = opts.harness || "omp";
	const handlers: Record<(typeof CAPTURE_EVENTS)[number], (event: unknown, ctx: unknown) => Promise<undefined>> = {
		// /reload-plugins interception: hot-swap worker, then let the host continue.
		input: async (event, ctx) => {
			try {
				const text = String(eventField(event, "text") ?? "").trim();
				if (text !== "/reload-plugins") return undefined;
				// Do NOT return { handled: true } — OMP must still run builtin reload.
				const notify = (ctx as { ui?: { notify?: (m: string, l?: string) => void } } | undefined)?.ui?.notify;
				try {
					const result = await reload("omp_reload_plugins");
					const ok = result.ok === true;
					notify?.(
						ok
							? `z0int bridge → generation ${result.generation} build=${result.build_id}`
							: `z0int bridge reload failed (${result.error}); keeping generation ${generation}`,
						ok ? "info" : "warning",
					);
				} catch (e) {
					notify?.(`z0int bridge reload error: ${e}`, "warning");
				}
			} catch {
				counters.handler_errors = (counters.handler_errors ?? 0) + 1;
			}
			return undefined;
		},
		before_agent_start: async (event, ctx) => {
			try {
				const prompt = String(eventField(event, "prompt") ?? "").trim();
				if (!prompt || prompt.startsWith("/")) return undefined;
				const sessionId = resolveSessionId(ctx);
				const info = turnContext(ctx);
				let h: WorkerHandle;
				try {
					h = await ensureWorker();
				} catch {
					recordDrop(harness, "worker_unavailable");
					return undefined;
				}
				const turn: ActiveTurn = {
					traceId: randomUUID().replaceAll("-", ""),
					sessionId,
					harness,
					openedGeneration: h.generation,
					turnsSeen: 0,
					turnsWithProviderUsage: 0,
					inputTokens: 0,
					outputTokens: 0,
					provider: null,
					model: null,
				};
				activeTurns.set(sessionId, turn);
				try {
					await request(h, turnOpenFrame({ traceId: turn.traceId, sessionId, pid: process.pid }, prompt, info, harness));
				} catch {
					if (activeTurns.get(sessionId)?.traceId === turn.traceId) activeTurns.delete(sessionId);
					recordDrop(harness, "worker_unavailable");
				}
			} catch {
				counters.handler_errors = (counters.handler_errors ?? 0) + 1;
			}
			return undefined;
		},
		turn_end: async (event, ctx) => {
			try {
				const turn = activeTurns.get(resolveSessionId(ctx));
				if (turn) accumulateTurnUsage(turn, eventField(event, "message") ?? null);
			} catch {
				counters.handler_errors = (counters.handler_errors ?? 0) + 1;
			}
			return undefined;
		},
		agent_end: async (event, ctx) => {
			try {
				// OMP says willContinue, senpi says willRetry: either way the turn is not over yet.
				if (eventField(event, "willContinue") === true || eventField(event, "willRetry") === true) return undefined;
				const messages = eventField(event, "messages");
				await closeActive(
					resolveSessionId(ctx),
					Array.isArray(messages) ? messages : [],
					"bridge_agent_end",
					eventField(event, "aborted") !== true,
				);
			} catch {
				counters.handler_errors = (counters.handler_errors ?? 0) + 1;
			}
			return undefined;
		},
	};
	const missing: string[] = [];
	const on = (pi as { on?: unknown } | undefined)?.on;
	for (const [event, handler] of Object.entries(handlers)) {
		try {
			if (typeof on !== "function") throw new Error("no on()");
			(on as (e: string, h: unknown) => void).call(pi, event, handler);
		} catch {
			missing.push(event);
		}
	}
	const status = missing.length ? "UNSUPPORTED" : "SUPPORTED";
	harnessStatus[harness] = { status, missing };
	if (status === "UNSUPPORTED") {
		recordDrop(harness, "unsupported_api");
	} else {
		// Eager start so the first turn is warm.
		void ensureWorker().catch(() => {
			/* fail-open; next turn retries */
		});
	}
	return harnessStatus[harness];
}

export default function z0intBridge(pi: ExtensionAPI) {
	registerIntelligence(pi);
	pi.setLabel("z0int bridge v2 + canonical intelligence");
	registerBridgeCapture(pi, { harness: "omp" });

	pi.registerCommand("z0int-bridge-status", {
		description: "Show resident z0int bridge worker generation/build",
		async handler(_args, ctx) {
			try {
				const h = await ensureWorker();
				const st = await request(h, { op: "status" }, 5000);
				ctx.ui.notify(
					`z0int bridge: gen=${st.generation} build=${st.build_id} id=${st.instance_id} protocol=${st.protocol}`,
					"info",
				);
			} catch (e) {
				ctx.ui.notify(String(e), "error");
			}
		},
	});

	pi.registerCommand("z0int-bridge-reload", {
		description: "Hot-reload resident z0int bridge worker (same as /reload-plugins intercept)",
		async handler(_args, ctx) {
			const result = await reload("z0int-bridge-reload-cmd");
			ctx.ui.notify(
				result.ok
					? `z0int bridge → generation ${result.generation} build=${result.build_id}`
					: `reload failed: ${result.error}`,
				result.ok ? "info" : "error",
			);
		},
	});

	pi.registerCommand("z0int-close", {
		description: "Close active z0int turn with optional --verified",
		async handler(args, ctx) {
			const turn = activeTurns.get(resolveSessionId(ctx));
			if (!turn) {
				ctx.ui.notify("z0int close: no active turn in this session", "warning");
				return;
			}
			const parts = String(args || "").trim().split(/\s+/).filter(Boolean);
			let measured: number | undefined;
			let verified = false;
			for (let i = 0; i < parts.length; i++) {
				if (parts[i] === "--measured" && parts[i + 1]) measured = Number(parts[++i]);
				if (parts[i] === "--verified") verified = true;
			}
			try {
				const h = await ensureWorker();
				const closed = await request(
					h,
					turnCloseFrame(
						{ traceId: turn.traceId, sessionId: turn.sessionId, pid: process.pid },
						"turn_close",
						{
							measured: measured ?? null,
							execution_completed: true,
							verified_success: verified ? true : null,
							source: verified ? "bridge_verified" : "z0int-close-cmd",
							verification_source: verified ? "operator" : null,
						},
						turn.harness,
					),
				);
				if (activeTurns.get(turn.sessionId)?.traceId === turn.traceId) activeTurns.delete(turn.sessionId);
				ctx.ui.notify(
					`z0int close: ${JSON.stringify({ ok: closed.ok !== false, trace: turn.traceId, gen: h.generation })}`,
					closed.ok === false ? "error" : "info",
				);
			} catch (e) {
				ctx.ui.notify(String(e), "error");
			}
		},
	});
}

export { reload, ensureWorker, stopWorker, request, BRIDGE_PROTOCOL, publishShadowTransport };
