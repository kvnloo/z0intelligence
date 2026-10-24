// Reuse the owner's OMP subscription read-only, as UniiChat Subscription.login does.
// No API keys, refresh, credential copies, account switching, or token logging.
import { Database } from 'bun:sqlite';
import { homedir } from 'node:os';
import { join } from 'node:path';

export function subscriptionLogin(env = process.env) {
  const file = join(env.OMP_AGENT_DIR || join(homedir(), '.omp', 'agent'), 'agent.db');
  let db;
  try {
    db = new Database(file, { readonly: true });
    const row = db.query("SELECT data FROM auth_credentials WHERE provider = 'openai-codex' AND credential_type = 'oauth' AND disabled_cause IS NULL ORDER BY updated_at DESC, id DESC LIMIT 1").get();
    const login = row && JSON.parse(row.data);
    if (!login || typeof login.access !== 'string' || !login.access ||
        typeof login.accountId !== 'string' || !login.accountId ||
        !Number.isFinite(login.expires) || login.expires <= Date.now()) {
      throw new Error('invalid subscription');
    }
    return login;
  } catch {
    throw new Error('A valid OMP openai-codex OAuth subscription is required. Refresh through OMP.');
  } finally { db?.close(); }
}

export async function compile(request, env = process.env) {
  const login = subscriptionLogin(env);
  const model = env.Z0INT_INTENT_MODEL || 'gpt-6-luna';
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), Number(env.Z0INT_INTENT_MODEL_TIMEOUT_MS || 60000));
  try {
    const response = await fetch('https://chatgpt.com/backend-api/codex/responses', {
      method: 'POST', signal: controller.signal,
      headers: { authorization: `Bearer ${login.access}`, 'chatgpt-account-id': login.accountId,
        originator: 'codex_cli_rs', 'user-agent': 'codex_cli_rs/0.162.1',
        'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify({ model, instructions: request.instructions,
        input: [{ role: 'user', content: [{ type: 'input_text', text: JSON.stringify(request.context) }] }],
        reasoning: { effort: env.Z0INT_INTENT_EFFORT || 'low' }, tools: [], store: false, stream: true }),
    });
    if (!response.ok || !response.body) throw new Error(`Subscription compiler HTTP ${response.status}`);
    let buffer = '', output = '', terminal = false;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      if (buffer.length + output.length > 2_000_000) throw new Error('Compiler response too large');
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trimEnd(); buffer = buffer.slice(newline + 1);
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === 'response.output_text.delta') output += event.delta;
        if (['error', 'response.failed', 'response.incomplete'].includes(event.type)) throw new Error('Subscription compiler did not complete');
        if (['response.completed', 'response.done'].includes(event.type)) terminal = true;
      }
      if (done) break;
    }
    if (!terminal || !output) throw new Error('Subscription compiler returned no completed draft');
    const draft = JSON.parse(output.replace(/^```(?:json)?\s*|\s*```$/g, ''));
    return { draft, model_id: `openai-codex/${model}` };
  } finally { clearTimeout(deadline); }
}

if (import.meta.main) {
  try { console.log(JSON.stringify(await compile(JSON.parse(await Bun.stdin.text())))); }
  catch (error) { console.error(error.name === 'AbortError' ? 'Subscription compiler deadline exceeded' : error.message); process.exitCode = 1; }
}
