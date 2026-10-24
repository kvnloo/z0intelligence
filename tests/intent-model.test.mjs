import { test, expect } from 'bun:test';
import { Database } from 'bun:sqlite';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { subscriptionLogin } from '../src/z0int/intent_model.mjs';

function store(data, type = 'oauth') {
  const home = mkdtempSync(join(tmpdir(), 'intent-subscription-'));
  const path = join(home, 'agent.db');
  const db = new Database(path);
  db.exec('CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY, provider TEXT, credential_type TEXT, data TEXT, disabled_cause TEXT, updated_at INTEGER)');
  db.query('INSERT INTO auth_credentials VALUES (1, ?, ?, ?, NULL, 1)').run('openai-codex', type, JSON.stringify(data));
  db.close();
  return { home, path };
}

test('reads only existing OMP OAuth without modifying credential store', () => {
  const s = store({ access: 'synthetic-access', accountId: 'synthetic-account', expires: Date.now() + 60000 });
  const before = readFileSync(s.path);
  expect(subscriptionLogin({ OMP_AGENT_DIR: s.home }).accountId).toBe('synthetic-account');
  expect(readFileSync(s.path).equals(before)).toBe(true);
});

test('rejects API-key rows and expired subscription without fallback', () => {
  for (const [data, type] of [
    [{ access: 'synthetic', accountId: 'account', expires: Date.now() + 60000 }, 'api_key'],
    [{ access: 'synthetic', accountId: 'account', expires: 1 }, 'oauth'],
    [{ access: 'synthetic', expires: Date.now() + 60000 }, 'oauth'],
  ]) {
    const s = store(data, type);
    expect(() => subscriptionLogin({ OMP_AGENT_DIR: s.home })).toThrow('OAuth subscription');
  }
});
