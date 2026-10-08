/** A fresh, cancellable model/tool loop owned by one RPC client and session. */
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { pathToFileURL } from "node:url";
import { showCompactRow } from "./compact-ui.ts";

export type Json = Record<string, unknown>;
/** timeoutMs = 0 means no client deadline (normal settle waits). */
export type NoticeHandler = (notice: Json) => void;
export type Call = ((op: string, extra?: Json, timeoutMs?: number) => Promise<Json>) & {
	onNotice?: (handler: NoticeHandler) => () => void;
};
type TextBlock = { type: "text"; text: string };
type ToolCall = { type: "toolCall"; name: string; id: string; arguments: Json };
type NativeBlock = { type: string; text?: string; thinking?: string };
// These are the consumed SDK fields, not a reconstruction of its native history.
// The complete returned object, including opaque provider payloads, is replayed.
type Assistant = { role: "assistant"; content: NativeBlock[]; stopReason: string; errorMessage?: string; timestamp: number };
type User = { role: "user"; content: string | TextBlock[]; timestamp: number };
type ToolResult = { role: "toolResult"; toolCallId: string; toolName: string; content: TextBlock[]; isError: boolean; timestamp: number };
type Message = Assistant | User | ToolResult;
type StreamEvent =
	| { type: "text_end" | "thinking_end"; content: string }
	| { type: "toolcall_end"; toolCall: ToolCall }
	| { type: "done"; message: Assistant }
	| { type: "error"; error: Assistant }
	| { type: "start" | "text_start" | "text_delta" | "thinking_start" | "thinking_delta" | "toolcall_start" | "toolcall_delta" | "image_end" };
type ModelPolicy = { api?: string; compat?: { supportsPromptCacheBreakpoints?: boolean; supportsAllTurnsReasoningContext?: boolean } };
type StreamOptions = { apiKey: unknown; cacheRetention: "short"; signal: AbortSignal; storeResponses: false; statefulResponses: false; onPayload: (payload: unknown, model?: ModelPolicy) => unknown };
type StreamSimple = (model: unknown, context: { systemPrompt: string[]; messages: Message[]; tools: typeof TOOLS }, options: StreamOptions) => AsyncIterable<StreamEvent>;

export type TurnCtx = {
	cwd?: string;
	model?: unknown;
	sessionManager?: { getSessionId: () => string };
	modelRegistry?: { getApiKey?: (model: unknown) => Promise<unknown> };
	ui?: {
		notify?: (message: string, level?: "info" | "warning" | "error") => void;
		setWidget?: (key: string, content: unknown, options?: { placement?: "aboveEditor" | "belowEditor" }) => void;
		onTerminalInput?: (handler: (data: string) => { consume?: boolean } | undefined) => () => void;
	};
};
export type TurnPi = {
	sendMessage?: (message: { customType: string; content: string; display: boolean }, options?: { triggerTurn?: boolean }) => void;
};
type TurnOwner = {
	call: Call;
	ctx: TurnCtx;
	pi: TurnPi;
	controller: AbortController;
	sessionId: string | undefined;
	done: Promise<void>;
	closing: boolean;
	submissions: Set<Promise<void>>;
	unlogged: string[];
	waitId?: string;
	cancelWait?: Promise<void>;
	cancelError?: unknown;
};
const owners = new WeakMap<Call, TurnOwner>();
const PI_AI = [
	"/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@oh-my-pi/pi-ai/src/index.ts",
	"/home/kvn/.bun/install/global/node_modules/@oh-my-pi/pi-ai/src/index.ts",
];

function agentsText(cwd: string | undefined): string {
	if (!cwd) return "";
	try { return readFileSync(join(cwd, "AGENTS.md"), "utf8"); } catch { return ""; }
}

// The SDK is outside this checkout and its install layout is selected at runtime.
async function loadStream(): Promise<StreamSimple> {
	for (const path of PI_AI) {
		if (!existsSync(path)) continue;
		const mod = (await import(pathToFileURL(path).href)) as { streamSimple?: StreamSimple };
		if (typeof mod.streamSimple === "function") return mod.streamSimple;
	}
	throw new Error("streamSimple is not installed");
}

export async function checkedCall(call: Call, op: string, extra: Json = {}, timeoutMs = 5000): Promise<Json> {
	const row = await call(op, extra, timeoutMs);
	if (row.ok === false) throw new Error(`OptChat ${op}: ${String(row.error ?? "request failed")}`);
	return row;
}

function textsOf(row: Json): string[] {
	return Array.isArray(row.texts) ? row.texts.map(String) : [];
}

async function active(owner: TurnOwner): Promise<Json> {
	owner.controller.signal.throwIfAborted();
	const status = await checkedCall(owner.call, "status");
	if (status.enabled !== true) owner.controller.abort(new DOMException("OptChat is off", "AbortError"));
	owner.controller.signal.throwIfAborted();
	return status;
}

async function record(owner: TurnOwner, kind: string, text: string): Promise<string> {
	await active(owner);
	const row = await checkedCall(owner.call, "record", { kind, text });
	if (row.logged !== true || typeof row.text !== "string") throw new Error(`OptChat record did not save ${kind}`);
	return row.text;
}

function show(owner: TurnOwner, text: string, kind: "talk" | "thought" | "tool" | "echo" = "talk"): void {
	owner.controller.signal.throwIfAborted();
	if (!text) return;
	owner.pi.sendMessage?.({ customType: `optchat-${kind}`, content: text, display: true }, { triggerTurn: false });
	if (!owner.pi.sendMessage) owner.ctx.ui?.notify?.(text.slice(0, 500), "info");
}

function object(value: unknown): Json | undefined {
	return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Json : undefined;
}

function rows(value: unknown): Json[] {
	return Array.isArray(value) ? value.map(object).filter((row): row is Json => row !== undefined) : [];
}

/** The public onPayload hook runs AFTER the SDK's content conversion/caching. */
function cachePayload(payload: unknown, model: ModelPolicy | undefined, pieces: string[], userText: string): unknown {
	const wire = object(payload);
	if (!wire) throw new Error("OptChat provider payload is not an object");
	const api = model?.api;
	if (api === "anthropic-messages") {
		// Replace the SDK's tool/system/rolling-tail marks, rather than exceeding
		// Anthropic's four-breakpoint budget with the three stable view cuts.
		for (const block of [...rows(wire.tools), ...rows(wire.system)]) delete block.cache_control;
		const messages = rows(wire.messages);
		for (const message of messages) {
			for (const block of rows(message.content)) delete block.cache_control;
		}
		const initial = messages.find((message) => message.role === "user");
		if (!initial) throw new Error("OptChat view is missing from the Anthropic payload");
		initial.content = [
			...pieces.map((text, index) => ({ type: "text", text, ...(index < pieces.length - 1 ? { cache_control: { type: "ephemeral" } } : {}) })),
			{ type: "text", text: userText },
		];
		wire.cache_control = { type: "ephemeral" };
	} else if (api === "openai-responses" || api === "azure-openai-responses" || api === "openai-codex-responses") {
		wire.store = false;
		const input = rows(wire.input);
		const initial = input.find((message) => message.role === "user");
		// Explicit cache fields are not part of the generic Responses API.
		// Only catalog-confirmed endpoints accept this supported policy.
		if (model?.compat?.supportsPromptCacheBreakpoints === true) {
			for (const message of input) {
				for (const block of rows(message.content)) delete block.prompt_cache_breakpoint;
			}
			if (!initial) throw new Error("OptChat view is missing from the Responses payload");
			initial.content = [
				...pieces.map((text, index) => ({ type: "input_text", text, ...(index < pieces.length - 1 ? { prompt_cache_breakpoint: { mode: "explicit" } } : {}) })),
				{ type: "input_text", text: userText },
			];
			// Keep the automatic request-end boundary as well as the explicit view cuts.
			wire.prompt_cache_options = { mode: "implicit", ttl: "30m" };
		}
		// Native encrypted reasoning is left to the SDK's compat-aware replay.
		// In particular, plain Responses endpoints must not get Codex-only fields.
		if (api === "openai-codex-responses" && model?.compat?.supportsAllTurnsReasoningContext === true) {
			wire.reasoning = { ...object(wire.reasoning), context: "all_turns" };
		}
	}
	const path = process.env.Z0INT_OPTCHAT_DUMP;
	if (path) appendFileSync(path, JSON.stringify(wire) + "\n");
	return wire;
}


async function command(command: string, args: string[], cwd: string, signal: AbortSignal, timeout: number, noMatches = false): Promise<string> {
	signal.throwIfAborted();
	const { promise, resolve, reject } = Promise.withResolvers<string>();
	const proc = spawn(command, args, { cwd, detached: true, signal, timeout, stdio: ["ignore", "pipe", "pipe"] });
	let output = "";
	proc.stdout.setEncoding("utf8");
	proc.stderr.setEncoding("utf8");
	proc.stdout.on("data", (chunk: string) => { output += chunk; });
	proc.stderr.on("data", (chunk: string) => { output += chunk; });
	const stop = () => {
		if (proc.pid) {
			try { process.kill(-proc.pid, "SIGKILL"); } catch { /* already exited */ }
		}
	};
	signal.addEventListener("abort", stop, { once: true });
	proc.once("error", reject);
	proc.once("exit", (_code, killedBy) => { if (killedBy) stop(); });
	proc.once("close", (code, killedBy) => {
		signal.removeEventListener("abort", stop);
		if (signal.aborted) { reject(signal.reason); return; }
		const text = output.trim();
		if (code === 0) resolve(text || "exit 0");
		else if (noMatches && code === 1 && !killedBy) resolve(text || "no matches");
		else reject(new Error(`${text ? text + "\n" : ""}${command} ${killedBy ? `terminated by ${killedBy}` : `exit ${code}`}`));
	});
	return promise;
}

async function runTool(owner: TurnOwner, cwd: string, name: string, args: Json): Promise<string> {
	await active(owner);
	const signal = owner.controller.signal;
	if (name === "zoom" || name === "date") {
		const row = await checkedCall(owner.call, name, { msg_id: Number(args.id ?? 0), ...(name === "zoom" ? { n: Number(args.n ?? 1) } : {}) });
		return String(row.text ?? "");
	}
	if (name === "bash") return command("bash", ["-lc", String(args.command ?? "")], cwd, signal, 30_000);
	if (name === "grep") return command("rg", ["-n", "--", String(args.pattern ?? ""), String(args.path ?? ".")], cwd, signal, 15_000, true);
	const requested = String(args.path ?? "");
	const path = isAbsolute(requested) ? requested : join(cwd, requested);
	if (name === "read") return readFile(path, { encoding: "utf8", signal });
	if (name === "write") {
		await writeFile(path, String(args.content ?? ""), { signal });
		return `wrote ${path}`;
	}
	if (name === "edit") {
		const old = String(args.old ?? "");
		const source = await readFile(path, { encoding: "utf8", signal });
		if (!old || !source.includes(old)) throw new Error(`edit failed: old text not found in ${path}`);
		await active(owner);
		await writeFile(path, source.replace(old, String(args.new ?? "")), { signal });
		return `edited ${path}`;
	}
	throw new Error(`tool ${name} is not executable in this harness`);
}

const TOOLS = [
	{
		name: "zoom",
		description: "Open the line id+n of the view into the two lines of n/2 under it; n = 1 gives the message whole.",
		parameters: {
			type: "object",
			properties: { id: { type: "integer", minimum: 0 }, n: { type: "integer", minimum: 1 } },
			required: ["id", "n"],
		},
	},
	{
		name: "date",
		description: "The date and time of message id.",
		parameters: { type: "object", properties: { id: { type: "integer", minimum: 0 } }, required: ["id"] },
	},
	{
		name: "bash",
		description: "Run a shell command.",
		parameters: { type: "object", properties: { command: { type: "string" } }, required: ["command"] },
	},
	{
		name: "read",
		description: "Read a file.",
		parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
	},
	{
		name: "write",
		description: "Write a file.",
		parameters: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] },
	},
	{
		name: "edit",
		description: "Replace one exact string in a file.",
		parameters: { type: "object", properties: { path: { type: "string" }, old: { type: "string" }, new: { type: "string" } }, required: ["path", "old", "new"] },
	},
	{
		name: "grep",
		description: "Search files for a pattern.",
		parameters: { type: "object", properties: { pattern: { type: "string" }, path: { type: "string" } }, required: ["pattern"] },
	},
];

async function freshCall(owner: TurnOwner, opened: Json): Promise<void> {
	const streamSimple = await loadStream();
	const model = owner.ctx.model;
	if (!model) throw new Error("no active model");
	const apiKey = await owner.ctx.modelRegistry?.getApiKey?.(model);
	const pieces = Array.isArray(opened.pieces) ? opened.pieces.map(String) : [String(opened.view ?? "")];
	const blocks = Array.isArray(opened.blocks) ? opened.blocks.map(String) : [];
	const userText = blocks[1] ?? "";
	const messages: Message[] = [{ role: "user", content: [...pieces.map((text): TextBlock => ({ type: "text", text })), { type: "text", text: userText }], timestamp: Date.now() }];
	const cwd = owner.ctx.cwd || process.cwd();
	for (;;) {
		await active(owner);
		let assistant: Assistant | undefined;
		const calls: ToolCall[] = [];
		const stream = streamSimple(model, { systemPrompt: [String(opened.system ?? "")], messages, tools: TOOLS }, {
			apiKey,
			cacheRetention: "short",
			storeResponses: false,
			statefulResponses: false,
			signal: owner.controller.signal,
			onPayload: (payload, policy) => cachePayload(payload, policy, pieces, userText),
		});
		for await (const event of stream) {
			if (event.type === "text_end") {
				const saved = await record(owner, "talk", event.content);
				await active(owner);
				show(owner, saved);
			} else if (event.type === "thinking_end") {
				await active(owner);
				show(owner, event.content, "thought");
			} else if (event.type === "toolcall_end") {
				const saved = await record(owner, "tool", `${event.toolCall.name} ${JSON.stringify(event.toolCall.arguments)}`);
				await active(owner);
				show(owner, saved, "tool");
				calls.push(event.toolCall);
			} else if (event.type === "done") {
				await active(owner);
				assistant = event.message;
			} else if (event.type === "error") {
				throw new Error(event.error.errorMessage || `model ${event.error.stopReason}`);
			}
		}
		await active(owner);
		if (!assistant || assistant.stopReason === "error" || assistant.stopReason === "aborted" || assistant.errorMessage) {
			throw new Error(assistant?.errorMessage || `model ${assistant?.stopReason ?? "stream ended without completion"}`);
		}
		messages.push(assistant);
		if (calls.length === 0) {
			if (assistant.stopReason === "toolUse") throw new Error("model requested tools without a finished tool call");
			return;
		}
		for (const tool of calls) {
			let result: string;
			let isError = false;
			try { result = await runTool(owner, cwd, tool.name, tool.arguments); }
			catch (error) {
				owner.controller.signal.throwIfAborted();
				isError = true;
				result = error instanceof Error ? error.message : String(error);
			}
			const saved = await record(owner, "echo", result);
			messages.push({ role: "toolResult", toolCallId: tool.id, toolName: tool.name, content: [{ type: "text", text: saved }], isError, timestamp: Date.now() });
			await active(owner);
			show(owner, saved, "echo");
		}
		await active(owner);
		const boundary = await checkedCall(owner.call, "boundary");
		owner.unlogged = textsOf(boundary);
		const injected: string[] = [];
		while (owner.unlogged.length > 0) {
			injected.push(await record(owner, "user", owner.unlogged[0]));
			owner.unlogged.shift();
		}
		if (injected.length > 0) messages.push({ role: "user", content: injected.join("\n\n"), timestamp: Date.now() });
	}
}

async function submit(owner: TurnOwner, text: string): Promise<void> {
	const submission = checkedCall(owner.call, "submit", { text }).then(() => {}, (error: unknown) => {
		owner.controller.abort(error);
		throw error;
	});
	owner.submissions.add(submission);
	try { await submission; } finally { owner.submissions.delete(submission); }
}

async function drain(owner: TurnOwner, text: string): Promise<void> {
	const signal = owner.controller.signal;
	const stopWait = () => {
		if (!owner.waitId) return;
		owner.cancelWait = checkedCall(owner.call, "cancel", { wait_id: owner.waitId }).then(() => {}, (error: unknown) => { owner.cancelError = error; });
	};
	signal.addEventListener("abort", stopWait, { once: true });
	let unlisten: (() => void) | undefined;
	let unnotice: (() => void) | undefined;
	let waiting = false;
	const reported = new Set<string>();
	const report = async (notice: Json) => {
		if (!waiting || signal.aborted || notice.event !== "compactor_error") return;
		const key = `${notice.level}:${notice.index}`;
		if (reported.has(key)) return;
		reported.add(key);
		await active(owner);
		if (waiting) owner.ctx.ui?.notify?.(`OptChat compactor ${String(notice.backend)} (${String(notice.model)}) failed at ${key}: ${String(notice.error)}`, "error");
	};
	try {
		unlisten = owner.ctx.ui?.onTerminalInput?.((data) => {
			if (data !== "\u001b") return undefined;
			owner.controller.abort(new DOMException("OptChat cancelled", "AbortError"));
			return { consume: true };
		});
		unnotice = owner.call.onNotice?.((notice) => {
			report(notice).catch((error: unknown) => { if (!signal.aborted) owner.controller.abort(error); });
		});
		await submit(owner, text);
		for (;;) {
			const status = await active(owner);
			// An idle queue never waits for the previous turn's compactor work.
			if (Number(status.pending ?? 0) === 0) {
				owner.closing = true;
				await Promise.all([...owner.submissions]);
				const finished = await checkedCall(owner.call, "finish");
				if (textsOf(finished).length === 0) return;
				owner.closing = false;
				continue;
			}
			if (status.ready !== true) {
				waiting = true;
				for (const notice of rows(status.errors)) await report(notice);
				owner.waitId = randomUUID();
				const hide = await showCompactRow(owner.ctx.ui, () => owner.controller.abort(new DOMException("OptChat cancelled", "AbortError")), "Summarizing the view…", signal);
				const { promise: interrupted, reject } = Promise.withResolvers<never>();
				const interrupt = () => reject(signal.reason);
				signal.addEventListener("abort", interrupt, { once: true });
				try {
					signal.throwIfAborted();
					const settled = await Promise.race([checkedCall(owner.call, "settle", { wait_id: owner.waitId }, 0), interrupted]);
					if (settled.cancelled === true || settled.ready !== true) owner.controller.abort(new DOMException("OptChat wait cancelled", "AbortError"));
					signal.throwIfAborted();
				} finally {
					signal.removeEventListener("abort", interrupt);
					waiting = false;
					hide();
					owner.waitId = undefined;
				}
			}
			await active(owner);
			owner.unlogged = textsOf(await checkedCall(owner.call, "take"));
			if (owner.unlogged.length === 0) continue;
			await active(owner);
			const opened = await checkedCall(owner.call, "begin", { texts: owner.unlogged, agents: agentsText(owner.ctx.cwd) }, 10_000);
			if (opened.ready !== true) throw new Error("OptChat could not open a summarized view");
			owner.unlogged = [];
			await freshCall(owner, opened);
			await active(owner);
			await checkedCall(owner.call, "persist", {}, 20_000);
			await checkedCall(owner.call, "finish");
		}
	} catch (error) {
		const cancelled = signal.aborted && signal.reason instanceof DOMException && signal.reason.name === "AbortError";
		owner.closing = true;
		owner.controller.abort(error);
		await Promise.allSettled([...owner.submissions]);
		await owner.cancelWait;
		await checkedCall(owner.call, "abort", { texts: owner.unlogged });
		owner.unlogged = [];
		await checkedCall(owner.call, "finish");
		if (owner.cancelError) throw owner.cancelError;
		if (!cancelled) throw error;
	} finally {
		waiting = false;
		unnotice?.();
		unlisten?.();
		signal.removeEventListener("abort", stopWait);
		if (owners.get(owner.call) === owner) owners.delete(owner.call);
	}
}

export async function cancelTurn(call: Call): Promise<void> {
	const owner = owners.get(call);
	if (!owner) return;
	owner.closing = true;
	owner.controller.abort(new DOMException("OptChat cancelled", "AbortError"));
	await owner.done;
}

export async function runTurn(call: Call, ctx: TurnCtx, text: string, pi: TurnPi): Promise<void> {
	const sessionId = ctx.sessionManager?.getSessionId();
	for (;;) {
		const owner = owners.get(call);
		if (owner && (owner.sessionId !== sessionId || owner.ctx.cwd !== ctx.cwd)) {
			await cancelTurn(call);
			continue;
		}
		if (owner?.closing || owner?.controller.signal.aborted) {
			await owner.done;
			continue;
		}
		if (owner) { await submit(owner, text); await owner.done; return; }
		const { promise, resolve, reject } = Promise.withResolvers<void>();
		const next: TurnOwner = { call, ctx, pi, controller: new AbortController(), sessionId, done: promise, closing: false, submissions: new Set(), unlogged: [] };
		owners.set(call, next);
		// Both outcomes are forwarded to the awaited promise; no detached rejection.
		drain(next, text).then(resolve, reject);
		await promise;
		return;
	}
}
