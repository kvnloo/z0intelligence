"""Real ephemeral HTTP listeners; no live service, providers or model loads."""
import base64
from contextlib import contextmanager
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from z0int import intelligence_service, remote_executor
from z0int.cognition.server import _handler_factory
from z0int.http_security import AccessTokens, route_scope


@contextmanager
def listener(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(server, method='GET', path='/healthz', headers=None, duplicate=None):
    conn = http.client.HTTPConnection(*server.socket.getsockname(), timeout=3)
    conn.putrequest(method, path, skip_host=True)
    headers = dict(headers or {})
    headers.setdefault('Host', f'127.0.0.1:{server.server_port}')
    if method == 'POST':
        headers.setdefault('Content-Length', '2')
    for key, value in headers.items():
        conn.putheader(key, value)
    if duplicate:
        conn.putheader(*duplicate)
    conn.endheaders(b'{}' if method == 'POST' else None)
    response = conn.getresponse()
    status = response.status
    response.read()
    conn.close()
    return status


@pytest.fixture(params=['intelligence', 'executor', 'cognition'])
def service(request, monkeypatch):
    monkeypatch.delenv('Z0INT_Z0BRIDGE_AUTH', raising=False)
    handlers = {
        'intelligence': intelligence_service.Handler,
        'executor': remote_executor.Handler,
        'cognition': _handler_factory(cast(Any, SimpleNamespace(runtime='test', resident=None))),
    }
    with listener(handlers[request.param]) as server:
        yield server, '/health' if request.param == 'cognition' else '/healthz'


@pytest.mark.parametrize('host', ['rebind.example', '127.0.0.1.evil', 'localhost.evil',
                                  '127.0.0.1:1', '172.19.0.1:11501', 'user@localhost'])
def test_rebinding_rejected(service, host):
    server, path = service
    assert request(server, path=path, headers={'Host': host}) == 421


@pytest.mark.parametrize('origin', ['https://evil.example', 'null', 'http://localhost', ''])
def test_all_browser_origins_rejected(service, origin):
    server, path = service
    assert request(server, path=path, headers={'Origin': origin}) == 403


def test_duplicate_host_rejected(service):
    server, path = service
    assert request(server, path=path, duplicate=('Host', 'evil.example')) == 421


@pytest.mark.parametrize('content_type', ['text/plain', 'application/x-www-form-urlencoded', ''])
def test_simple_post_rejected_before_dispatch(service, content_type):
    server, _ = service
    assert request(server, 'POST', '/v1/worker', {'Content-Type': content_type}) == 415


def test_local_calls_without_bearer_still_work(service):
    server, path = service
    assert request(server, path=path) == 200
    assert request(server, path=path, headers={'Host': f'localhost:{server.server_port}'}) == 200
    assert request(server, 'POST', '/unknown', {'Content-Type': 'application/json; charset=utf-8'}) == 404


def test_existing_json_callers_reach_routes(monkeypatch):
    monkeypatch.setattr(intelligence_service, 'plan_intelligence', lambda args: {'ok': True})
    monkeypatch.setattr(remote_executor, 'execute', lambda args: {'ok': True})
    sup = SimpleNamespace(release=lambda reason: False, resident=None)
    for handler, path in [(intelligence_service.Handler, '/v1/plan'),
                          (remote_executor.Handler, '/v1/execute'),
                          (_handler_factory(cast(Any, sup)), '/z0int/release')]:
        with listener(handler) as server:
            assert request(server, 'POST', path, {'Content-Type': 'application/json'}) == 200


def enc(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


@pytest.fixture
def identity(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_numbers()
    jwks = tmp_path / 'jwks.json'
    jwks.write_text(json.dumps({'keys': [{'kty': 'EC', 'crv': 'P-256', 'kid': 'test-key',
                                        'x': enc(public.x.to_bytes(32, 'big')),
                                        'y': enc(public.y.to_bytes(32, 'big'))}]}))
    monkeypatch.setenv('Z0INT_Z0BRIDGE_ISSUER', 'z0bridge:test')
    monkeypatch.setenv('Z0INT_Z0BRIDGE_JWKS', str(jwks))
    monkeypatch.setenv('Z0INT_Z0BRIDGE_AUTH', '1')

    def mint(scope='z0int:decide', header=None, **overrides):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import utils
        now = int(time.time())
        claims = dict(iss='z0bridge:test', aud='z0int', sub='cred_test', dev='dev_test',
                      app='hermes', jti='test', scope=scope, iat=now, nbf=now, exp=now + 120)
        claims.update(overrides)
        head = dict(alg='ES256', typ='at+jwt', kid='test-key')
        head.update(header or {})
        text = enc(json.dumps(head).encode()) + '.' + enc(json.dumps(claims).encode())
        r, s = utils.decode_dss_signature(key.sign(text.encode(), ec.ECDSA(hashes.SHA256())))
        return text + '.' + enc(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
    return mint


def test_token_enforcement_and_scope_before_dispatch(identity, monkeypatch):
    from z0int import worker_routing
    monkeypatch.setattr(worker_routing, 'dispatch_worker', lambda args: {'ok': True})
    with listener(intelligence_service.Handler) as server:
        assert request(server) == 200  # probes stay open
        base = {'Content-Type': 'application/json'}
        assert request(server, 'POST', '/v1/worker', base) == 401
        assert request(server, 'POST', '/v1/worker', {**base, 'Authorization': 'Bearer ' + identity()}) == 401
        assert request(server, 'POST', '/v1/worker', {**base, 'Authorization': 'Bearer ' + identity('z0int:dispatch')}) == 200


@pytest.mark.parametrize('overrides', [dict(aud='noop'), dict(iss='wrong'), dict(exp=1),
    dict(nbf=9999999999), dict(iat=9999999999), dict(exp=int(time.time()) + 900),
    dict(scope='z0int:context.read'), dict(exp='9999999999'), dict(sub=None)])
def test_invalid_claims_refused(identity, overrides):
    assert not AccessTokens().verify(identity(**overrides), 'z0int:decide')


@pytest.mark.parametrize('header', [dict(alg='none'), dict(alg='HS256'), dict(kid='unknown'),
                                  dict(typ='JWT'), dict(crit=['unknown'])])
def test_invalid_headers_refused(identity, header):
    assert not AccessTokens().verify(identity(header=header), 'z0int:decide')


def test_tampered_signature_refused(identity):
    token = identity()
    parts = token.split('.')
    parts[2] = enc(bytes(64))
    assert not AccessTokens().verify('.'.join(parts), 'z0int:decide')


def test_missing_identity_fails_closed(monkeypatch):
    monkeypatch.setenv('Z0INT_Z0BRIDGE_AUTH', '1')
    monkeypatch.delenv('Z0INT_Z0BRIDGE_JWKS', raising=False)
    monkeypatch.delenv('Z0INT_Z0BRIDGE_ISSUER', raising=False)
    with listener(intelligence_service.Handler) as server:
        assert request(server, path='/v1/providers') == 503


@pytest.mark.parametrize('path,scope', [('/v1/file-search', 'context.read'),
    ('/v1/providers', 'context.read'), ('/v1/context/pack', 'context.read'),
    ('/v1/intelligence', 'decide'), ('/v1/plan', 'decide'), ('/v1/automatic/consumed', 'decide'),
    ('/v1/experimental/noul', 'decide'), ('/v1/integrations/agent-orchestrator/outcome', 'decide'),
    ('/v1/authority/claim', 'dispatch'), ('/v1/governed-worker', 'dispatch'),
    ('/v1/agentweb/observation', 'dispatch'), ('/v1/execute', 'dispatch'),
    ('/v1/observe/outcome', 'observe')])
def test_scope_catalog(path, scope):
    assert route_scope(path) == 'z0int:' + scope


def test_bridge_and_pod_allowlists_are_listener_specific():
    for handler, address, alias in [
        (intelligence_service.Handler, '172.19.0.1', 'z0intelligence.hermes-lab.svc.cluster.local'),
        (remote_executor.Handler, '10.42.0.5', 'z0intelligence-executor.hermes-lab.svc.cluster.local'),
    ]:
        with listener(handler) as server:
            assert request(server, headers={'Host': f'{alias}:{server.server_port}'}) == 421
            # Exercise actual handler parsing using a simulated bind identity;
            # the socket remains ephemeral loopback and never claims a live port.
            server.server_address = (address, server.server_port)
            assert request(server, headers={'Host': f'{address}:{server.server_port}'}) == 200
            assert request(server, headers={'Host': f'{alias}:{server.server_port}'}) == 200
            assert request(server, headers={'Host': f'evil.example:{server.server_port}'}) == 421


def test_child_process_always_gets_private_api_key(monkeypatch, tmp_path):
    from z0int.cognition import serving
    captured = []
    from pathlib import Path

    def spawn(cmd, **kwargs):
        key_path = Path(cmd[cmd.index('--api-key-file') + 1])
        assert key_path.stat().st_mode & 0o777 == 0o600
        assert len(key_path.read_text().strip()) >= 32
        captured.append(cmd)

    monkeypatch.setattr(serving.subprocess, 'Popen', spawn)
    monkeypatch.setattr(serving, 'wait_health', lambda url: True)
    server = serving.LlamaServer(binary='unused', gguf=tmp_path / 'model.gguf',
                                 context=32, port=18159)
    server.log = tmp_path / 'child.log'
    server.start()
    cmd = captured[0]
    assert cmd[cmd.index('--host') + 1] == '127.0.0.1'
    assert '--api-key' not in cmd
    assert server.api_key not in cmd
    assert not Path(cmd[cmd.index('--api-key-file') + 1]).exists()


def test_duplicate_content_type_rejected(service):
    server, _ = service
    assert request(server, 'POST', '/unknown', {'Content-Type': 'application/json'},
                   duplicate=('Content-Type', 'text/plain')) == 415
