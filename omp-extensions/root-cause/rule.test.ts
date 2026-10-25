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
  expect(findings.every(item => item.mode === "report-only" && item.message.includes("use z0-guard mitigate"))).toBe(true);
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
