"""OpenAI chat-completions stub for the C4a Hermes e2e (loopback only, inside a private net namespace).

Every POST /v1/chat/completions gets one assistant message (streamed when asked) and usage; GET /v1/models lists
one model. Each request body is appended verbatim to the log with the arm label from $C4A_ARM_FILE, so the off and
shadow arms can be compared byte for byte after normalisation. Synthetic prompts only.
usage: chat_stub.py <port> <log.jsonl> <arm-file>
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG, ARM = int(sys.argv[1]), sys.argv[2], sys.argv[3]
lock = threading.Lock()
TEXT = 'Noted. The release checklist is in data/release.txt.'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, json.dumps({'object': 'list', 'data': [{'id': 'c4a-stub', 'object': 'model',
                                                                 'owned_by': 'stub'}]}).encode())

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            body = {'unparsed': raw.decode('utf-8', 'replace')}
        try:
            arm = open(ARM).read().strip()
        except OSError:
            arm = '?'
        with lock, open(LOG, 'a') as fh:
            fh.write(json.dumps({'arm': arm, 'path': self.path, 't': time.time(), 'body': body}) + '\n')
        usage = {'prompt_tokens': 42, 'completion_tokens': 9, 'total_tokens': 51}
        if not body.get('stream'):
            self._send(200, json.dumps({'id': 'chatcmpl-c4a', 'object': 'chat.completion', 'created': 0,
                                        'model': 'c4a-stub', 'usage': usage,
                                        'choices': [{'index': 0, 'finish_reason': 'stop',
                                                     'message': {'role': 'assistant', 'content': TEXT}}]}).encode())
            return
        chunks = [{'role': 'assistant', 'content': ''}, {'content': TEXT}]
        events = [{'id': 'chatcmpl-c4a', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'c4a-stub',
                   'choices': [{'index': 0, 'delta': d, 'finish_reason': None}]} for d in chunks]
        events.append({'id': 'chatcmpl-c4a', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'c4a-stub',
                       'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': usage})
        payload = ''.join(f'data: {json.dumps(e)}\n\n' for e in events) + 'data: [DONE]\n\n'
        self._send(200, payload.encode(), 'text/event-stream')


ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
