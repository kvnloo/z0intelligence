/**
 * OMO (omo-ai / senpi) memory entry: the z0-memory `context` seam recorded as harness `omo`.
 *
 * Activation writes a one-line re-export of this file:
 *
 *   ~/.omo/agent/extensions/z0-memory.js:
 *     export { default } from "file:///<pinned checkout>/omp-extensions/z0-memory/omo.ts";
 *
 * A senpi build whose `on()` rejects `context` has no model-visible push seam: that is recorded as
 * B=UNSUPPORTED (push_status.json, read by `z0int memory eval`), a stop condition, never a silent pass.
 */
import { recordPushStatus } from "../../harness-adapters/memory-client.mjs";
import { registerMemory } from "./index.ts";

export default function omoMemory(pi: { on(event: string, handler: (...args: any[]) => unknown): void }) {
	const status = registerMemory(pi, "omo");
	recordPushStatus(process.env, "omo", status);
	return status;
}
