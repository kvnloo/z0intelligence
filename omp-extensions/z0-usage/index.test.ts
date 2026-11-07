import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "bun:test";

import z0Usage, { formatUsageText, readUsageText } from "./index.ts";

test("formatUsageText prints three savings and every model tok/s", () => {
	const text = formatUsageText({
		savings: { sol_pi: 1240, rlm: 880, z0int_offload: 1860 },
		models: [
			{ id: "laya_421m", tok_s: 117.147 },
			{ id: "openjev_06b", tok_s: 87.93 },
			{ id: "frontier", tok_s: 42 },
		],
	});
	expect(text).toContain("/usage");
	expect(text).toContain("SoL-Pi 1240 tok saved");
	expect(text).toContain("RLM 880 tok saved");
	expect(text).toContain("z0int 1860 tok saved");
	expect(text).toContain("laya_421m 117 tok/s");
	expect(text).toContain("openjev_06b 88 tok/s");
	expect(text).toContain("frontier 42 tok/s");
});

test("registerCommand z0-usage notifies the formatted snapshot", () => {
	const home = join(tmpdir(), `z0-usage-${Date.now()}`);
	mkdirSync(join(home, "runtime"), { recursive: true });
	writeFileSync(
		join(home, "runtime", "usage-surface.json"),
		JSON.stringify({
			savings: { sol_pi: 1240, rlm: 880, z0int_offload: 1860 },
			models: [{ id: "laya_421m", tok_s: 117.147 }],
		}),
	);
	process.env.Z0INT_HOME = home;

	const commands = new Map<string, { handler: (args: string, ctx: unknown) => unknown }>();
	const pi = {
		setLabel: () => undefined,
		registerCommand: (name: string, definition: { handler: (args: string, ctx: unknown) => unknown }) => {
			commands.set(name, definition);
		},
	};
	z0Usage(pi);
	const ctx = { notifications: [] as string[], ui: { notify: (message: string) => ctx.notifications.push(message) } };
	const text = commands.get("z0-usage")?.handler("", ctx);
	expect(String(text)).toContain("SoL-Pi 1240 tok saved");
	expect(ctx.notifications[0]).toContain("laya_421m 117 tok/s");
	expect(readUsageText()).toContain("RLM 880 tok saved");
});
