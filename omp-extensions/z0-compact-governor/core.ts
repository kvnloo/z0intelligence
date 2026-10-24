export type Arm = "25" | "50" | "native";
export type Config = { mode: "measure" | "active"; arm: Arm | "adaptive"; floorPercent: number; ceilingPercent: number; cooldownMs: number; packetTimeoutMs: number; packetTtlMs: number };
export const defaults: Config = { mode: "measure", arm: "adaptive", floorPercent: 10, ceilingPercent: 70, cooldownMs: 300_000, packetTimeoutMs: 10_000, packetTtlMs: 120_000 };
export function config(value: unknown): Config {
 const v = value && typeof value === "object" ? value as Record<string, unknown> : {};
 const n = (key: keyof Config, low: number, high: number) => typeof v[key] === "number" && Number.isFinite(v[key]) ? Math.max(low, Math.min(high, v[key] as number)) : defaults[key] as number;
 const floorPercent = n("floorPercent", 1, 50);
 return { mode: v.mode === "active" ? "active" : "measure", arm: ["25", "50", "native", "adaptive"].includes(String(v.arm)) ? v.arm as Config["arm"] : "adaptive", floorPercent, ceilingPercent: Math.max(floorPercent + 1, n("ceilingPercent", 2, 95)), cooldownMs: n("cooldownMs", 0, 3_600_000), packetTimeoutMs: n("packetTimeoutMs", 100, 30_000), packetTtlMs: n("packetTtlMs", 1000, 600_000) };
}
export type Probe = { kind: "path" | "id" | "decision" | "blocker"; literal: string };
export function text(value: unknown): string {
 if (typeof value === "string") return value;
 if (Array.isArray(value)) return value.map(text).filter(Boolean).join("\n");
 if (!value || typeof value !== "object") return "";
 const v = value as Record<string, unknown>;
 // Never serialize hidden reasoning, images, metadata or tool-call arguments.
 if (v.type === "thinking" || v.type === "image") return "";
 return typeof v.text === "string" ? v.text : text(v.content ?? v.message);
}
export function probes(value: unknown, max = 12): Probe[] {
 const lines = text(value).split("\n").filter(line => !/secret|password|api.?key|bearer|credential|token\s*[=:]/i.test(line));
 const buckets: Record<Probe["kind"], string[]> = { path: [], id: [], decision: [], blocker: [] };
 for (const line of lines) {
  buckets.path.push(...(line.match(/(?:\/[\w.@-]+){2,}(?:\/[\w.@-]+)*|\b(?:src|tests|docs|operations)\/[\w./@-]+/g) ?? []));
  buckets.id.push(...(line.match(/\b[0-9a-f]{8,40}\b|\b[A-Z][A-Z0-9_-]{2,}-\d+\b/g) ?? []));
  if (/\b(decid(?:ed|e)|must|never|chosen|approved|use .* instead)\b/i.test(line)) buckets.decision.push(line.trim().slice(0, 240));
  if (/\b(block(?:ed|er)|awaiting|unresolved|pending approval)\b/i.test(line)) buckets.blocker.push(line.trim().slice(0, 240));
 }
 const result: Probe[] = []; const seen = new Set<string>();
 for (let i = 0; result.length < max; i++) {
  let found = false;
  for (const kind of Object.keys(buckets) as Probe["kind"][]) {
   const literal = [...new Set(buckets[kind])].reverse()[i];
   if (!literal) continue;
   found = true;
   if (!seen.has(literal) && result.length < max) { seen.add(literal); result.push({ kind, literal }); }
  }
  if (!found) break;
 }
 return result;
}
export function recall(set: Probe[], post: string) {
 const hits = set.map(p => ({ kind: p.kind, retained: post.includes(p.literal) }));
 return { probe_count: set.length, retained: hits.filter(p => p.retained).length, recall_score: set.length ? hits.filter(p => p.retained).length / set.length : null, by_kind: Object.fromEntries(["path", "id", "decision", "blocker"].map(k => { const ps = hits.filter(p => p.kind === k); return [k, { total: ps.length, retained: ps.filter(p => p.retained).length }]; })) };
}
export type Outcome = { arm: Arm; recall_score: number | null; policy_evidence?: boolean; tokens_before?: number; tokens_after?: number; latency_ms?: number; status?: string };
export function chooseArm(rows: Outcome[], sequence: number): { arm: Arm; reason: string } {
 const arms: Arm[] = ["25", "50", "native"];
 const eligible = rows.filter(r => r.status === "compacted" && r.policy_evidence === true && r.recall_score !== null);
 const samples = arms.map(arm => eligible.filter(r => r.arm === arm).slice(-20));
 const min = Math.min(...samples.map(s => s.length));
 if (min < 5) { const candidates = arms.filter((_a, i) => samples[i].length === min); return { arm: candidates[sequence % candidates.length], reason: "balanced_exploration_insufficient_evidence" }; }
 // Periodic exploration detects drift; a poor latest recall switches away immediately.
 if (sequence % 5 === 0) return { arm: arms[Math.floor(sequence / 5) % 3], reason: "drift_exploration" };
 const score = (rs: Outcome[]) => rs.reduce((sum, r) => sum + r.recall_score! - 0.1 * ((r.tokens_after ?? 0) / Math.max(1, r.tokens_before ?? 1)) - Math.min(0.1, (r.latency_ms ?? 0) / 600_000), 0) / rs.length;
 const last = eligible.at(-1);
 const ranked = arms.map((arm, i) => ({ arm, score: score(samples[i]) })).filter(r => !(last && last.recall_score! < 0.75 && r.arm === last.arm)).sort((a, b) => b.score - a.score);
 return { arm: ranked[0]?.arm ?? "native", reason: last && last.recall_score! < 0.75 ? "recall_degradation_switch" : "measured_retention_cost" };
}
export function shouldCompact(c: Config, arm: Arm, percent: number, boundary: boolean, requested: boolean, elapsed: number): string | undefined {
 if (!Number.isFinite(percent) || percent < c.floorPercent || elapsed < c.cooldownMs) return;
 if (percent >= c.ceilingPercent) return "hard_ceiling";
 if (requested && boundary) return "model_boundary_request";
 if (boundary && arm !== "native" && percent >= Number(arm)) return "natural_boundary";
}
export type Entry = { type?: string; id?: string; message?: unknown; content?: unknown; summary?: string; firstKeptEntryId?: string };
/** Conservative visible reconstruction: latest summary + kept messages only, never old branch/preserveData. */
export function visibleContext(branch: Entry[]): string {
 const index = branch.findLastIndex(e => e.type === "compaction");
 if (index < 0) return branch.filter(e => e.type === "message" || e.type === "custom_message").map(e => text(e.message ?? e.content)).join("\n");
 const compact = branch[index];
 const kept = branch.findIndex(e => e.id === compact.firstKeptEntryId);
 const tail = branch.slice(kept >= 0 ? kept : index + 1).filter(e => e.type === "message" || e.type === "custom_message");
 return [compact.summary ?? "", ...tail.map(e => text(e.message ?? e.content))].join("\n");
}
