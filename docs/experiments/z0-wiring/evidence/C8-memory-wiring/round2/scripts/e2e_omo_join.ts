// C8 round-3 e2e, OMO join leg: the real z0int-bridge omo.ts (capture, real bridge worker) and z0-memory omo.ts
// (memory seam, shadow) on a fake senpi ExtensionAPI, one turn per question: before_agent_start -> context ->
// agent_end. The opportunity row the worker's detached build writes must carry the shadow receipt (checked later).
// usage: bun e2e_omo_join.ts <worktree> <questions.tsv> <task cwd>
import { readFileSync } from "node:fs";
import { join } from "node:path";

const [wt, tsv, cwd] = process.argv.slice(2);
const bridge = await import(join(wt, "omp-extensions", "z0int-bridge", "index.ts"));
const capture = (await import(join(wt, "omp-extensions", "z0int-bridge", "omo.ts"))).default;
const memory = (await import(join(wt, "omp-extensions", "z0-memory", "omo.ts"))).default;
const handlers = new Map<string, Array<(e: unknown, c: unknown) => unknown>>();
const pi = { on: (ev: string, fn: (e: unknown, c: unknown) => unknown) => handlers.set(ev, [...(handlers.get(ev) ?? []), fn]) };
capture(pi);
memory(pi);
const fire = async (ev: string, e: unknown, c: unknown) => { for (const fn of handlers.get(ev) ?? []) await fn(e, c); };
const questions = readFileSync(tsv, "utf8").split("\n").filter(Boolean).slice(0, 2).map(l => l.split("\t"));
for (const [qid, prompt] of questions) {
	const ctx = { cwd, sessionManager: { getSessionId: () => `join-omo-${qid}` }, model: { provider: "sandbox", id: "m", baseUrl: "http://127.0.0.1:9/v1" } };
	await fire("before_agent_start", { prompt }, ctx);
	await fire("context", { type: "context", messages: [{ role: "user", content: [{ type: "text", text: prompt }], timestamp: Date.now() }] }, ctx);
	await fire("agent_end", { messages: [] }, ctx);
	console.log(JSON.stringify({ qid, ok: true }));
}
await new Promise(r => setTimeout(r, 3000));
await bridge.stopWorker();
