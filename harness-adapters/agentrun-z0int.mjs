/**
 * Read-only AgentRun effect adapter for z0intelligence's existing plan endpoint.
 * No dispatch, provider selection override, credential forwarding, or retries.
 */
const REQUEST_KEYS = new Set([
  'trace_id', 'parent_agent', 'function', 'task',
  'expected_parent_tokens', 'expected_parent_ms', 'free_only',
]);
const isRecord = x => x !== null && typeof x === 'object' && !Array.isArray(x);

export class Z0intPlanError extends Error {}

export function buildPlanRequest(request) {
  if (!isRecord(request) || Object.keys(request).some(key => !REQUEST_KEYS.has(key))) {
    throw new Z0intPlanError('Invalid or unsupported plan request fields.');
  }
  for (const name of ['trace_id', 'parent_agent', 'function', 'task']) {
    const text = request[name];
    if (typeof text !== 'string' || !text.trim() || text.length > (name === 'task' ? 24_000 : 200)) {
      throw new Z0intPlanError(`Invalid ${name}.`);
    }
  }
  for (const name of ['expected_parent_tokens', 'expected_parent_ms']) {
    if (!Number.isFinite(request[name]) || request[name] < 0) {
      throw new Z0intPlanError(`Invalid ${name}.`);
    }
  }
  if (request.free_only !== undefined && typeof request.free_only !== 'boolean') {
    throw new Z0intPlanError('Invalid free_only.');
  }
  // Workflow documents cannot grant remote-context permission, or activate
  // execution. Keep the z0 authority as the sole source of routing decisions.
  const body = {
    harness: 'agentrun',
    trace_id: request.trace_id,
    parent_agent: request.parent_agent,
    function: request.function,
    task: request.task,
    expected_parent_tokens: request.expected_parent_tokens,
    expected_parent_ms: request.expected_parent_ms,
    allow_remote: false,
    free_only: request.free_only ?? true,
  };
  if (Buffer.byteLength(JSON.stringify(body), 'utf8') > 40_000) {
    throw new Z0intPlanError('Plan request exceeds size limit.');
  }
  return body;
}

export function planEndpoint(baseURL = 'http://127.0.0.1:11501') {
  let url;
  try { url = new URL(baseURL); }
  catch { throw new Z0intPlanError('Invalid z0intelligence service URL.'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname)
    || url.username || url.password || url.search || url.hash || (url.pathname !== '/' && url.pathname !== '')) {
    throw new Z0intPlanError('AgentRun planner requires a loopback HTTP service origin.');
  }
  return new URL('/v1/plan', url);
}

async function readBoundedPlanBody(response, maxBytes = 96_000) {
  const declared = response.headers?.get?.('content-length');
  if (declared != null && (!/^\d+$/.test(declared) || Number(declared) > maxBytes)) {
    try { await response.body?.cancel?.(); } catch {}
    throw new Z0intPlanError('Oversized or invalid plan response length.');
  }
  const reader = response.body?.getReader?.();
  if (!reader) {
    // Preserve injected text-only transports; native fetch bodies are streamed.
    const raw = await response.text();
    if (Buffer.byteLength(raw, 'utf8') > maxBytes) throw new Z0intPlanError('Oversized plan response.');
    return raw;
  }
  const decoder = new TextDecoder('utf-8');
  const pieces = [];
  let bytes = 0;
  let complete = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > maxBytes) throw new Z0intPlanError('Oversized plan response.');
      pieces.push(decoder.decode(value, {stream:true}));
    }
    pieces.push(decoder.decode());
    complete = true;
    return pieces.join('');
  } finally {
    if (!complete) {
      try { await reader.cancel(); } catch {}
    }
    reader.releaseLock();
  }
}

export function createZ0intPlanEffect({ baseURL, fetchImpl = globalThis.fetch, timeoutMs = 5000 } = {}) {
  const endpoint = planEndpoint(baseURL);
  if (typeof fetchImpl !== 'function' || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000) {
    throw new Z0intPlanError('Invalid host transport configuration.');
  }
  return async function runEffect(params) {
    if (params?.node?.via !== 'tool' || params.node.tool !== 'z0int.plan') {
      throw new Z0intPlanError('AgentRun effect is not on the read-only allowlist.');
    }
    const body = buildPlanRequest(params.input?.request);
    const signal = params.signal
      ? AbortSignal.any([params.signal, AbortSignal.timeout(timeoutMs)])
      : AbortSignal.timeout(timeoutMs);
    // No retry: the host owns dispatch and replay semantics.
    const response = await fetchImpl(endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal,
    });
    if (!response?.ok) throw new Z0intPlanError('z0intelligence plan endpoint rejected the request.');
    const raw = await readBoundedPlanBody(response);
    let result;
    try { result = JSON.parse(raw); }
    catch { throw new Z0intPlanError('Malformed plan response.'); }
    if (!isRecord(result) || result.ok !== true || result.mode !== 'shadow'
      || result.executed !== false || !isRecord(result.route)
      || typeof result.route.kind !== 'string' || !result.route.kind
      || typeof result.route.reason !== 'string' || result.route.executed !== false) {
      throw new Z0intPlanError('z0intelligence did not return a read-only plan.');
    }
    return result;
  };
}
