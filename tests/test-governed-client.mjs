import test from "node:test";
import assert from "node:assert/strict";
import {buildIntelligenceRequest} from "../harness-adapters/governed-client.mjs";

const base = {
  task: "rewrite public text",
  context: "public",
  parent_agent: "session",
  trace_id: "trace",
  function: "cheap_bounded_worker",
  allow_remote: true,
  max_tokens: 256,
  expected_parent_tokens: 5000,
  expected_parent_ms: 60000,
};

test("governed canary requires all three gates", () => {
  const yes = buildIntelligenceRequest(base, {Z0INT_GOVERNED_REMOTE:"1"});
  assert.equal(yes.path, "/v1/governed-worker");
  assert.equal(yes.governed, true);
  assert.deepEqual(yes.body, {
    harness: "omp",
    trace_id: "trace",
    parent_agent: "session",
    task: "rewrite public text",
    context: "public",
    max_tokens: 256,
    allow_remote: true,
  });

  assert.equal(buildIntelligenceRequest({...base, allow_remote:false}, {Z0INT_GOVERNED_REMOTE:"1"}).path, "/v1/intelligence");
  assert.equal(buildIntelligenceRequest({...base, function:"summarization"}, {Z0INT_GOVERNED_REMOTE:"1"}).path, "/v1/intelligence");
  assert.equal(buildIntelligenceRequest(base, {Z0INT_GOVERNED_REMOTE:"0"}).path, "/v1/intelligence");
});

test("non-governed calls preserve the original payload plus OMP identity", () => {
  const result = buildIntelligenceRequest({...base, state:{x:1}}, {});
  assert.equal(result.path, "/v1/intelligence");
  assert.equal(result.body.harness, "omp");
  assert.equal(result.body.function, "cheap_bounded_worker");
  assert.deepEqual(result.body.state, {x:1});
});

test("harness cannot smuggle provider model or AODL into governed body", () => {
  const result = buildIntelligenceRequest({
    ...base,
    provider:"deepseek",
    model:"paid",
    aodl:{fake:true},
    observed:{tokens:0},
  }, {Z0INT_GOVERNED_REMOTE:"1"});
  assert.equal(result.path, "/v1/governed-worker");
  assert.equal("provider" in result.body, false);
  assert.equal("model" in result.body, false);
  assert.equal("aodl" in result.body, false);
  assert.equal("observed" in result.body, false);
});

test("rejects non-object arguments", () => {
  assert.throws(() => buildIntelligenceRequest(null, {}), /Invalid tool arguments/);
  assert.throws(() => buildIntelligenceRequest([], {}), /Invalid tool arguments/);
});
