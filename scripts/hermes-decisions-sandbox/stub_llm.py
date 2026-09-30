"""Deterministic OpenAI-compatible stub for the Hermes sandbox proof (no network, no real model)."""
import json, sys, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[2], 'a') if len(sys.argv) > 2 else None

def text_of(m):
    c = m.get('content')
    if isinstance(c, list):
        return ' '.join(b.get('text', '') for b in c if isinstance(b, dict))
    return c or ''

def decide(messages):
    last_user = next((text_of(m) for m in reversed(messages) if m.get('role') == 'user'), '')
    after_user = []
    for m in reversed(messages):
        if m.get('role') == 'user':
            break
        after_user.append(m)
    tool_results = [m for m in after_user if m.get('role') == 'tool']
    low = last_user.lower()
    if 'use a tool' in low and not tool_results:
        return None, [{'id': 'call_' + uuid.uuid4().hex[:8], 'type': 'function',
                       'function': {'name': 'todo', 'arguments': json.dumps({'todos': [{'id': '1', 'content': 'stub step', 'status': 'pending'}]})}}]
    if 'delegate' in low and not tool_results:
        return None, [{'id': 'call_' + uuid.uuid4().hex[:8], 'type': 'function',
                       'function': {'name': 'delegate_task', 'arguments': json.dumps({'goal': 'say hello from the child', 'context': 'stub'})}}]
    if 'ask me' in low:
        return 'Which environment should I deploy to, staging or production?', None
    return 'Done. (' + last_user[:40].rstrip('?') + ')', None

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        self._json({'object': 'list', 'data': [{'id': 'stub-model', 'object': 'model', 'context_length': 131072}]})
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        msgs = req.get('messages') or []
        text, calls = decide(msgs)
        if LOG:
            LOG.write(json.dumps({'n_messages': len(msgs), 'last_user': next((text_of(m)[:60] for m in reversed(msgs) if m.get('role') == 'user'), ''), 'reply_tool': bool(calls)}) + '\n'); LOG.flush()
        msg = {'role': 'assistant', 'content': text}
        if calls: msg['tool_calls'] = calls
        finish = 'tool_calls' if calls else 'stop'
        usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}
        cid, now = 'chatcmpl-' + uuid.uuid4().hex[:8], int(time.time())
        if not req.get('stream'):
            return self._json({'id': cid, 'object': 'chat.completion', 'created': now, 'model': 'stub-model',
                               'choices': [{'index': 0, 'message': msg, 'finish_reason': finish}], 'usage': usage})
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        def ev(delta, fin=None, u=None):
            d = {'id': cid, 'object': 'chat.completion.chunk', 'created': now, 'model': 'stub-model',
                 'choices': [{'index': 0, 'delta': delta, 'finish_reason': fin}]}
            if u: d['usage'] = u
            self.wfile.write(b'data: ' + json.dumps(d).encode() + b'\n\n')
        ev({'role': 'assistant'})
        if text: ev({'content': text})
        if calls: ev({'tool_calls': [dict(c, index=i) for i, c in enumerate(calls)]})
        ev({}, finish)
        self.wfile.write(b'data: ' + json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': now, 'model': 'stub-model', 'choices': [], 'usage': usage}).encode() + b'\n\n')
        self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()

ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
