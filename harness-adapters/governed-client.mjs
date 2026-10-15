export function buildIntelligenceRequest(args, env = process.env) {
  if (typeof args !== "object" || args === null || Array.isArray(args)) {
    throw new Error("Invalid tool arguments");
  }
  const record = args;
  const governed =
    env.Z0INT_GOVERNED_REMOTE === "1" &&
    record.function === "cheap_bounded_worker" &&
    record.allow_remote === true;

  if (!governed) {
    return {path: "/v1/intelligence", body: {...record, harness: "omp"}, governed: false};
  }

  return {
    path: "/v1/governed-worker",
    governed: true,
    body: {
      harness: "omp",
      trace_id: record.trace_id,
      parent_agent: record.parent_agent,
      task: record.task,
      context: record.context,
      max_tokens: record.max_tokens,
      allow_remote: true,
    },
  };
}
