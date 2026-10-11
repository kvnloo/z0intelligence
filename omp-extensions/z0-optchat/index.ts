/** OptChat is opt-in; each enabled input uses a fresh view, not OMP history. */
import { spawn } from "node:child_process";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { cancelTurn, checkedCall, runTurn } from "./turn.ts";
import type { Call, Json, NoticeHandler, TurnCtx, TurnPi } from "./turn.ts";

const registration = Symbol.for("z0intelligence.optchat");
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
type InputEvent = { text?: string; source?: string };
type Command = { description: string; handler: (args: string, ctx: TurnCtx) => Promise<void> };
type ToolResult = { content: { type: "text"; text: string }[]; isError: boolean };
type Tool = {
	name: string;
	label: string;
	description: string;
	parameters: Json;
	execute: (id: string, args: { id?: number; n?: number }, signal?: AbortSignal) => Promise<ToolResult>;
};
type Pi = TurnPi & {
	on?: (event: string, handler: (event: InputEvent, ctx: TurnCtx) => unknown) => void;
	registerTool?: (tool: Tool) => void;
	registerCommand?: (name: string, command: Command) => void;
	events?: object;
	setLabel?: (label: string) => void;
};
type Pending = { proc: ChildProcessWithoutNullStreams; settle: (row: Json) => void };

function flagPath(): string {
	const home = process.env.Z0INT_HOME || join(homedir(), ".z0int");
	return join(home, "optchat", "enabled");
}

function enabled(): boolean {
	try { return readFileSync(flagPath(), "utf8").trim() === "on"; } catch { return false; }
}

function setEnabled(on: boolean): void {
	const path = flagPath();
	mkdirSync(join(path, ".."), { recursive: true });
	writeFileSync(path, on ? "on\n" : "off\n");
}

function pythonBin(): string {
	const fromEnv = process.env.Z0INT_PYTHON;
	if (fromEnv && existsSync(fromEnv)) return fromEnv;
	const venv = join(ROOT, ".venv", "bin", "python");
	return existsSync(venv) ? venv : "python3";
}

/** Only this extension instance's child, buffers and RPC requests are owned here. */
class RpcClient {
	#child: ChildProcessWithoutNullStreams | undefined;
	#pending = new Map<string, Pending>();
	#disposed = false;
	#fault: string | undefined;
	#notices = new Set<NoticeHandler>();

	constructor() {
		this.call.onNotice = (handler) => {
			this.#notices.add(handler);
			return () => { this.#notices.delete(handler); };
		};
	}

	#fail(proc: ChildProcessWithoutNullStreams, error: string): void {
		if (this.#child === proc) this.#fault = error;
		for (const [id, entry] of this.#pending) {
			if (entry.proc !== proc) continue;
			this.#pending.delete(id);
			entry.settle({ ok: false, error });
		}
	}

	#ensure(): ChildProcessWithoutNullStreams {
		if (this.#disposed) throw new Error("OptChat RPC client is disposed");
		if (this.#child && this.#child.exitCode === null && this.#child.signalCode === null) {
			if (this.#fault || this.#child.killed) throw new Error(this.#fault || "OptChat child is stopping");
			return this.#child;
		}
		const proc = spawn(pythonBin(), ["-u", "-m", "z0int.optchat"], {
			env: { ...process.env, PYTHONPATH: join(ROOT, "src") },
			stdio: ["pipe", "pipe", "pipe"],
		});
		this.#child = proc;
		this.#fault = undefined;
		// A restart gets a new buffer. Old partial stdout cannot corrupt its RPCs.
		let buffer = "";
		let stderr = "";
		proc.stderr.setEncoding("utf8");
		proc.stdout.setEncoding("utf8");
		proc.stderr.on("data", (chunk: string) => { stderr = (stderr + chunk).slice(-4096); });
		proc.stdout.on("data", (chunk: string) => {
			if (this.#child !== proc) return;
			buffer += chunk;
			let newline = buffer.indexOf("\n");
			while (newline >= 0) {
				const line = buffer.slice(0, newline);
				buffer = buffer.slice(newline + 1);
				try {
					const row = JSON.parse(line) as Json;
					if (row.event === "compactor_error" && row.id === undefined) {
						for (const handler of this.#notices) handler(row);
					}
					const id = String(row.id ?? "");
					const entry = this.#pending.get(id);
					if (entry?.proc === proc) { this.#pending.delete(id); entry.settle(row); }
				} catch { /* Skip malformed stdout, never attach it to a later child. */ }
				newline = buffer.indexOf("\n");
			}
		});
		proc.once("error", (error) => {
			buffer = "";
			this.#fail(proc, `OptChat child spawn error: ${error.message}`);
			if (this.#child === proc) this.#child = undefined;
		});
		proc.stdin.on("error", (error) => {
			this.#fail(proc, `OptChat child stdin error: ${error.message}`);
			proc.kill("SIGTERM");
		});
		proc.once("exit", (code, signal) => {
			buffer = "";
			this.#fail(proc, `OptChat child exited ${signal ? `on ${signal}` : `with code ${code}`}${stderr.trim() ? `: ${stderr.trim()}` : ""}`);
			if (this.#child === proc) this.#child = undefined;
		});
		return proc;
	}

	readonly call: Call = (op, extra = {}, timeoutMs = 1500) => {
		const id = randomUUID();
		let proc: ChildProcessWithoutNullStreams;
		try { proc = this.#ensure(); } catch (error) { return Promise.resolve({ ok: false, error: String(error) }); }
		const { promise, resolve } = Promise.withResolvers<Json>();
		const timer = timeoutMs > 0 ? setTimeout(() => {
			this.#pending.delete(id);
			resolve({ ok: false, error: `OptChat ${op} timed out` });
		}, timeoutMs) : undefined;
		this.#pending.set(id, { proc, settle: (row) => { clearTimeout(timer); resolve(row); } });
		try {
			proc.stdin.write(JSON.stringify({ id, op, ...extra }) + "\n", (error) => {
				if (error) {
					this.#fail(proc, `OptChat child write error: ${error.message}`);
					proc.kill("SIGTERM");
				}
			});
		} catch (error) {
			this.#fail(proc, `OptChat child write error: ${String(error)}`);
			proc.kill("SIGTERM");
		}
		return promise;
	};

	dispose(): void {
		this.#disposed = true;
		this.#notices.clear();
		const proc = this.#child;
		if (!proc) return;
		this.#fail(proc, "OptChat RPC client shut down");
		proc.kill("SIGTERM");
	}
}

export default function optchat(pi: Pi): void {
	if (pi.events && Reflect.get(pi.events, registration) === true) return;
	if (pi.events) Reflect.set(pi.events, registration, true);
	const client = new RpcClient();
	let shuttingDown = false;
	let toolsRegistered = false;
	const registerTools = () => {
		if (toolsRegistered || !pi.registerTool) return;
		toolsRegistered = true;
		for (const name of ["zoom", "date"]) {
			pi.registerTool({
				name: `optchat_${name}`,
				label: `OptChat ${name}`,
				description: name === "zoom"
					? "Open the line id+n of the view into the two lines of n/2 under it; n = 1 gives the message whole."
					: "The date and time of message id.",
				parameters: {
					type: "object",
					properties: { id: { type: "integer", minimum: 0 }, ...(name === "zoom" ? { n: { type: "integer", minimum: 1 } } : {}) },
					required: name === "zoom" ? ["id", "n"] : ["id"],
				},
				async execute(_id, args, signal) {
					if (shuttingDown || !enabled() || signal?.aborted) return { content: [{ type: "text", text: "OptChat is off or cancelled." }], isError: true };
					const row = await client.call(name, { msg_id: args.id ?? 0, ...(name === "zoom" ? { n: args.n ?? 1 } : {}) });
					if (!enabled() || signal?.aborted) return { content: [{ type: "text", text: "OptChat is off or cancelled." }], isError: true };
					return { content: [{ type: "text", text: String(row.text ?? row.error ?? "") }], isError: row.ok === false };
				},
			});
		}
	};
	if (enabled()) { pi.setLabel?.("OptChat"); registerTools(); }

	pi.registerCommand?.("optchat", {
		description: "Turn the OptChat view on or off: /optchat on|off|status",
		async handler(args, ctx) {
			const cmd = String(args || "status").trim().split(/\s+/)[0] || "status";
			if (cmd !== "on" && cmd !== "off" && cmd !== "status") { ctx.ui?.notify?.("usage: /optchat on|off|status", "warning"); return; }
			if (shuttingDown) return;
			try {
				if (cmd === "on" || cmd === "off") {
					const on = cmd === "on";
					setEnabled(on);
					if (on) { registerTools(); pi.setLabel?.("OptChat"); await checkedCall(client.call, "enabled", { on }); }
					else await Promise.all([cancelTurn(client.call), checkedCall(client.call, "enabled", { on })]);
				}
				if (!enabled()) { ctx.ui?.notify?.("OptChat off. The next message uses the OMP window again.", "warning"); return; }
				const status = await checkedCall(client.call, "status");
				const missing = Number(status.placeholders ?? 0);
				const messages = Number(status.messages ?? 0);
				ctx.ui?.notify?.(missing > 0
					? `OptChat on. The one chat has ${messages} messages; ${missing} lines are still summarizing in the background. This session is not compacted.`
					: "OptChat on. The view is ready. The existing OMP window is not sent.", "info");
			} catch (error) { ctx.ui?.notify?.(String(error), "error"); }
		},
	});
	pi.registerCommand?.("optchat-browse", {
		description: "Write the OptChat memory as one HTML page",
		async handler(_args, ctx) {
			if (shuttingDown) return;
			try { const row = await checkedCall(client.call, "browse"); ctx.ui?.notify?.(`OptChat memory: ${row.path}`, "info"); }
			catch (error) { ctx.ui?.notify?.(String(error), "error"); }
		},
	});
	pi.on?.("input", (event, ctx) => {
		if (shuttingDown || !enabled() || event.source === "extension") return undefined;
		const text = String(event.text ?? "");
		if (!text.trim() || text.trimStart().startsWith("/")) return undefined;
		runTurn(client.call, ctx, text, pi).catch((error: unknown) => {
			if (shuttingDown || !enabled()) return;
			try { ctx.ui?.notify?.(`OptChat turn failed: ${String(error)}`, "error"); } catch { /* The detached turn rejection is handled even if host UI fails. */ }
		});
		return { handled: true };
	});
	for (const event of ["session_before_switch", "session_before_branch", "session_before_tree", "session_stop"]) {
		pi.on?.(event, () => cancelTurn(client.call));
	}
	pi.on?.("session_shutdown", async () => {
		shuttingDown = true;
		try { await cancelTurn(client.call); } finally { client.dispose(); }
	});
}
