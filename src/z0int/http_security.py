"""Browser boundary and opt-in offline z0bridge access-token verification.

No secrets or tokens are logged. A pinned local JWKS snapshot avoids network IO
on the request path; replace the snapshot and restart on signing-key rotation.
"""
from __future__ import annotations

import base64
import ipaddress
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast
import json
import os
from pathlib import Path
import threading
import time


_POLICY_LOCK = threading.Lock()


def route_scope(path: str) -> str:
    if path in ('/v1/file-search', '/v1/context/pack', '/v1/providers',
                '/v1/bridge/capabilities', '/v1/models', '/metrics',
                '/z0int/status', '/z0int/history'):
        return 'z0int:context.read'
    if path.startswith('/v1/observe/'):
        return 'z0int:observe'
    if (path in ('/v1/intelligence', '/v1/plan', '/v1/chat/completions')
            or path.startswith(('/v1/experimental/', '/v1/automatic',
                                '/v1/integrations/agent-orchestrator/'))):
        return 'z0int:decide'
    # Unknown routes fail closed; in particular authority/worker/agentweb/execute.
    return 'z0int:dispatch'


def _decode(value: str) -> bytes:
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


class AccessTokens:
    def __init__(self):
        self.issuer = os.environ['Z0INT_Z0BRIDGE_ISSUER']
        if not self.issuer:
            raise ValueError('issuer required')
        from cryptography.hazmat.primitives.asymmetric import ec
        jwks = json.loads(Path(os.environ['Z0INT_Z0BRIDGE_JWKS']).read_text())
        self.keys = {}
        for key in jwks['keys']:
            if (key.get('kty') != 'EC' or key.get('crv') != 'P-256'
                    or key.get('alg', 'ES256') != 'ES256'
                    or key.get('use', 'sig') != 'sig'):
                continue
            kid = key['kid']
            if kid in self.keys:
                raise ValueError('duplicate kid')
            self.keys[kid] = ec.EllipticCurvePublicNumbers(
                int.from_bytes(_decode(key['x']), 'big'),
                int.from_bytes(_decode(key['y']), 'big'), ec.SECP256R1(),
            ).public_key()
        if not self.keys:
            raise ValueError('no ES256 public keys')

    def verify(self, token: str, scope: str) -> bool:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        from cryptography.exceptions import InvalidSignature
        try:
            if len(token) > 16384:
                return False
            head, body, signature = token.split('.')
            header = json.loads(_decode(head))
            claims = json.loads(_decode(body))
            if (header.get('alg') != 'ES256' or header.get('typ') != 'at+jwt'
                    or 'crit' in header or 'b64' in header):
                return False
            key = self.keys[header['kid']]
            raw = _decode(signature)
            if len(raw) != 64:
                return False
            der = utils.encode_dss_signature(int.from_bytes(raw[:32], 'big'),
                                             int.from_bytes(raw[32:], 'big'))
            key.verify(der, (head + '.' + body).encode('ascii'), ec.ECDSA(hashes.SHA256()))
            now = time.time()
            if any(type(claims.get(k)) is not int for k in ('iat', 'nbf', 'exp')):
                return False
            if not (claims['iat'] <= now and claims['nbf'] <= now < claims['exp']
                    and 0 < claims['exp'] - claims['iat'] <= 300):
                return False
            if (claims.get('iss') != self.issuer or claims.get('aud') != 'z0int'
                    or not all(isinstance(claims.get(k), str) and claims[k]
                               for k in ('sub', 'dev', 'app', 'jti', 'scope'))):
                return False
            return scope in claims['scope'].split()
        except (ValueError, KeyError, TypeError, AttributeError, InvalidSignature):
            return False


class LocalHTTPGuard(BaseHTTPRequestHandler):
    """Guard before any route/body handling, including unsupported HTTP methods."""

    # Exact deployed service DNS aliases, never arbitrary Host or forwarded headers.
    service_hostname = None

    def parse_request(self):
        if not super().parse_request():
            return False
        bound, port = cast(HTTPServer, self.server).server_address[:2]
        hosts = {'127.0.0.1', 'localhost'}
        # Allow the specific numeric bridge/pod interface only on its own listener.
        try:
            address = ipaddress.ip_address(str(bound))
            if not address.is_unspecified:
                hosts.add(str(address))
                if not address.is_loopback and self.service_hostname:
                    hosts.add(self.service_hostname)
        except ValueError:
            pass
        allowed = {f'{host}:{port}' for host in hosts}
        if port == 80:
            allowed.update(hosts)
        values = self.headers.get_all('Host', [])
        if len(values) != 1 or values[0] not in allowed:
            return self._refuse(421, 'untrusted_host')
        # Empty allow-list intentionally: no browser caller is currently enrolled.
        if self.headers.get_all('Origin', []):
            return self._refuse(403, 'untrusted_origin')
        if self.command == 'POST':
            types = self.headers.get_all('Content-Type', [])
            if len(types) != 1 or types[0].split(';', 1)[0].strip().lower() != 'application/json':
                return self._refuse(415, 'json_content_type_required')
        path = self.path.split('?', 1)[0]
        if os.environ.get('Z0INT_Z0BRIDGE_AUTH', '0') == '1' and path not in (
                '/healthz', '/readyz', '/health', '/api/version'):
            try:
                with _POLICY_LOCK:
                    if not hasattr(self.server, '_z0int_access_tokens'):
                        setattr(self.server, '_z0int_access_tokens', AccessTokens())
                verifier = getattr(self.server, '_z0int_access_tokens')
            except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
                return self._refuse(503, 'identity_not_configured')
            auth = self.headers.get_all('Authorization', [])
            if (len(auth) != 1 or not auth[0].startswith('Bearer ')
                    or not verifier.verify(auth[0][7:], route_scope(path))):
                return self._refuse(401, 'invalid_token_or_scope')
        return True

    def _refuse(self, code, error):
        # Close rather than treating an unread POST body as the next request.
        self.close_connection = True
        body = json.dumps({'error': error}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        if code == 401:
            self.send_header('WWW-Authenticate', 'Bearer')
        self.end_headers()
        self.wfile.write(body)
        return False
