/**
 * Shadow context offload for OMP. Large tool results are archived verbatim and
 * the model sees head + tail + a recall handle. Fail-open: a pack error leaves
 * the original result. This does not route, deny, or change the model.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const PY = process.env.Z0INT_PYTHON || join(ROOT, ".venv", "bin", "python");
const THRESHOLD = Number(process.env.Z0INT_OBS_THRESHOLD || "6000");
const PACK_MS = Number(process.env.Z0INT_OBS_PACK_MS || "400");

function textOf(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content.map((part) => {
		if (typeof part === "string") return part;
		if (part && typeof part === "object" && "text" in part && typeof part.text === "string") return part.text;
		return "";
	}).join("\n");
}

function sessionOf(event: unknown): string {
	if (event && typeof event === "object" && "sessionId" in event && typeof event.sessionId === "string") {
		return event.sessionId;
	}
	return "omp";
}

function pack(text: string, session: string): Promise<string | null> {
	if (!existsSync(PY) || text.length <= THRESHOLD) return Promise.resolve(null);
	const { promise, resolve } = Promise.withResolvers<string | null>();
	const child = spawn(PY, ["-c", [
		"import json,sys",
		"from z0int.claude_code_obs import pack",
		"raw=json.load(sys.stdin)",
		"print(json.dumps(pack(raw['text'], raw['session'])))",
	].join("\n")], { stdio: ["pipe", "pipe", "ignore"] });
	const timer = setTimeout(() => { child.kill(); resolve(null); }, PACK_MS);
	let out = "";
	child.stdout.on("data", (chunk) => { out += chunk; });
	child.on("close", () => {
		clearTimeout(timer);
		try {
			const value: unknown = JSON.parse(out);
			resolve(typeof value === "string" ? value : null);
		} catch {
			resolve(null);
		}
	});
	child.on("error", () => { clearTimeout(timer); resolve(null); });
	child.stdin.write(JSON.stringify({ text, session }));
	child.stdin.end();
	return promise;
}

export default function z0Obspack(pi: ExtensionAPI) {
	pi.setLabel("z0 ObservationPack");
	pi.on("tool_result", async (event) => {
		try {
			const text = textOf(event.content);
			if (text.length <= THRESHOLD) return;
			const packed = await pack(text, sessionOf(event));
			if (!packed) return;
			return { content: [{ type: "text", text: packed }] };
		} catch {
			return;
		}
	});
}
