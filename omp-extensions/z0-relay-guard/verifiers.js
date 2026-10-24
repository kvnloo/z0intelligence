import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

const object = v => v !== null && typeof v === "object" && !Array.isArray(v);
const requireValue = (condition, message) => { if (!condition) throw new Error(message); };
const text = (v, name) => { requireValue(typeof v === "string" && v.trim().length > 0 && !v.includes("\0"), `${name} must be nonempty text`); return v; };
export const VERIFIER_TYPES = Object.freeze(["command_exit_zero", "file_contains", "url_status", "pr_state", "service_active", "metric_threshold", "human_review"]);
const comparisons = { "<": (a,b) => a < b, "<=": (a,b) => a <= b, "==": (a,b) => a === b, ">=": (a,b) => a >= b, ">": (a,b) => a > b, "!=": (a,b) => a !== b };

export function validateVerifier(v) {
  requireValue(object(v) && VERIFIER_TYPES.includes(v.type), "Unknown verifier family");
  text(v.target, "target");
  requireValue(!/^(?:unknown|unspecified|tbd|todo|placeholder|none)$/i.test(v.target.trim()), "Verifier requires a real target");
  switch (v.type) {
    case "command_exit_zero": text(v.cmd, "cmd"); break;
    case "file_contains":
      text(v.path, "path"); text(v.pattern, "pattern");
      requireValue(v.target === v.path, "file_contains target must equal path"); break;
    case "url_status": {
      const url = new URL(text(v.url, "url"));
      requireValue(["http:", "https:"].includes(url.protocol) && !url.username && !url.password, "url_status requires an HTTP(S) URL without credentials");
      requireValue(Number.isInteger(v.code) && v.code >= 100 && v.code <= 599, "Invalid HTTP status code");
      requireValue(v.target === v.url, "url_status target must equal url"); break;
    }
    case "pr_state":
      requireValue(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(v.repo), "Invalid PR repository");
      requireValue(Number.isSafeInteger(v.number) && v.number > 0, "Invalid PR number");
      requireValue(typeof v.head_sha === "string" && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(v.head_sha), "PR requires full head SHA");
      requireValue(["open", "closed", "merged"].includes(v.state), "Invalid PR state");
      requireValue(v.target === `${v.repo}#${v.number}@${v.head_sha}`, "PR target must identify repository, number and head SHA"); break;
    case "service_active":
      requireValue(typeof v.unit === "string" && /^[A-Za-z0-9_@.:\\-]+$/.test(v.unit) && !v.unit.startsWith("-"), "Invalid service unit");
      requireValue(v.target === v.unit, "service_active target must equal unit"); break;
    case "metric_threshold":
      text(v.query, "query"); requireValue(Object.hasOwn(comparisons, v.op), "Invalid metric comparison");
      requireValue(typeof v.value === "number" && Number.isFinite(v.value), "Metric threshold must be finite"); break;
    case "human_review": break;
  }
  return v;
}

export function normalizeVerifier(binding, target = binding?.target) {
  const v = structuredClone(binding);
  requireValue(object(v), "Acceptance must be a verifier binding");
  if (v.target === undefined) v.target = target;
  requireValue(v.target === target, "Acceptance target differs from task target");
  return validateVerifier(v);
}

function execute(file, args, options) {
  return new Promise(resolve => execFile(file, args, { cwd: options.cwd, timeout: options.timeoutMs ?? 30000, maxBuffer: 4 * 1024 * 1024, encoding: "utf8", shell: false }, (error, stdout, stderr) => resolve({ exitCode: error ? (Number.isInteger(error.code) ? error.code : 1) : 0, stdout, stderr, error: error?.message })));
}

/** Explicit command verifier is the ONLY shell entry point. Other families use argv or APIs. */
export async function runVerifier(binding, options = {}) {
  const v = normalizeVerifier(binding);
  const startedAt = new Date().toISOString();
  const toolCallId = `intent-verifier:${randomUUID()}`;
  let result;
  try {
    switch (v.type) {
      case "command_exit_zero": {
        const run = await execute("/bin/sh", ["-c", v.cmd], options);
        result = { ...run, passed: run.exitCode === 0 }; break;
      }
      case "file_contains": {
        const contents = await readFile(v.path, "utf8");
        result = { passed: contents.includes(v.pattern), exitCode: 0 }; break;
      }
      case "url_status": {
        const response = await (options.fetch ?? globalThis.fetch)(v.url, { method: "GET", redirect: "manual", signal: AbortSignal.timeout(options.timeoutMs ?? 30000) });
        await response.body?.cancel();
        result = { passed: response.status === v.code, exitCode: 0, status: response.status }; break;
      }
      case "pr_state": {
        const run = await execute("gh", ["api", `repos/${v.repo}/pulls/${v.number}`], options);
        if (run.exitCode !== 0) { result = { ...run, passed: false }; break; }
        const pr = JSON.parse(run.stdout);
        const state = pr.merged_at ? "merged" : pr.state;
        result = { ...run, passed: pr.number === v.number && pr.head?.sha === v.head_sha && state === v.state && pr.base?.repo?.full_name === v.repo, observedHead: pr.head?.sha, observedState: state }; break;
      }
      case "service_active": {
        const run = await execute("systemctl", ["is-active", "--", v.unit], options);
        result = { ...run, passed: run.exitCode === 0 && run.stdout.trim() === "active" }; break;
      }
      case "metric_threshold": {
        requireValue(typeof options.metricQuery === "function", "metric_threshold requires a trusted metricQuery adapter");
        const sample = await options.metricQuery(v.query, v.target);
        requireValue(object(sample) && sample.target === v.target && typeof sample.value === "number" && Number.isFinite(sample.value), "Metric adapter must return finite value and exact target");
        result = { passed: comparisons[v.op](sample.value, v.value), exitCode: 0, measuredValue: sample.value }; break;
      }
      case "human_review": {
        requireValue(typeof options.humanReview === "function", "human_review requires an authenticated user approval adapter");
        const approval = await options.humanReview(v.target);
        requireValue(object(approval) && approval.approved === true && approval.actor === "user" && approval.target === v.target && typeof approval.entryId === "string" && typeof approval.sentenceId === "string", "Human review requires explicit user approval, never agent approval");
        result = { passed: true, exitCode: 0, humanApproval: { entryId: approval.entryId, sentenceId: approval.sentenceId } }; break;
      }
    }
  } catch (error) {
    result = { passed: false, exitCode: 1, error: error instanceof Error ? error.message : String(error) };
  }
  return { ...result, executed: true, isError: result.exitCode !== 0, toolName: `intent_verifier:${v.type}`, toolCallId, target: v.target, startedAt, finishedAt: new Date().toISOString() };
}

export const createRunner = (options = {}) => binding => runVerifier(binding, options);
