/**
 * z0-memory: the z0 memory seam for OMP (and, through omo.ts, OMO/senpi) on the `context` event.
 *
 * `context` fires before each provider request with the messages about to be sent; a returned `{messages}` is
 * model-visible only, so the host's history and session file never hold the brief (oh-my-pi #107 / the
 * cognitive-state canary's virtual-context pattern, re-homed here). Only a request that starts a user turn (its
 * last message is the user's) gets the brief, inserted just before that message; tool continuations stay native.
 *
 * memory_inject: env Z0INT_MEMORY_INJECT (off | shadow | canary | on; default shadow). Shadow never returns
 * anything. The model endpoint is the active model's baseUrl (a cloud model stays native until the owner sets
 * allow_cloud_injection for this harness in the z0 memory config). Every failure is native context.
 * MCP: mcp.json in this directory is the memory-only `z0-memory` server entry for ~/.omp/agent/mcp.json.
 */
import { createMemoryClient } from "../../harness-adapters/memory-client.mjs";

type Pi = { on(event: string, handler: (event: any, ctx?: any) => unknown): void };
export type PushStatus = { status: "SUPPORTED" | "UNSUPPORTED"; missing: string[] };

function text(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content.filter((b: any) => b?.type === "text").map((b: any) => String(b.text ?? "")).join("\n");
}

export function registerMemory(pi: Pi, harness: "omp" | "omo"): PushStatus {
	const client = createMemoryClient({ harness, env: process.env });
	if (client.mode === "off") return { status: "SUPPORTED", missing: [] };
	try {
		pi.on("context", async (event: { messages?: any[] }, ctx?: any) => {
			try {
				const messages = event?.messages ?? [];
				const last = messages.length - 1;
				if (last < 0 || messages[last]?.role !== "user") return undefined;
				const query = text(messages[last].content);
				if (!query.trim()) return undefined;
				const sessionId = ctx?.sessionManager?.getSessionId?.() ?? ctx?.sessionId ?? harness;
				const users = messages.filter((m: any) => m?.role === "user").length;
				const brief = await client.turn({ sessionId, turnId: `user-${users}`, query,
					endpoint: ctx?.model?.baseUrl ?? process.env.Z0INT_MEMORY_ENDPOINT, cwd: ctx?.cwd });
				if (!brief) return undefined;
				const injected = { role: "user", content: [{ type: "text", text: brief }], timestamp: messages[last].timestamp };
				return { messages: [...messages.slice(0, last), injected, messages[last]] };
			} catch {
				return undefined;
			}
		});
	} catch {
		return { status: "UNSUPPORTED", missing: ["context"] };
	}
	return { status: "SUPPORTED", missing: [] };
}

export default function z0Memory(pi: Pi): PushStatus {
	return registerMemory(pi, "omp");
}
