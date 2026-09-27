#!/usr/bin/env python3
"""Codex MCP transport adapter; routing and execution belong to z0intelligence."""
import json
import os
import urllib.request
from pathlib import Path
import sys
ROOT = Path('/mnt/zer0models/workspace/zer0/oss/.work/z0intelligence')
sys.path[:0] = [str(ROOT / 'src'), '/mnt/zer0models/workspace/zer0/oss/tools']
from z0int.worker_routing import list_models, delegate_worker

def route_worker(args):
    request = urllib.request.Request(os.environ.get("Z0INT_SERVICE_URL", "http://127.0.0.1:11501") + "/v1/intelligence", data=json.dumps({**args, "harness": os.environ.get("Z0INT_HARNESS", "codex")}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)

import recall_mcp as protocol

def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}

text = {'type': 'string'}
common = {'task': text, 'context': text, 'parent_agent': text,
          'max_tokens': {'type': 'integer', 'minimum': 1, 'maximum': 2048, 'default': 512}}
protocol.SERVER_NAME = 'z0intelligence'
protocol.TOOLS = [
    {'name': 'list_models', 'description': 'List configured z0intelligence worker providers, default models and credential availability; does not claim successful execution.', 'inputSchema': schema({})},
    {'name': 'route_worker', 'description': 'Canonical capability router: returns PARENT_ONLY when delegation is unwarranted, otherwise executes an evidenced typed function or bounded text worker via the shared host service. Supply stable trace_id, function, estimates for text work and explicit allow_remote authorization. Returns canonical execution receipts; selection alone is not execution.', 'inputSchema': schema({**common, 'trace_id': text, 'function': text, 'state': {'type': 'object'}, 'expected_parent_tokens': {'type': 'number', 'minimum': 0}, 'expected_parent_ms': {'type': 'number', 'minimum': 0}, 'allow_remote': {'type': 'boolean', 'default': False}, 'experimental': {'type': 'boolean', 'default': False}}, ('task', 'parent_agent', 'trace_id', 'function'))},
    {'name': 'delegate_worker', 'description': 'Explicitly select one registered provider/model for a real bounded text worker. Returns output and canonical receipt; for explicit user model requests. Use route_worker for automatic routing.',
     'inputSchema': schema({**common, 'provider': text, 'model': text, 'reason': text}, ('task', 'parent_agent', 'provider', 'model', 'reason'))}]
protocol.HANDLERS = {'list_models': lambda args: list_models(), 'route_worker': route_worker, 'delegate_worker': delegate_worker}
if os.environ.get('Z0INT_SHARED_ONLY') == '1':
    protocol.TOOLS = [tool for tool in protocol.TOOLS if tool['name'] == 'route_worker']
    protocol.HANDLERS = {'route_worker': route_worker}
if os.environ.get('Z0INT_HARNESS') == 'dsh':
    def dsh_schema(value):
        if isinstance(value, dict):
            return {k: dsh_schema(v) for k, v in value.items() if k not in ('minimum', 'maximum')}
        if isinstance(value, list):
            return [dsh_schema(v) for v in value]
        return value
    # DSH supports a smaller schema vocabulary; the service still enforces bounds.
    protocol.TOOLS = dsh_schema(protocol.TOOLS)
original_handle = protocol.handle

def handle(message):
    reply = original_handle(message)
    if reply and message.get('method') == 'tools/call':
        result = reply.get('result', {})
        if not result.get('isError'):
            payload = json.loads(result['content'][0]['text'])
            result['isError'] = payload.get('ok') is False
    return reply

protocol.handle = handle
def serve():
    protocol.serve()

if __name__ == '__main__':
    serve()
