import assert from "node:assert/strict";
import { installOptchat, PREPARATION_BUDGET_MS } from "../native.ts";
import type { Message } from "../native.ts";
import { FakeBackend, FakePi } from "./fake.ts";

const user = (content: string, timestamp: number): Message => ({ role: "user", content, attribution: "user", timestamp });
function fixture(on = true, preparationBudgetMs?: number) {
	const pi = new FakePi();
	const backend = new FakeBackend();
	backend.on = on;
	installOptchat(pi, backend, { preparationBudgetMs });
	return { pi, backend };
}
export type Regression = { scenario: string; passed: true; elapsedMs?: number; calls?: number };

/** Real timers and a deliberately non-cancellable RPC exercise late-completion safety. */
export async function slowPreparation(budgetMs = PREPARATION_BUDGET_MS, delayMs = 1100): Promise<Regression> {
	const { pi, backend } = fixture(true, budgetMs);
	pi.history = Array.from({ length: 29 }, (_, i) => user(`historical ${i}`, i));
	const call = backend.call;
	backend.call = async (op, args, timeout) => {
		// Intentionally ignore the RPC timeout: only the whole-operation race can save us.
		await Bun.sleep(delayMs);
		return call(op, args, timeout);
	};
	await pi.start(user("current", 100));
	const started = performance.now();
	const [first, concurrent] = await Promise.all([pi.context(), pi.context()]);
	const elapsedMs = performance.now() - started;
	assert.deepEqual(first, pi.history);
	assert.deepEqual(concurrent, first);
	assert.ok(elapsedMs < budgetMs + 3000, `preparation exceeded budget: ${elapsedMs}ms`);
	const atDeadline = backend.calls.length;
	await Bun.sleep(delayMs * 2);
	assert.ok(backend.calls.length <= atDeadline + 1, "preparation continued importing after its deadline");
	assert.deepEqual(await pi.context(), first, "late work changed the fallback prefix");
	return { scenario: "slow-backend-deadline", passed: true, elapsedMs, calls: backend.calls.length };
}

export async function continuation(): Promise<Regression> {
	const { pi, backend } = fixture();
	pi.history = [user("prior", 1)];
	await pi.start(user("current", 2));
	const first = await pi.context();
	await pi.zoom(); await pi.zoom();
	const live: Message[] = [
		{ role: "assistant", content: [{ type: "toolCall", id: "r", name: "read", arguments: {} }], timestamp: 3 },
		{ role: "toolResult", toolCallId: "r", content: "original source bytes", timestamp: 4 },
	];
	pi.history.push(...live);
	const toolContinuation = await pi.context();
	assert.deepEqual(toolContinuation, [...first, ...live]);
	await pi.continueAutomatically();
	assert.deepEqual(await pi.context(), [...toolContinuation, pi.history.at(-1)]);
	assert.match(await pi.zoom(), /limit reached/);
	assert.equal(backend.calls.filter(call => call.op === "view_ids").length, 1);
	// Steering is a real user message, but belongs to the CURRENT run.
	const steer = user("steer this run", 5);
	pi.hooks.message_start?.({ message: steer }, pi.ctx);
	pi.history.push(steer);
	assert.deepEqual((await pi.context()).at(-1), steer);
	assert.match(await pi.zoom(), /limit reached/);
	// Non-interactive user prompts still carry real message identity.
	await pi.start(user("new user prompt", 6), false);
	await pi.context();
	assert.equal(await pi.zoom(), "source detail");
	return { scenario: "automatic-and-tool-continuation", passed: true };
}

export async function compaction(): Promise<Regression> {
	const { pi } = fixture();
	pi.history = [user("prior", 1)];
	await pi.start(user("current", 2));
	const original: Message = { role: "toolResult", toolCallId: "read", content: [{ type: "text", text: "complete original tool bytes" }], timestamp: 3 };
	pi.history.push(original);
	const first = await pi.context();
	assert.deepEqual(pi.hooks.session_before_compact?.(), { cancel: true });
	assert.deepEqual(await pi.context(), first);
	// Simulate a host compaction that bypasses cancellation, including omitted live messages.
	pi.history = [user("host compaction summary", 7), { role: "assistant", content: "replacement suffix", timestamp: 8 }];
	pi.hooks.session_compact?.();
	assert.deepEqual(await pi.context(), pi.history);
	pi.history.push(user("still the same run", 9));
	assert.deepEqual(await pi.context(), pi.history, "degraded turn resumed mixing histories");
	assert.match(await pi.zoom(), /off/);
	await pi.start(user("new turn", 10));
	assert.match(String((await pi.context())[0]?.content), /OptChat history/);
	// Pruning has no cancellation hook, and can mutate content in place without compaction.
	pi.history.push(structuredClone(original));
	await pi.context();
	const last = pi.history.at(-1);
	if (!last) throw new Error("missing tool output");
	last.content = "[pruned]";
	last.prunedAt = 123;
	assert.deepEqual(await pi.context(), pi.history);
	delete last.prunedAt;
	last.content = "complete original tool bytes";
	assert.deepEqual(await pi.context(), pi.history, "pruning fallback must remain sticky");
	return { scenario: "compaction-and-pruning-mid-turn", passed: true };
}

export async function streamingToggle(): Promise<Regression> {
	const { pi, backend } = fixture();
	pi.history = [user("prior", 1)];
	await pi.start(user("current", 2));
	const first = await pi.context();
	await pi.zoom(); await pi.zoom();
	const updates = pi.toolUpdates;
	const calls = backend.calls.length;
	await pi.command("optchat", "on");
	assert.deepEqual(await pi.context(), first);
	assert.equal(pi.toolUpdates, updates);
	assert.equal(backend.calls.length, calls);
	assert.match(await pi.zoom(), /limit reached/);
	await pi.command("optchat", "off");
	assert.match(pi.ui.at(-1) ?? "", /off pending next user turn/);
	assert.equal(backend.on, true);
	assert.equal(backend.pauses, 0);
	await pi.continueAutomatically();
	assert.deepEqual((await pi.context()).slice(0, first.length), first);
	assert.equal(pi.toolUpdates, updates);
	assert.match(await pi.zoom(), /limit reached/);
	// An on cancels a pending off without resetting the current turn.
	await pi.command("optchat", "on");
	assert.equal(pi.ui.at(-1), "OptChat on.");
	assert.match(await pi.zoom(), /limit reached/);
	await pi.command("optchat", "off");
	await pi.start(user("next user turn", 3));
	assert.equal(backend.on, false);
	assert.deepEqual(await pi.context(), pi.history);
	assert.deepEqual(pi.activeTools, ["read", "bash"]);
	return { scenario: "streaming-toggle-and-redundant-on", passed: true };
}

export async function offAfterOn(): Promise<Regression> {
	const { pi, backend } = fixture(false);
	await pi.command("optchat", "on");
	assert.equal(pi.tools.size, 2);
	await pi.start(user("enabled turn", 1));
	await pi.context();
	await pi.command("optchat", "off");
	await pi.start(user("disabled turn", 2));
	await pi.context();
	// OMP exposes no unregisterTool: zero ACTIVE definitions, two inert registry entries.
	assert.deepEqual(pi.activeTools, ["read", "bash"]);
	assert.equal(pi.tools.size, 2);
	const calls = backend.calls.length;
	for (const tool of pi.tools.values()) {
		const result = await tool.execute("direct-off-execution", { id: 0, n: 1 });
		assert.equal(result.isError, true);
		assert.match(result.content[0]?.text ?? "", /off/);
	}
	assert.equal(backend.calls.length, calls);
	await pi.command("optchat", "on");
	assert.deepEqual(pi.activeTools, ["read", "bash"]);
	await pi.continueAutomatically();
	assert.deepEqual(pi.activeTools, ["read", "bash"]);
	await pi.start(user("enabled again", 3));
	await pi.context();
	assert.equal(pi.tools.size, 2);
	assert.ok(pi.activeTools.includes("optchat_zoom"));
	return { scenario: "off-after-on-tool-state", passed: true };
}

export async function regressions(budgetMs = PREPARATION_BUDGET_MS, delayMs = 1100): Promise<Regression[]> {
	return [await slowPreparation(budgetMs, delayMs), await continuation(), await compaction(), await streamingToggle(), await offAfterOn()];
}
