/** Pure lexical audit, not a shell sandbox. No target access or effects. */
export type Ownership = {
 ownedPids?: readonly string[]; ownedPatterns?: readonly string[];
 /** Trusted harness install context only; never populated from tool input/text. */
 stampedInstallPaths?: readonly string[];
 home?: string;
};
export type Finding = { law: "R1" | "I"; mode: "report-only"; kind: string; message: string };
const message = "Raw mitigation observed: use z0-guard mitigate. Report-only: tool execution is unchanged.";
const base = (word: string) => word.slice(word.lastIndexOf("/") + 1);
const limitPath = (path: string) => /(?:^|\/)(?:io\.max|memory\.max)$/.test(path);
const sourceMessage = "Live config must have a tracked source. Report-only: execution unchanged; use a tracked, content-stamped install.";
const sourceFinding = (kind: string): Finding => ({ law: "I", mode: "report-only", kind, message: sourceMessage });
function normalized(path: string, ownership: Ownership): string {
 const home = ownership.home ?? process.env.HOME ?? "";
 const expanded = path.replace(/^(?:~|\$\{HOME\}|\$HOME)(?=\/|$)/, home);
 const parts: string[] = [];
 for (const part of expanded.split("/")) {
  if (part === "..") parts.pop(); else if (part && part !== ".") parts.push(part);
 }
 return (expanded.startsWith("/") ? "/" : "") + parts.join("/");
}
function userUnit(path: string, ownership: Ownership): boolean {
 const root = normalized(`${ownership.home ?? process.env.HOME ?? ""}/.config/systemd/user`, ownership);
 return normalized(path, ownership).startsWith(root + "/");
}
function unstamped(path: string, ownership: Ownership): boolean {
 return userUnit(path, ownership) && !ownership.stampedInstallPaths?.some(item => normalized(item, ownership) === normalized(path, ownership));
}

// Quotes are retained as one argument; shell control operators delimit commands.
// Unknown shell syntax is not interpreted or executed by this audit.
function tokens(command: string): string[][] {
 const result: string[][] = [[]];
 let word = "", quote = "", active = false;
 const flush = () => { if (active) result[result.length - 1].push(word); word = ""; active = false; };
 for (let i = 0; i < command.length; i++) {
  const ch = command[i];
  if (ch === "\\" && quote !== "'") { active = true; if (++i < command.length && command[i] !== "\n") word += command[i]; continue; }
  if (quote) { if (ch === quote) quote = ""; else word += ch; active = true; continue; }
  if (ch === "'" || ch === '"') { quote = ch; active = true; continue; }
  if (ch === "#" && !active) { flush(); while (i < command.length && command[i] !== "\n") i++; result.push([]); continue; }
  if (/[\s;|&()<>]/.test(ch)) {
   flush();
   if (/[;|&()\n]/.test(ch)) result.push([]);
   else if (ch === ">" || ch === "<") result[result.length - 1].push(ch);
  } else { word += ch; active = true; }
 }
 flush(); return result;
}

export function auditCommand(command: string, ownership: Ownership = {}, depth = 0): Finding[] {
 const kinds = new Set<string>();
 const sourceKinds = new Set<string>();
 if (depth > 8) return [{ law: "R1", mode: "report-only", kind: "unparsed-nesting", message }];
 for (const original of tokens(command)) {
  const args = [...original];
  while (args.length) {
   if (/^[A-Za-z_][A-Za-z_0-9]*=/.test(args[0])) { args.shift(); continue; }
   const wrapper = base(args[0]);
   if (!["sudo", "env", "command", "exec", "nohup"].includes(wrapper)) break;
   args.shift();
   while (args[0]?.startsWith("-")) {
    const option = args.shift();
    if (wrapper === "sudo" && ["-u", "-g", "-h", "-p", "-C", "-T", "-r", "-t", "--user", "--group", "--host", "--prompt", "--chdir"].includes(option ?? "")) args.shift();
    if (wrapper === "env" && ["-u", "--unset", "-C", "--chdir"].includes(option ?? "")) args.shift();
   }
  }
  const name = base(args[0] || "");
  if (name === "systemctl" && args.includes("set-property")) kinds.add("systemctl-set-property");
  if (name === "systemctl" && args.includes("--user") && args.includes("set-property") && !args.includes("--runtime")) sourceKinds.add("persistent-user-property");
  const writes = args.filter((arg, index) =>
   (index > 0 && args[index - 1] === ">") ||
   (["tee", "cp", "mv", "install", "touch", "rm", "truncate"].includes(name) && !arg.startsWith("-")));
  for (const path of writes) if (unstamped(path, ownership)) sourceKinds.add("unstamped-user-unit-edit");
  if (name === "dd") for (const arg of args) if (arg.startsWith("of=") && unstamped(arg.slice(3), ownership)) sourceKinds.add("unstamped-user-unit-edit");
  if (args.some((arg, index) => limitPath(arg) && (args.slice(0, index).includes(">") || ["tee", "cp", "install", "dd"].includes(name)))) kinds.add("cgroup-limit-write");
  if (name === "dd" && args.some(arg => arg.startsWith("of=") && limitPath(arg.slice(3)))) kinds.add("cgroup-limit-write");
  if (["renice", "ionice"].includes(name)) kinds.add("service-priority-change");
  if (["kill", "pkill", "killall"].includes(name)) {
   const targets = args.slice(1).filter(arg => !arg.startsWith("-"));
   const queryOnly = args.includes("-l") || args.includes("--list") || (name === "kill" && (args.includes("-0") || args.includes("--signal=0")));
   const owned = targets.length > 0 && targets.every(target => (name === "kill" ? ownership.ownedPids : ownership.ownedPatterns)?.includes(target));
   if (!queryOnly && !owned) kinds.add("other-or-unverified-lane-signal");
  }
  if (["sh", "bash", "dash", "zsh"].includes(name)) {
   const flag = args.findIndex(arg => /^-[^-]*c/.test(arg));
   if (flag >= 0 && args[flag + 1]) for (const finding of auditCommand(args[flag + 1], ownership, depth + 1)) {
    if (finding.law === "I") sourceKinds.add(finding.kind); else kinds.add(finding.kind);
   }
  }
 }
 return [...[...kinds].map(kind => ({ law: "R1" as const, mode: "report-only" as const, kind, message })), ...[...sourceKinds].map(sourceFinding)];
}

export function auditTool(toolName: string, input: Record<string, unknown>, ownership: Ownership = {}): Finding[] {
 if (["bash", "terminal", "execute"].includes(toolName) && typeof input.command === "string") return auditCommand(input.command, ownership);
 if (["write", "write_file", "edit", "patch"].includes(toolName)) {
  const path = input.path ?? input.file_path;
  if (typeof path === "string" && limitPath(path)) return [{ law: "R1", mode: "report-only", kind: "cgroup-limit-write", message }];
  if (typeof path === "string" && unstamped(path, ownership)) return [sourceFinding("unstamped-user-unit-edit")];
 }
 return [];
}
