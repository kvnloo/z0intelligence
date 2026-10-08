/**
 * Wait row while the view still has a placeholder. Not OMP /compact.
 * Does not call session.compact().
 */
import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

type Hide = () => void;
type Ui = {
	setWidget?: (
		key: string,
		content:
			| undefined
			| ((tui: unknown, theme: { fg?: (name: string, text: string) => string }) => { stop?: () => void }),
		options?: { placement?: "aboveEditor" | "belowEditor" },
	) => void;
};

const TUI = [
	"/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@oh-my-pi/pi-tui/src/index.ts",
	"/home/kvn/.bun/install/global/node_modules/@oh-my-pi/pi-tui/src/index.ts",
];
const THEME = [
	"/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@oh-my-pi/pi-tui/src/theme/index.ts",
	"/home/kvn/.bun/install/global/node_modules/@oh-my-pi/pi-tui/src/theme/index.ts",
];
const KEY = "z0-optchat-compact";

// Static import cannot resolve @oh-my-pi/pi-tui from this extension directory.
async function loadTui(): Promise<{
	Loader: new (...args: unknown[]) => { setWorkingRow: (spec: () => unknown, interrupt: () => void) => void; stop: () => void };
	getSymbolTheme: () => { spinnerFrames?: string[] };
}> {
	let loaderMod: { Loader?: unknown } | undefined;
	let themeMod: { getSymbolTheme?: () => { spinnerFrames?: string[] } } | undefined;
	for (const path of TUI) {
		if (!existsSync(path)) continue;
		loaderMod = (await import(pathToFileURL(path).href)) as { Loader?: unknown };
		break;
	}
	for (const path of THEME) {
		if (!existsSync(path)) continue;
		themeMod = (await import(pathToFileURL(path).href)) as { getSymbolTheme?: () => { spinnerFrames?: string[] } };
		break;
	}
	if (typeof loaderMod?.Loader !== "function" || typeof themeMod?.getSymbolTheme !== "function") {
		throw new Error("compact loader is not installed");
	}
	return {
		Loader: loaderMod.Loader as new (...args: unknown[]) => {
			setWorkingRow: (spec: () => unknown, interrupt: () => void) => void;
			stop: () => void;
		},
		getSymbolTheme: themeMod.getSymbolTheme,
	};
}

export async function showCompactRow(
	ui: Ui | undefined,
	onStop?: () => void,
	label = "Summarizing the view…",
	signal?: AbortSignal,
): Promise<Hide> {
	if (!ui?.setWidget || signal?.aborted) return () => {};
	const { Loader, getSymbolTheme } = await loadTui();
	if (signal?.aborted) return () => {};
	const startedAt = Date.now();
	let loader: { stop: () => void } | undefined;
	let hidden = false;
	const hide = () => {
		if (hidden) return;
		hidden = true;
		signal?.removeEventListener("abort", hide);
		loader?.stop();
		ui.setWidget?.(KEY, undefined);
	};
	signal?.addEventListener("abort", hide, { once: true });
	ui.setWidget(
		KEY,
		(tui, theme) => {
			const accent = (text: string) => theme.fg?.("accent", text) ?? text;
			const muted = (text: string) => theme.fg?.("muted", text) ?? text;
			const row = new Loader(tui, accent, muted, label, getSymbolTheme().spinnerFrames);
			row.setWorkingRow(
				() => ({
					label,
					startedAt,
					interruptKey: "escape",
				}),
				() => onStop?.(),
			);
			loader = row;
			if (hidden || signal?.aborted) row.stop();
			return row;
		},
		{ placement: "aboveEditor" },
	);
	return hide;
}
