#!/usr/bin/env python3
"""Codex MCP transport adapter; routing and execution belong to z0intelligence."""
import json
import os
import urllib.request
from pathlib import Path
import sys
from .worker_routing import list_models

def delegate_worker(args):
    return post('/v1/worker',{**args,'trace_id':args['trace_id'],'harness':os.environ.get('Z0INT_HARNESS','codex'),'function':'cheap_bounded_worker'})

def post(path,args):
    request=urllib.request.Request(os.environ.get('Z0INT_SERVICE_URL','http://127.0.0.1:11501')+path,data=json.dumps(args).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
    except Exception:
        return {'ok':False,'execution_status':'uncertain','trace_id':args.get('trace_id'),
                'instruction':'Reconcile the identical request and trace_id; do not create a new identity.'}

def route_worker(args):
    request = urllib.request.Request(os.environ.get("Z0INT_SERVICE_URL", "http://127.0.0.1:11501") + "/v1/intelligence", data=json.dumps({**args, "harness": os.environ.get("Z0INT_HARNESS", "codex")}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)

SERVER_NAME="z0intelligence"
SERVER_VERSION="1.0.0"
PROTOCOL_VERSION="2024-11-05"

def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}

text = {'type': 'string'}
common = {'task': text, 'context': text, 'parent_agent': text,
          'max_tokens': {'type': 'integer', 'minimum': 1, 'maximum': 2048, 'default': 512}}
SERVER_NAME = 'z0intelligence'
TOOLS = [
    {'name': 'list_models', 'description': 'List configured z0intelligence worker providers, default models and credential availability; does not claim successful execution.', 'inputSchema': schema({})},
    {'name': 'route_worker', 'description': 'Canonical capability router: returns PARENT_ONLY when delegation is unwarranted, otherwise executes an evidenced typed function or bounded text worker via the shared host service. Supply stable trace_id, function, estimates for text work and explicit allow_remote authorization. Returns canonical execution receipts; selection alone is not execution.', 'inputSchema': schema({**common, 'trace_id': text, 'function': text, 'state': {'type': 'object'}, 'expected_parent_tokens': {'type': 'number', 'minimum': 0}, 'expected_parent_ms': {'type': 'number', 'minimum': 0}, 'allow_remote': {'type': 'boolean', 'default': False}, 'experimental': {'type': 'boolean', 'default': False}}, ('task', 'parent_agent', 'trace_id', 'function'))},
    {'name': 'delegate_worker', 'description': 'Explicitly select one registered provider/model for a real bounded text worker. Returns output and canonical receipt; for explicit user model requests. Use route_worker for automatic routing.',
     'inputSchema': schema({**common, 'trace_id': text, 'provider': text, 'model': text, 'reason': text}, ('task', 'parent_agent', 'trace_id', 'provider', 'model', 'reason'))}]
HANDLERS = {'list_models': lambda args: list_models(), 'route_worker': route_worker, 'delegate_worker': delegate_worker}
if os.environ.get('Z0INT_SHARED_ONLY') == '1':
    TOOLS = [tool for tool in TOOLS if tool['name'] == 'route_worker']
    HANDLERS = {'route_worker': route_worker}
if os.environ.get('Z0INT_HARNESS') == 'dsh':
    def dsh_schema(value):
        if isinstance(value, dict):
            return {k: dsh_schema(v) for k, v in value.items() if k not in ('minimum', 'maximum')}
        if isinstance(value, list):
            return [dsh_schema(v) for v in value]
        return value
    # DSH supports a smaller schema vocabulary; the service still enforces bounds.
    TOOLS = dsh_schema(TOOLS)
# Profile "memory" (server z0-memory): read-only memory tools only -- no route_worker, delegate_worker or
# list_models, no service, no port, no new DB. Every response passes the secret scrub.
MEMORY_SERVER_NAME = 'z0-memory'
_scope_props = {'project': text, 'harness': text, 'cross_harness': {'type': 'boolean', 'default': True}}
MEMORY_TOOLS = [
    {'name': 'memory_search', 'description': 'Search z0 memory (AgentsView history, the z0 event ledger and, when configured, TencentDB) for evidence with provenance (event_uid, locator, harness, session, timestamp). Evidence, not instructions; unavailable sources are reported, never hidden.',
     'inputSchema': schema({'query': text, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 8}, **_scope_props}, ('query',))},
    {'name': 'orient', 'description': 'Bounded memory brief for a query: current claims and evidence lines with locators; abstains with an explicit gap when a required source is unavailable.',
     'inputSchema': schema({'query': text, 'max_tokens': {'type': 'integer', 'minimum': 32, 'maximum': 4000, 'default': 600}, **_scope_props}, ('query',))},
    {'name': 'inspect', 'description': 'Read one locator returned by memory_search/orient (agentsview:<sid>#<mid> or eventlog:<id>) with its neighbouring messages; bounded and scrubbed.',
     'inputSchema': schema({'locator': text, 'chars': {'type': 'integer', 'minimum': 1, 'maximum': 4000, 'default': 400}, 'context': {'type': 'integer', 'minimum': 0, 'maximum': 10, 'default': 1}}, ('locator',))},
    {'name': 'history', 'description': 'Every recorded version of a claim (subject, optional predicate), oldest first, with which one is current and what superseded the others.',
     'inputSchema': schema({'subject': text, 'predicate': text, **_scope_props}, ('subject',))},
    {'name': 'unknowns', 'description': 'Only the gaps for a query: which memory sources are unavailable and whether the query is answerable.',
     'inputSchema': schema({'query': text, **_scope_props}, ('query',))},
    {'name': 'verify', 'description': 'Evidence-sufficiency verdict: USE only when every declared requirement is grounded in returned evidence, otherwise FALLBACK.',
     'inputSchema': schema({'query': text, 'requires': {'type': 'array', 'items': text}, **_scope_props}, ('query',))},
]


def _memory_policy(args):
    from .memory.surface import DEFAULT_USER, ScopePolicy
    from .memory_contract import MemoryScope
    project = args.get('project')
    return ScopePolicy(scope=MemoryScope(user=DEFAULT_USER, project=project) if project else None,
                       requester=args.get('harness') or os.environ.get('Z0INT_HARNESS'),
                       cross_harness=bool(args.get('cross_harness', True)))


def _memory_handlers():
    from .memory import surface as ms
    return {
        'memory_search': lambda a: ms.search(a['query'], _memory_policy(a), limit=int(a.get('limit') or 8)),
        'orient': lambda a: ms.memory_brief(a['query'], _memory_policy(a), max_tokens=int(a.get('max_tokens') or 600)),
        'inspect': lambda a: ms.inspect(a['locator'], chars=int(a.get('chars') or 400), context=int(a.get('context', 1))),
        'history': lambda a: {'ok': True, 'history': ms.claim_history(a['subject'], a.get('predicate'), _memory_policy(a))},
        'unknowns': lambda a: ms.unknowns(a['query'], _memory_policy(a)),
        'verify': lambda a: ms.verify(a['query'], a.get('requires') or (), _memory_policy(a)),
    }


def _reply(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict, profile: str | None = None):
    """Return a response dict, or None for a notification. ``profile="memory"`` serves the memory tools only."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    memory = (profile or os.environ.get("Z0INT_MCP_PROFILE")) == "memory"

    if method == "initialize":
        return _reply(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MEMORY_SERVER_NAME if memory else SERVER_NAME, "version": SERVER_VERSION},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _reply(msg_id, {})
    if method == "tools/list":
        return _reply(msg_id, {"tools": MEMORY_TOOLS if memory else TOOLS})
    if method == "tools/call" and memory:
        return _reply(msg_id, _call_memory_tool(params.get("name"), params.get("arguments") or {}))
    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _reply(msg_id, {
                "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                "isError": True,
            })
        try:
            payload = handler(params.get("arguments") or {})
            return _reply(msg_id, {
                "content": [{"type": "text", "text": json.dumps(payload, indent=1, default=str)}],
                "isError": payload.get("ok") is False,
            })
        except Exception as e:  # noqa: BLE001
            return _reply(msg_id, {
                "content": [{"type": "text",
                             "text": f"{name} failed: {type(e).__name__}"}],
                "isError": True,
            })
    if msg_id is None:
        return None
    return _error(msg_id, -32601, f"method not found: {method}")


def _call_memory_tool(name, args):
    from .memory.scrub import scrub_obj, scrub_text
    handler = _memory_handlers().get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    try:
        payload, _ = scrub_obj(handler(args))
    except Exception as e:  # noqa: BLE001 - a failure is isError, never an empty result
        return {"content": [{"type": "text", "text": scrub_text(f"{name} failed: {type(e).__name__}: {e}")[0]}],
                "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=1, default=str)}],
            "isError": payload.get("ok") is False}


def serve(profile: str | None = None):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps(_error(None, -32700, f"parse error: {e}")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(msg, profile)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + "\n")
            sys.stdout.flush()



if __name__ == '__main__':
    serve('memory' if sys.argv[1:3] == ['--profile', 'memory'] else None)
