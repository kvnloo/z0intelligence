import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

type StartupEvent = { systemPrompt: string[] };
type StartupResult = { systemPrompt: string[] } | undefined;
type StartupAPI = {
	on(event: "before_agent_start", handler: (event: StartupEvent) => StartupResult): void;
};

// Keep the reader identical across harnesses. The installed path is content-stamped.
export default function register(pi: StartupAPI) {
	pi.on("before_agent_start", (event) => {
		try {
			const reader = fileURLToPath(new URL("../../src/z0int/orientation.py", import.meta.url));
			const context = execFileSync(process.env.Z0INT_STATE_PACKET_PYTHON || "/usr/bin/python3", [reader], {
				encoding: "utf8", timeout: 5000, maxBuffer: 300000, stdio: ["ignore", "pipe", "ignore"],
			}).trim();
			return context ? { systemPrompt: [...event.systemPrompt, context] } : undefined;
		} catch {
			return undefined;
		}
	});
}
