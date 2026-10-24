import { createHash } from "node:crypto";
import { mkdir, open, rmdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

export const STATE_PACKET = "/mnt/zer0models/workspace/zer0/data/operations/repo-distill/STATE-PACKET.md";
export const DEFAULT_THRESHOLDS = Object.freeze({ checkChars: 180, contractChars: 1000, contractSentences: 4, contractImperatives: 3, contractScore: 6 });
export const sourceHash = text => createHash("sha256").update(text, "utf8").digest("hex");

export function stripOverride(raw) {
  const match = raw.match(/^\s*(\/aodl(?:\s+|$)|quick:\s*)/i);
  return { text: match ? raw.slice(match[0].length) : raw, override: match ? (/\/aodl/i.test(match[1]) ? "/aodl" : "quick:") : null };
}

// The packet is orientation, not a registry. Extract only explicitly named repo
// URLs and project/product directory components, never its section headings.
export function projectsFromPacket(packet) {
  const names = new Set();
  for (const match of packet.matchAll(/https:\/\/github\.com\/[A-Za-z0-9_.-]+\/([A-Za-z0-9_.-]+)/g)) names.add(match[1].replace(/\.git$/, "").toLowerCase());
  for (const match of packet.matchAll(/\/(?:projects|products)\/([A-Za-z0-9_.-]+)/g)) names.add(match[1].toLowerCase());
  return [...names].sort();
}

const words = (text, pattern) => [...text.matchAll(pattern)].map(match => match[0].toLowerCase());
const unique = values => [...new Set(values)];
const imperative = /\b(?:add|amend|analy[sz]e|apply|audit|build|change|check|configure|create|delete|deploy|disable|edit|enable|fix|implement|install|investigate|look|make|merge|migrate|move|nuke|push|read|refactor|remove|replace|restart|review|run|send|set|ship|show|start|stop|test|update|use|verify|write)\b/gi;
const risky = /\b(?:live|prod(?:uction)?|deploy(?:ment|ed|ing)?|merg(?:e|ing)|delet(?:e|ion|ing)|nuk(?:e|ing)|push(?:ing)?|phones?|s22\+?|s25\+?|services?|systemd|configs?|configuration|keys?|credentials?|secrets?|permissions?)\b/gi;
// Destructive scope requires a contract even in questions, vents, or cancellations.
const destructive = /\b(?:nuk(?:e|es|ed|ing)|wip(?:e|es|ed|ing)|delet(?:e|es|ed|ing|ion)|reset(?:s|ting)?|drop(?:s|ped|ping)?|forc(?:e|es|ed|ing)|rm|all\s+(?:repos?(?:itories)?|commits?))\b/i;
const prior = /\b(?:again|already|like before|we decided|last time|ln)\b/gi;
const correction = /\b(?:not what|i (?:asked|said|told)|you (?:forgot|ignored|missed|broke)|doesn'?t work|still (?:wrong|broken)|stop (?:doing|guessing)|instead of|why (?:did|didn'?t|are) you|wrong|frustrat\w*|mad|angry|pissed|wtf|fuck\w*|damn)\b/gi;
const systems = /\b(?:groot|mbp|macos|linux|android|ios|phones?|s22\+?|s25\+?|hermes|omp|k3s|kubernetes|servers?|hosts?)\b/gi;
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function triage(raw, { projects = [], packetAvailable = true, openContract = false, hasImages = false, thresholds = DEFAULT_THRESHOLDS } = {}) {
  if (typeof raw !== "string") throw new TypeError("Input text must be a string");
  const { text, override } = stripOverride(raw);
  const normalized = text.trim();
  const imperatives = words(normalized, imperative);
  const risks = unique(words(normalized, risky));
  const destructiveScope = destructive.test(normalized);
  const priors = unique(words(normalized, prior));
  const corrections = unique(words(normalized, correction));
  const namedProjects = projects.filter(name => new RegExp(`(^|[^a-z0-9_.-])${escape(name)}(?=$|[^a-z0-9_.-])`, "i").test(normalized));
  const repositories = unique(words(normalized, /(?:https:\/\/github\.com\/[\w.-]+\/[\w.-]+|\/[\w./-]+\/(?:[\w.-]+))/gi));
  const namedSystems = unique(words(normalized, systems));
  const sentences = normalized.split(/[.!?]+(?:\s|$)|\n+/).filter(part => part.trim()).length;
  const delegation = /\b(?:delegat\w*|parallel|subagents?|workers?|fan[ -]?out)\b/i.test(normalized);
  const multi = namedProjects.length > 1 || repositories.length > 1 || namedSystems.length > 1 || /\b(?:all|both|multiple|across|every)\b[^.!?\n]{0,60}\b(?:repos?(?:itories)?|systems?|projects?|services?|phones?)\b/i.test(normalized);
  const question = /^(?:what|why|where|when|who|how|which|is|are|does|do|can you explain|could you explain)\b/i.test(normalized) || /\?\s*$/.test(normalized);
  const readOnly = imperatives.every(verb => ["read", "show", "look", "check", "review", "analyze", "analyse", "investigate", "audit"].includes(verb));
  const features = { chars: normalized.length, sentence_count: sentences, imperative_count: imperatives.length, prior_references: priors, live_risk_terms: risks, destructive_scope: destructiveScope, named_projects: namedProjects, named_systems: namedSystems, repository_mentions: repositories.length, multi_system: multi, delegation, corrections, question, read_only: readOnly, packet_available: packetAvailable, open_contract: Boolean(openContract), has_images: hasImages };
  const rule_ids = [];
  let score = 0;
  const add = (condition, id, weight) => { if (condition) { rule_ids.push(id); score += weight; } };
  add(priors.length > 0, "prior-reference", 2);
  add(risks.length > 0, "live-risk", 3);
  add(destructiveScope, "destructive-scope", 6);
  add(multi, "multi-system", 4);
  add(delegation, "delegation", 4);
  add(namedProjects.length > 0, "packet-project", 1);
  add(corrections.length > 0, "correction-frustration", 6);
  add(normalized.length >= thresholds.checkChars, "length-check", 1);
  add(normalized.length >= thresholds.contractChars, "length-contract", 6);
  add(sentences >= thresholds.contractSentences, "many-sentences", 4);
  add(imperatives.length >= thresholds.contractImperatives, "many-imperatives", 4);
  add(hasImages, "nontext-input", 3);
  add(!packetAvailable, "packet-unavailable", 1);
  let tier = "CHECK";
  if (destructiveScope || openContract || score >= thresholds.contractScore || (risks.length > 0 && !readOnly) || (multi && !readOnly) || (delegation && !readOnly)) {
    tier = "CONTRACT";
    if (openContract) rule_ids.push("open-contract-amendment");
  } else if (score === 0 && normalized && (question || imperatives.length === 1 || /^(?:thanks|thank you|hello|hi)[!. ]*$/i.test(normalized))) {
    tier = "FAST";
    rule_ids.push("bounded-simple-input");
  } else rule_ids.push("uncertain-escalate");
  // A quick override cannot bypass the deterministic destructive-scope rule.
  if (override && (override !== "quick:" || !destructiveScope)) { tier = override === "/aodl" ? "CONTRACT" : "FAST"; rule_ids.push(override === "/aodl" ? "override-aodl" : "override-quick"); }
  return { tier, override, features, rule_ids, score, text, sourceHash: sourceHash(raw), slm_used: false };
}

export function sessionDirectory(sessionId, home = process.env.Z0INT_HOME || join(homedir(), ".z0int")) {
  if (typeof sessionId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(sessionId) || sessionId === "." || sessionId === "..") throw new Error("Unsafe or missing session id");
  return join(home, "intent", sessionId);
}

export async function appendReceipt(sessionId, entryId, result, { home, now = () => new Date().toISOString() } = {}) {
  if (typeof entryId !== "string" || !entryId) throw new Error("Missing input entry id");
  const directory = sessionDirectory(sessionId, home);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  // Lock contention fails closed rather than exceeding the cheap-path deadline;
  // never remove another process's lock or guess whether it is stale.
  const lock = join(directory, "triage.lock");
  await mkdir(lock, { mode: 0o700 });
  try {
    const receipt = { entry_id: entryId, sourceHash: result.sourceHash, tier: result.tier, override: result.override, features: result.features, rule_ids: result.rule_ids, slm_used: false, ts: now() };
    const file = await open(join(directory, "triage.jsonl"), "a", 0o600);
    try { await file.writeFile(JSON.stringify(receipt) + "\n", "utf8"); await file.sync(); }
    finally { await file.close(); }
    return receipt;
  } finally { await rmdir(lock); }
}
