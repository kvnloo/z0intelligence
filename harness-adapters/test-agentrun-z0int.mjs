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
    assert.throws(() => planEndpoint(url), Z0intPlanError);
  }
});

test('reject execution-shaped responses, malformed JSON, and service failures', async () => {
  for (const candidate of [
    {...plan('REMOTE_WORKER'), executed:true},
    {...plan('REMOTE_WORKER'), route:{kind:'REMOTE_WORKER',reason:'x',executed:true}},
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

const runResponse = response => createZ0intPlanEffect({fetchImpl: async () => response})(
  {node:{via:'tool',tool:'z0int.plan'},input:{request:req}},
);

test('cancel a chunked oversized response before consuming its whole body', async t => {
  let pulls = 0;
  let cancelled = false;
  const response = new Response(new ReadableStream({
    pull(controller) {
      if (pulls === 512) return controller.close();
      pulls++;
      controller.enqueue(new Uint8Array(16_384));
    },
    cancel() { cancelled = true; },
  }, {highWaterMark:0}));
  await assert.rejects(runResponse(response), /Oversized plan response/);
  t.diagnostic(`bytes pulled: ${pulls * 16_384}; cancelled: ${cancelled}`);
  assert.ok(pulls * 16_384 <= 96_000 + 16_384, `pulled ${pulls * 16_384} bytes`);
  assert.equal(cancelled, true);
  assert.equal(response.body.locked, false);
});

test('refuse oversized or invalid declared length before reading any body', async () => {
  for (const length of ['96001', '999999999999999999999999', '-1', 'invalid']) {
    let pulls = 0;
    let cancelled = false;
    const response = new Response(new ReadableStream({
      pull(controller) {
        pulls++;
        controller.enqueue(new TextEncoder().encode(JSON.stringify(plan('PARENT_ONLY'))));
        controller.close();
      },
      cancel() { cancelled = true; },
    }, {highWaterMark:0}), {headers:{'content-length':length}});
    await assert.rejects(runResponse(response), /plan response length/);
    assert.equal(pulls, 0, length);
    assert.equal(cancelled, true, length);
  }
});

test('decode a valid UTF-8 plan across byte boundaries', async () => {
  const expected = plan('PARENT_ONLY');
  expected.route.reason = 'Local résumé — bounded 日本語 plan';
  const bytes = new TextEncoder().encode(JSON.stringify(expected));
  let offset = 0;
  const response = new Response(new ReadableStream({
    pull(controller) {
      if (offset === bytes.length) return controller.close();
      controller.enqueue(bytes.slice(offset, ++offset));
    },
  }));
  assert.deepEqual(await runResponse(response), expected);
});

test('accept an exactly 96000-byte plan and reject one extra byte', async () => {
  const expected = plan('PARENT_ONLY');
  expected.padding = '';
  expected.padding = 'x'.repeat(96_000 - Buffer.byteLength(JSON.stringify(expected), 'utf8'));
  const raw = JSON.stringify(expected);
  assert.equal(Buffer.byteLength(raw, 'utf8'), 96_000);
  assert.deepEqual(await runResponse(new Response(raw)), expected);
  await assert.rejects(runResponse(new Response(raw + ' ')), /Oversized plan response/);
});

test('release the reader when the response stream fails', async () => {
  let pulls = 0;
  const failure = new Error('fixture stream failure');
  const response = new Response(new ReadableStream({
    pull(controller) {
      if (pulls++) return controller.error(failure);
      controller.enqueue(new Uint8Array([123]));
    },
  }, {highWaterMark:0}));
  await assert.rejects(runResponse(response), failure);
  assert.equal(response.body.locked, false);
});

test('retain the size check for text-only injected transports', async () => {
  await assert.rejects(runResponse({ok:true,text:async()=>'x'.repeat(96_001)}), /Oversized plan response/);
});
