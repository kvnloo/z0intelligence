/**
 * OptChat beside the existing z0 extensions. It does not replace continuity,
 * TencentDB, ObservationPack, or the bridge.
 *
 * The view is model-visible only. The host session file is not rewritten.
 * Compaction is not on this path: a short message is its own line, and a
 * long one stays "(not summarized yet)" until a later compactor run.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const registration = Symbol.for("z0intelligence.optchat");
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const CAP = 30_000;

type Json = Record<string, unknown>;

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

function call(op: string, extra: Json = {}): Promise<Json> {
	const id = randomUUID();
	const proc = ensure();
	return new Promise((resolve) => {
		const timer = setTimeout(() => {
			pending.delete(id);
			resolve({ ok: false, error: "timeout" });
		}, 1500);
		pending.set(id, (row) => {
			clearTimeout(timer);
			resolve(row);
		});
		proc.stdin.write(JSON.stringify({ id, op, ...extra }) + "\n");
	});
}

function textOf(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content.filter((b) => b && typeof b === "object" && b.type === "text").map((b) => String(b.text ?? "")).join("\n");
}

export default function optchat(pi: { on?: Function; registerTool?: Function; events?: object }) {
	if (pi.events && Reflect.get(pi.events, registration) === true) return;
	if (pi.events) Reflect.set(pi.events, registration, true);

	pi.on?.("before_agent_start", async (event: { prompt?: string }) => {
		const prompt = String(event?.prompt ?? "").trim();
		if (!prompt || prompt.startsWith("/")) return undefined;
		const view = await call("view");
		await call("append", { kind: "user", text: prompt });
		if (view.ok !== true || typeof view.view !== "string" || !view.view.includes("+")) return undefined;
		return {
			message: {
				customType: "z0-optchat",
				display: false,
				content: "OptChat view of earlier messages. Zoom before acting on a summary.\n" + view.view,
			},
		};
	});

	pi.on?.("tool_call", (event: { toolName?: string; input?: unknown }) => {
		const name = String(event?.toolName ?? "tool");
		void call("append", { kind: "tool", text: `${name} ${JSON.stringify(event?.input ?? {})}` });
	});

	pi.on?.("tool_result", (event: { content?: unknown; text?: unknown }) => {
		const raw = typeof event?.text === "string" ? event.text : textOf(event?.content);
		void call("append", { kind: "echo", text: raw.slice(0, CAP + 200) });
	});

	pi.on?.("agent_end", (event: { messages?: unknown[] }) => {
		const messages = Array.isArray(event?.messages) ? event.messages : [];
		const last = messages[messages.length - 1] as { role?: string; content?: unknown } | undefined;
		if (last?.role === "assistant") {
			const text = textOf(last.content);
			if (text.trim()) void call("append", { kind: "talk", text });
		}
	});

	pi.registerTool?.({
		name: "optchat_zoom",
		label: "OptChat zoom",
		loadMode: "essential",
		description: "Open one OptChat line. n is a power of 2. id is the message index the view printed before +n. n=1 returns the whole message.",
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
		description: "Local date and time of OptChat message id. The view does not carry dates.",
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
