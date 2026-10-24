import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {appendFile, mkdir} from 'node:fs/promises';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
export const instanceId = randomUUID();

export function call(operation, value) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.Z0INT_PYTHON || 'python3', ['-m', 'z0int.automatic', operation], {
      env: {...process.env, PYTHONPATH: root + 'src' + (process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : '')},
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let out = '';
    const timer = setTimeout(() => child.kill(), 30000);
    child.stdout.on('data', chunk => {
      out += chunk;
      if (out.length > 200000) child.kill();
    });
    child.stderr.resume();
    child.on('error', reject);
    child.stdin.on('error', () => {});
    child.on('close', code => {
      clearTimeout(timer);
      try {
        if (code) throw new Error('automatic adapter failed');
        resolve(JSON.parse(out));
      } catch (error) {
        reject(error);
      }
    });
    child.stdin.end(JSON.stringify(value));
  });
}

export async function route(harness, sessionId, turnId, text) {
  try {
    return await call('event', {harness, session_id: sessionId, turn_id: turnId, instance_id: instanceId, text});
  } catch {
    return {action: 'native', ready: false};
  }
}

export async function delivered(harness, result) {
  if (!result.receipt_id) return;
  try {
    await call('consume', {harness, instance_id: instanceId, receipt_id: result.receipt_id});
  } catch {
    /* Native behavior survives service failure; readiness does not turn green. */
  }
}

export function ompHotPath() {
  return {action: 'native', deferred: true, spawn: false};
}

export function scheduleOmpAutomatic({sessionId, turnId, text, request}) {
  const started = Date.now();
  const decision = ompHotPath();
  if (typeof request === 'function') {
    void Promise.resolve(request({
      op: 'automatic_event',
      payload: {harness: 'omp', session_id: sessionId, turn_id: turnId, instance_id: instanceId, text},
    }, 5000)).catch(() => {});
  }
  const blocked_ms = Date.now() - started;
  const row = {
    schema: 'z0int.hook_rtt.v1',
    ts: Date.now() / 1000,
    blocked_ms,
    spawn: false,
    deferred: typeof request === 'function',
    session_id: sessionId,
    turn_id: turnId,
  };
  const dir = join(process.env.Z0INT_HOME || join(homedir(), '.z0int'), 'stream');
  void mkdir(dir, {recursive: true}).then(() => appendFile(join(dir, 'hook_rtt.jsonl'), JSON.stringify(row) + '\n')).catch(() => {});
  return {...decision, blocked_ms};
}
