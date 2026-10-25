import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { auditTool, type Ownership } from "./rule";

/** Opt-in source only. Never returns block/input or executes the observed command. */
export default function rootCauseReport(api: ExtensionAPI, installContext: () => Ownership = () => ({})) {
 api.on("tool_call", async event => {
  try {
   const findings = auditTool(event.toolName, event.input, installContext());
   if (findings.length) return { additionalContext: findings.map(item => `[${item.law} ${item.mode}: ${item.kind}] ${item.message}`).join("\n") };
  } catch {
   // OMP turns thrown/rejected handlers into block:true. Report mode must not throw.
   return { additionalContext: "[R1 report-only] Mitigation audit unavailable; execution unchanged." };
  }
 });
}
