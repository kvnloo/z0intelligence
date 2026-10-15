import assert from "node:assert/strict";
import { regressions } from "./regressions.ts";
import { installOptchat } from "../native.ts";
import type { Message } from "../native.ts";
import { FakeBackend, FakePi } from "./fake.ts";

export type Metrics = {
	mode: "legacy" | "new" | "off";
	calls: number;
	prefixStablePercent: number;
	cacheRead: number;
	cacheWrite: number;
	inTurnDropped: number;
	zoomHints: number;
	zoomRequests: number;
	zoomExecuted: number;
	perCall: { call: number; prefixStablePercent: number; commonPrefixChars: number; cacheRead: number; cacheWrite: number; inTurnDropped: number }[];
};
const key = (message: Message) => JSON.stringify(message);
export function longestCommonPrefix(left: string, right: string): number {
	let index = 0;
	while (index < Math.min(left.length, right.length) && left[index] === right[index]) index++;
	return index;
}
const history = (): Message[] => Array.from({ length: 8 }, (_, index) => ({
	role: index % 2 === 0 ? "user" : "assistant", content: `Earlier turn ${index}: ${"historical detail ".repeat(100)}`, timestamp: index,
}));

/** Faithful old hook: reread the changing view and keep only the last native message. */
function legacy(messages: Message[], round: number): Message[] {
	return [{ role: "user", content: `<chat>\n0+${messages.length}|view at round ${round}\n${"changing summaries ".repeat(150)}\n</chat>\nZoom before acting on a summary.`, timestamp: round }, ...messages.slice(-1)];
}

export async function replay(mode: Metrics["mode"]): Promise<Metrics> {
	const pi = new FakePi();
	const backend = new FakeBackend();
	backend.on = mode !== "off";
	if (mode !== "legacy") installOptchat(pi, backend);
	pi.history = history();
	const user: Message = { role: "user", content: "Inspect ten files, then finish the patch.", timestamp: 10 };
	await pi.start(user);
	const inTurn = [user];
	let previous: Message[] | undefined;
	let initialPrefix: string | undefined;
	let zoomHints = 0;
	let zoomRequests = 0;
	let zoomExecuted = 0;
	const perCall: Metrics["perCall"] = [];
	for (let round = 0; round <= 10; round++) {
		backend.viewRevision++;
		const output = mode === "legacy" ? legacy(structuredClone(pi.history), round) : await pi.context();
		const dropped = inTurn.filter(message => !output.some(candidate => key(candidate) === key(message))).length;
		// Concatenated message records avoid JSON-array closing punctuation counting as a miss.
		const wire = output.map(key).join("\n") + "\n";
		const previousWire = previous?.map(key).join("\n");
		const common = previousWire === undefined ? 0 : longestCommonPrefix(previousWire + "\n", wire);
		const stable = previousWire === undefined ? 100 : common / (previousWire.length + 1) * 100;
		perCall.push({ call: round + 1, prefixStablePercent: stable, commonPrefixChars: common,
			cacheRead: common / 4, cacheWrite: (wire.length - common) / 4, inTurnDropped: dropped });
		zoomHints += Number(wire.includes("Zoom before acting on a summary"));
		if (mode === "new") {
			assert.equal(dropped, 0);
			assert.deepEqual(output.slice(-inTurn.length), inTurn);
			const prefix = output.slice(0, -inTurn.length).map(key).join("\n");
			initialPrefix ??= prefix;
			assert.equal(prefix, initialPrefix, "historical prefix changed inside the tool loop");
			if (previous) assert.equal(common, previousWire?.length === undefined ? 0 : previousWire.length + 1);
			assert.equal(backend.calls.filter(call => call.op === "view_ids").length, 1);
		}
		if (mode === "off") assert.deepEqual(output, pi.history);
		if (round < 10) {
			if (mode !== "off") zoomRequests++;
			if (mode === "legacy") zoomExecuted++;
			else if (mode === "new") {
				const result = await pi.zoom();
				if (round < 2) { assert.equal(result, "source detail"); zoomExecuted++; }
				else assert.match(result, /limit reached/);
			}
			const assistant: Message = { role: "assistant", content: [
				{ type: "text", text: `Checking file ${round}.` },
				{ type: "toolCall", id: `read-${round}`, name: "read", arguments: { path: `file-${round}.ts` } },
			], timestamp: 20 + round * 2, opaqueProviderPayload: { exact: round } };
			const result: Message = { role: "toolResult", toolCallId: `read-${round}`, toolName: "read",
				content: [{ type: "text", text: `File ${round}: ${"useful source code ".repeat(80)}` }], isError: false, timestamp: 21 + round * 2 };
			inTurn.push(assistant, result);
			pi.history.push(assistant, result);
		}
		previous = output;
	}
	if (mode === "new") { assert.ok(zoomHints <= 1); assert.equal(zoomExecuted, 2); }
	if (mode === "off") {
		assert.equal(pi.tools.size, 0); assert.equal(pi.toolUpdates, 0); assert.equal(backend.calls.length, 0); assert.deepEqual(pi.ui, []);
	}
	const sum = (field: "cacheRead" | "cacheWrite" | "inTurnDropped") => perCall.reduce((total, call) => total + call[field], 0);
	return { mode, calls: perCall.length,
		prefixStablePercent: perCall.slice(1).reduce((total, call) => total + call.prefixStablePercent, 0) / (perCall.length - 1),
		cacheRead: sum("cacheRead"), cacheWrite: sum("cacheWrite"), inTurnDropped: sum("inTurnDropped"),
		zoomHints, zoomRequests, zoomExecuted, perCall };
}

export async function comparison(): Promise<Metrics[]> {
	return Promise.all([replay("legacy"), replay("new"), replay("off")]);
}

if (import.meta.main) {
	const mode = process.argv[2];
	if (mode === "regressions") {
		console.log(JSON.stringify(await regressions(), null, 2));
		process.exit(0);
	}
	if (mode && !["legacy", "new", "off"].includes(mode)) throw new Error("usage: replay.ts [legacy|new|off|regressions]");
	const results = mode === "legacy" || mode === "new" || mode === "off" ? [await replay(mode)] : await comparison();
	console.log("Simulated message-prefix cache estimates (chars/4), not provider billing. Cold call excluded from stability %.");
	console.table(results.map(({ perCall: _, ...row }) => row));
	console.log(JSON.stringify(results, null, 2));
}
