import type { Call } from "./turn.ts";

export type Message = { role: string; content?: unknown; timestamp?: number; [key: string]: unknown };
export type Context = {
	sessionManager: { getSessionId(): string; getBranch(): { type: string; message?: Message }[] };
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
type Turn = {
	id: number;
	session: string;
	anchor: string | undefined;
	zoom: number;
	prepared?: Promise<{ prefix: Message[]; originals: string[] }>;
};
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
export function installOptchat(pi: Pi, services: Services): void {
	let stopped = false;
	let serial = 0;
	let turn: Turn | undefined;
	let chunks: Chunk[] = [];
	let session: string | undefined;
	let registered = false;
	let activation: Promise<void> = Promise.resolve();
	const active = () => !stopped && services.enabled();
	const reset = () => { turn = undefined; chunks = []; session = undefined; };
	const request: Call = async (op, args) => {
		const row = await services.call(op, args, 5000);
		if (row.ok === false) throw new Error(`OptChat ${op}: ${String(row.error)}`);
		return row;
	};
	const registerTools = () => {
		if (!registered) {
			registered = true;
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
	const syncTools = async () => {
		const on = active();
		if (on) registerTools();
		if (!registered) return;
		const current = pi.getActiveTools();
		const next = current.filter(name => !names.includes(name));
		if (on) next.push(...names);
		if (JSON.stringify(next) !== JSON.stringify(current)) await pi.setActiveTools(next);
	};
	// OMP action methods are unbound during extension loading. Only register here.
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
					services.setEnabled(command === "on");
					reset();
					if (command === "off") await services.pause();
					await activation;
					activation = syncTools();
					await activation;
					// Off never starts an RPC process. Its next enabled request rechecks the flag.
					if (active()) await request("enabled", { on: true });
				}
				ctx.ui?.notify(`OptChat ${active() ? "on" : "off"}.`, "info");
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
	pi.on("before_agent_start", async (_event, ctx) => {
		await activation;
		activation = syncTools();
		await activation;
		if (!active()) { reset(); await services.pause(); return; }
		const id = ctx.sessionManager.getSessionId();
		if (session !== id) { reset(); session = id; }
		const history = ctx.sessionManager.getBranch().filter(entry => entry.type === "message");
		const last = history.at(-1)?.message;
		turn = { id: ++serial, session: id, anchor: last && serialized(last), zoom: 0 };
	});
	async function prepare(owner: Turn, history: Message[]) {
		await activation;
		const historicalRequest: Call = async (op, args) => {
			if (!active() || turn !== owner) throw new Error("historical preparation cancelled");
			const row = await request(op, args);
			if (!active() || turn !== owner) throw new Error("historical preparation cancelled");
			return row;
		};
		const old = chunks.flatMap(chunk => chunk.source).map(serialized);
		if (!old.every((key, index) => history[index] && serialized(history[index]) === key)) chunks = [];
		const covered = chunks.reduce((count, chunk) => count + chunk.source.length, 0);
		if (history.length > covered) chunks.push({ source: history.slice(covered), ids: [] });
		const prefix: Message[] = [];
		for (const chunk of chunks) {
			if (!active() || turn !== owner) break;
			if (!chunk.frozen && chunk.source.every(textOnly)) {
				try {
					for (const message of chunk.source.slice(chunk.ids.length)) {
						const kind = message.role === "user" ? "user" : message.role === "assistant" ? "talk" : "echo";
						// Use note for tool output to avoid the legacy echo truncation.
						const row = await historicalRequest("append", { kind: kind === "echo" ? "note" : kind, text: serialized(message) });
						if (typeof row.i !== "number") throw new Error("missing historical message id");
						chunk.ids.push(row.i);
					}
					const row = await historicalRequest("view_ids", { ids: chunk.ids });
					if (row.ready === true && typeof row.view === "string") chunk.frozen = {
						role: "user", content: `OptChat history from earlier turns (summary data):\n${row.view}`, timestamp: 0,
					};
				} catch { /* Keep the complete originals; retry only on the next user turn. */ }
			}
			prefix.push(...(chunk.frozen ? [chunk.frozen] : chunk.source));
		}
		return { prefix, originals: history.map(serialized) };
	}
	pi.on("context", async (event, ctx) => {
		const owner = turn;
		if (!active()) {
			if (registered) await syncTools();
			reset(); await services.pause();
			return;
		}
		if (!owner || owner.session !== ctx.sessionManager.getSessionId()) return;
		if (!owner.prepared) {
			// The branch anchor was captured before OMP appended this prompt and its preludes.
			const boundary = owner.anchor === undefined ? 0 : event.messages.findLastIndex(message => serialized(message) === owner.anchor) + 1;
			owner.prepared = boundary === 0 ? Promise.resolve({ prefix: [], originals: [] }) : prepare(owner, event.messages.slice(0, boundary));
		}
		const prepared = await owner.prepared;
		if (!active() || turn !== owner) return;
		// Compaction or another extension can invalidate the boundary. Preserve all messages in that case.
		if (!prepared.originals.every((key, index) => event.messages[index] && serialized(event.messages[index]) === key)) return;
		try {
			// Later context handlers may mutate their input. Never expose the cached objects.
			return { messages: [...structuredClone(prepared.prefix), ...event.messages.slice(prepared.originals.length)] };
		} catch { return; }
	});
	pi.on("session_switch", reset);
	pi.on("session_start", async () => { reset(); activation = syncTools(); await activation; });
	pi.on("session_shutdown", async () => { stopped = true; reset(); await services.dispose(); });
}
