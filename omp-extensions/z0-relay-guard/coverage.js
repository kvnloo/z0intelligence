const fail = message => { throw new Error(message); };

export const sentences = text => text.split(/[.?!\r\n]+/).map(part => part.trim()).filter(Boolean);

/** Both relay COVERAGE and final ASKS blocks use the same quoted-sentence grammar. */
export function coverageLine(line, mode) {
  const match = /^- ("(?:[^"\\]|\\.)*") -> (.+)$/.exec(line);
  if (!match) fail(`Invalid coverage line: ${line}`);
  const sentence = JSON.parse(match[1]);
  const disposition = match[2];
  let kind = disposition, value = "";
  if (disposition.startsWith("dropped:")) { kind = "dropped"; value = disposition.slice(8).trim(); }
  else if (disposition.startsWith("covered:")) { kind = "covered"; value = disposition.slice(8).trim(); }
  else if (disposition.startsWith("delegated:")) { kind = "delegated"; value = disposition.slice(10).trim(); }
  else if (disposition.startsWith("superseded by ")) { kind = "superseded"; value = disposition.slice(14).trim(); }
  const allowed = mode === "asks" ? ["covered", "delegated", "dropped", "not-an-ask"]
    : ["covered", "dropped", "superseded", "not-an-ask"];
  if (!allowed.includes(kind)) fail(`Invalid ${mode} disposition: ${disposition}`);
  if (["dropped", "delegated", "superseded"].includes(kind) && !value) fail(`${kind} requires a value`);
  if (kind === "covered" && (mode === "asks" ? !value : disposition !== "covered")) fail(`Invalid ${mode} covered evidence`);
  if (kind === "delegated" && !/^[A-Za-z0-9_.-]+$/.test(value)) fail("Delegated ask requires one agent id");
  if (kind === "superseded" && /\s/.test(value)) fail("Superseded coverage requires one source id");
  return { sentence, kind, value };
}
