import type { Call } from "./turn.ts";

// OMP references below are relative to /home/kvn/workspace/oh-my-pi.
// Its source manifest is 18.1.10; the installed executable reports 18.11.0.
// These are checked source contracts, not a claim of installed provider-wire QA.
export type Message = { role: string; content?: unknown; timestamp?: number; [key: string]: unknown };
export type Context = {
	sessionManager: { getSessionId(): string; getBranch(): { type: string; message?: Message }[] };
	isIdle(): boolean;
	ui?: { notify(message: string, level: "info" | "warning" | "error"): void };
};
export type Tool = {
	name: string;
	label: string;
	description: string;
	parameters: object;
	defaultInactive: boolean;
	execute(id: string, args: { id: number; n?: number }, signal?: AbortSignal): Promise<{
		content: { type: "text"; text: string }[]; isError: boolean;
	}>;
};
type Command = { description: string; handler(args: string, ctx: Context): Promise<void> };
export type EventHandlers = {
	before_agent_start: (event: { prompt: string }, ctx: Context) => void | Promise<void>;
	input: (event: { text: string; source: "interactive" | "rpc" | "extension" }, ctx: Context) => void | Promise<void>;
	message_start: (event: { message: Message }, ctx: Context) => void;
	session_before_compact: () => { cancel: true } | undefined;
	session_compact: () => void;
	context: (event: { messages: Message[] }, ctx: Context) => Promise<{ messages: Message[] } | undefined>;
	session_shutdown: () => void | Promise<void>;
	session_switch: () => void;
	session_start: () => void | Promise<void>;
};
export type Pi = {
	on<E extends keyof EventHandlers>(event: E, handler: EventHandlers[E]): void;
	registerTool(tool: Tool): void;
	registerCommand(name: string, command: Command): void;
	getActiveTools(): string[];
	setActiveTools(names: string[]): Promise<void>;
};
export type Services = { call: Call; enabled(): boolean; setEnabled(on: boolean): void; pause(): void | Promise<void>; dispose(): void | Promise<void> };
type Chunk = { source: Message[]; ids: number[]; frozen?: Message };
type Prepared = { prefix: Message[]; originals: string[] };
type Turn = {
	session: string;
	anchor: string | undefined;
	zoom: number;
	on: boolean;
	degraded: boolean;
	snapshot: Message[];
	prepared?: Promise<Prepared>;
};
// OMP source: packages/coding-agent/src/extensibility/extensions/runner.ts:86,1320
// gives each context handler 30s and discards a late result. Leave 5s for cloning,
// tool activation and host scheduling. One deadline covers ALL preparation RPCs.
export const PREPARATION_BUDGET_MS = 25_000;
export type Options = { preparationBudgetMs?: number };
const names = ["optchat_zoom", "optchat_date"];
const serialized = (message: Message) => JSON.stringify(message);

function textOnly(message: Message): boolean {
	if (!["user", "assistant", "toolResult"].includes(message.role)) return false;
	return typeof message.content === "string" || (Array.isArray(message.content) && message.content.every(
		(block: unknown) => typeof block === "object" && block !== null && "type" in block &&
			["text", "toolCall"].includes(String(block.type)),
	));
}

/** Only complete historical chunks are replaced. Originals remain available on every failure. */
export function installOptchat(pi: Pi, services: Services, options: Options = {}): void {
	let stopped = false;
	let requested: boolean | undefined;
	let anchor: string | undefined;
	let starting = false;
	let turn: Turn | undefined;
	let chunks: Chunk[] = [];
	let session: string | undefined;
	let registered = false;
	let activation: Promise<void> = Promise.resolve();
	const active = () => !stopped && (turn ? turn.on && !turn.degraded : services.enabled());
	const reset = () => { turn = undefined; chunks = []; session = undefined; anchor = undefined; starting = false; };
	const request: Call = async (op, args, timeoutMs = 5000) => {
		const row = await services.call(op, args, timeoutMs);
		if (row.ok === false) throw new Error(`OptChat ${op}: ${String(row.error)}`);
		return row;
	};
	const registerTools = () => {
		if (!registered) {
			registered = true;
			pi.on("session_before_compact", () => {
				// shared-events.ts:376-380 supports cancellation. pruning.ts:304
				// has no equivalent veto; context detects rewrites instead.
				// session-maintenance.ts:863 honors cancel. Merely registering
				// this hook disables speculative/async compaction (:1265,1324),
				// so defer registration until first enable. No hook-unregister API.
				if (active() && turn) return { cancel: true };
			});
			// extensions/types.ts:625 defines defaultInactive; activation is explicit.
			for (const operation of ["zoom", "date"]) pi.registerTool({
				name: `optchat_${operation}`, label: `OptChat ${operation}`, defaultInactive: true,
				description: operation === "zoom"
					? "Read a historical summary's source by id and span n. At most two requests per user turn."
					: "Read the timestamp of historical message id.",
				parameters: { type: "object", properties: {
					id: { type: "integer", minimum: 0 },
					...(operation === "zoom" ? { n: { type: "integer", minimum: 1 } } : {}),
				}, required: operation === "zoom" ? ["id", "n"] : ["id"] },
				async execute(_id, args, signal) {
					const reply = (text: string, isError = false) => ({ content: [{ type: "text" as const, text }], isError });
					if (!active() || signal?.aborted) return reply("OptChat is off or cancelled.", true);
					const owner = turn;
					if (operation === "zoom") {
						if (!owner || owner.zoom >= 2) return reply("OptChat zoom limit reached for this turn.");
						owner.zoom++;
					}
					try {
						const row = await request(operation, { msg_id: args.id, ...(operation === "zoom" ? { n: args.n ?? 1 } : {}) });
						if (!active() || signal?.aborted || owner !== turn) return reply("OptChat is off or cancelled.", true);
						return reply(String(row.text ?? ""));
					} catch (error) { return reply(String(error), true); }
				},
			});
		}
	};
	const syncTools = async (on = !stopped && (turn ? turn.on : services.enabled())) => {
		// No unregisterTool exists in OMP ExtensionAPI (types.ts:1299,1450-1456).
		// loader.ts:180-187 retains definitions in extension.tools. setActiveTools
		// selects only named tools (session/session-tools.ts:862-875), controlling
		// model visibility; direct execution is separately guarded above.
		if (on) registerTools();
		if (!registered) return;
		const current = pi.getActiveTools();
		const next = current.filter(name => !names.includes(name));
		if (on) next.push(...names);
		if (JSON.stringify(next) !== JSON.stringify(current)) await pi.setActiveTools(next);
	};
	const activate = (on: boolean) => {
		// Capture the transition synchronously. A later streaming command must not
		// be consumed by a queued activation belonging to the previous submission.
		requested = undefined;
		if (services.enabled() !== on) services.setEnabled(on);
		activation = activation.then(async () => {
			await syncTools(on);
			if (!on) await services.pause();
		});
		return activation;
	};
	// OMP loader.ts:73-114 leaves action APIs unbound during loading.
	if (active()) registerTools();
	pi.registerCommand("optchat", {
		description: "OptChat on|off|status. Missing flag means off.",
		async handler(args, ctx) {
			const command = args.trim() || "status";
			if (!["on", "off", "status"].includes(command)) {
				ctx.ui?.notify("usage: /optchat on|off|status", "warning"); return;
			}
			try {
				if (command !== "status") {
					const on = command === "on";
					const effective = turn?.on ?? services.enabled();
					if (on !== (requested ?? effective)) {
						requested = on === effective ? undefined : on;
						// agent-session.ts:5934 executes commands during streaming.
						// Once a user turn exists, even idle/auto-continuation gaps keep
						// its flag, subprocess and tool roster until a REAL user prompt.
						if (!turn && ctx.isIdle()) {
							await activate(on);
						}
					}
				}
				const pending = requested === undefined ? "" : ` ${requested ? "on" : "off"} pending next user turn.`;
				ctx.ui?.notify(`OptChat ${active() ? "on" : "off"}.${pending}`, "info");
			} catch (error) { ctx.ui?.notify(String(error), "error"); }
		},
	});
	pi.registerCommand("optchat-browse", {
		description: "Write OptChat memory as an HTML page when enabled.",
		async handler(_args, ctx) {
			if (!active()) return;
			try { const row = await request("browse"); ctx.ui?.notify(`OptChat memory: ${String(row.path)}`, "info"); }
			catch (error) { ctx.ui?.notify(String(error), "error"); }
		},
	});
	// Input is an early, awaited interactive submission hook (input-controller.ts:764).
	// Apply pending tool/flag changes here when idle, BEFORE agent.ts:1330 snapshots
	// the tool roster. ExtensionContext.isIdle is in extensions/types.ts:481.
	// Commands and streaming steering must leave the turn intact.
	pi.on("input", async (event, ctx) => {
		if (!ctx.isIdle() || event.text.trimStart().startsWith("/")) return;
		turn = undefined;
		await activate(requested ?? services.enabled());
	});
	pi.on("before_agent_start", (_event, ctx) => {
		const id = ctx.sessionManager.getSessionId();
		if (session !== id) { reset(); session = id; }
		// This event ALSO fires for synthetic prompts (agent-session.ts:3747,6310).
		// Stage an anchor only. It does not reset the snapshot or zoom counter.
		const last = ctx.sessionManager.getBranch().filter(entry => entry.type === "message").at(-1)?.message;
		anchor = last && serialized(last);
		starting = true;
	});
	const beginUserTurn = (message: Message, ctx: Context) => {
		// agent-session.ts:6040-6056 creates user messages for real submissions,
		// and developer/agent/synthetic messages for automatic continuations (:3747).
		// agent-loop.ts:994 emits input message_start before the first provider call.
		if (!starting || message.role !== "user" || message.attribution === "agent" || message.synthetic === true) return;
		starting = false;
		const id = ctx.sessionManager.getSessionId();
		if (session !== id) { reset(); session = id; }
		turn = { session: id, anchor, zoom: 0, on: requested ?? services.enabled(), degraded: false, snapshot: [] };
		// Also handles non-interactive submissions, which do not emit input.
		void activate(turn.on);
	};
	pi.on("message_start", (event, ctx) => {
		if (event.message.synthetic === true && event.message.attribution === "agent") {
			starting = false; return;
		}
		beginUserTurn(event.message, ctx);
	});
	pi.on("session_compact", () => { if (turn) turn.degraded = true; });
	async function prepare(owner: Turn, history: Message[], deadline: number): Promise<Prepared> {
		const old = chunks.flatMap(chunk => chunk.source).map(serialized);
		const matches = old.every((key, index) => history[index] && serialized(history[index]) === key);
		// Work on copies. Neither timeout nor a late RPC can publish a partial prefix.
		const candidate = matches ? structuredClone(chunks) : [];
		const covered = candidate.reduce((count, chunk) => count + chunk.source.length, 0);
		if (history.length > covered) candidate.push({ source: structuredClone(history.slice(covered)), ids: [] });
		const render = (rows: Chunk[]) => rows.flatMap(chunk => chunk.frozen ? [chunk.frozen] : chunk.source);
		const fallback: Prepared = { prefix: structuredClone(render(candidate)), originals: history.map(serialized) };
		const budget = Math.max(0, deadline - performance.now());
		let expired = false;
		const check = () => {
			if (expired || performance.now() >= deadline || !active() || turn !== owner) throw new Error("preparation cancelled");
		};
		const historicalRequest: Call = async (op, args) => {
			check();
			const row = await request(op, args, Math.max(1, Math.min(5000, deadline - performance.now())));
			check();
			return row;
		};
		let timer: ReturnType<typeof setTimeout> | undefined;
		const timeout = new Promise<Prepared>(resolve => {
			timer = setTimeout(() => { expired = true; resolve(fallback); }, budget);
		});
		const work = async (): Promise<Prepared> => {
			await activation;
			for (const chunk of candidate) {
				check();
				if (chunk.frozen || !chunk.source.every(textOnly)) continue;
				for (const message of chunk.source.slice(chunk.ids.length)) {
					// note avoids the backend's legacy echo truncation for tool output.
					const kind = message.role === "user" ? "user" : message.role === "assistant" ? "talk" : "note";
					const row = await historicalRequest("append", { kind, text: serialized(message) });
					if (typeof row.i !== "number") throw new Error("missing historical message id");
					chunk.ids.push(row.i);
				}
				const row = await historicalRequest("view_ids", { ids: chunk.ids });
				if (row.ready === true && typeof row.view === "string") chunk.frozen = {
					role: "user", content: `OptChat history from earlier turns (summary data):\n${row.view}`, timestamp: 0,
				};
			}
			check();
			chunks = candidate;
			return { prefix: render(candidate), originals: fallback.originals };
		};
		try { return await Promise.race([work().catch(() => fallback), timeout]); }
		finally { clearTimeout(timer); expired = true; }
	}
	pi.on("context", async (event, ctx) => {
		const deadline = performance.now() + Math.min(options.preparationBudgetMs ?? PREPARATION_BUDGET_MS, PREPARATION_BUDGET_MS);
		// Notifications are fire-and-forget in agent-session.ts:2865-2873.
		// Confirm from the authoritative context too, before EVER sending originals:
		// a delayed message_start must not install compression on a later tool call.
		if (starting) {
			const boundary = anchor === undefined ? 0 : event.messages.findLastIndex(message => serialized(message) === anchor) + 1;
			if (anchor === undefined || boundary > 0) {
				const submitted = event.messages.slice(boundary).find(message => message.role === "user" || message.synthetic === true);
				if (submitted) beginUserTurn(submitted, ctx);
			}
		}
		starting = false;
		const owner = turn;
		if (!owner || !active() || owner.session !== ctx.sessionManager.getSessionId()) return;
		// Keep the entire previously observed context, including opaque live payloads,
		// detached from both the host and later extension handlers. No mixed histories:
		// after a rewrite, this turn returns host history unchanged on EVERY call.
		if (!owner.snapshot.every((message, index) => event.messages[index] && serialized(event.messages[index]) === serialized(message)) ||
			event.messages.some(message => message.prunedAt !== undefined)) {
			owner.degraded = true; return;
		}
		try { owner.snapshot = structuredClone(event.messages); }
		catch { owner.degraded = true; return; }
		if (!owner.prepared) {
			const boundary = owner.anchor === undefined ? 0 : event.messages.findLastIndex(message => serialized(message) === owner.anchor) + 1;
			if (owner.anchor !== undefined && boundary === 0) { owner.degraded = true; return; }
			owner.prepared = prepare(owner, owner.snapshot.slice(0, boundary), deadline);
		}
		const snapshot = owner.snapshot;
		const prepared = await owner.prepared;
		if (!active() || turn !== owner) return;
		return { messages: structuredClone([...prepared.prefix, ...snapshot.slice(prepared.originals.length)]) };
	});
	pi.on("session_switch", reset);
	pi.on("session_start", async () => { reset(); activation = syncTools(); await activation; });
	pi.on("session_shutdown", async () => { stopped = true; reset(); await services.dispose(); });
}
