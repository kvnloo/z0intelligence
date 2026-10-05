/**
 * OptChat for OMP. Off until `/optchat on`.
 *
 * On: the message is handled. The next call is fresh: system prompt, view,
 * then the new text. The existing OMP window is not sent. Off: OMP is unchanged.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { showCompactRow } from "./compact-ui.ts";
import { runTurn } from "./turn.ts";

const registration = Symbol.for("z0intelligence.optchat");
const ROOT = fileURLToPath(new URL("../..", import.meta.url));

type Json = Record<string, unknown>;

function flagPath(): string {
	const home = process.env.Z0INT_HOME || join(homedir(), ".z0int");
	return join(home, "optchat", "enabled");
}

function enabled(): boolean {
	try {
		return readFileSync(flagPath(), "utf8").trim() === "on";
	} catch {
		return false;
	}
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
	if (existsSync(venv)) return venv;
	return "python3";
}

let child: ChildProcessWithoutNullStreams | null = null;
let buf = "";
const pending = new Map<string, (row: Json) => void>();

function ensure(): ChildProcessWithoutNullStreams {
	if (child && child.exitCode === null) return child;
	child = spawn(pythonBin(), ["-m", "z0int.optchat"], {
		env: { ...process.env, PYTHONPATH: join(ROOT, "src") },
		stdio: ["pipe", "pipe", "pipe"],
	});
	child.stderr.resume();
	child.stdout.on("data", (chunk: Buffer) => {
		buf += chunk.toString("utf8");
		let nl = buf.indexOf("\n");
		while (nl >= 0) {
			const line = buf.slice(0, nl);
			buf = buf.slice(nl + 1);
			try {
				const row = JSON.parse(line) as Json;
				const id = String(row.id ?? "");
				pending.get(id)?.(row);
				pending.delete(id);
			} catch {
				/* torn line; the next one is whole */
			}
			nl = buf.indexOf("\n");
		}
	});
	return child;
}

function call(op: string, extra: Json = {}, timeoutMs = 1500): Promise<Json> {
	const id = randomUUID();
	const proc = ensure();
	const { promise, resolve } = Promise.withResolvers<Json>();
	const timer = setTimeout(() => {
		pending.delete(id);
		resolve({ ok: false, error: "timeout" });
	}, timeoutMs);
	pending.set(id, (row) => {
		clearTimeout(timer);
		resolve(row);
	});
	proc.stdin.write(JSON.stringify({ id, op, ...extra }) + "\n");
	return promise;
}

export default function optchat(pi: {
	on?: Function;
	registerTool?: Function;
	registerCommand?: Function;
	events?: object;
	setLabel?: Function;
	sendUserMessage?: (content: string, options?: { deliverAs?: "followUp" }) => void;
	sendMessage?: (message: { customType: string; content: string; display: boolean }, options?: { triggerTurn?: boolean }) => void;
}): void {
	if (pi.events && Reflect.get(pi.events, registration) === true) return;
	if (pi.events) Reflect.set(pi.events, registration, true);
	pi.setLabel?.("OptChat");

	pi.registerCommand?.("optchat", {
		description: "Turn the OptChat view on or off: /optchat on|off|status",
		async handler(args: string, ctx: { ui?: { notify?: (text: string, level?: string) => void; setWidget?: (key: string, content: unknown, options?: { placement?: "aboveEditor" }) => void } }) {
			const cmd = String(args || "status").trim().split(/\s+/)[0] || "status";
			if (cmd === "on" || cmd === "off") setEnabled(cmd === "on");
			else if (cmd !== "status") {
				ctx.ui?.notify?.("usage: /optchat on|off|status", "warning");
				return;
			}
			const on = enabled();
			if (on) {
				const hide = await showCompactRow(ctx.ui).catch(() => () => {});
				try {
					await call("settle", { timeout: 120 }, 130_000);
				} finally {
					hide();
				}
			}
			ctx.ui?.notify?.(
				on
					? "OptChat on. Compacting the view. The existing OMP window is not sent."
					: "OptChat off. The next message uses the OMP window again.",
				on ? "info" : "warning",
			);
		},
	});
	pi.registerCommand?.("optchat-browse", {
		description: "Write the OptChat memory as one HTML page",
		async handler(_args: string, ctx: { ui?: { notify?: (text: string, level?: string) => void } }) {
			const row = await call("browse", {}, 5000);
			ctx.ui?.notify?.(
				row.ok === false ? String(row.error) : `OptChat memory: ${row.path}`,
				row.ok === false ? "error" : "info",
			);
		},
	});


	pi.on?.("input", (event: { text?: string; source?: string }, ctx: Parameters<typeof runTurn>[1]) => {
		if (!enabled()) return undefined;
		if (event?.source === "extension") return undefined;
		const text = String(event?.text ?? "").trim();
		if (!text || text.startsWith("/")) return undefined;
		void runTurn(call, ctx, text, pi);
		return { handled: true };
	});

	pi.registerTool?.({
		name: "optchat_zoom",
		label: "OptChat zoom",
		loadMode: "essential",
		description: "Open the line id+n of the view into the two lines of n/2 under it; n = 1 gives the message whole.",
		parameters: {
			type: "object",
			properties: {
				id: { type: "integer", minimum: 0 },
				n: { type: "integer", minimum: 1 },
			},
			required: ["id", "n"],
		},
		async execute(_id: string, args: { id?: number; n?: number }) {
			const row = await call("zoom", { msg_id: args?.id ?? 0, n: args?.n ?? 1 });
			return { content: [{ type: "text", text: String(row.text ?? row.error ?? "") }], isError: row.ok === false };
		},
	});

	pi.registerTool?.({
		name: "optchat_date",
		label: "OptChat date",
		loadMode: "essential",
		description: "The date and time of message id.",
		parameters: {
			type: "object",
			properties: { id: { type: "integer", minimum: 0 } },
			required: ["id"],
		},
		async execute(_id: string, args: { id?: number }) {
			const row = await call("date", { msg_id: args?.id ?? 0 });
			return { content: [{ type: "text", text: String(row.text ?? row.error ?? "") }], isError: row.ok === false };
		},
	});
}
