"""Real OMP extension entry-point acceptance for hot path context delivery (#139).

No provider calls. A localhost fake provider returns a specialized context result
to the real Python automatic adapter, and the registered TS hook must deliver it.
"""
import json
import subprocess
from pathlib import Path


def test_omp_enabled_context_is_model_visible_and_disabled_remains_spawn_free():
    root = Path(__file__).resolve().parents[1]
    module_url = (root / "omp-extensions" / "z0int-intelligence" / "index.ts").as_uri()
    script = r"""
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import plugin from __PLUGIN_URL__;

const home = mkdtempSync(join(tmpdir(), 'z0-omp-hook-'));
mkdirSync(join(home, 'config'));
writeFileSync(join(home, 'config', 'automatic.json'), JSON.stringify({omp: {enabled: true}}));
process.env.Z0INT_HOME = home;
const calls = [];
const server = createServer(async (req, res) => {
  calls.push(req.url);
  for await (const _chunk of req) { /* consume request body */ }
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/v1/automatic') {
    res.end(JSON.stringify({ok: true, executed: true, dispatch_receipt_id: 'fixture-1', value: 'verified-fixture'}));
  } else if (req.url === '/v1/automatic/consumed') {
    res.end(JSON.stringify({ok: true, receipt_id: 'consume-fixture-1'}));
  } else {
    res.statusCode = 404;
    res.end('{}');
  }
});
try {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  process.env.Z0INT_SERVICE_URL = `http://127.0.0.1:${server.address().port}`;
  let hook;
  const pi = {
    events: {},
    on: (name, handler) => { if (name === 'before_agent_start') hook = handler; },
    registerTool: () => {},
  };
  plugin(pi);
  assert.equal(typeof hook, 'function');
  const ctx = {sessionManager: {getSessionId: () => 'fixture-session'}};
  // Force disabled state even though the file says enabled: the handler must
  // not start Python, call the provider, or invent context.
  process.env.Z0INT_AUTO_OMP = '0';
  const disabled = await hook({prompt: 'Summarize test'}, ctx);
  assert.equal(disabled, undefined);
  assert.deepEqual(calls, []);
  delete process.env.Z0INT_AUTO_OMP;
  const enabled = await hook({prompt: 'Summarize test'}, ctx);
  assert.equal(enabled?.message?.customType, 'z0intelligence');
  assert.equal(enabled?.message?.display, true);
  assert.match(enabled.message.content, /verified-fixture/);
  assert.deepEqual(calls, ['/v1/automatic', '/v1/automatic/consumed']);
  console.log('PASS real OMP hook: disabled spawn-free, enabled model-visible context and delivery receipt');
} finally {
  await new Promise(resolve => server.close(resolve));
  rmSync(home, {recursive: true, force: true});
}
""".replace("__PLUGIN_URL__", json.dumps(module_url))
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=root, capture_output=True, text=True, timeout=25,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "PASS real OMP hook" in proc.stdout
