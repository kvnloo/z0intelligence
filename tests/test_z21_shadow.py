import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from z0int.z21_shadow import Labeler, summarize


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        replies = []
        requests = []

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            status, reply, delay = self.replies.pop(0)
            time.sleep(delay)
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(json.dumps({'choices': [{'message': {'content': reply}}]}).encode())
            except BrokenPipeError:
                pass

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_port}', Handler
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def record(text='hello'):
    return {'id': 'a', 'text': text, 'n_tool_calls_so_far': 2, 'context_chars': 10}


@pytest.mark.parametrize('task,word', [(t, w) for t, words in [('effort', ['low', 'medium', 'high']), ('route', ['haiku', 'sonnet', 'opus'])] for w in words])
def test_labels(server, task, word):
    url, handler = server
    handler.replies.append((200, f'({word.upper()}!) explanation', 0))
    labeler = Labeler(url, 'test', 1)
    row = labeler.label(task, record())
    assert (row['label'], row['reason']) == (word, 'ok')
    path, payload = handler.requests[0]
    assert path == '/v1/chat/completions'
    assert payload['temperature'] == 0 and payload['max_tokens'] == 8
    assert payload['model'] == 'test'


@pytest.mark.parametrize('task,fallback', [('effort', 'high'), ('route', 'opus')])
def test_unparsed_and_cap(server, task, fallback):
    url, handler = server
    handler.replies.append((200, 'garbage high', 0))
    labeler = Labeler(url, 'test', 1)
    assert (lambda r: (r['label'], r['reason']))(labeler.label(task, record())) == (fallback, 'unparsed')
    assert (lambda r: (r['label'], r['reason']))(labeler.label(task, record())) == (None, 'cap')
    assert labeler.calls == 1 and len(handler.requests) == 1


@pytest.mark.parametrize('status,delay', [(500, 0), (200, 0.2)])
def test_errors(server, status, delay):
    url, handler = server
    handler.replies.append((status, 'low', delay))
    labeler = Labeler(url, 'test', 1, timeout=0.05)
    row = labeler.label('effort', record())
    assert (row['label'], row['reason']) == ('high', 'error')
    assert labeler.failures == 1 and labeler.calls == 1


@pytest.mark.parametrize('task,limit', [('effort', 2000), ('route', 4000)])
def test_truncation(server, task, limit):
    url, handler = server
    handler.replies.append((200, 'high' if task == 'effort' else 'opus', 0))
    Labeler(url, 'test', 1).label(task, record('x' * (limit + 50)))
    content = handler.requests[0][1]['messages'][1]['content']
    assert 'x' * limit in content and 'x' * (limit + 1) not in content
    if task == 'effort':
        assert '2' in content and '10' in content


def test_cli_resume(server, tmp_path):
    url, handler = server
    handler.replies.append((200, 'low', 0))
    source, output = tmp_path / 'in.jsonl', tmp_path / 'out.jsonl'
    source.write_text('\n'.join(json.dumps(record()) for _ in range(2)) + '\n' + json.dumps(dict(record(), id='b')) + '\n')
    output.write_text(json.dumps({'id': 'a', 'task': 'effort', 'label': 'high'}) + '\n')
    command = [sys.executable, '-m', 'z0int.z21_shadow', 'effort', '--in', str(source), '--out', str(output), '--base-url', url, '--model', 'test', '--max-calls', '1']
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row['id'] for row in rows] == ['a', 'b']
    assert rows[1]['label'] == 'low' and rows[1]['model'] == 'test'
    summary = json.loads(result.stdout)
    assert summary['calls'] == 1 and summary['failures'] == 0 and summary['cap_hits'] == 0
    assert len(handler.requests) == 1


def test_summarize():
    rows = [{'id': 'a', 'label': 'low'}, {'id': 'b', 'label': 'high'}, {'id': 'c', 'label': 'low'}]
    assert summarize(rows, {'a': 3, 'b': 1}) == {
        'low': {'count': 2, 'weight': 3, 'share': 75},
        'high': {'count': 1, 'weight': 1, 'share': 25},
    }
    assert summarize([{'id': 'x', 'label': None}], {})[None]['share'] == 0
