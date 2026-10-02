import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from z0int.backends import registry
from z0int.backends.base import request_from_mapping
from z0int.backends.llama_http import LlamaHttpBackend, _letter_distribution


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        self._send({'status': 'ok'} if self.path == '/health' else {'data': [{'id': 'stub.gguf'}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/apply-template':
            return self._send({'prompt': req['messages'][0]['content'] + '\n'})
        prompt = req['prompt']
        if prompt.rstrip().endswith('Answer yes or no.'):
            top = [{'token': ' yes', 'logprob': math.log(0.8)}, {'token': 'No', 'logprob': math.log(0.2)}]
        else:
            # Always prefer the letter whose option says "tests": order-invariant truth.
            line = next((l for l in prompt.splitlines() if 'tests' in l), 'A')  # warm-up prompts have no options
            top = [{'token': ' ' + line[0], 'logprob': math.log(0.9)}, {'token': '7', 'logprob': math.log(0.1)}]
        # Real servers return n_probs (64) candidates, so unseen letters fall below a small floor.
        top += [{'token': f'x{i}', 'logprob': math.log(1e-6)} for i in range(60)]
        self._send({'completion_probabilities': [{'top_logprobs': top}]})


def serve():
    srv = HTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_letter_distribution_renormalizes_and_floors():
    top = [{'token': ' A', 'logprob': math.log(0.5)}, {'token': '1', 'logprob': math.log(0.4)}]
    top += [{'token': f'x{i}', 'logprob': math.log(1e-6)} for i in range(60)]
    d = _letter_distribution(top, 3)
    assert abs(sum(d.values()) - 1) < 1e-9 and d['A'] > 0.9 and d['B'] == d['C'] > 0


def test_end_to_end_choice_and_boolean():
    srv = serve()
    try:
        backend = LlamaHttpBackend(f'http://127.0.0.1:{srv.server_port}')
        assert backend.health().ready and backend.health().model == 'stub.gguf'
        req = request_from_mapping({'state': 's', 'questions': [
            {'id': 'b', 'type': 'boolean', 'instructions': 'q?'},
            {'id': 'c', 'type': 'choice', 'instructions': 'which?', 'criteria': [
                {'id': 'browse', 'description': 'open a browser'}, {'id': 'test', 'description': 'run tests'}]}]})
        res = backend.evaluate(req)
        b, c = res.answers
        assert b.value is True and abs(b.probabilities['true'] - 0.8) < 1e-5
        assert c.value == 'test' and c.probabilities['test'] > 0.99
    finally:
        srv.shutdown()


def test_registered_with_aliases():
    assert registry.resolve_backend_id('z0-slm') == 'llama_http'


def test_unreachable_server_is_not_ready():
    assert LlamaHttpBackend('http://127.0.0.1:9').health().ready is False


def test_url_precedence_env_config_default(tmp_path, monkeypatch):
    from z0int.backends.llama_http import DEFAULT_URL, configured_url

    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    monkeypatch.delenv('Z0INT_SLM_URL', raising=False)
    assert configured_url() == (DEFAULT_URL, 'default')

    cfg = tmp_path / 'config' / 'z0int.json'
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'models_plan': {}, 'backends': {'llama_http': {'url': 'http://172.21.0.1:11510/'}}}))
    backend = LlamaHttpBackend()
    assert backend.url == 'http://172.21.0.1:11510' and backend.url_source.startswith('config:')

    monkeypatch.setenv('Z0INT_SLM_URL', 'http://10.0.0.2:9999')
    assert configured_url() == ('http://10.0.0.2:9999', 'env:Z0INT_SLM_URL')
    assert LlamaHttpBackend('http://x:1').url_source == 'argument'


def test_url_config_malformed_falls_back(tmp_path, monkeypatch):
    from z0int.backends.llama_http import DEFAULT_URL, configured_url

    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    monkeypatch.delenv('Z0INT_SLM_URL', raising=False)
    cfg = tmp_path / 'config' / 'z0int.json'
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{not json')
    assert configured_url() == (DEFAULT_URL, 'default')
    cfg.write_text(json.dumps({'backends': {'llama_http': {'url': ''}}}))
    assert configured_url() == (DEFAULT_URL, 'default')


def test_bench_roster_llama_http_arms(tmp_path, monkeypatch):
    from z0int.backends.bench.contract import is_roster_candidate
    from z0int.backends.bench.roster import create_backend_for_candidate, probe_candidate

    srv = serve()
    try:
        url = f'http://127.0.0.1:{srv.server_port}'
        monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
        monkeypatch.delenv('Z0INT_SLM_URL', raising=False)
        cfg = tmp_path / 'config' / 'z0int.json'
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({'backends': {'llama_http': {'url': url, 'arms': {
            'stub': {'model': 'stub.gguf', 'license': 'apache-2.0', 'sha256': 'ab' * 32, 'device': 'vulkan0'},
            'absent': {'model': 'nope.gguf'}}}}}))
        assert is_roster_candidate('llama_http:stub') and not is_roster_candidate('llama_http:')
        assert probe_candidate('llama_http').status == 'available'
        st = probe_candidate('llama_http:stub')
        assert st.status == 'available' and st.commercial_use is True
        absent = probe_candidate('llama_http:absent')
        assert absent.status == 'unavailable' and 'not offered' in absent.reason
        assert probe_candidate('llama_http:unknown').status == 'unavailable'
        backend = create_backend_for_candidate('llama_http:stub')
        assert backend.model_name == 'stub.gguf' and backend.revision == 'ab' * 32 and backend.device == 'vulkan0'
        assert backend._body({'prompt': 'x'})['model'] == 'stub.gguf'
        assert backend.health(load=True).loaded
    finally:
        srv.shutdown()
