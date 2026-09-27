import { randomUUID } from "node:crypto";
import { route, delivered } from "../../harness-adapters/automatic-client.mjs";
import { readFileSync } from "node:fs";

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export default function(pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event, ctx) => {
    const result = await route("omp", ctx.sessionManager.getSessionId(), randomUUID(), event.prompt);
    if (result.action === "context" && typeof result.context === "string") {
      const message = {customType:"z0intelligence",content:result.context,display:true};
      await delivered("omp", result);
      return {message};
    }
    await delivered("omp", result);
    return undefined;
  });
  const schema: unknown = JSON.parse(readFileSync(new URL("./schema.json", import.meta.url), "utf8"));
  if (typeof schema !== "object" || schema === null || Array.isArray(schema)) throw new Error("Invalid canonical tool schema");
  pi.registerTool({
    name: "z0int_route_worker", label: "z0intelligence", loadMode: "essential",
    description: "Use the shared capability router for bounded delegation and typed decisions. Tiny tasks return PARENT_ONLY. Remote context requires explicit authorization. Stable trace_id permits safe replay; consume and verify returned output. Do not inflate estimates to force delegation.",
    parameters: schema,
    async execute(_id: string, args: unknown, signal?: AbortSignal) {
      try {
        if (typeof args !== "object" || args === null || Array.isArray(args)) throw new Error("Invalid tool arguments");
        const response = await fetch((process.env.Z0INT_SERVICE_URL || "http://127.0.0.1:11501") + "/v1/intelligence", {
          method: "POST", headers: {"Content-Type":"application/json"},
          body: JSON.stringify({...args, harness:"omp"}),
          signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(120000)]) : AbortSignal.timeout(120000),
        });
        if (!response.ok) throw new Error("HTTP " + response.status);
        const result: unknown = await response.json();
        if (typeof result !== "object" || result === null || !("ok" in result)) throw new Error("Invalid service response");
        return {content:[{type:"text",text:JSON.stringify(result)}],details:result,isError:result.ok === false};
      } catch {
        const result = {ok:false,execution_status:"unknown",requires_reconciliation:true,error:"service_unavailable_or_result_uncertain",instruction:"Reuse the identical trace_id to reconcile; do not blindly repeat side effects."};
        return {content:[{type:"text",text:JSON.stringify(result)}],details:result,isError:true};
      }
    }
  });
}
