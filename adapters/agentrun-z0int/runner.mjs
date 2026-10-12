/** Optional AgentRun workflow above the canonical z0intelligence read-only planner. */
import { runWorkflow, workflowSha256 } from '@parcha/agentrun-dsl';
import { createJevRunner } from '@parcha/agentrun-jev';
import { createZ0intPlanEffect } from '../../harness-adapters/agentrun-z0int.mjs';
import { planReviewWorkflow } from './workflow.mjs';

export class AgentRunSetupError extends Error {}

const isRecord = x => x !== null && typeof x === 'object' && !Array.isArray(x);

export async function runPlanReviewWithAdapters(input, adapters, { signal, mode = 'offline adapters' } = {}) {
  if (!isRecord(adapters) || typeof adapters.runEffect !== 'function' || typeof adapters.runJudge !== 'function') {
    throw new AgentRunSetupError('Both tool and judgment adapters are required.');
  }
  const calls = [];
  const observe = (kind, fn) => async params => {
    const row = { kind, label: params.label ?? params.node?.label, status: 'started' };
    calls.push(row);
    try {
      const result = await fn(params);
      row.status = 'returned';
      return result;
    } catch (error) {
      row.status = 'failed';
      throw error;
    }
  };
  // Intentionally no runNode or write-capable effects: this is a plan-only slice.
  const result = await runWorkflow(planReviewWorkflow, input, {
    runEffect: observe('tool', adapters.runEffect),
    runJudge: observe('judge', adapters.runJudge),
    signal,
  });
  return {
    result,
    report: {
      mode, workflow_sha256: workflowSha256(planReviewWorkflow),
      status: result.status,
      planOnly: true,
      routeKind: result.status === 'complete' ? result.output.route.kind : null,
      toolCalls: calls.filter(c => c.kind === 'tool').length,
      judgeCalls: calls.filter(c => c.kind === 'judge').length,
      calls,
      // No task, provider response, raw errors, prompt, or fake verified_success.
    },
  };
}

export async function runLivePlanReview(input, {
  serviceURL, fetchImpl, allowRemoteJev = false, jev = {}, env = process.env,
  signal, timeoutMs = 30_000,
} = {}) {
  if (allowRemoteJev !== true) {
    throw new AgentRunSetupError('Explicit allowRemoteJev=true is required before transmitting any task to Jev.');
  }
  if (!isRecord(jev) || Object.keys(jev).some(key => !['apiKey', 'baseURL', 'model', 'timeoutMs', 'maxAttempts'].includes(key))) {
    throw new AgentrunSetupError('Unsupported Jev host configuration.');
  }
  const apiKey = jev.apiKey ?? env.TYPESAFE_API_KEY;
  if (typeof apiKey !== 'string' || !apiKey.trim()) {
    throw new AgentRunSetupError('Jev key unavailable in the server environment (TYPESAFE_API_KEY).');
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120_000) {
    throw new AgentRunSetupError('Invalid workflow deadline.');
  }
  const deadline = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, deadline]) : deadline;
  const started = performance.now();
  const runJudge = createJevRunner({ ...jev, apiKey, signal: combined });
  const runEffect = createZ0intPlanEffect({ baseURL: serviceURL, fetchImpl });
  const response = await runPlanReviewWithAdapters(input, { runEffect, runJudge }, {
    signal: combined, mode: 'live Jev + z0int local read-only plan',
  });
  response.report.wallMs = Math.round((performance.now() - started) * 100) / 100;
  return response;
}
