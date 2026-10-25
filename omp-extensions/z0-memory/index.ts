/**
 * z0-memory: the z0 memory seam for OMP (and, through omo.ts, OMO/senpi) on the `context` event.
 *
 * `context` fires before each provider request with the messages about to be sent; a returned `{messages}` is
 * model-visible only, so the host's history and session file never hold the brief (oh-my-pi #107 / the
 * cognitive-state canary's virtual-context pattern, re-homed here). Only a request that starts a user turn (its
 * last message is the user's) gets the brief, inserted just before that message; tool continuations stay native.
 *
 * memory_inject: env Z0INT_MEMORY_INJECT (off | shadow | canary | on; default shadow). Shadow never returns
 * anything. The model endpoint is the active model's baseUrl and nothing else (no baseUrl counts as cloud; a cloud
 * model stays native until the owner sets allow_cloud_injection for this harness in the z0 memory config). The turn
 * key is the turn the z0int-bridge capture opened for the session when it is loaded (one canonical turn_key, so the
 * turn's MemoryUseReceipt joins its opportunity_record), else the user message's own timestamp, so a later turn
 * never reuses an earlier key after compaction; a re-sent
 * request of the same turn is a replay (no brief, counted in the client). Every failure is native context.
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
				// The turn the z0int-bridge capture opened for this session (same canonical turn_key, so the receipt joins
				// its opportunity_record); without the bridge, the user message's own id.
				const openTurn = (globalThis as any)[Symbol.for("z0int.bridge.openTurn")];
				const bridged = typeof openTurn === "function" ? openTurn(harness, sessionId) : undefined;
				const stamp = messages[last].timestamp;
				const turnId = bridged ?? (stamp != null ? `user@${stamp}` : `user-${messages.filter((m: any) => m?.role === "user").length}`);
				const brief = await client.turn({ sessionId, turnId, query, endpoint: ctx?.model?.baseUrl, cwd: ctx?.cwd });
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
