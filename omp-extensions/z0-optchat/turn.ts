/**
 * Enabled turn: settle, render the view, log the new text, then one fresh
 * model call. The OMP session is not sent.
 */
import { spawnSync } from "node:child_process";
import { showCompactRow } from "./compact-ui.ts";
import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { pathToFileURL } from "node:url";

type Json = Record<string, unknown>;
type Call = (op: string, extra?: Json, timeoutMs?: number) => Promise<Json>;
type Block = { type?: string; text?: string; name?: string; id?: string; arguments?: Record<string, unknown> };
type Assistant = { role?: string; content?: Block[]; stopReason?: string; errorMessage?: string };
type Complete = (model: unknown, context: unknown, options?: unknown) => Promise<Assistant>;

export type TurnCtx = {
	cwd?: string;
	model?: unknown;
	modelRegistry?: { getApiKey?: (model: unknown) => Promise<unknown> };
	ui?: {
		notify?: (message: string, level?: "info" | "warning" | "error") => void;
		setWidget?: (key: string, content: unknown, options?: { placement?: "aboveEditor" | "belowEditor" }) => void;
	};
};

export type TurnPi = {
	sendMessage?: (
		message: { customType: string; content: string; display: boolean },
		options?: { triggerTurn?: boolean },
	) => void;
};

const pending: string[] = [];
let running = false;
const PI_AI = [
	"/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@oh-my-pi/pi-ai/src/index.ts",
	"/home/kvn/.bun/install/global/node_modules/@oh-my-pi/pi-ai/src/index.ts",
];

function agentsText(cwd: string | undefined): string {
	if (!cwd) return "";
	try {
		return readFileSync(join(cwd, "AGENTS.md"), "utf8");
	} catch {
		return "";
	}
}

// Static import cannot resolve @oh-my-pi/pi-ai from this extension directory.
// The install path is chosen at runtime between the two known omp layouts.

async function loadComplete(): Promise<Complete> {
	for (const path of PI_AI) {
		if (!existsSync(path)) continue;
		const mod = (await import(pathToFileURL(path).href)) as { completeSimple?: Complete };
		if (typeof mod.completeSimple === "function") return mod.completeSimple;
	}
	throw new Error("completeSimple is not installed");
}

function textOf(message: Assistant | undefined): string {
	return (message?.content ?? [])
		.filter((block) => block.type === "text" && block.text)
		.map((block) => block.text)
		.join("");
}

function toolCalls(message: Assistant | undefined): Block[] {
	return (message?.content ?? []).filter((block) => block.type === "toolCall" && block.name && block.id);
}

function resolvePath(cwd: string, path: string): string {
	return isAbsolute(path) ? path : join(cwd, path);
}

async function runTool(call: Call, cwd: string, name: string, args: Record<string, unknown>): Promise<string> {
	if (name === "zoom") {
		const row = await call("zoom", { msg_id: Number(args.id ?? 0), n: Number(args.n ?? 1) }, 5000);
		return String(row.text ?? row.error ?? "");
	}
	if (name === "date") {
		const row = await call("date", { msg_id: Number(args.id ?? 0) }, 5000);
		return String(row.text ?? row.error ?? "");
	}
	if (name === "bash") {
		const command = String(args.command ?? "");
		const result = spawnSync("bash", ["-lc", command], { cwd, encoding: "utf8", timeout: 30_000 });
		const out = `${result.stdout ?? ""}${result.stderr ?? ""}`.trim();
		return out || `exit ${result.status ?? 1}`;
	}
	if (name === "read") return readFileSync(resolvePath(cwd, String(args.path ?? "")), "utf8");
	if (name === "grep") {
		const result = spawnSync("rg", ["-n", "--", String(args.pattern ?? ""), String(args.path ?? ".")], {
			cwd,
			encoding: "utf8",
			timeout: 15_000,
		});
		return `${result.stdout ?? ""}${result.stderr ?? ""}`.trim() || "no matches";
	}
	if (name === "write") {
		const path = resolvePath(cwd, String(args.path ?? ""));
		writeFileSync(path, String(args.content ?? ""));
		return `wrote ${path}`;
	}
	if (name === "edit") {
		const path = resolvePath(cwd, String(args.path ?? ""));
		const old = String(args.old ?? "");
		const next = String(args.new ?? "");
		const source = readFileSync(path, "utf8");
		if (!old || !source.includes(old)) return `edit failed: old text not found in ${path}`;
		writeFileSync(path, source.replace(old, next));
		return `edited ${path}`;
	}
	return `tool ${name} is not executable in this harness`;
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
		parameters: {
			type: "object",
			properties: { id: { type: "integer", minimum: 0 } },
			required: ["id"],
		},
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
		parameters: {
			type: "object",
			properties: { path: { type: "string" }, content: { type: "string" } },
			required: ["path", "content"],
		},
	},
	{
		name: "edit",
		description: "Replace one exact string in a file.",
		parameters: {
			type: "object",
			properties: { path: { type: "string" }, old: { type: "string" }, new: { type: "string" } },
			required: ["path", "old", "new"],
		},
	},
	{
		name: "grep",
		description: "Search files for a pattern.",
		parameters: {
			type: "object",
			properties: { pattern: { type: "string" }, path: { type: "string" } },
			required: ["pattern"],
		},
	},
];

function show(pi: TurnPi, ctx: TurnCtx, text: string): void {
	if (!text) return;
	pi.sendMessage?.({ customType: "optchat-talk", content: text, display: true }, { triggerTurn: false });
	if (!pi.sendMessage) ctx.ui?.notify?.(text.slice(0, 500), "info");
}

function dumpRequest(payload: unknown): void {
	const path = process.env.Z0INT_OPTCHAT_DUMP;
	if (!path) return;
	appendFileSync(path, JSON.stringify(payload) + "\n");
}

async function freshCall(call: Call, ctx: TurnCtx, pi: TurnPi, opened: Json): Promise<void> {
	const complete = await loadComplete();
	const model = ctx.model;
	if (!model) throw new Error("no active model");
	const apiKey = await ctx.modelRegistry?.getApiKey?.(model);
	const pieces = Array.isArray(opened.pieces) ? opened.pieces.map(String) : [String(opened.view ?? "")];
	const blocks = Array.isArray(opened.blocks) ? opened.blocks.map(String) : [];
	const userText = blocks[1] ?? "";
	const content = [
		...pieces.map((text) => ({ type: "text", text, cache_control: { type: "ephemeral" } })),
		{ type: "text", text: userText },
	];
	const messages: unknown[] = [{ role: "user", content }];
	const cwd = ctx.cwd || process.cwd();
	dumpRequest({ system: opened.system, messages, tools: TOOLS.map((tool) => tool.name) });
	let reply = "";
	for (let round = 0; round < 8; round++) {
		const assistant = await complete(
			model,
			{ systemPrompt: [String(opened.system ?? "")], messages, tools: TOOLS },
			{ apiKey, cacheRetention: "short" },
		);
		messages.push(assistant);
		const said = textOf(assistant);
		const thought = (assistant.content ?? [])
			.filter((block) => block.type === "thinking" || block.type === "reasoning")
			.map((block) => String((block as { thinking?: string; text?: string }).thinking ?? block.text ?? ""))
			.join("");
		if (thought) show(pi, ctx, thought);
		if (said) {
			reply = said;
			await call("record", { kind: "talk", text: said }, 5000);
			show(pi, ctx, said);
		}
		if (assistant.errorMessage) ctx.ui?.notify?.(assistant.errorMessage, "error");
		const calls = toolCalls(assistant);
		if (calls.length === 0) break;
		for (const tool of calls) {
			const args = tool.arguments ?? {};
			const name = String(tool.name);
			await call("record", { kind: "tool", text: `${name} ${JSON.stringify(args)}` }, 5000);
			let result = "";
			let isError = false;
			try {
				result = await runTool(call, cwd, name, args);
			} catch (err) {
				isError = true;
				result = err instanceof Error ? err.message : String(err);
			}
			await call("record", { kind: "echo", text: result }, 5000);
			messages.push({
				role: "toolResult",
				toolCallId: tool.id,
				toolName: name,
				content: [{ type: "text", text: result }],
				isError,
			});
		}
		const extraRow = await call("boundary", {}, 5000);
		const extra = Array.isArray(extraRow.texts) ? extraRow.texts.map(String) : [];
		if (extra.length > 0) {
			for (const text of extra) await call("record", { kind: "user", text }, 5000);
			messages.push({ role: "user", content: extra.join("\n\n") });
		}
	}
	dumpRequest({ system: opened.system, messages, tools: TOOLS.map((tool) => tool.name), reply });
	if (!reply) ctx.ui?.notify?.("OptChat finished without a reply.", "warning");
}

async function drain(call: Call, ctx: TurnCtx, pi: TurnPi): Promise<void> {
	if (running) return;
	running = true;
	try {
		for (;;) {
			const taken = await call("take", {}, 5000);
			const texts = Array.isArray(taken.texts) ? taken.texts.map(String) : [];
			if (texts.length === 0) break;
			const hide = await showCompactRow(ctx.ui).catch(() => () => {});
			let settled: Json;
			try {
				settled = await call("settle", { timeout: 120 }, 130_000);
			} finally {
				hide();
			}
			if (settled.ok === false) {
				ctx.ui?.notify?.(`OptChat server: ${String(settled.error ?? "unavailable")}`, "error");
				return;
			}
			if (settled.ready !== true) {
				await call("abort", { texts }, 5000);
				const why = settled.error ? ` Compactor: ${String(settled.error)}` : "";
				ctx.ui?.notify?.(
					`OptChat left the message in the log, unanswered. The view is still compacting.${why}`,
					"warning",
				);
				continue;
			}
			const opened = await call("begin", { texts, agents: agentsText(ctx.cwd) }, 10_000);
			if (opened.ok === false || opened.ready !== true) {
				await call("abort", { texts }, 5000);
				ctx.ui?.notify?.("OptChat could not open a fresh turn. The message is logged, unanswered.", "warning");
				continue;
			}
			await freshCall(call, ctx, pi, opened);
			await call("persist", {}, 20_000);
			await call("finish", {}, 5000);
		}
	} catch (err) {
		const msg = err instanceof Error ? err.message : String(err);
		ctx.ui?.notify?.(`OptChat turn failed: ${msg}`, "error");
		dumpRequest({ error: msg });
	} finally {
		running = false;
		const left = await call("finish", {}, 5000);
		if (Array.isArray(left.texts) && left.texts.length > 0) void drain(call, ctx, pi);
	}
}

export function runTurn(call: Call, ctx: TurnCtx, text: string, pi: TurnPi): Promise<void> {
	return call("submit", { text }, 5000).then((row) => {
		if (row.action === "turn") return drain(call, ctx, pi);
	});
}

