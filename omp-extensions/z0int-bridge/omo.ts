/**
 * OMO (omo-ai / senpi) capture entry: the z0int-bridge capture handlers only, recorded as harness `omo`.
 *
 * No z0int-intelligence routing and no route_worker tool are registered here (new route_worker exposure
 * stays gated by z0intelligence#95). Activation writes a one-line re-export of this file:
 *
 *   ~/.omo/agent/extensions/z0-capture.js:
 *     export { default } from "file:///<pinned checkout>/omp-extensions/z0int-bridge/omo.ts";
 *
 * Senpi has no agent identity on the handler ctx, so OMO turns are never marked subagent here.
 */
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { registerBridgeCapture } from "./index.ts";

export default function omoCapture(pi: ExtensionAPI) {
	return registerBridgeCapture(pi, { harness: "omo" });
}
