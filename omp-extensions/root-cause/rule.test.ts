import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { describe, expect, test } from "bun:test";
import rootCauseReport from "./index";
import { auditCommand, auditTool } from "./rule";

describe("R1 report-only raw mitigation rule", () => {
 for (const command of [
  "systemctl --user set-property ctx.service CPUQuota=20%",
  "sudo /usr/bin/systemctl set-property x.service MemoryMax=1G",
  "true && systemctl set-property x.service IOWeight=10",
  "sudo -u kvn systemctl --user set-property ctx.service CPUQuota=20%",
  "printf 1 >> /sys/fs/cgroup/x/memory.max",
  "systemctl --user set-property x.service CPUQuota=10% # report",
  "printf 1 > /sys/fs/cgroup/x/io.max",
  "echo 1 | sudo tee /sys/fs/cgroup/x/memory.max",
  "dd if=/tmp/value of=/sys/fs/cgroup/x/io.max",
  "renice -n 10 -p 123", "ionice -c 3 -p 123",
  "kill -STOP 123", "pkill -f other-lane", "killall ctx",
  "bash -lc 'systemctl --user set-property ctx.service CPUQuota=10%'",
 ]) test(command, () => {
  const findings = auditCommand(command);
  expect(findings.length).toBeGreaterThan(0);
  expect(findings.filter(item => item.law === "R1").every(item => item.mode === "report-only" && item.message.includes("use z0-guard mitigate"))).toBe(true);
 });
 for (const command of [
  "systemctl --user show ctx.service", "cat /sys/fs/cgroup/x/io.max",
  "echo 'systemctl set-property x.service CPUQuota=10%'",
  "# kill 123\nprintf ok", "z0-guard mitigate --help", "kill -0 123",
 ]) test(`non-mitigation: ${command}`, () => expect(auditCommand(command)).toEqual([]));
 test("ownership is explicit host context, not tool-supplied input", () => {
  expect(auditCommand("kill 123", { ownedPids: ["123"] })).toEqual([]);
  expect(auditCommand("kill 123 456", { ownedPids: ["123"] })).toHaveLength(1);
  expect(auditTool("bash", { command: "kill 123", ownedPids: ["123"] })).toHaveLength(1);
  expect(auditCommand("pkill -f my-worker", { ownedPatterns: ["my-worker"] })).toEqual([]);
 });
 test("direct file tools report writes only", () => {
  expect(auditTool("write", { path: "/sys/fs/cgroup/x/memory.max" })).toHaveLength(1);
  expect(auditTool("read", { path: "/sys/fs/cgroup/x/memory.max" })).toEqual([]);
 });
 test("adapter uses tool_call, supplies context, and never blocks or rewrites", async () => {
  let handler: (event: { toolName: string; input: Record<string, unknown> }) => Promise<{ additionalContext: string } | undefined> = async () => undefined;
  // Deliberate partial host mock: this extension uses only on("tool_call").
  rootCauseReport({ on: (name: string, callback: typeof handler) => {
   expect(name).toBe("tool_call"); handler = callback;
  } } as unknown as ExtensionAPI);
  const input = { command: "kill -STOP 123" };
  const result = await handler({ toolName: "bash", input });
  expect(Object.keys(result ?? {})).toEqual(["additionalContext"]);
  expect(input).toEqual({ command: "kill -STOP 123" });
  expect(await handler({ toolName: "bash", input: { command: "true" } })).toBeUndefined();
  expect(Object.keys((await handler({ toolName: "bash", get input() { throw new Error("bad event"); } })) ?? {})).toEqual(["additionalContext"]);
 });
});

describe("I tracked live-config source audit", () => {
 const home = "/home/test";
 const path = `${home}/.config/systemd/user/x.service.d/override.conf`;
 const onlyI = (command: string) => auditCommand(command, { home }).filter(item => item.law === "I");
 test("persistent user properties report; runtime is allowed by I", () => {
  expect(onlyI("systemctl --user set-property x.service CPUQuota=20%")).toHaveLength(1);
  expect(onlyI("sudo -u test systemctl --user --runtime set-property x.service CPUQuota=20%")).toEqual([]);
  expect(onlyI("systemctl set-property x.service CPUQuota=20%")).toEqual([]);
  expect(onlyI("echo 'systemctl --user set-property x.service CPUQuota=20%'")).toEqual([]);
  expect(onlyI("systemctl --user set-property x.service CPUQuota=20% # --runtime")).toHaveLength(1);
 });
 test("file edits require host-owned exact install scope, not markers", () => {
  expect(auditTool("write", { path, content: "# content-stamped install", stampedInstallPaths: [path] }, { home })).toHaveLength(1);
  expect(auditTool("write", { path }, { home, stampedInstallPaths: [path] })).toEqual([]);
  expect(auditTool("edit", { path: path + ".other" }, { home, stampedInstallPaths: [path] })).toHaveLength(1);
  expect(auditTool("read", { path }, { home })).toEqual([]);
  expect(auditTool("write", { path: "~/dotfiles/x.service" }, { home })).toEqual([]);
  expect(auditTool("edit", { path: "~/.config/systemd/user/x.service" }, { home })).toHaveLength(1);
 });
 test("shell writes and nested commands audit without effects", () => {
  for (const command of [`printf x > ${path}`, `tee ${path}`, `cp source ${path}`, `dd of=${path}`,
                         `bash -lc 'printf x > ${path}'`]) expect(onlyI(command)).toHaveLength(1);
  expect(onlyI(`cat ${path}`)).toEqual([]);
  expect(auditCommand(`printf x > ${path}`, { home, stampedInstallPaths: [path] })).toEqual([]);
 });
 test("adapter accepts only host-supplied scoped install context", async () => {
  let handler: (event: { toolName: string; input: Record<string, unknown> }) => Promise<{ additionalContext: string } | undefined> = async () => undefined;
  const api = { on: (_name: string, callback: typeof handler) => { handler = callback; } } as unknown as ExtensionAPI;
  rootCauseReport(api, () => ({ home, stampedInstallPaths: [path] }));
  expect(await handler({ toolName: "write", input: { path } })).toBeUndefined();
  const input = { path: path + ".other", content: "# stamped", stampedInstallPaths: [path + ".other"] };
  const result = await handler({ toolName: "write", input });
  expect(Object.keys(result ?? {})).toEqual(["additionalContext"]);
  expect(result?.additionalContext).toContain("[I report-only:");
  expect(input.content).toBe("# stamped");
 });
});
