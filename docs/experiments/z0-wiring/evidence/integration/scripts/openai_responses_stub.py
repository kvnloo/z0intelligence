"""Integration copy: every logged row also carries the full request body and the arm label read from
$STUB_ARM_FILE (for the shadows on/off request comparison). Synthetic prompts only.

Minimal OpenAI-Responses-compatible stub for the C1 Codex e2e (loopback only, private netns).

Every /v1/responses call gets one assistant message ("Which file should I tidy?") and a completed event with
usage; GET /v1/models lists one model. Requests are logged as one-line summaries.
usage: openai_stub.py <port> <log.jsonl>
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
lock = threading.Lock()


def _arm():
    try:
        return open(__import__('os').environ['STUB_ARM_FILE']).read().strip()
    except (KeyError, OSError):
        return '?'

counter = [0]
TEXT = 'Which file should I tidy?'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _log(self, row):
        row = dict(row, arm=_arm(), body=getattr(self, '_body', None))
        with lock, open(LOG, 'a') as fh:
            fh.write(json.dumps(row) + '\n')

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._log({'method': 'GET', 'path': self.path})
        if self.path.split('?')[0].endswith('/models'):
            return self._json(200, {'object': 'list', 'data': [{'id': 'gpt-stub', 'object': 'model'}],
                                    'models': [{'slug': 'gpt-stub'}]})
        self._json(404, {'error': {'message': 'stub', 'type': 'not_found'}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        self._body = body
        path = self.path.split('?')[0]
        self._log({'method': 'POST', 'path': path, 'model': body.get('model'), 'stream': body.get('stream'),
                   'tools': len(body.get('tools') or []), 'input_items': len(body.get('input') or [])})
        if not path.endswith('/responses'):
            return self._json(404, {'error': {'message': 'stub', 'type': 'not_found'}})
        with lock:
            counter[0] += 1
            n = counter[0]
        item = {'type': 'message', 'id': f'msg_{n}', 'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': TEXT, 'annotations': []}]}
        response = {'id': f'resp_{n}', 'object': 'response', 'model': body.get('model'), 'status': 'completed',
                    'output': [item], 'usage': {'input_tokens': 11, 'output_tokens': 6, 'total_tokens': 17,
                                                'input_tokens_details': {'cached_tokens': 0},
                                                'output_tokens_details': {'reasoning_tokens': 0}}}
        events = [('response.created', {'type': 'response.created', 'response': dict(response, status='in_progress',
                                                                                     output=[])}),
                  ('response.output_item.added', {'type': 'response.output_item.added', 'output_index': 0,
                                                  'item': dict(item, status='in_progress', content=[])}),
                  ('response.output_text.delta', {'type': 'response.output_text.delta', 'item_id': item['id'],
                                                  'output_index': 0, 'content_index': 0, 'delta': TEXT}),
                  ('response.output_item.done', {'type': 'response.output_item.done', 'output_index': 0, 'item': item}),
                  ('response.completed', {'type': 'response.completed', 'response': response})]
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.send_header('connection', 'close')
        self.end_headers()
        for name, data in events:
            self.wfile.write(f'event: {name}\ndata: {json.dumps(data)}\n\n'.encode())
        self.wfile.flush()
        self.close_connection = True


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
