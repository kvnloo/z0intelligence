import { randomUUID } from "node:crypto";
import { ompAutomaticContextEnabled, scheduleOmpAutomatic, route, delivered } from "../../harness-adapters/automatic-client.mjs";
import { buildIntelligenceRequest } from "../../harness-adapters/governed-client.mjs";
import { readFileSync } from "node:fs";

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const registration = Symbol.for("z0intelligence.omp.registration");

export default function(pi: ExtensionAPI) {
  if (Reflect.get(pi.events, registration) === true) return;
  Reflect.set(pi.events, registration, true);
  pi.on("before_agent_start", async (event, ctx) => {
    const prompt = typeof event?.prompt === "string" ? event.prompt : "";
    if (!prompt || prompt.startsWith("/")) return;
    const sessionId = ctx.sessionManager.getSessionId();
    const turnId = randomUUID();
    // The daemon's automatic_event op is receipt-only: it cannot supply a
    // model-visible message to this turn. Retain the prior delivery behavior
    // for operator-enabled specialization; optimize the default-disabled path.
    if (ompAutomaticContextEnabled()) {
      const result = await route("omp", sessionId, turnId, prompt);
      if (result.action === "context" && typeof result.context === "string") {
        const message = {customType:"z0intelligence",content:result.context,display:true};
        await delivered("omp", result);
        return {message};
      }
      await delivered("omp", result);
      return undefined;
    }
    const holder = globalThis as { __omp_z0int_bridge_transport__?: { request?: (body: unknown, timeoutMs?: number) => Promise<unknown> } };
    scheduleOmpAutomatic({
      sessionId, turnId, text: prompt,
      request: holder.__omp_z0int_bridge_transport__?.request,
    });
    return undefined;
  });
  const schema: unknown = JSON.parse(readFileSync(new URL("./schema.json", import.meta.url), "utf8"));
  if (typeof schema !== "object" || schema === null || Array.isArray(schema)) throw new Error("Invalid canonical tool schema");
  pi.registerTool({
    name: "z0int_route_worker", label: "z0intelligence", loadMode: "essential",
    description: "Use the shared capability router for bounded delegation and typed decisions. With function=cheap_bounded_worker, allow_remote=true, and operator-enabled governed remote mode, the host attaches AODL authority before off-host execution. Tiny tasks otherwise return PARENT_ONLY. Stable trace_id permits safe replay; consume and verify returned output.",
    parameters: schema,
    async execute(_id: string, args: unknown, signal?: AbortSignal) {
      try {
        if (typeof args !== "object" || args === null || Array.isArray(args)) throw new Error("Invalid tool arguments");
        const {path, body} = buildIntelligenceRequest(args as Record<string, unknown>, process.env);
        const response = await fetch((process.env.Z0INT_SERVICE_URL || "http://127.0.0.1:11501") + path, {
          method: "POST", headers: {"Content-Type":"application/json"},
          body: JSON.stringify(body),
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
