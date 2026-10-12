import test from 'node:test';
import assert from 'node:assert/strict';
import { createJevRunner } from '@parcha/agentrun-jev';
import { inspectWorkflow } from '@parcha/agentrun-dsl';
import { planReviewWorkflow } from './workflow.mjs';
import { runLivePlanReview, runPlanReviewWithAdapters, AgentRunSetupError } from './runner.mjs';

const input = { request: {
  trace_id: 'fixture-plan-1', parent_agent:'agentrun-ci', function:'cheap_bounded_worker',
  task:'Read a public report', expected_parent_tokens:5000,
  expected_parent_ms:45000, free_only:true,
}};
const plan = kind => ({ok:true,mode:'shadow',executed:false,route:{
  kind,reason:'Existing z0 route policy result',executed:false,registry_sha256:'a'.repeat(64),
}});
const decision = (choice,confidence=0.96) => ({answers:{routeFits:{
  type:'choice',choice,confidence,
  probabilities:Object.fromEntries(['yes','no','uncertain'].map(k=>[k,k===choice?confidence:(1-confidence)/2])),
}}});

const deps = (kind, verdict='yes',confidence=0.96) => {
  const calls = [];
  return {calls, adapters:{
    async runEffect({node,input:args}) {
      calls.push('tool');
      assert.equal(node.tool,'z0int.plan');
      assert.deepEqual(args.request,input.request);
      return plan(kind);
    },
    async runJudge({kind:nodeKind,questions,state}) {
      calls.push('judge');
      assert.equal(nodeKind,'judge');
      assert.deepEqual(Object.keys(questions),['routeFits']);
      assert.equal(state.task,input.request.task);
      return decision(verdict,confidence);
    },
  }};
};

test('the fixed workflow can be inspected without running any authored code', () => {
  const structure = inspectWorkflow(planReviewWorkflow);
  assert.ok(structure);
  assert.equal(planReviewWorkflow.v,2);
  assert.equal(planReviewWorkflow.root.steps[0].tool,'z0int.plan');
});

test('PARENT_ONLY never transmits a query to Jev', async () => {
  const a=deps('PARENT_ONLY');
  const {result,report}=await runPlanReviewWithAdapters(input,a.adapters);
  assert.equal(result.status,'complete');
  assert.equal(result.output.route.kind,'PARENT_ONLY');
  assert.deepEqual(a.calls,['tool']);
  assert.equal(report.judgeCalls,0);
  assert.equal(report.planOnly,true);
});

test('a nonparent route receives one Jev judgment and may complete', async () => {
  const a=deps('REMOTE_WORKER');
  const {result,report}=await runPlanReviewWithAdapters(input,a.adapters);
  assert.equal(result.status,'complete');
  assert.deepEqual(a.calls,['tool','judge']);
  assert.equal(report.judgeCalls,1);
  assert.equal(report.routeKind,'REMOTE_WORKER');
  assert.equal(JSON.stringify(report).includes(input.request.task),false);
});

test('unsupported or low-confidence candidates escalate instead of authorizing execution', async () => {
  for (const [verdict,confidence] of [['no',.97],['uncertain',.96],['yes',.7]]) {
    const a=deps('REMOTE_WORKER',verdict,confidence);
    const {result,report}=await runPlanReviewWithAdapters(input,a.adapters);
    assert.equal(result.status,'escalated');
    assert.equal(report.status,'escalated');
    assert.deepEqual(a.calls,['tool','judge']);
  }
});

test('real Jev adapter accepts an injected offline transport (not a live model)', async () => {
  const networkCalls=[];
  const runJudge=createJevRunner({maxAttempts:1,client:{
    async systemOne(question,options) {
      networkCalls.push(question);
      options.signal?.throwIfAborted();
      assert.deepEqual(Object.keys(question.questions),['routeFits']);
      return decision('yes');
    },
  }});
  const a=deps('REMOTE_WORKER');
  const {result}=await runPlanReviewWithAdapters(input,{runEffect:a.adapters.runEffect,runJudge});
  assert.equal(result.status,'complete');
  assert.equal(networkCalls.length,1);
});

test('live execution requires opt-in and real Jev configuration before calling models', async () => {
  await assert.rejects(runLivePlanReview(input,{}), AgentRunSetupError);
  await assert.rejects(runLivePlanReview(input,{allowRemoteJev:true,env:{}}),AgentRunSetupError);
});
