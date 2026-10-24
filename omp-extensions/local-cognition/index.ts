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
import { createHash, randomUUID } from "node:crypto";
import { existsSync, openSync, readFileSync, readSync, closeSync, statSync } from "node:fs";
import { promises as fs } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const DEFAULT_TIMEOUT_MS = 4000;
// A single small candidate on the existing farm router; no second runtime.
const DEFAULT_MODELS = ["qwen3_06b"];
const DEFAULT_AUTHORITY = ["read"];
const DEFAULT_TOOL_NAMES = ["bash", "read", "write", "edit", "grep", "glob"];
// How many earlier tool names of the session the shadow model is shown.
const RECENT_TOOLS = 8;
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

/** What the lane learned from one tool call. `ok` is true only when the worker answered. */
type ShadowOutcome = "slm_executed" | "compiler_only" | "unavailable" | "timeout" | "error" | "no_transport";

/** What one requested model did. A model that never ran is never an abstention. */
type ModelStatus = "selected" | "abstained" | "invalid_call" | "unavailable" | "timeout";

type ModelObservation = {
	label: string;
	model: string | null;
	backend: string | null;
	status: ModelStatus;
	ran: boolean;
	selected_action: string | null;
	agrees_with_observed: boolean | null;
	latency_ms: number | null;
};

type ShadowClassification = { outcome: ShadowOutcome; models: ModelObservation[]; legal_count: number | null };

type StatusRow = {
	ts: number;
	ok: boolean;
	outcome?: ShadowOutcome;
	via?: string;
	tool?: string;
	trace_id?: unknown;
	error?: string;
	session_id?: string | null;
	/** Where the observation ended up: in the session, or nowhere because the session had changed. */
	recorded?: "session_entry" | "session_changed" | "no_session_api";
};

type ObservationIdentity = {
	sessionId: string | null;
	toolCallId: string;
	tool: string;
	/** Reads the session OMP is in now, so a late result is not written into a different one. */
	currentSessionId?: () => string | null;
};

// --- settings / env -----------------------------------------------------

let settingsCache: Jsonish | null = null;

function z0intHome(): string {
	return process.env.Z0INT_HOME || join(homedir(), ".z0int");
}

type DecisionPolicy = {
	version: string;
	sha256: string;
	reason?: string;
	families: Record<string, Jsonish>;
	synthetic?: boolean;
	serving: Jsonish[];
};
let policyCache: { path: string; mtime: number; sha: string; policy: DecisionPolicy } | null = null;
const healthCache = new Map<string, { ts: number; healthy: boolean | null }>();
const healthPending = new Set<string>();

/**
 * Sort keys and retain JSON numeric types before normalizing Python's canonical
 * float spelling (notably 1.0, -0.0 and zero-padded scientific exponents).
 * Integer tokens use BigInt so metadata cannot lose precision in JS.
 */
function canonicalPolicyBody(raw: string): string {
	const tokens = raw.match(/"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\]:,]/g) ?? [];
	let index = 0;
	const compareKeys = (a: string, b: string): number => {
		const left = Array.from(a, c => c.codePointAt(0)!);
		const right = Array.from(b, c => c.codePointAt(0)!);
		for (let i = 0; i < Math.min(left.length, right.length); i++) {
			if (left[i] !== right[i]) return left[i] - right[i];
		}
		return left.length - right.length;
	};
	const parse = (root = false): string => {
		const token = tokens[index++];
		if (token === "{") {
			const rows = new Map<string, string>();
			while (tokens[index] !== "}") {
				const key = JSON.parse(tokens[index++]) as string;
				if (tokens[index++] !== ":") throw new Error("invalid_policy_json");
				const value = parse();
				if (!root || key !== "version") rows.set(key, value);
				if (tokens[index] !== ",") break;
				index++;
			}
			if (tokens[index++] !== "}") throw new Error("invalid_policy_json");
			return `{${[...rows.keys()].sort(compareKeys).map(k => `${JSON.stringify(k)}:${rows.get(k)}`).join(",")}}`;
		}
		if (token === "[") {
			const rows: string[] = [];
			while (tokens[index] !== "]") {
				rows.push(parse());
				if (tokens[index] !== ",") break;
				index++;
			}
			if (tokens[index++] !== "]") throw new Error("invalid_policy_json");
			return `[${rows.join(",")}]`;
		}
		if (/^-?\d/.test(token)) {
			if (!/[.eE]/.test(token)) return BigInt(token).toString();
			const value = Number(token);
			if (!Number.isFinite(value)) throw new Error("nonfinite_policy_number");
			if (Object.is(value, -0)) return "-0.0";
			if (value === 0) return "0.0";
			if (Math.abs(value) >= 1e-4 && Math.abs(value) < 1e16) {
				const decimal = value.toString();
				return decimal.includes(".") ? decimal : `${decimal}.0`;
			}
			const [mantissa, exponent] = value.toExponential().split("e");
			const power = Number(exponent);
			return `${mantissa}e${power < 0 ? "-" : "+"}${Math.abs(power).toString().padStart(2, "0")}`;
		}
		return token.startsWith('"') ? JSON.stringify(JSON.parse(token)) : token;
	};
	return parse(true);
}

function object(value: unknown): value is Jsonish {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}
function strings(value: unknown): boolean {
	return Array.isArray(value) && value.every(v => typeof v === "string" && v.length > 0);
}
function timestamp(value: unknown): boolean {
	return typeof value === "string" && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
}
const POLICY_STAGES = ["none", "shadow", "canary", "live"];

/** Independent, fail-open consumer of the same content-addressed policy as Python. */
export function loadDecisionPolicy(): DecisionPolicy {
	try {
		const path = join(z0intHome(), "policy", "decision-policy.json");
		const raw = readFileSync(path, "utf8");
		const sha = createHash("sha256").update(raw).digest("hex");
		const mtime = statSync(path).mtimeMs;
		if (policyCache?.path === path && policyCache.mtime === mtime && policyCache.sha === sha) return policyCache.policy;
		const body: unknown = JSON.parse(raw);
		if (!object(body) || body.schema !== "z0.decision_policy.v1" || typeof body.version !== "string"
			|| !timestamp(body.compiled_at) || typeof body.findings_head !== "string" || typeof body.compiler !== "string"
			|| !object(body.families) || !Array.isArray(body.serving) || !Array.isArray(body.changes)) throw new Error("invalid_schema");
		if (body.synthetic !== undefined && typeof body.synthetic !== "boolean") throw new Error("invalid_synthetic");
		for (const [key, rule] of Object.entries(body.families)) {
			if (!key || !object(rule) || !POLICY_STAGES.includes(String(rule.stage)) || !object(rule.arm)
				|| typeof rule.threshold !== "number" || !Number.isFinite(rule.threshold) || rule.threshold < 0 || rule.threshold > 1
				|| typeof rule.canary_share !== "number" || !Number.isFinite(rule.canary_share) || rule.canary_share < 0 || rule.canary_share > 1
				|| !["jev", "frontier"].includes(String(rule.abstain_to)) || !strings(rule.evidence)
				|| typeof rule.rule_id !== "string" || !rule.rule_id || !timestamp(rule.expires_at)
				|| (rule.demoted_reason != null && typeof rule.demoted_reason !== "string")) throw new Error("invalid_family");
			if (rule.synthetic !== undefined && typeof rule.synthetic !== "boolean") throw new Error("invalid_synthetic");
			if (rule.stage !== "none" && (typeof rule.arm.kind !== "string" || !rule.arm.kind)) throw new Error("invalid_arm");
			if (["kind", "model", "quant", "engine", "backend", "device"].some(k =>
				k in (rule.arm as Jsonish) && (typeof (rule.arm as Jsonish)[k] !== "string" || !(rule.arm as Jsonish)[k]))) throw new Error("invalid_arm");
			if (rule.family !== undefined && typeof rule.family !== "string") throw new Error("invalid_family");
			if (rule.scope !== undefined && (!object(rule.scope) || Object.entries(rule.scope).some(([k, v]) =>
				!["device", "model", "quant", "engine", "backend"].includes(k) || typeof v !== "string" || !v))) throw new Error("invalid_scope");
		}
		for (const endpoint of body.serving) {
			if (!object(endpoint) || ["device", "model", "engine", "backend", "url"].some(k =>
				typeof endpoint[k] !== "string" || !endpoint[k]) || !strings(endpoint.evidence)) throw new Error("invalid_serving");
		}
		for (const change of body.changes) {
			if (!object(change) || typeof change.rule_id !== "string" || !POLICY_STAGES.includes(String(change.from))
				|| !POLICY_STAGES.includes(String(change.to)) || !strings(change.because)) throw new Error("invalid_change");
		}
		const digest = createHash("sha256").update(canonicalPolicyBody(raw)).digest("hex");
		if (body.version !== digest) throw new Error("policy_hash_mismatch");
		const policy = { version: digest, sha256: digest, synthetic: body.synthetic === true, families: body.families as Record<string, Jsonish>, serving: body.serving as Jsonish[] };
		policyCache = { path, mtime, sha, policy };
		return policy;
	} catch (error) {
		return { version: "none", sha256: "none", reason: `policy_unavailable:${error instanceof Error ? error.message : String(error)}`, families: {}, serving: [] };
	}
}

function policyStage(rule: Jsonish | undefined): string {
	return rule && Date.parse(String(rule.expires_at)) > Date.now() ? String(rule.stage) : "none";
}

function servingHealth(endpoint: Jsonish | undefined): boolean | null {
	if (!endpoint || typeof endpoint.url !== "string") return null;
	const url = endpoint.url;
	// Keep the same sorted canonical five-field scope hash as Python.
	const scope = Object.fromEntries(["backend", "device", "engine", "model", "quant"].map(k => [k, endpoint[k] ?? null]));
	const digest = createHash("sha256").update(JSON.stringify(scope)).digest("hex");
	const key = `${url}#${digest}`;
	const path = join(z0intHome(), "policy", "serving-health.json");
	try {
		const shared: unknown = JSON.parse(readFileSync(path, "utf8"));
		const row = object(shared) && object(shared.endpoints) ? shared.endpoints[url] : undefined;
		if (object(shared) && shared.schema === "z0.serving_health.v1" && object(row)
			&& row.scope_sha256 === digest && timestamp(row.checked_at)) {
			const age = Date.now() - Date.parse(String(row.checked_at));
			if (age >= 0 && age <= 60_000 && ["healthy", "unhealthy", "unknown"].includes(String(row.status))) {
				return row.status === "healthy" && row.model === (endpoint.expected_model_id ?? endpoint.model) ? true : row.status === "unhealthy" ? false : null;
			}
		}
	} catch { /* missing/corrupt cache cannot authorize an offload */ }
	const cached = healthCache.get(key);
	if (cached && Date.now() - cached.ts >= 0 && Date.now() - cached.ts <= 60_000) return cached.healthy;
	if (!healthPending.has(key)) {
		healthPending.add(key);
		const base = url.replace(/\/$/, "").replace(/\/v1$/, "");
		const checked = (status: string, model: unknown = null, revision: unknown = null): Jsonish => ({
			scope_sha256: digest, status, checked_at: new Date().toISOString(),
			model: typeof model === "string" ? model : null, revision: typeof revision === "string" ? revision : null,
		});
		void fetch(`${base}/v1/models`, { signal: AbortSignal.timeout(1500) })
			.then(async response => {
				const body: unknown = await response.json();
				const rows = object(body) && Array.isArray(body.data) ? body.data.filter(object) : [];
				const match = rows.find(row => row.id === (endpoint.expected_model_id ?? endpoint.model));
				const observed = match ?? rows[0];
				return checked(response.ok && match ? "healthy" : "unhealthy", observed?.id, observed?.revision ?? observed?.model_revision);
			})
			.catch(() => checked("unknown"))
			.then(async entry => {
				healthCache.set(key, { ts: Date.parse(String(entry.checked_at)),
					healthy: entry.status === "healthy" ? true : entry.status === "unhealthy" ? false : null });
				let temporary: string | undefined;
				try {
					await fs.mkdir(join(z0intHome(), "policy"), { recursive: true });
					let endpoints: Jsonish = {};
					try {
						const previous: unknown = JSON.parse(await fs.readFile(path, "utf8"));
						if (object(previous) && previous.schema === "z0.serving_health.v1" && object(previous.endpoints)) endpoints = previous.endpoints;
					} catch { /* start a fresh cache */ }
					temporary = `${path}.${randomUUID()}.tmp`;
					await fs.writeFile(temporary, JSON.stringify({ schema: "z0.serving_health.v1", endpoints: { ...endpoints, [url]: entry } }), { mode: 0o600 });
					await fs.rename(temporary, path);
					temporary = undefined;
				} catch { /* a failed cache write cannot affect the tool call */ }
				finally {
					if (temporary) await fs.unlink(temporary).catch(() => undefined);
					healthPending.delete(key);
				}
			});
	}
	return null;
}

export function policyStamp(policy = loadDecisionPolicy(), family = "omp.tool"): Jsonish {
	const scope = nested(loadCognitionSettings(), "policy_scope");
	const candidates = Object.entries(policy.families).filter(([key, rule]) => {
		const narrowing = object(scope) ? scope : {};
		const arm = object(rule.arm) ? rule.arm : {};
		return (key === family || rule.family === family) && Object.entries(narrowing).every(([k, v]) => arm[k] === v);
	}).sort(([ak, a], [bk, b]) => Object.keys((b.scope as Jsonish) ?? {}).length - Object.keys((a.scope as Jsonish) ?? {}).length || bk.localeCompare(ak));
	const rule = candidates[0]?.[1];
	const stage = policyStage(rule);
	const arm = object(rule?.arm) ? rule.arm : {};
	const dimensions = ["device", "model", "quant", "engine", "backend"];
	const endpoint = dimensions.every(k => typeof arm[k] === "string") ? policy.serving.find(row => dimensions.every(k => row[k] === arm[k])) : undefined;
	const reason = policy.reason ?? (!rule ? "family_not_in_policy" : stage === "none" && rule.stage !== "none" ? "evidence_expired" : typeof rule.demoted_reason === "string" ? rule.demoted_reason : undefined);
	return { policy_version: policy.version, policy_sha256: policy.sha256, policy_family: candidates[0]?.[0] ?? family,
		rule_id: rule?.rule_id ?? "none", stage, family,
		synthetic: policy.synthetic === true || Object.values(policy.families).some(entry => entry.synthetic === true),
		serving_healthy: servingHealth(endpoint),
		...(object(scope) ? { policy_scope: scope } : {}), ...(reason ? { policy_reason: reason } : {}) };
}

function receiptPolicyFields(payload: Jsonish): Jsonish {
	const fields: Jsonish = {};
	for (const key of ["policy_version", "policy_sha256", "policy_family", "policy_scope", "rule_id", "stage", "family", "policy_reason", "serving_healthy", "synthetic"]) {
		if (payload[key] !== undefined) fields[key] = payload[key];
	}
	return fields;
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
	policyCache = null;
	healthCache.clear();
	statusRing.length = 0;
	counters.tool_calls = 0;
	counters.settled = 0;
	counters.by_outcome = {};
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

export function shadowModels(policy = loadDecisionPolicy()): string[] {
	const models = [...new Set(Object.values(policy.families)
		.filter(rule => POLICY_STAGES.indexOf(policyStage(rule)) >= 1)
		.map(rule => (rule.arm as Jsonish).model)
		.filter((model): model is string => typeof model === "string" && model.length > 0))];
	if (models.length) return models;
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
 * the injected extension API / handler context, then fall back to a small
 * builtin default. The list never depends on the call being observed or on
 * earlier calls: a tool that is in it only because it was called is the answer
 * the model is asked for. A call to a tool outside the list is therefore not a
 * legal choice, and the scorecard counts it as such.
 */
export function resolveAvailableTools(pi: unknown, ctx: unknown): string[] {
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
			// What OMP's extension API actually offers: the enabled tool names.
			safeCall(pi, "getActiveTools"),
		];
		for (const source of sources) {
			const extracted = extractToolNames(source);
			if (extracted.length) {
				names = extracted;
				break;
			}
		}
	}
	if (names.length === 0) names = [...DEFAULT_TOOL_NAMES];
	return [...new Set(names.filter(Boolean))].sort();
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

/** The session OMP is actually running, read from the handler context. */
export function liveSessionId(ctx: unknown): string | null {
	const manager = (ctx as { sessionManager?: unknown } | undefined)?.sessionManager;
	const id = safeCall(manager, "getSessionId");
	return typeof id === "string" && id.length > 0 ? id : null;
}

/**
 * `recent` is the session's tool names before this call, oldest first. The
 * model is asked to predict this call, so `state` is built from that history
 * alone: the observed tool name and its input stay in `facts`, which no model
 * is shown.
 */
export function buildShadowPayload(
	event: ToolCallLike,
	toolNames: string[],
	sessionId: string | null = null,
	recent: readonly string[] = [],
): Jsonish {
	const toolName = typeof event?.toolName === "string" ? event.toolName : "";
	// Authority and budget are sized to the tool list, so every tool the session
	// could call is a legal choice. They are not taken from the observed tool:
	// that would show the model whether the call was a write. Nothing is granted
	// by this; the worker fixes the selected and executed action to null.
	const risks = new Set(toolNames.map(riskFor));
	const policy = loadDecisionPolicy();
	return {
		...policyStamp(policy),
		op: "cognition_shadow",
		trace_id: randomUUID().replaceAll("-", ""),
		// An environment variable describes the process, not the session a
		// tool call belongs to; it is only a fallback when OMP offers no id.
		session_id: sessionId ?? process.env.OMP_SESSION_ID ?? null,
		state: `OMP session. Previous tool calls, oldest first: ${recent.join(", ") || "none"}`,
		recent_tools: [...recent],
		actions: buildActions(toolNames),
		granted_capabilities: toolNames,
		authority: [...new Set([...authorityList(), ...risks])],
		budget_units: Math.max(8, toolNames.length),
		satisfied: [],
		facts: {
			tool_name: toolName,
			tool_call_id: typeof event?.toolCallId === "string" ? event.toolCallId : "",
		},
		objective: null,
		risk_class: risks.has("write") ? "write" : "read",
		shadows: shadowModels(policy),
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
const counters: { tool_calls: number; settled: number; by_outcome: Record<string, number> } = {
	tool_calls: 0,
	settled: 0,
	by_outcome: {},
};

function recordStatus(row: StatusRow): void {
	statusRing.push(row);
	if (statusRing.length > STATUS_RING) statusRing.shift();
	counters.settled += 1;
	const outcome = row.outcome ?? "error";
	counters.by_outcome[outcome] = (counters.by_outcome[outcome] ?? 0) + 1;
}

/** Tool calls seen against observations that settled, by outcome. */
export function coverage(): { tool_calls: number; settled: number; by_outcome: Record<string, number> } {
	return { tool_calls: counters.tool_calls, settled: counters.settled, by_outcome: { ...counters.by_outcome } };
}

function text(value: unknown): string | null {
	return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Read a worker response for what each requested model actually did.
 *
 * The worker marks every row without a decision `abstained`, including models
 * that were never reached. Here a model has abstained only if its backend
 * answered; an unreached model is `unavailable` and one that ran out of time
 * is `timeout`. With no model rows at all the worker only compiled the legal
 * action set.
 */
export function classifyShadowResponse(response: unknown, observedTool: string): ShadowClassification {
	const body = response && typeof response === "object" ? (response as Jsonish) : {};
	if (body.ok !== true) return { outcome: "error", models: [], legal_count: null };
	const legal = Array.isArray(body.legal_ids) ? body.legal_ids.length : null;
	const rows = Array.isArray(body.shadow) ? (body.shadow as unknown[]) : [];
	const models: ModelObservation[] = rows
		.filter((row): row is Jsonish => !!row && typeof row === "object")
		.map(row => {
			const backend = text(row.backend);
			const failure = text(row.error) ?? (backend === null ? text(row.parse_error) : null);
			const ran = backend !== null && text(row.error) === null;
			const selected = ran ? text(row.selected_action) : null;
			let status: ModelStatus;
			if (!ran) status = failure === "shadow_timeout" ? "timeout" : "unavailable";
			else if (row.invalid_call === true) status = "invalid_call";
			else if (selected !== null) status = "selected";
			else status = "abstained";
			return {
				label: text(row.label) ?? "?",
				model: text(row.model),
				backend,
				status,
				ran,
				selected_action: status === "selected" ? selected : null,
				agrees_with_observed: status === "selected" ? selected === observedTool : null,
				latency_ms: ran && typeof row.latency_ms === "number" ? row.latency_ms : null,
			};
		});
	let outcome: ShadowOutcome;
	if (models.length === 0) outcome = "compiler_only";
	else if (models.some(model => model.ran)) outcome = "slm_executed";
	else if (models.every(model => model.status === "timeout")) outcome = "timeout";
	else outcome = "unavailable";
	return { outcome, models, legal_count: legal };
}

const ENTRY_TYPE = "z0int-cognition-shadow";

/**
 * Put the observation in the OMP session it belongs to, so OMP's own trace
 * can show it beside the tool call. A session entry is a record, not a
 * message: it is never part of what the model reads.
 */
function appendObservation(
	pi: ExtensionAPI | undefined,
	identity: ObservationIdentity,
	payload: Jsonish,
	classified: ShadowClassification,
	row: StatusRow,
	elapsedMs: number,
): StatusRow["recorded"] {
	try {
		const append = pi?.appendEntry;
		if (typeof append !== "function") return "no_session_api";
		// pi.appendEntry writes to whichever session is current. If the user
		// switched or restarted while the worker was answering, this observation
		// belongs to the old session and must not appear in the new one.
		if (identity.currentSessionId && identity.currentSessionId() !== identity.sessionId) return "session_changed";
		const transport = getTransport();
		(append as (customType: string, data: Jsonish) => void).call(pi, ENTRY_TYPE, {
			schema: "z0int.cognition_shadow_observation.v1",
			...receiptPolicyFields(payload),
			ts: row.ts,
			route: "shadow",
			tier: "shadow",
			session_id: identity.sessionId,
			tool_call_id: identity.toolCallId,
			tool: identity.tool,
			trace_id: payload.trace_id,
			outcome: classified.outcome,
			models: classified.models.map(model => ({ ...model, ...receiptPolicyFields(payload) })),
			legal_count: classified.legal_count,
			requested_models: Array.isArray(payload.shadows) ? payload.shadows : [],
			round_trip_ms: Math.round(elapsedMs),
			via: row.via ?? null,
			error: row.error ?? null,
			worker: transport
				? { kind: transport.kind ?? null, generation: transport.generation ?? null, build_id: transport.buildId ?? null }
				: null,
		});
		return "session_entry";
	} catch {
		/* observe-only: a session that cannot record must not affect the tool call */
		return "no_session_api";
	}
}

/** The share of the extension's timeout the worker may spend waiting on models. */
export function workerBudgetMs(timeoutMs: number): number {
	return Math.max(50, Math.floor(timeoutMs * 0.8));
}

/** Fire-and-forget. Resolves only after the send settles, but callers never await. */
export async function sendShadow(
	payload: Jsonish,
	timeoutMs: number,
	pi?: ExtensionAPI,
	identity?: ObservationIdentity,
): Promise<StatusRow> {
	const startedMs = Date.now();
	const synthetic = payload.synthetic === true;
	if (typeof payload.policy_version !== "string") Object.assign(payload, policyStamp());
	payload.synthetic = synthetic || payload.synthetic === true;
	// Give the worker a budget inside ours. Left to its own 30 s default it
	// keeps waiting on a slow model after this side has recorded a timeout, and
	// its receipt then says the model answered while the session entry says it
	// did not. The margin leaves time for the timed-out rows to come back.
	if (typeof payload.timeout_ms !== "number") payload.timeout_ms = workerBudgetMs(timeoutMs);
	const tool = typeof payload.facts === "object" && payload.facts !== null
		? String((payload.facts as Jsonish).tool_name ?? "")
		: "";
	const who: ObservationIdentity = identity ?? {
		sessionId: typeof payload.session_id === "string" ? payload.session_id : null,
		toolCallId: typeof payload.facts === "object" && payload.facts !== null
			? String((payload.facts as Jsonish).tool_call_id ?? "")
			: "",
		tool,
	};
	const base: StatusRow = { ...receiptPolicyFields(payload), ts: startedMs / 1000, ok: false, trace_id: payload.trace_id, tool };
	const settle = (row: StatusRow, classified: ShadowClassification): StatusRow => {
		const settled: StatusRow = { ...row, outcome: classified.outcome, session_id: who.sessionId };
		settled.recorded = appendObservation(pi, who, payload, classified, settled, Date.now() - startedMs);
		recordStatus(settled);
		return settled;
	};
	const answered = (response: unknown, via: string): StatusRow => {
		const classified = classifyShadowResponse(response, tool);
		// The worker answering is not the same as a model running; the outcome says which.
		const reason = classified.outcome === "error" && response && typeof response === "object"
			? text((response as Jsonish).reason) ?? "worker_error"
			: undefined;
		return settle({ ...base, ok: classified.outcome !== "error", via, ...(reason ? { error: reason } : {}) }, classified);
	};
	const failed = (outcome: ShadowOutcome, error: string): StatusRow =>
		settle({ ...base, error }, { outcome, models: [], legal_count: null });
	try {
		const transport = getTransport();
		if (transport) {
			return answered(await withTimeout(transport.request(payload, timeoutMs), timeoutMs), "transport");
		}
		if (parseBool(process.env.OMP_Z0INT_COGNITION_BRIDGE_FALLBACK) === false) {
			return failed("no_transport", "no_transport");
		}
		const module = await bridgeModule();
		const ensureWorker = module?.ensureWorker;
		const request = module?.request;
		if (typeof ensureWorker === "function" && typeof request === "function") {
			const handle = await (ensureWorker as () => Promise<unknown>)();
			return answered(
				await withTimeout(
					(request as (h: unknown, body: Jsonish, ms: number) => Promise<Jsonish>)(
						handle,
						payload,
						timeoutMs,
					),
					timeoutMs,
				),
				"z0int-bridge-import",
			);
		}
		return failed("no_transport", "no_transport");
	} catch (error) {
		// Swallowed on purpose: a shadow timeout is not a tool failure.
		const message = error instanceof Error ? error.message : String(error);
		return failed(message.startsWith("cognition_shadow timeout") ? "timeout" : "error", message);
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
			// The worker writes `abstained` for unreached models too; say which it was.
			if (entry.backend === null || entry.backend === undefined) {
				return `${label}:${entry.error === "shadow_timeout" ? "timeout" : "unavailable"}`;
			}
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
	const recentTools: string[] = [];
	const sessionTools = new Set<string>();

	pi.on?.("session_start", () => {
		recentTools.length = 0;
		sessionTools.clear();
	});

	pi.on?.("tool_call", (event, ctx) => {
		try {
			if (!shadowEnabled()) return undefined;
			const observed =
				event && typeof event === "object" && typeof (event as ToolCallLike).toolName === "string"
					? String((event as ToolCallLike).toolName)
					: "";
			if (!observed) return undefined;
			counters.tool_calls += 1;
			const toolNames = resolveAvailableTools(pi, ctx);
			const sessionId = liveSessionId(ctx);
			// Copied before this call is recorded, so the history never holds the answer.
			const payload = buildShadowPayload(event as ToolCallLike, toolNames, sessionId, [...recentTools]);
			payload.session_tools = [...sessionTools].sort();
			sessionTools.add(observed);
			recentTools.push(observed);
			if (recentTools.length > RECENT_TOOLS) recentTools.shift();
			const toolCallId = (event as ToolCallLike).toolCallId;
			void sendShadow(payload, cognitionTimeoutMs(), pi, {
				sessionId,
				toolCallId: typeof toolCallId === "string" ? toolCallId : "",
				tool: observed,
				currentSessionId: () => liveSessionId(ctx),
			});
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
			const seen = coverage();
			const summary = `observed ${seen.tool_calls} tool calls, ${seen.settled} settled ${JSON.stringify(seen.by_outcome)}`;
			const text =
				rows.length === 0
					? `z0int cognition: no shadow rows yet (${summary})`
					: [
							`z0int cognition: ${summary}`,
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
