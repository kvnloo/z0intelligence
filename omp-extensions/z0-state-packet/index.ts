import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

type StartupEvent = { systemPrompt: string[]; prompt?: unknown };
type StartupResult = { systemPrompt: string[] } | undefined;
type StartupAPI = {
	on(event: "before_agent_start", handler: (event: StartupEvent) => StartupResult): void;
};

function read(reader: string, args: string[], input?: string): string {
	try {
		return execFileSync(process.env.Z0INT_STATE_PACKET_PYTHON || "/usr/bin/python3", [reader, ...args], {
			encoding: "utf8", timeout: 5000, maxBuffer: 300000, input: input ?? "",
			stdio: ["pipe", "pipe", "ignore"],
		}).trim();
	} catch {
		return "";
	}
}

// Keep the reader identical across harnesses. The installed path is content-stamped.
export default function register(pi: StartupAPI) {
	pi.on("before_agent_start", (event) => {
		const reader = fileURLToPath(new URL("../../src/z0int/orientation.py", import.meta.url));
		const prompt = typeof event?.prompt === "string" ? event.prompt : "";
		const parts = [read(reader, []), prompt ? read(reader, ["--corrections", "--raw"], prompt) : ""].filter(Boolean);
		return parts.length ? { systemPrompt: [...event.systemPrompt, ...parts] } : undefined;
	});
}
