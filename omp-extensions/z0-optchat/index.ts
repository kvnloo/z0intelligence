/** Opt-in compression on the native OMP loop. */
import { spawn } from "node:child_process";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { installOptchat } from "./native.ts";
import type { Pi } from "./native.ts";
import type { Call, Json, NoticeHandler } from "./turn.ts";

const registration = Symbol.for("z0intelligence.optchat");
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
type Pending = { proc: ChildProcessWithoutNullStreams; settle: (row: Json) => void };

export function flagPath(): string {
	const configured = process.env.Z0INT_OPTCHAT_ROOT;
	if (configured) {
		const root = configured === "~" ? homedir() : configured.startsWith("~/") ? join(homedir(), configured.slice(2)) : configured;
		return join(root, "..", "enabled");
	}
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
	#epoch = 0;
	#stopping: Promise<void> | undefined;
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

	readonly call: Call = async (op, extra = {}, timeoutMs = 1500) => {
		const epoch = this.#epoch;
		await this.#stopping;
		if (epoch !== this.#epoch) return { ok: false, error: "OptChat request cancelled" };
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

	async dispose(): Promise<void> {
		this.#disposed = true;
		this.#notices.clear();
		await this.pause();
	}

	async pause(): Promise<void> {
		this.#epoch++;
		if (this.#stopping) return this.#stopping;
		const proc = this.#child;
		if (!proc) return;
		this.#fail(proc, "OptChat RPC client shut down");
		this.#child = undefined;
		const stopping = new Promise<void>(resolve => {
			const timer = setTimeout(() => { proc.kill("SIGKILL"); }, 2000);
			proc.once("close", () => { clearTimeout(timer); resolve(); });
			proc.kill("SIGTERM");
		});
		this.#stopping = stopping;
		await stopping;
		if (this.#stopping === stopping) this.#stopping = undefined;
	}
}

export default function optchat(pi: Pi): void {
	if (Reflect.get(pi, registration) === true) return;
	Reflect.set(pi, registration, true);
	const client = new RpcClient();
	installOptchat(pi, { call: client.call, enabled, setEnabled, pause: () => client.pause(), dispose: () => client.dispose() });
}
