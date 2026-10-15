import { describe, expect, test } from "bun:test";
import { installOptchat } from "./native.ts";
import type { Message } from "./native.ts";
import optchat, { flagPath } from "./index.ts";
import { FakeBackend, FakePi } from "./replay/fake.ts";

const user = (text: string, timestamp = 1): Message => ({ role: "user", content: text, timestamp });
function setup(on = true) {
	const pi = new FakePi();
	const backend = new FakeBackend();
	backend.on = on;
	installOptchat(pi, backend);
	return { pi, backend };
}

describe("native OMP OptChat", () => {
	test("enabled initialization only registers definitions while action APIs are unbound", async () => {
		const pi = new FakePi();
		const backend = new FakeBackend();
		let bound = false;
		pi.getActiveTools = () => {
			if (!bound) throw new Error("runtime not initialized");
			return pi.activeTools;
		};
		installOptchat(pi, backend);
		expect(pi.tools.size).toBe(2);
		expect(pi.toolUpdates).toBe(0);
		bound = true;
		await pi.hooks.session_start?.();
		expect(pi.activeTools).toContain("optchat_zoom");
	});
	test("real entry point defaults off with an absent flag and registers once", async () => {
		const home = process.env.Z0INT_HOME;
		const root = process.env.Z0INT_OPTCHAT_ROOT;
		try {
			process.env.Z0INT_HOME = `${import.meta.dir}/../../.cache/absent-optchat-home`;
			delete process.env.Z0INT_OPTCHAT_ROOT;
			const pi = new FakePi();
			optchat(pi);
			const context = pi.hooks.context;
			optchat(pi);
			expect(pi.hooks.context).toBe(context);
			await pi.start(user("ordinary prompt"));
			expect(await pi.context()).toEqual(pi.history);
			expect(pi.tools.size).toBe(0);
			expect(pi.toolUpdates).toBe(0);
			expect(pi.ui).toEqual([]);
			await pi.hooks.session_shutdown?.();
		} finally {
			if (home === undefined) delete process.env.Z0INT_HOME; else process.env.Z0INT_HOME = home;
			if (root === undefined) delete process.env.Z0INT_OPTCHAT_ROOT; else process.env.Z0INT_OPTCHAT_ROOT = root;
		}
	});
	test("off is inert, including browse, session events and context", async () => {
		const { pi, backend } = setup(false);
		pi.history = [user("prior")];
		await pi.start(user("now", 2));
		expect(await pi.context()).toEqual(pi.history);
		await pi.command("optchat-browse");
		expect(pi.tools.size).toBe(0);
		expect(pi.toolUpdates).toBe(0);
		expect(pi.ui).toEqual([]);
		expect(backend.calls).toEqual([]);
		expect(pi.hooks.session_before_compact).toBeUndefined();
	});
	test("commands toggle schemas, preserve native tools and report status", async () => {
		const { pi, backend } = setup(false);
		await pi.command("optchat", "on");
		expect(backend.on).toBe(true);
		expect([...pi.tools.keys()]).toEqual(["optchat_zoom", "optchat_date"]);
		expect(pi.activeTools).toEqual(["read", "bash", "optchat_zoom", "optchat_date"]);
		await pi.command("optchat-browse");
		expect(pi.ui.at(-1)).toContain("synthetic-memory.html");
		await pi.command("optchat", "off");
		expect(pi.activeTools).toEqual(["read", "bash"]);
		const calls = backend.calls.length;
		await pi.command("optchat", "status");
		await pi.command("optchat-browse");
		expect(backend.calls.length).toBe(calls);
		expect(pi.ui.at(-1)).toBe("OptChat off.");
		expect(await pi.zoom()).toContain("off");
		await pi.command("optchat", "on");
		expect(pi.tools.size).toBe(2);
		await pi.command("optchat", "invalid");
		expect(pi.ui.at(-1)).toContain("usage:");
	});
	test("concurrent context calls prepare once and reuse opaque live messages unchanged", async () => {
		const { pi, backend } = setup();
		pi.history = [user("prior")];
		await pi.start(user("now", 2));
		const [first, second] = await Promise.all([pi.context(), pi.context()]);
		expect(first).toEqual(second);
		expect(backend.calls.filter(call => call.op === "view_ids")).toHaveLength(1);
		const opaque: Message = { role: "assistant", content: [{ type: "thinking", signature: "keep", thinking: "reason" }], providerPayload: { bytes: [0, 255] } };
		pi.history.push(opaque);
		expect((await pi.context()).at(-1)).toEqual(opaque);
		expect(backend.calls.filter(call => call.op === "view_ids")).toHaveLength(1);
	});
	test("later turns append new chunks without refreshing frozen views; zoom budget resets only per prompt", async () => {
		const { pi, backend } = setup();
		pi.history = [user("prior")];
		await pi.start(user("first", 2));
		const first = await pi.context();
		await Promise.all([pi.zoom(), pi.zoom()]);
		expect(await pi.zoom()).toContain("limit reached");
		backend.viewRevision++;
		await pi.start(user("second", 3));
		const second = await pi.context();
		expect(second[0]).toEqual(first[0]);
		expect(second).toHaveLength(3);
		expect(await pi.zoom()).toBe("source detail");
		expect(backend.calls.filter(call => call.op === "append")).toHaveLength(2);
	});
	test("downstream context mutations cannot alter the cached prefix", async () => {
		const { pi } = setup();
		pi.history = [user("prior")];
		await pi.start(user("now", 2));
		const first = await pi.context();
		const bytes = JSON.stringify(first[0]);
		if (!first[0]) throw new Error("missing prefix");
		first[0].content = "another extension mutated this call";
		expect(JSON.stringify((await pi.context())[0])).toBe(bytes);
	});
	test("delayed message_start cannot install a new prefix or reset zoom after the first context", async () => {
		const { pi } = setup();
		pi.history = [user("prior")];
		const notify = pi.hooks.message_start;
		delete pi.hooks.message_start;
		const prompt = user("real non-interactive prompt", 2);
		await pi.start(prompt, false);
		const first = await pi.context();
		expect(first[0]?.content).toContain("OptChat history");
		await pi.zoom(); await pi.zoom();
		notify?.({ message: prompt }, pi.ctx);
		expect(await pi.context()).toEqual(first);
		expect(await pi.zoom()).toContain("limit reached");
	});
	test("timeout discards every tentative chunk and retains the previous frozen prefix", async () => {
		const pi = new FakePi();
		const backend = new FakeBackend();
		installOptchat(pi, backend, { preparationBudgetMs: 50 });
		pi.history = [user("oldest")];
		await pi.start(user("first turn", 2));
		const frozen = (await pi.context())[0];
		backend.ready = false;
		await pi.start(user("second turn", 3));
		const previous = await pi.context();
		backend.ready = true;
		const originalCall = backend.call;
		const late = Promise.withResolvers<Record<string, unknown>>();
		let views = 0;
		backend.call = async (op, args, timeout) => {
			if (op === "view_ids" && ++views === 2) return late.promise;
			return originalCall(op, args, timeout);
		};
		await pi.start(user("third turn", 4));
		const output = await pi.context();
		expect(views).toBe(2);
		expect(output).toEqual([...previous, user("third turn", 4)]);
		expect(output[0]).toEqual(frozen);
		late.resolve({ ok: true, ready: true, view: "late summary" });
		await Bun.sleep(1);
		expect(await pi.context()).toEqual(output);
	});
	test("unavailable summaries keep originals for the whole turn and retry next turn", async () => {
		const { pi, backend } = setup();
		backend.ready = false;
		pi.history = [user("prior")];
		await pi.start(user("first", 2));
		expect(await pi.context()).toEqual(pi.history);
		backend.ready = true;
		expect(await pi.context()).toEqual(pi.history);
		await pi.start(user("second", 3));
		expect((await pi.context())[0]?.content).toContain("OptChat history");
		expect(backend.calls.filter(call => call.op === "append")).toHaveLength(2);
	});
	test("RPC failures retain all originals and are not retried inside the tool loop", async () => {
		const { pi, backend } = setup();
		backend.fail = true;
		pi.history = [user("prior")];
		await pi.start(user("now", 2));
		expect(await pi.context()).toEqual(pi.history);
		backend.fail = false;
		expect(await pi.context()).toEqual(pi.history);
		expect(backend.calls).toHaveLength(1);
	});
	test("images and opaque historical messages remain complete", async () => {
		const { pi, backend } = setup();
		pi.history = [{ role: "user", content: [{ type: "image", data: "opaque", mimeType: "image/png" }] }];
		await pi.start(user("describe", 2));
		expect(await pi.context()).toEqual(pi.history);
		expect(backend.calls).toEqual([]);
	});
	test("branch changes, missing anchors and session changes cannot drop new history", async () => {
		const { pi } = setup();
		pi.history = [user("prior")];
		await pi.start(user("first", 2));
		await pi.context();
		pi.history = [user("different branch", 3)];
		await pi.start(user("next", 4));
		expect((await pi.context())[0]?.content).toContain("OptChat history");
		pi.session = "new-session";
		expect(await pi.context()).toEqual(pi.history);
		await pi.start(user("fresh", 5));
		pi.history = [user("compaction replaced the anchor", 6)];
		expect(await pi.context()).toEqual(pi.history);
	});
	test("off during asynchronous preparation stays pending until the next user turn", async () => {
		const { pi, backend } = setup();
		const pending = Promise.withResolvers<Record<string, unknown>>();
		backend.call = async () => pending.promise;
		pi.history = [user("prior")];
		await pi.start(user("now", 2));
		const context = pi.context();
		await pi.command("optchat", "off");
		pending.resolve({ ok: true, i: 0, ready: true, view: "old" });
		const first = await context;
		expect(first[0]?.content).toContain("OptChat history");
		expect(await pi.context()).toEqual(first);
		expect(pi.activeTools).toContain("optchat_zoom");
		expect(backend.on).toBe(true);
		await pi.start(user("next", 3));
		expect(await pi.context()).toEqual(pi.history);
		expect(pi.activeTools).toEqual(["read", "bash"]);
		expect(backend.on).toBe(false);
		expect(backend.calls).toEqual([]);
	});
	test("shutdown disposes once and leaves subsequent context inert", async () => {
		const { pi, backend } = setup();
		await pi.start(user("now"));
		pi.hooks.session_shutdown?.();
		expect(backend.disposals).toBe(1);
		expect(await pi.context()).toEqual(pi.history);
	});
	test("an external off flag is sampled at the next user turn", async () => {
		const { pi, backend } = setup();
		pi.history = [user("prior")];
		await pi.start(user("now", 2));
		const first = await pi.context();
		const calls = backend.calls.length;
		backend.on = false;
		expect(await pi.context()).toEqual(first);
		expect(pi.activeTools).toContain("optchat_zoom");
		await pi.start(user("next", 3));
		expect(await pi.context()).toEqual(pi.history);
		expect(pi.activeTools).toEqual(["read", "bash"]);
		expect(backend.calls.length).toBe(calls);
		expect(pi.ui).toEqual([]);
	});
	test("flag follows backend home and harness override", () => {
		const originalHome = process.env.Z0INT_HOME;
		const originalRoot = process.env.Z0INT_OPTCHAT_ROOT;
		try {
			process.env.Z0INT_HOME = "/synthetic/home";
			delete process.env.Z0INT_OPTCHAT_ROOT;
			expect(flagPath()).toBe("/synthetic/home/optchat/enabled");
			process.env.Z0INT_OPTCHAT_ROOT = "/synthetic/harness/chat";
			expect(flagPath()).toBe("/synthetic/harness/enabled");
		} finally {
			if (originalHome === undefined) delete process.env.Z0INT_HOME; else process.env.Z0INT_HOME = originalHome;
			if (originalRoot === undefined) delete process.env.Z0INT_OPTCHAT_ROOT; else process.env.Z0INT_OPTCHAT_ROOT = originalRoot;
		}
	});
});
