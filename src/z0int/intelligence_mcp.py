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


def server_instructions():
    """When-to-offload guidance; Claude Code surfaces initialize.instructions to the model."""
    if os.environ.get("Z0INT_HARNESS") != "claude-code":
        return None
    from .claude_code_engagement import MCP_INSTRUCTIONS
    return MCP_INSTRUCTIONS

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
def _reply(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict):
    """Return a response dict, or None for a notification."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _reply(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            **({"instructions": server_instructions()} if server_instructions() else {}),
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _reply(msg_id, {})
    if method == "tools/list":
        return _reply(msg_id, {"tools": TOOLS})
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


def serve():
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
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + "\n")
            sys.stdout.flush()



if __name__ == '__main__':serve()
