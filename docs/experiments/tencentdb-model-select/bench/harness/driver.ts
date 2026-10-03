/**
 * tdai model-select benchmark driver.
 *
 * Runs INSIDE the deployed memory-core image (sha256:55fec3a6…, /app), mounted at /app/bench, so
 * every prompt, template, parser, dedup/candidate-recall rule, L1 writer, scene extractor and
 * persona generator is the gateway's own code. The LLM is reached exactly as the gateway reaches
 * it: StandaloneLLMRunner (AI SDK generateText, OpenAI-compatible /chat/completions), configured by
 * the gateway's own loadGatewayConfig() from the live tdai-gateway.yaml + TDAI_LLM_* env.
 *
 * What is *not* the gateway: the scheduler. The stateful pipeline's timers are replaced by a
 * deterministic replay of the same rules (see PREREG §3.3): per session, L0 is written one
 * round at a time exactly like POST /v3/conversation/add; L1 fires on the warm-up schedule
 * 1,2,4 then every 5 user rounds (enableWarmup, everyNConversations=5) and once more at session
 * end (l1IdleTimeoutSeconds flush); L2 (scene) runs once per session after the final L1, for the
 * profile scopes L1 reported; L3 (persona) runner is invoked after every L2 and decides itself
 * via PersonaTrigger.
 *
 * Usage (inside container):
 *   node --import tsx /app/bench/driver.ts <job.json>
 * job.json: { corpus, convIds[], outDir, layers: "l1"|"l1l2l3"|{convId: layers}, runTag }
 * LLM endpoint/model/key come from TDAI_LLM_BASE_URL / TDAI_LLM_MODEL / TDAI_LLM_API_KEY.
 */
import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { loadGatewayConfig } from "../src/gateway/config.js";
import {
  initDataDirectories, initStores, resetStores, createL1Runner, createL2Runner, createL3Runner,
} from "../src/utils/pipeline-factory.js";
import { StandaloneLLMRunnerFactory } from "../src/adapters/standalone/llm-runner.js";
import type { LLMRunner, LLMRunParams } from "../src/core/types.js";
import type { L0Record } from "../src/core/store/types.js";

type Job = {
  corpus: string;
  convIds: string[];
  outDir: string;
  layers: Record<string, "l1" | "l1l2l3">;
  runTag: string;
};

const ISO = { teamId: "default", userId: "default", agentId: "default" }; // Hermes plugin sends "default"

// ---------------------------------------------------------------- call recorder
type CallRec = {
  n: number; conv: string; session: number; phase: string; taskId: string;
  startedAt: string; ms: number; ok: boolean; error?: string;
  outputChars?: number; output?: string; usage?: unknown;
  systemPromptChars: number; promptChars: number; logs: string[];
};

class RecordingRunner implements LLMRunner {
  constructor(private inner: LLMRunner & { lastUsage?: unknown }, private sink: Sink) {}
  async run(params: LLMRunParams): Promise<string> {
    const rec: CallRec = {
      n: ++this.sink.n, conv: this.sink.conv, session: this.sink.session, phase: this.sink.phase,
      taskId: params.taskId, startedAt: new Date().toISOString(), ms: 0, ok: false,
      systemPromptChars: (params.systemPrompt ?? "").length, promptChars: params.prompt.length, logs: [],
    };
    this.sink.calls.push(rec);
    this.sink.current = rec;
    const t0 = Date.now();
    try {
      const text = await this.inner.run(params);
      rec.ms = Date.now() - t0; rec.ok = true; rec.output = text; rec.outputChars = text.length;
      rec.usage = this.inner.lastUsage;
      return text;
    } catch (err) {
      rec.ms = Date.now() - t0; rec.error = err instanceof Error ? err.message : String(err);
      throw err;
    }
  }
}

class Sink {
  n = 0; conv = ""; session = 0; phase = ""; calls: CallRec[] = []; current?: CallRec; log: string[] = [];
  logger() {
    const push = (lvl: string) => (msg: string) => {
      const line = `${new Date().toISOString()} ${lvl} ${msg}`;
      this.log.push(line);
      // attribute parser verdicts to the call that produced the text (calls are sequential)
      if (this.current && /l1-empty reason=|Repaired non-strict|No JSON array|Failed to parse|not an array|Batch conflict detection failed|Batch dedup failed|Skipping memory with invalid type|Invalid action|NO_JSON|PARSE_FAIL/.test(msg)) {
        this.current.logs.push(`${lvl} ${msg.slice(0, 400)}`);
      }
    };
    return { debug: (_: string) => {}, info: push("INFO"), warn: push("WARN"), error: push("ERROR") };
  }
}

// ---------------------------------------------------------------- helpers
function listFiles(dir: string, out: string[] = []): string[] {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) listFiles(p, out); else out.push(p);
  }
  return out;
}

async function runConversation(conv: any, job: Job, sink: Sink) {
  const layers = job.layers[conv.id] ?? "l1";
  const convOut = path.join(job.outDir, conv.id);
  const dataDir = path.join(convOut, "data");
  fs.rmSync(convOut, { recursive: true, force: true });
  fs.mkdirSync(dataDir, { recursive: true });

  // Same config path as the gateway: yaml + TDAI_* env overrides.
  process.env.TDAI_DATA_DIR = dataDir;
  const gw = loadGatewayConfig();
  const cfg = gw.memory;
  if (!cfg.llm?.enabled) throw new Error("LLM not enabled after config splice (missing TDAI_LLM_API_KEY?)");
  const logger = sink.logger() as any;

  initDataDirectories(dataDir);
  const { vectorStore, embeddingService } = await initStores(cfg, dataDir, logger);
  if (!vectorStore) throw new Error("store init failed");

  const factory = new StandaloneLLMRunnerFactory({ config: gw.llm, logger });
  const l1Runner = new RecordingRunner(factory.createRunner({ enableTools: false }) as any, sink);
  const l2l3Runner = new RecordingRunner(factory.createRunner({ enableTools: true }) as any, sink);

  const l1 = createL1Runner({ pluginDataDir: dataDir, cfg, openclawConfig: undefined, vectorStore, embeddingService, logger, llmRunner: l1Runner });
  const l2 = createL2Runner({ pluginDataDir: dataDir, cfg, openclawConfig: undefined, vectorStore, logger, llmRunner: l2l3Runner });
  const l3 = createL3Runner({ pluginDataDir: dataDir, cfg, openclawConfig: undefined, vectorStore, logger, llmRunner: l2l3Runner });

  const everyN = cfg.pipeline.everyNConversations;
  const warm = cfg.pipeline.enableWarmup;
  const events: any[] = [];
  const t0 = Date.now();
  sink.conv = conv.id;

  for (let si = 0; si < conv.sessions.length; si++) {
    const sess = conv.sessions[si];
    const sessionId = `bench-${conv.id}-s${si + 1}-${job.runTag}`;
    sink.session = si + 1;
    let threshold = warm ? 1 : 0;           // warmup_threshold (0 = graduated → everyN)
    let count = 0;
    const scopes = new Set<string>();

    const runL1 = async (why: string) => {
      sink.phase = `l1:${why}`;
      for (let guard = 0; guard < 20; guard++) {
        const r = await l1({ sessionKey: sessionId });
        events.push({ t: Date.now() - t0, session: si + 1, kind: "l1", why, ...r });
        r.profileScopes.forEach((s) => scopes.add(s));
        if (r.processedCount === 0) break;
        if (!(r.hasFullBacklog || (why === "idle" && r.hasMore))) break;
      }
    };

    for (const round of sess.rounds) {
      // == POST /v3/conversation/add (v2-router handleConversationAdd) for one Hermes turn ==
      const msgs = [{ role: "user", ...round.user }, { role: "assistant", ...round.assistant }];
      const base = Date.now();
      const recs: L0Record[] = msgs.map((m, i) => ({
        id: `msg-${randomUUID().replace(/-/g, "")}`,
        sessionKey: sessionId, sessionId,
        teamId: ISO.teamId, userId: ISO.userId, agentId: ISO.agentId,
        role: m.role as any, messageText: m.content,
        recordedAt: new Date(base + i).toISOString(),
        timestamp: new Date(m.timestamp).getTime(),
      }) as L0Record);
      if (vectorStore.insertL0Batch && !embeddingService) await vectorStore.insertL0Batch(recs);
      else for (const r of recs) await vectorStore.upsertL0(r, undefined);
      await new Promise((r) => setTimeout(r, 3)); // distinct recordedAt ms between turns

      // == stateful pipeline threshold rule (one user message = one round) ==
      count += 1;
      const eff = !warm || threshold <= 0 ? everyN : Math.min(threshold, everyN);
      if (count >= eff) {
        count = 0;
        if (warm && threshold > 0) { const nx = threshold * 2; threshold = nx >= everyN ? 0 : nx; }
        await runL1("threshold");
      }
    }
    await runL1("idle"); // l1IdleTimeoutSeconds flush at session end

    if (layers === "l1l2l3") {
      for (const key of scopes) {
        sink.phase = "l2";
        try {
          const r = await l2(key, undefined);
          events.push({ t: Date.now() - t0, session: si + 1, kind: "l2", key, result: r ?? null });
        } catch (err) {
          events.push({ t: Date.now() - t0, session: si + 1, kind: "l2", key, error: String(err) });
        }
        sink.phase = "l3";
        try {
          await l3();
          events.push({ t: Date.now() - t0, session: si + 1, kind: "l3" });
        } catch (err) {
          events.push({ t: Date.now() - t0, session: si + 1, kind: "l3", error: String(err) });
        }
      }
    }
  }

  // ---- final state dump ----
  const rows = await vectorStore.queryL1Records({} as any);
  const records = rows.map((r: any) => ({
    id: r.record_id, content: r.content, type: r.type, priority: r.priority, scene_name: r.scene_name,
    session_id: r.session_id, metadata: r.metadata_json, created: r.created_at ?? r.timestamp_str,
  }));
  const mdFiles = listFiles(dataDir).filter((f) => f.endsWith(".md"));
  const artifacts = mdFiles.map((f) => ({ path: path.relative(dataDir, f), content: fs.readFileSync(f, "utf-8") }));
  vectorStore.close?.();
  resetStores(dataDir);

  const result = {
    conv: conv.id, layers, runTag: job.runTag, model: gw.llm.model, wallMs: Date.now() - t0,
    events, records, artifacts,
    calls: sink.calls.filter((c) => c.conv === conv.id),
  };
  fs.writeFileSync(path.join(convOut, "result.json"), JSON.stringify(result, null, 1));
  fs.writeFileSync(path.join(convOut, "driver.log"), sink.log.join("\n"));
  sink.log = [];
  return result;
}

async function main() {
  const job: Job = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
  const corpus = JSON.parse(fs.readFileSync(job.corpus, "utf-8"));
  const byId = new Map(corpus.conversations.map((c: any) => [c.id, c]));
  const sink = new Sink();
  for (const id of job.convIds) {
    const conv = byId.get(id);
    if (!conv) throw new Error(`unknown conv ${id}`);
    const t = Date.now();
    try {
      const r = await runConversation(conv, job, sink);
      console.log(`[driver] ${id} done in ${((Date.now() - t) / 1000).toFixed(1)}s records=${r.records.length} calls=${r.calls.length}`);
    } catch (err) {
      console.log(`[driver] ${id} FAILED: ${err instanceof Error ? err.stack : String(err)}`);
      fs.mkdirSync(path.join(job.outDir, id), { recursive: true });
      fs.writeFileSync(path.join(job.outDir, id, "driver-error.txt"), String(err instanceof Error ? err.stack : err));
    }
  }
  // the gateway's background reporters may keep handles open
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
