/**
 * OMP /usage companion: context savings (SoL-Pi, RLM, z0int offload) and
 * tok/s for every model in the network. Does not replace builtin quota /usage.
 *
 * tok/s is never taken from assistant-message usage counts.
 */
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

type Json = Record<string, unknown>;

function snapshotPath(): string {
	const home = process.env.Z0INT_HOME || join(homedir(), ".z0int");
	return join(home, "runtime", "usage-surface.json");
}

function asCount(value: unknown): number {
	return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

export function formatUsageText(raw: Json | null): string {
	const savings = (raw?.savings && typeof raw.savings === "object" ? raw.savings : {}) as Json;
	const models = Array.isArray(raw?.models) ? raw.models : [];
	const lines = [
		"/usage",
		`SoL-Pi ${asCount(savings.sol_pi ?? savings.solPi)} tok saved`,
		`RLM ${asCount(savings.rlm)} tok saved`,
		`z0int ${asCount(savings.z0int_offload ?? savings.z0intOffload)} tok saved`,
	];
	for (const model of models) {
		if (!model || typeof model !== "object") continue;
		const row = model as Json;
		const id = typeof row.id === "string" && row.id ? row.id : "unknown";
		const tok = row.tok_s;
		if (typeof tok === "number" && Number.isFinite(tok)) {
			lines.push(`${id} ${Math.round(tok)} tok/s`);
		} else {
			lines.push(`${id} — tok/s`);
		}
	}
	return lines.join("\n");
}

export function readUsageText(path = snapshotPath()): string {
	if (!existsSync(path)) {
		return formatUsageText(null);
	}
	try {
		return formatUsageText(JSON.parse(readFileSync(path, "utf8")) as Json);
	} catch {
		return formatUsageText(null);
	}
}

export default function z0Usage(pi: {
	setLabel?: (label: string) => void;
	registerCommand?: (
		name: string,
		definition: { description?: string; handler: (args: string, ctx: unknown) => unknown },
	) => void;
}): void {
	pi.setLabel?.("z0 usage network");
	pi.registerCommand?.("z0-usage", {
		description: "Show /usage context savings (SoL-Pi, RLM, z0int) and network tok/s",
		handler(_args: string, ctx: unknown) {
			const text = readUsageText();
			const notify = (ctx as { ui?: { notify?: (message: string, level?: string) => void } })?.ui?.notify;
			notify?.(text, "info");
			return text;
		},
	});
}
