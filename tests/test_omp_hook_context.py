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


# Replaces node:child_process.spawn with a recorder before the extension under
# test is imported, so no interpreter is ever started. The fake child closes
# with exit code 1, which both extensions treat as "adapter failed".
FAKE_SPAWN = r"""
import assert from 'node:assert/strict';
import cp from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {EventEmitter} from 'node:events';
import * as fs from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
const spawns = [];
cp.spawn = (cmd, args) => {
  spawns.push([cmd, ...(args || [])]);
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = Object.assign(new EventEmitter(), {resume() {}});
  child.stdin = {on() {}, end() {}};
  child.kill = () => {};
  setImmediate(() => child.emit('close', 1));
  return child;
};
syncBuiltinESMExports();
"""


def _run_node(script, tmp_path, **env):
    import os

    home = tmp_path / "home"
    home.mkdir()
    base = {k: v for k, v in os.environ.items() if k not in ("Z0INT_AUTO_OMP", "EVOLUTION_LAB_PYTHON")}
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", FAKE_SPAWN + script],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=25,
        env={**base, "HOME": str(home), "Z0INT_HOME": str(tmp_path / "z0home"), **env},
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    return proc.stdout


def test_omp_hook_spawns_only_when_operator_enabled_and_defers_to_worker(tmp_path):
    root = Path(__file__).resolve().parents[1]
    module_url = (root / "omp-extensions" / "z0int-intelligence" / "index.ts").as_uri()
    script = r"""
const home = process.env.Z0INT_HOME;
const rtt = () => {
  try { return fs.readFileSync(join(home, 'stream', 'hook_rtt.jsonl'), 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)); }
  catch { return []; }
};
const waitRows = async n => {
  for (let i = 0; i < 300 && rtt().length < n; i++) await new Promise(r => setTimeout(r, 10));
  return rtt();
};
const setConfig = value => {
  fs.mkdirSync(join(home, 'config'), {recursive: true});
  fs.writeFileSync(join(home, 'config', 'automatic.json'), JSON.stringify(value));
};
const plugin = (await import(__PLUGIN_URL__)).default;
let hook;
plugin({events: {}, on: (name, handler) => { if (name === 'before_agent_start') hook = handler; }, registerTool() {}});
const ctx = {sessionManager: {getSessionId: () => 'fixture-session'}};
const requests = [];
globalThis.__omp_z0int_bridge_transport__ = {
  request: (body, timeoutMs) => { requests.push({body, timeoutMs}); return Promise.resolve({ok: true}); },
};

// No automatic.json at all: native turn, no spawn, receipt handed to the worker.
assert.equal(await hook({prompt: 'Summarize test'}, ctx), undefined);
assert.deepEqual(spawns, []);
assert.equal(requests.length, 1);
assert.equal(requests[0].body.op, 'automatic_event');
assert.equal(requests[0].body.payload.harness, 'omp');
assert.equal(requests[0].body.payload.session_id, 'fixture-session');
assert.equal(requests[0].body.payload.text, 'Summarize test');
let rows = await waitRows(1);
assert.equal(rows.length, 1);
assert.equal(rows[0].schema, 'z0int.hook_rtt.v1');
assert.equal(rows[0].spawn, false);
assert.equal(rows[0].deferred, true);
assert.equal(rows[0].session_id, 'fixture-session');

// Only a literal `true` enables the spawning path.
for (const value of [{}, {omp: {}}, {omp: {enabled: 'true'}}, {omp: {enabled: 1}}]) {
  setConfig(value);
  assert.equal(await hook({prompt: 'Summarize test'}, ctx), undefined);
  assert.deepEqual(spawns, [], JSON.stringify(value));
}

// Operator-enabled file, environment kill-switch wins.
setConfig({omp: {enabled: true}});
process.env.Z0INT_AUTO_OMP = '0';
assert.equal(await hook({prompt: 'Summarize test'}, ctx), undefined);
assert.deepEqual(spawns, []);
assert.equal(requests.length, 6);
delete process.env.Z0INT_AUTO_OMP;

// Control: the recorder does see the operator-enabled spawn, and that path
// does not also defer to the worker.
assert.equal(await hook({prompt: 'Summarize test'}, ctx), undefined);
assert.equal(spawns.length, 1);
assert.deepEqual(spawns[0].slice(1), ['-m', 'z0int.automatic', 'event']);
assert.equal(requests.length, 6);
rows = await waitRows(6);
assert.equal(rows.length, 6);
assert.equal(fs.existsSync(join(homedir(), '.z0int')), false);
console.log('PASS OMP hook spawn gate');
""".replace("__PLUGIN_URL__", json.dumps(module_url))
    assert "PASS OMP hook spawn gate" in _run_node(script, tmp_path)


def test_flyforge_shadow_never_spawns_an_unusable_interpreter(tmp_path):
    root = Path(__file__).resolve().parents[1]
    module_url = (root / "omp-extensions" / "flyforge-jev" / "index.ts").as_uri()
    script = r"""
const fx = process.env.FIXTURES;
fs.mkdirSync(join(fx, 'bin'), {recursive: true});
fs.mkdirSync(join(fx, 'dir-python'));
fs.writeFileSync(join(fx, 'nonexec-python'), '#!/bin/sh\n');
fs.chmodSync(join(fx, 'nonexec-python'), 0o644);
fs.symlinkSync(join(fx, 'nowhere'), join(fx, 'dangling-python'));
fs.writeFileSync(join(fx, 'bin', 'fake-python'), '#!/bin/sh\n');
fs.chmodSync(join(fx, 'bin', 'fake-python'), 0o755);
fs.symlinkSync(join(fx, 'bin', 'fake-python'), join(fx, 'link-python'));
process.env.PATH = join(fx, 'bin');
process.env.EVOLUTION_LAB_ROOT = fx;
let version = 0;
async function spawnsFor(python) {
  process.env.EVOLUTION_LAB_PYTHON = python;
  // PY is read once at module load: import a fresh copy per interpreter.
  const mod = await import(__PLUGIN_URL__ + '?v=' + version++);
  let hook;
  mod.default({setLabel() {}, on: (name, handler) => { if (name === 'before_agent_start') hook = handler; }});
  const before = spawns.length;
  await hook({prompt: 'fix the bug'}, {sessionId: 's'});
  return spawns.slice(before);
}
const unusable = {
  missing: join(fx, 'absent', 'bin', 'python'),
  directory: join(fx, 'dir-python'),
  not_executable: join(fx, 'nonexec-python'),
  dangling_symlink: join(fx, 'dangling-python'),
};
for (const [label, python] of Object.entries(unusable)) {
  assert.deepEqual(await spawnsFor(python), [], label);
}
// An early return writes no shadow or stream rows.
assert.equal(fs.existsSync(join(homedir(), '.z0int')), false);
const usable = {
  absolute_executable: join(fx, 'bin', 'fake-python'),
  symlink_to_executable: join(fx, 'link-python'),
  bare_name_on_path: 'fake-python',
  // A bare name is the OS's to resolve (PATH may be unset or hold relative entries): never refused here.
  bare_name_not_on_path: 'no-such-python-zz9',
};
for (const [label, python] of Object.entries(usable)) {
  const seen = await spawnsFor(python);
  assert.deepEqual(seen.map(call => call.slice(0, 3)), [
    [python, '-m', 'evolution_lab'], [python, '-m', 'evolution_lab'],
  ], label);
  assert.deepEqual(seen.map(call => call[3]).sort(), ['jev-predict', 'next-action-decide'], label);
}
console.log('PASS flyforge interpreter guard');
""".replace("__PLUGIN_URL__", json.dumps(module_url))
    fixtures = tmp_path / "interp"
    fixtures.mkdir()
    assert "PASS flyforge interpreter guard" in _run_node(script, tmp_path, FIXTURES=str(fixtures))
