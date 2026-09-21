/**
 * z0int local cognition — OMP **shadow-only** dogfood lane (oh-my-pi#83).
 *
 * For every `tool_call` OMP is about to run, this extension asks the resident
 * z0int bridge worker to:
 *
 *   1. compile the deterministic legal action set from the observed tool call
 *      plus the tool names available in the turn, and
 *   2. run the configured local models over **that set only**, recording what
 *      they would have chosen.
 *
 * Safety boundary (do not weaken):
 *
 *   - the `tool_call` handler is synchronous and always returns `undefined`;
 *     it never blocks, revises input, or approves/denies anything;
 *   - sending is fire-and-forget with a hard timeout; a timeout or error is
 *     swallowed and recorded, never surfaced as a tool failure;
 *   - no tool, permission, approval, retry, cancellation or provider/model is
 *     ever changed by this extension.
 *
 * It is deliberately self-contained (no imports from OMP packages) so it can be
 * exercised with plain `bun test`.
 */
import { randomUUID } from "node:crypto";
import { existsSync, openSync, readFileSync, readSync, closeSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const DEFAULT_TIMEOUT_MS = 4000;
const DEFAULT_MODELS = ["nemotron_orchestrator_8b", "functiongemma_270m"];
const DEFAULT_AUTHORITY = ["read"];
const DEFAULT_TOOL_NAMES = ["bash", "read", "write", "edit", "grep", "glob"];
const MAX_STATE_CHARS = 2000;
const STATUS_RING = 100;

/** Tool names whose call has real filesystem/process side effects. */
const WRITE_TOOLS = new Set([
	"bash",
	"write",
	"edit",
	"multiedit",
	"apply_patch",
	"notebook_edit",
	"write_file",
	"create_file",
]);

type Jsonish = Record<string, unknown>;

type ToolCallLike = {
	type?: string;
	toolCallId?: unknown;
	toolName?: unknown;
	input?: unknown;
};

type ExtensionAPI = {
	setLabel?: (label: string) => void;
	on?: (event: string, handler: (event: unknown, ctx: unknown) => unknown) => void;
	registerCommand?: (
		name: string,
		definition: {
			description?: string;
			handler: (args: string, ctx: unknown) => Promise<void> | void;
		},
	) => void;
	[key: string]: unknown;
};

type BridgeTransport = {
	kind?: string;
	generation?: number;
	buildId?: string;
	instanceId?: string;
	request: (body: Jsonish, timeoutMs?: number) => Promise<Jsonish>;
	warm?: (backend: string) => Promise<Jsonish>;
};

type StatusRow = {
	ts: number;
	ok: boolean;
	via?: string;
	tool?: string;
	trace_id?: unknown;
	error?: string;
};

// --- settings / env -----------------------------------------------------

let settingsCache: Jsonish | null = null;

function z0intHome(): string {
	return process.env.Z0INT_HOME || join(homedir(), ".z0int");
}

function settingsPath(): string {
	return join(z0intHome(), "config", "cognition.json");
}

function receiptPath(): string {
	return join(z0intHome(), "shadow", "cognition-shadow.jsonl");
}

/** Load `~/.z0int/config/cognition.json` once; never throws. */
export function loadCognitionSettings(): Jsonish {
	if (settingsCache) return settingsCache;
	let loaded: Jsonish = {};
	try {
		const path = settingsPath();
		if (existsSync(path)) {
			const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
			if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
				loaded = parsed as Jsonish;
			}
		}
	} catch {
		loaded = {};
	}
	settingsCache = loaded;
	return loaded;
}

/** Test hook: forget the cached settings file. */
export function __resetLocalCognitionForTest(): void {
	settingsCache = null;
	statusRing.length = 0;
}

function parseBool(raw: unknown): boolean | null {
	if (typeof raw === "boolean") return raw;
	if (typeof raw !== "string") return null;
	const value = raw.trim().toLowerCase();
	if (["0", "false", "off", "no", "disable", "disabled"].includes(value)) return false;
	if (["1", "true", "on", "yes", "enable", "enabled"].includes(value)) return true;
	return null;
}

function splitList(raw: string): string[] {
	return raw
		.split(",")
		.map(part => part.trim())
		.filter(Boolean);
}

function nested(settings: Jsonish, key: string): unknown {
	const cognition = settings.cognition;
	if (cognition && typeof cognition === "object" && !Array.isArray(cognition)) {
		const value = (cognition as Jsonish)[key];
		if (value !== undefined) return value;
	}
	return settings[key];
}

/**
 * Kill-switch: env wins, settings file next, default on.
 * `0`/`false`/`off` (and friends) disable.
 */
export function shadowEnabled(): boolean {
	const env = parseBool(process.env.OMP_Z0INT_COGNITION_SHADOW);
	if (env !== null) return env;
	const setting = parseBool(nested(loadCognitionSettings(), "shadow"));
	if (setting !== null) return setting;
	return true;
}

export function cognitionTimeoutMs(): number {
	const fromEnv = Number(process.env.OMP_Z0INT_COGNITION_TIMEOUT_MS);
	if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
	const fromSettings = nested(loadCognitionSettings(), "timeout_ms");
	if (typeof fromSettings === "number" && Number.isFinite(fromSettings) && fromSettings > 0) {
		return fromSettings;
	}
	return DEFAULT_TIMEOUT_MS;
}

export function shadowModels(): string[] {
	const raw = process.env.OMP_Z0INT_COGNITION_MODELS;
	if (raw && raw.trim()) return splitList(raw);
	const fromSettings = nested(loadCognitionSettings(), "models");
	if (Array.isArray(fromSettings)) {
		const models = fromSettings.filter((m): m is string => typeof m === "string" && m.length > 0);
		if (models.length) return models;
	}
	return [...DEFAULT_MODELS];
}

export function authorityList(): string[] {
	const raw = process.env.OMP_Z0INT_COGNITION_AUTHORITY;
	if (raw && raw.trim()) return splitList(raw);
	const fromSettings = nested(loadCognitionSettings(), "authority");
	if (Array.isArray(fromSettings)) {
		const authority = fromSettings.filter((a): a is string => typeof a === "string" && a.length > 0);
		if (authority.length) return authority;
	}
	return [...DEFAULT_AUTHORITY];
}

// --- payload ------------------------------------------------------------

function safeJson(value: unknown): string {
	try {
		return JSON.stringify(value) ?? "";
	} catch {
		return String(value);
	}
}

function riskFor(toolName: string): string {
	return WRITE_TOOLS.has(toolName) ? "write" : "read";
}

function safeCall(target: unknown, method: string): unknown {
	try {
		if (!target || typeof target !== "object") return undefined;
		const fn = (target as Jsonish)[method];
		return typeof fn === "function" ? (fn as () => unknown).call(target) : undefined;
	} catch {
		return undefined;
	}
}

function extractToolNames(value: unknown): string[] {
	if (!value) return [];
	if (Array.isArray(value)) {
		return value
			.map(item => {
				if (typeof item === "string") return item;
				if (item && typeof item === "object") {
					const record = item as Jsonish;
					const name = record.name ?? record.id;
					return typeof name === "string" ? name : "";
				}
				return "";
			})
			.filter(Boolean);
	}
	if (value instanceof Map) return [...value.keys()].map(String);
	if (typeof value === "object") return Object.keys(value as Jsonish);
	return [];
}

/**
 * The set of tool names available this turn.
 *
 * OMP does not hand the `tool_call` event a tool list, so we look for one on
 * the injected extension API / handler context, then fall back to the names
 * observed in this session, then to a small builtin default. The observed tool
 * is always included so its own action is a candidate.
 */
export function resolveAvailableTools(
	pi: unknown,
	ctx: unknown,
	observed: string,
	seen: Iterable<string> = [],
): string[] {
	const env = process.env.OMP_Z0INT_COGNITION_TOOLS;
	let names: string[] = env && env.trim() ? splitList(env) : [];
	if (names.length === 0) {
		const sources = [
			safeCall(ctx, "getTools"),
			(ctx as Jsonish | undefined)?.tools,
			safeCall(pi, "getTools"),
			(pi as Jsonish | undefined)?.tools,
			safeCall((pi as Jsonish | undefined)?.pi, "getTools"),
			safeCall((pi as Jsonish | undefined)?.pi, "getAllRegisteredTools"),
		];
		for (const source of sources) {
			const extracted = extractToolNames(source);
			if (extracted.length) {
				names = extracted;
				break;
			}
		}
	}
	if (names.length === 0) names = [...seen];
	if (names.length === 0) names = [...DEFAULT_TOOL_NAMES];
	const set = new Set(names.filter(Boolean));
	if (observed) set.add(observed);
	return [...set];
}

function buildActions(toolNames: string[]): Jsonish[] {
	return toolNames.map(name => ({
		action_id: name,
		kind: "tool",
		description: `Call the OMP tool ${name}`,
		tool: name,
		family: "omp.tool",
		risk_class: riskFor(name),
		cost_units: 1,
		arguments_schema: { type: "object", additionalProperties: true },
	}));
}

export function buildShadowPayload(event: ToolCallLike, toolNames: string[]): Jsonish {
	const toolName = typeof event?.toolName === "string" ? event.toolName : "";
	const input = event?.input && typeof event.input === "object" ? event.input : {};
	return {
		op: "cognition_shadow",
		trace_id: randomUUID().replaceAll("-", ""),
		session_id: process.env.OMP_SESSION_ID ?? null,
		state: `OMP tool_call: ${toolName}\ninput: ${safeJson(input).slice(0, MAX_STATE_CHARS)}`,
		actions: buildActions(toolNames),
		granted_capabilities: toolNames,
		authority: authorityList(),
		budget_units: 8,
		satisfied: [],
		facts: {
			tool_name: toolName,
			tool_call_id: typeof event?.toolCallId === "string" ? event.toolCallId : "",
		},
		objective: null,
		risk_class: riskFor(toolName),
		shadows: shadowModels(),
	};
}

// --- transport ----------------------------------------------------------

function getTransport(): BridgeTransport | null {
	const holder = globalThis as { __omp_z0int_bridge_transport__?: BridgeTransport };
	const transport = holder.__omp_z0int_bridge_transport__;
	return transport && typeof transport.request === "function" ? transport : null;
}

let bridgeModulePromise: Promise<Jsonish | null> | null = null;

function bridgeModule(): Promise<Jsonish | null> {
	if (!bridgeModulePromise) {
		// Lazy, variable specifier: the sibling z0int-bridge extension is optional.
		const specifier = "../z0int-bridge/index.ts";
		bridgeModulePromise = import(/* @vite-ignore */ specifier)
			.then(module => (module as Jsonish) ?? null)
			.catch(() => null);
	}
	return bridgeModulePromise;
}

export function withTimeout<T>(value: Promise<T> | T, ms: number): Promise<T> {
	return new Promise<T>((resolve, reject) => {
		const timer = setTimeout(() => reject(new Error(`cognition_shadow timeout after ${ms}ms`)), ms);
		(timer as { unref?: () => void }).unref?.();
		Promise.resolve(value).then(
			resolved => {
				clearTimeout(timer);
				resolve(resolved);
			},
			error => {
				clearTimeout(timer);
				reject(error);
			},
		);
	});
}

const statusRing: StatusRow[] = [];

function recordStatus(row: StatusRow): void {
	statusRing.push(row);
	if (statusRing.length > STATUS_RING) statusRing.shift();
}

/** Fire-and-forget. Resolves only after the send settles, but callers never await. */
export async function sendShadow(payload: Jsonish, timeoutMs: number): Promise<StatusRow> {
	const started = Date.now() / 1000;
	const tool = typeof payload.facts === "object" && payload.facts !== null
		? String((payload.facts as Jsonish).tool_name ?? "")
		: "";
	const base: StatusRow = { ts: started, ok: false, trace_id: payload.trace_id, tool };
	try {
		const transport = getTransport();
		if (transport) {
			await withTimeout(transport.request(payload, timeoutMs), timeoutMs);
			const row = { ...base, ok: true, via: "transport" };
			recordStatus(row);
			return row;
		}
		if (parseBool(process.env.OMP_Z0INT_COGNITION_BRIDGE_FALLBACK) === false) {
			const row = { ...base, error: "no_transport" };
			recordStatus(row);
			return row;
		}
		const module = await bridgeModule();
		const ensureWorker = module?.ensureWorker;
		const request = module?.request;
		if (typeof ensureWorker === "function" && typeof request === "function") {
			const handle = await (ensureWorker as () => Promise<unknown>)();
			await withTimeout(
				(request as (h: unknown, body: Jsonish, ms: number) => Promise<Jsonish>)(
					handle,
					payload,
					timeoutMs,
				),
				timeoutMs,
			);
			const row = { ...base, ok: true, via: "z0int-bridge-import" };
			recordStatus(row);
			return row;
		}
		const row = { ...base, error: "no_transport" };
		recordStatus(row);
		return row;
	} catch (error) {
		// Swallowed on purpose: a shadow timeout is not a tool failure.
		const row = { ...base, error: error instanceof Error ? error.message : String(error) };
		recordStatus(row);
		return row;
	}
}

// --- status command -----------------------------------------------------

function parseCount(args: unknown, fallback: number): number {
	const text = String(args ?? "").trim();
	const match = text.match(/\d+/);
	if (!match) return fallback;
	const value = Number(match[0]);
	return Number.isFinite(value) && value > 0 ? Math.min(value, 200) : fallback;
}

function readReceiptTail(limit: number): Jsonish[] {
	const path = receiptPath();
	try {
		if (!existsSync(path)) return [];
		const size = statSync(path).size;
		if (size === 0) return [];
		const windowBytes = 256 * 1024;
		const start = Math.max(0, size - windowBytes);
		const length = size - start;
		const fd = openSync(path, "r");
		try {
			const buffer = Buffer.alloc(length);
			readSync(fd, buffer, 0, length, start);
			const lines = buffer
				.toString("utf8")
				.split("\n")
				.map(line => line.trim())
				.filter(Boolean);
			const tail = start > 0 ? lines.slice(1) : lines;
			return tail
				.slice(-limit)
				.map(line => {
					try {
						const parsed: unknown = JSON.parse(line);
						return parsed && typeof parsed === "object" ? (parsed as Jsonish) : { raw: line };
					} catch {
						return { raw: line };
					}
				});
		} finally {
			closeSync(fd);
		}
	} catch {
		return [];
	}
}

function formatReceiptRow(row: Jsonish): string {
	if (typeof row.raw === "string") return row.raw;
	const shadow = Array.isArray(row.shadow) ? (row.shadow as Jsonish[]) : [];
	const decisions = shadow
		.map(entry => {
			const label = String(entry.label ?? "?");
			if (entry.invalid_call === true) {
				return `${label}:invalid(${String(entry.attempted_action ?? "?")})`;
			}
			if (entry.selected_action) return `${label}:${String(entry.selected_action)}`;
			return `${label}:abstain`;
		})
		.join(" ");
	const legal = Array.isArray(row.legal_ids) ? (row.legal_ids as unknown[]).length : 0;
	return `trace=${String(row.trace_id ?? "?")} legal=${legal} shadow=[${decisions}] executed=${String(
		row.executed_action ?? "null",
	)}`;
}

// --- extension ----------------------------------------------------------

export default function localCognition(pi: ExtensionAPI): void {
	pi.setLabel?.("z0int local cognition (shadow-only)");
	const seenTools = new Set<string>();

	pi.on?.("session_start", () => {
		seenTools.clear();
	});

	pi.on?.("tool_call", (event, ctx) => {
		try {
			if (!shadowEnabled()) return undefined;
			const observed =
				event && typeof event === "object" && typeof (event as ToolCallLike).toolName === "string"
					? String((event as ToolCallLike).toolName)
					: "";
			if (!observed) return undefined;
			seenTools.add(observed);
			const toolNames = resolveAvailableTools(pi, ctx, observed, seenTools);
			const payload = buildShadowPayload(event as ToolCallLike, toolNames);
			void sendShadow(payload, cognitionTimeoutMs());
		} catch {
			/* observe-only: never surface, never block */
		}
		// Never return a ToolCallEventResult: no block, no input revision.
		return undefined;
	});

	pi.registerCommand?.("z0int-cognition-status", {
		description: "Show the last N local-cognition shadow rows (observe-only)",
		async handler(args: string, ctx: unknown) {
			const limit = parseCount(args, 10);
			let rows = readReceiptTail(limit);
			let source = "receipts";
			if (rows.length === 0) {
				rows = statusRing.slice(-limit) as unknown as Jsonish[];
				source = "memory";
			}
			const text =
				rows.length === 0
					? "z0int cognition: no shadow rows yet"
					: [
							`z0int cognition (${source}, last ${rows.length}):`,
							...rows.map(row =>
								source === "receipts" ? formatReceiptRow(row) : JSON.stringify(row),
							),
						].join("\n");
			const notify = (ctx as { ui?: { notify?: (message: string, level?: string) => void } })?.ui
				?.notify;
			notify?.(text, "info");
		},
	});
}

export {
	DEFAULT_AUTHORITY,
	DEFAULT_MODELS,
	DEFAULT_TIMEOUT_MS,
	DEFAULT_TOOL_NAMES,
	receiptPath,
	statusRing,
	z0intHome,
};
