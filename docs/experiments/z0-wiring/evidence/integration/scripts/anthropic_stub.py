"""Integration copy: every logged row also carries the full request body and the arm label read from
$STUB_ARM_FILE (for the shadows on/off request comparison). Synthetic prompts only.

Minimal Anthropic-Messages-compatible stub for the C1 Claude Code e2e (loopback only, private netns).

Scripts one delegation: the main turn (prompt carries MAIN_MARKER) gets one Agent/Task tool_use whose prompt
carries SUB_MARKER; the subagent's call gets a plain answer; the main turn's follow-up (tool_result) gets a
final answer. Every other request (titles, quota probes) gets "ok". Requests are logged as one-line summaries.
usage: anthropic_stub.py <port> <log.jsonl>
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
MAIN_MARKER, SUB_MARKER = 'MAIN-MARKER-c1', 'SUB-MARKER-c1'
# C1_STUB_BACKGROUND=1: leave run_in_background unset (CC 2.1.288 then runs the subagent in the background and
# hands its result back as a <task-notification> prompt: the Stop/transcript race the fix round covers).
BACKGROUND = __import__('os').environ.get('C1_STUB_BACKGROUND') == '1'
lock = threading.Lock()


def _arm():
    try:
        return open(__import__('os').environ['STUB_ARM_FILE']).read().strip()
    except (KeyError, OSError):
        return '?'

counter = [0]


def text_of(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content or [] if isinstance(b, dict) and b.get('type') == 'text')


def decide(body):
    msgs = body.get('messages') or []
    tools = [t.get('name') for t in body.get('tools') or [] if isinstance(t, dict)]
    first = text_of(msgs[0].get('content')) if msgs else ''
    last_has_result = any(isinstance(m.get('content'), list) and any(
        isinstance(b, dict) and b.get('type') == 'tool_result' for b in m['content']) for m in msgs[1:])
    agent_tool = 'Agent' if 'Agent' in tools else 'Task' if 'Task' in tools else None
    if last_has_result and MAIN_MARKER in first:
        return 'final', [{'type': 'text', 'text': 'All done: the subagent finished the subtask.'}], 'end_turn'
    if SUB_MARKER in first:
        return 'subagent', [{'type': 'text', 'text': 'Subagent report: nothing to change.'}], 'end_turn'
    if MAIN_MARKER in first and agent_tool:
        return 'delegate', [{'type': 'tool_use', 'id': 'toolu_c1_0001', 'name': agent_tool,
                             'input': {'description': 'check the notes', 'subagent_type': 'general-purpose',
                                       **({} if BACKGROUND else {'run_in_background': False}),
                                       'prompt': f'{SUB_MARKER}: read nothing, reply with one sentence.'}}], 'tool_use'
    return 'aux', [{'type': 'text', 'text': 'ok'}], 'end_turn'


def sse(handler, events):
    handler.send_response(200)
    handler.send_header('content-type', 'text/event-stream')
    handler.send_header('cache-control', 'no-cache')
    handler.send_header('connection', 'close')  # no length: the end of the body is the end of the connection
    handler.end_headers()
    for name, data in events:
        handler.wfile.write(f'event: {name}\ndata: {json.dumps(data)}\n\n'.encode())
    handler.wfile.flush()
    handler.close_connection = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._log({'method': 'GET', 'path': self.path})
        self._json(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'stub'}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        self._body = body
        path = self.path.split('?')[0]
        if path.endswith('/count_tokens'):
            self._log({'method': 'POST', 'path': path})
            return self._json(200, {'input_tokens': 42})
        if not path.endswith('/v1/messages'):
            self._log({'method': 'POST', 'path': path})
            return self._json(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'stub'}})
        kind, content, stop = decide(body)
        with lock:
            counter[0] += 1
            mid = f'msg_c1_{counter[0]:04d}'
        agent_schema = [sorted((t.get('input_schema') or {}).get('properties') or {}) for t in body.get('tools') or []
                        if isinstance(t, dict) and t.get('name') in ('Agent', 'Task')]
        self._log({'method': 'POST', 'path': path, 'model': body.get('model'), 'stream': bool(body.get('stream')),
                   'tools': len(body.get('tools') or []), 'messages': len(body.get('messages') or []), 'reply': kind,
                   'agent_tool_fields': agent_schema})
        model = body.get('model') or 'claude-stub'
        usage = {'input_tokens': 12, 'output_tokens': 7, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}
        if not body.get('stream'):
            return self._json(200, {'id': mid, 'type': 'message', 'role': 'assistant', 'model': model, 'content': content,
                                    'stop_reason': stop, 'stop_sequence': None, 'usage': usage})
        events = [('message_start', {'type': 'message_start', 'message': {
            'id': mid, 'type': 'message', 'role': 'assistant', 'model': model, 'content': [], 'stop_reason': None,
            'stop_sequence': None, 'usage': dict(usage, output_tokens=1)}})]
        for i, block in enumerate(content):
            if block['type'] == 'text':
                events += [('content_block_start', {'type': 'content_block_start', 'index': i,
                                                    'content_block': {'type': 'text', 'text': ''}}),
                           ('content_block_delta', {'type': 'content_block_delta', 'index': i,
                                                    'delta': {'type': 'text_delta', 'text': block['text']}})]
            else:
                events += [('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': {
                    'type': 'tool_use', 'id': block['id'], 'name': block['name'], 'input': {}}}),
                           ('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': {
                               'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])}})]
            events.append(('content_block_stop', {'type': 'content_block_stop', 'index': i}))
        events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None},
                                      'usage': {'output_tokens': 7}}),
                   ('message_stop', {'type': 'message_stop'})]
        sse(self, events)

    def _log(self, row):
        row = dict(row, arm=_arm(), body=getattr(self, '_body', None))
        with lock, open(LOG, 'a') as fh:
            fh.write(json.dumps(row) + '\n')


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
