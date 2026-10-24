#!/usr/bin/env node
import { createHash } from "node:crypto";
import { createReadStream, readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { performance } from "node:perf_hooks";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { appendReceipt, DEFAULT_THRESHOLDS, projectsFromPacket, sourceHash, STATE_PACKET, triage } from "../omp-extensions/z0-intent-service/triage.js";

export const AUDIT_ROOT = "/mnt/zer0models/workspace/zer0/data/operations/ask-audit";
export const OUTPUT_ROOT = "/mnt/zer0models/workspace/zer0/data/operations/intent-service";
export function splitFor(row) {
  // Session-grouped SHA256 split keeps adjacent turns and repeated asks in the
  // same partition. No labels or text influence split assignment.
  const key = `${row.harness}\0${row.session}`;
  return createHash("sha256").update(key).digest().readUInt32BE(0) / 0x1_0000_0000 < 0.30 ? "holdout" : "train";
}
const key = row => `${row.harness}\0${row.session}\0${row.entry_id}`;
async function jsonLines(path) {
  const rows = [];
  for await (const line of createInterface({ input: createReadStream(path), crlfDelay: Infinity })) if (line.trim()) rows.push(JSON.parse(line));
  return rows;
}

export function replayCases(labels, messages) {
  const byId = new Map(messages.map(row => [key(row), row]));
  const cases = new Map();
  const insert = (row, label, role, provenance) => {
    const id = key(row), old = cases.get(id);
    // A confirmed ask failure beats a control label for the same source.
    if (!old || (label === "failure" && old.label !== "failure")) cases.set(id, { harness: row.harness, session: row.session, entry_id: row.entry_id, text: row.text, ts: row.ts, previous_kevin: row.previous_kevin ?? null, label, role, provenance });
  };
  for (const label of labels) {
    if (typeof label.outcome !== "string") throw new Error("Missing reviewed outcome in labels.jsonl");
    const failed = label.outcome !== "not-a-miss";
    const target = byId.get(key(label)) ?? label;
    insert(target, failed ? "failure" : "control", "reviewed-target", { review_id: label.review_id, outcome: label.outcome, cause: label.cause, stratum: label.stratum });
    // Report original asks independently, not just easy correction/frustration
    // detection. Missing source is explicit rather than silently dropped.
    if (failed) for (const evidence of label.evidence ?? []) if (evidence.kind === "ask") {
      const ask = byId.get(key(evidence)) ?? { ...evidence, text: evidence.quote };
      if (typeof ask.text !== "string") throw new Error("Failure ask has no source text");
      insert(ask, "failure", "original-ask", { review_id: label.review_id, outcome: label.outcome, cause: label.cause, source: byId.has(key(evidence)) ? "messages.jsonl" : "labels.jsonl:evidence.quote" });
    }
  }
  // Unreviewed messages are population-only, not invented negative controls.
  for (const row of messages) if (!cases.has(key(row))) insert(row, "unreviewed", "population", { source: "messages.jsonl" });
  return [...cases.values()].map(row => ({ ...row, split: splitFor(row) }));
}

export function metrics(rows) {
  const reviewed = rows.filter(row => row.label !== "unreviewed");
  const confusion = { failure: { FAST: 0, CHECK: 0, CONTRACT: 0 }, control: { FAST: 0, CHECK: 0, CONTRACT: 0 } };
  for (const row of reviewed) confusion[row.label][row.tier]++;
  const failureCount = reviewed.filter(row => row.label === "failure").length;
  const caught = confusion.failure.CHECK + confusion.failure.CONTRACT;
  const latency = rows.map(row => row.latency_ms).sort((a, b) => a - b);
  const asks = reviewed.filter(row => row.label === "failure" && row.role === "original-ask");
  return { rows: rows.length, reviewed: reviewed.length, confusion, failure_recall_check_contract: failureCount ? caught / failureCount : null, failure_count: failureCount, original_ask_failure_count: asks.length, original_ask_failure_recall: asks.length ? asks.filter(row => row.tier !== "FAST").length / asks.length : null, fast_share: rows.length ? rows.filter(row => row.tier === "FAST").length / rows.length : null, reviewed_control_fast_share: reviewed.filter(row => row.label === "control").length ? confusion.control.FAST / reviewed.filter(row => row.label === "control").length : null, triage_p95_ms: latency.length ? latency[Math.max(0, Math.ceil(latency.length * 0.95) - 1)] : null, worst_misses: reviewed.filter(row => row.label === "failure" && row.tier === "FAST").sort((a, b) => (a.role === "original-ask" ? 0 : 1) - (b.role === "original-ask" ? 0 : 1) || b.features.chars - a.features.chars || key(a).localeCompare(key(b))).slice(0, 10) };
}
function classify(rows, projects, thresholds) {
  return rows.map(row => {
    const start = performance.now();
    const result = triage(row.text, { projects, thresholds });
    const latency_ms = performance.now() - start;
    return { ...row, ...result, latency_ms };
  });
}
export function calibrate(cases, projects, minimumRecall = 0.98) {
  if (!(minimumRecall > 0 && minimumRecall <= 1)) throw new Error("Recall target must be in (0,1]");
  const train = cases.filter(row => row.split === "train");
  if (!train.some(row => row.label === "failure")) throw new Error("Cannot calibrate without training failures");
  const candidates = [];
  for (const checkChars of [60, 100, 140, 180, 240]) for (const contractScore of [4, 6, 8]) {
    const thresholds = { ...DEFAULT_THRESHOLDS, checkChars, contractScore };
    candidates.push({ thresholds, metrics: metrics(classify(train, projects, thresholds)) });
  }
  candidates.sort((a, b) => {
    const passA = Number(a.metrics.failure_recall_check_contract >= minimumRecall), passB = Number(b.metrics.failure_recall_check_contract >= minimumRecall);
    return passB - passA || (passA ? b.metrics.fast_share - a.metrics.fast_share : b.metrics.failure_recall_check_contract - a.metrics.failure_recall_check_contract) || b.thresholds.contractScore - a.thresholds.contractScore || a.thresholds.checkChars - b.thresholds.checkChars;
  });
  return { selected: candidates[0].thresholds, target_met: candidates[0].metrics.failure_recall_check_contract >= minimumRecall, minimum_recall: minimumRecall, training_only: true, candidates };
}

export async function measureCheapPath(cases, projects, thresholds, home) {
  const samples = [];
  for (const row of cases) {
    const started = performance.now();
    const result = triage(row.text, { projects, thresholds });
    await appendReceipt(`replay-${sourceHash(`${row.harness}\0${row.session}`).slice(0, 24)}`, row.entry_id, result, { home });
    samples.push({ entry_id: row.entry_id, latency_ms: performance.now() - started });
  }
  const sorted = samples.map(sample => sample.latency_ms).sort((a, b) => a - b);
  const p95 = sorted.length ? sorted[Math.ceil(sorted.length * 0.95) - 1] : null;
  return { samples, p95_ms: p95, under_one_second_p95: p95 === null ? null : p95 < 1000, includes: "deterministic triage + exclusive lock + receipt append + fsync; excludes service/human UI" };
}

export async function main(argv = process.argv.slice(2)) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!["--audit", "--out", "--packet", "--recall"].includes(argv[i]) || !argv[i + 1]) throw new Error("Usage: replay-intent-triage.mjs [--audit ABSOLUTE_DIR] [--out PRIVATE_OUTPUT_DIR] [--packet ABSOLUTE_FILE] [--recall 0.98]");
    args[argv[i]] = argv[i + 1];
  }
  const audit = args["--audit"] ?? AUDIT_ROOT;
  const packetPath = args["--packet"] ?? STATE_PACKET;
  if (!isAbsolute(audit) || !isAbsolute(packetPath)) throw new Error("Audit and packet paths must be absolute");
  const output = resolve(args["--out"] ?? join(OUTPUT_ROOT, `triage-replay-${new Date().toISOString().replace(/[:.]/g, "-")}`));
  const beneath = relative(OUTPUT_ROOT, output);
  const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const inRepo = relative(repoRoot, output);
  if (!beneath || beneath.startsWith(`..${sep}`) || beneath === ".." || isAbsolute(beneath) || (!inRepo.startsWith(`..${sep}`) && inRepo !== ".." && !isAbsolute(inRepo))) throw new Error("Private replay artifacts must use a new subdirectory under operations/intent-service, never Git");
  const labels = await jsonLines(join(audit, "labels.jsonl"));
  const messages = await jsonLines(join(audit, "messages.jsonl"));
  const packetText = readFileSync(packetPath, "utf8");
  const projects = projectsFromPacket(packetText);
  const cases = replayCases(labels, messages);
  const calibration = calibrate(cases, projects, Number(args["--recall"] ?? 0.98));
  // No holdout text or result participates in selecting thresholds.
  const rows = classify(cases, projects, calibration.selected);
  const defaults = classify(cases, projects, DEFAULT_THRESHOLDS);
  await mkdir(OUTPUT_ROOT, { recursive: true, mode: 0o700 });
  await mkdir(output, { mode: 0o700 });
  const cheapPath = await measureCheapPath(cases, projects, calibration.selected, join(output, "receipt-benchmark"));
  const report = {
    schema: "intent-triage-replay.v1", generated_at: new Date().toISOString(), audit,
    packet: { path: packetPath, sourceHash: sourceHash(packetText), projects },
    split: "SHA256(harness NUL session), uint32/2^32 < .30 holdout; session grouped",
    calibration, train: metrics(rows.filter(row => row.split === "train")), holdout: metrics(rows.filter(row => row.split === "holdout")),
    default_thresholds: DEFAULT_THRESHOLDS, default_train: metrics(defaults.filter(row => row.split === "train")), default_holdout: metrics(defaults.filter(row => row.split === "holdout")),
    cheap_path: { ...cheapPath, samples: undefined },
    caveats: [
      "Labels measure historical instruction failures, not gold FAST/CHECK/CONTRACT tiers; confusion uses reviewed failure/control versus routed tier.",
      "Unreviewed messages contribute only population FAST share, never negative-control truth.",
      "Original failure asks and reviewed correction targets are separately identified; sessions remain in one split.",
      "triage_p95_ms times pure classification; cheap_path.p95_ms additionally measures durable receipt writing. Neither includes service or human review.",
      "Selected calibration thresholds are reported only; this harness does not modify production defaults or STATE-PACKET.",
    ],
  };
  await writeFile(join(output, "replay.jsonl"), rows.map(row => JSON.stringify(row)).join("\n") + "\n", { mode: 0o600, flag: "wx" });
  await writeFile(join(output, "cheap-path.jsonl"), cheapPath.samples.map(sample => JSON.stringify(sample)).join("\n") + "\n", { mode: 0o600, flag: "wx" });
  await writeFile(join(output, "report.json"), JSON.stringify(report, null, 2) + "\n", { mode: 0o600, flag: "wx" });
  console.log(JSON.stringify({ output, train: { ...report.train, worst_misses: undefined }, holdout: { ...report.holdout, worst_misses: undefined }, cheap_path: report.cheap_path, calibration_target_met: calibration.target_met }));
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(error => { console.error(error.message); process.exitCode = 1; });
