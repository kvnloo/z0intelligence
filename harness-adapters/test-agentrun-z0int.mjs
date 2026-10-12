import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildPlanRequest, planEndpoint, createZ0intPlanEffect, Z0intPlanError,
} from './agentrun-z0int.mjs';

const req = {
  trace_id: 'trace-001', parent_agent: 'agentrun-local', function: 'cheap_bounded_worker',
  task: 'Summarize public benchmark metadata', expected_parent_tokens: 6000,
  expected_parent_ms: 45000, free_only: true,
};
const plan = kind => ({ ok: true, mode: 'shadow', executed: false, route: {
  kind, reason: 'Observed overhead exceeds small-task budget', executed: false,
  registry_sha256: 'a'.repeat(64),
}});

// Pure ingress tests don't require npm, Jev, z0int service, or network.
test('only local read-only z0 route, no remote override or alternative tool', async () => {
  const requests = [];
  const effect = createZ0intPlanEffect({ fetchImpl: async (url, opts) => {
    requests.push({ url, opts });
    return { ok: true, text: async () => JSON.stringify(plan('PARENT_ONLY')) };
  }});
  const result = await effect({node: {via:'tool', tool:'z0int.plan'}, input:{request:req}});
  assert.equal(result.route.kind, 'PARENT_ONLY');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url.href, 'http://127.0.0.1:11501/v1/plan');
  const sent = JSON.parse(requests[0].opts.body);
  assert.deepEqual(sent, {harness:'agentrun', ...req, allow_remote:false});
  assert.equal(requests[0].opts.method,'POST');
  assert.ok(requests[0].opts.signal);
  await assert.rejects(effect({node:{via:'tool', tool:'shell.run'},input:{request:req}}), Z0intPlanError);
});

test('refuse spoofed policy keys and malformed budgets before network', async () => {
  for (const extra of [{allow_remote:true},{harness:'omp'},{state:{foo:'secret'}},
                       {model:'paid'},{free_only:'yes'},{expected_parent_ms:-1},
                       {expected_parent_tokens:Infinity},{task:''}]) {
    assert.throws(() => buildPlanRequest({...req,...extra}), Z0intPlanError);
  }
  assert.equal(buildPlanRequest({...req, free_only:false}).free_only, false);
});

test('only connect to the local service, never remote or credential-bearing URLs', () => {
  assert.equal(planEndpoint('http://localhost:11501').pathname, '/v1/plan');
  assert.equal(planEndpoint('http://[::1]:11501').pathname, '/v1/plan');
  for (const url of ['https://localhost:11501','http://example.com',
                     'http://127.0.0.1:11501/execute', 'http://user:pass@localhost:11501',
                     'file:///etc/passwd', 'http://localhost:11501?path=/v1/intelligence']) {
    assert.throws(() => planEndppoint(url), Z0intPlanError);
  }
});

test('reject execution-shaped responses, malformed JSON, and service failures', asyc () => {
  for (const candidate of [
    {...plan('REMOTE_WORKER'), executed:true},
    {...plan(	REMOTE_WORKER'), route:{kind:'REMOTE_WORKER',reason:'x',executed:true}},
    {ok:true,mode:'active',executed:false,route:{kind:'PARENT_ONLY', reason:'x', executed:false}},
  ]) {
    const effect = createZ0intPlanEffect({fetchImpl:async()=>({ok:true,text:async()=>JSON.stringify(candidate)})});
    await assert.rejects(effect({node:{via:'tool',tool:'z0int.plan'},input:{request:req}}), Z0intPlanError);
  }
  for (const response of [{ok:false,text:async()=>''},{ok:true,text:async()=>'not json'}]) {
    const effect = createZ0intPlanEffect({fetchImpl:async()=>response});
  await assert.rejects(effect({node:{via:'tool',tool:'z0int.plan'},input:{request:req}}), Z0intPlanError);
  }
});
