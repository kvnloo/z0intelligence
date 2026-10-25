"""Isolated headless OMP provider-stack execution for shadow evaluation.

Source credentials and model caches are read-only. Each invocation gets an
access-only OAuth snapshot and an empty private HOME/cwd/agent directory.
The installed executable, not a reconstructed provider wire, owns inference.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import selectors
import signal
import subprocess
import time
import uuid

DEFAULT_BINARY = '/home/kvn/.local/share/tern/omp/bin/omp'
DEFAULT_ROOT = '/mnt/zer0models/workspace/zer0/data/operations/shadow-evals'
FLAGS = ('-p', '--no-tools', '--no-lsp', '--no-pty', '--no-extensions', '--no-skills',
         '--no-rules', '--no-title', '--no-prewalk', '--mode', 'json')


class OmpError(RuntimeError):
    """Fixed error codes only; never include upstream bodies or credentials."""

    def __init__(self, reason, *, code=None, headers=None):
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.headers = headers or {}


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _private_file(path, text):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)


def _readonly(path):
    connection = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def _table_snapshot(source, target_path, table, rows):
    """Preserve the installed store's actual schema; do not invent versions."""
    if table not in ('auth_credentials', 'model_cache'):
        raise OmpError('unsupported_snapshot_table')
    schema = source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if schema is None or not isinstance(schema[0], str):
        raise OmpError('snapshot_schema_unavailable')
    columns = [row['name'] for row in source.execute('PRAGMA table_info(' + table + ')')]
    if not columns or any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) for name in columns):
        raise OmpError('invalid_snapshot_columns')
    fd = os.open(target_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    target = sqlite3.connect(target_path)
    try:
        target.execute(schema[0])
        statement = 'INSERT INTO ' + table + ' (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
        target.executemany(statement, [[row.get(name) for name in columns] for row in rows])
        target.commit()
    finally:
        target.close()


def _source_paths(config):
    original_agent = Path(os.environ.get('OMP_AGENT_DIR', Path.home() / '.omp/agent'))
    auth = Path(config.get('omp_auth_db', config.get('usage_db', original_agent / 'agent.db'))).expanduser()
    models = Path(config.get('omp_models_db', original_agent / 'models.db')).expanduser()
    return auth, models


def prepare_agent_dir(offer, config):
    """Prepare one isolated invocation; does not start OMP or contact providers.

    Returned ``env`` contains only private paths/nonsecret execution settings.
    OAuth refresh material is omitted, and expiry must exceed the whole call
    deadline plus OMP's observed 60-second preflight skew.
    """
    provider, model = offer.get('provider'), offer.get('model')
    if (not isinstance(provider, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', provider)
            or not isinstance(model, str) or not model or any(x in model for x in ('\n', '\r', ':'))):
        raise OmpError('invalid_exact_model_selector')
    timeout = config.get('timeout_s', 120)
    if not _positive(timeout):
        raise OmpError('invalid_timeout')
    root = Path(config.get('root', DEFAULT_ROOT)).expanduser().absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise OmpError('experiment_root_not_private')
    calls = root / 'omp-calls'
    calls.mkdir(mode=0o700, exist_ok=True)
    if calls.is_symlink() or calls.stat().st_mode & 0o077:
        raise OmpError('call_root_not_private')
    call = calls / uuid.uuid4().hex
    call.mkdir(mode=0o700)
    directories = {name: call / name for name in ('agent', 'cwd', 'home', 'tmp', 'xdg-data', 'xdg-state',
                                                   'xdg-cache', 'xdg-config', 'xdg-runtime')}
    for path in directories.values():
        path.mkdir(mode=0o700)
    auth_path, models_path = _source_paths(config)
    auth_source = models_source = None
    try:
        auth_source = _readonly(auth_path)
        credential = auth_source.execute(
            "SELECT * FROM auth_credentials WHERE provider=? AND credential_type='oauth' "
            "AND disabled_cause IS NULL ORDER BY updated_at DESC,id DESC LIMIT 1", (provider,)).fetchone()
        if credential is None:
            raise OmpError('oauth_credential_unavailable')
        data = json.loads(credential['data'])
        if (not isinstance(data, dict) or not isinstance(data.get('access'), str) or not data['access']
                or not _positive(data.get('expires')) or data['expires'] <= (time.time() + timeout + 60) * 1000):
            raise OmpError('oauth_expired_or_insufficient_lifetime')
        if provider == 'anthropic' and not data['access'].startswith('sk-ant-oat'):
            raise OmpError('not_subscription_oauth')
        if provider == 'openai-codex' and not data.get('accountId'):
            raise OmpError('oauth_account_missing')
        expected = config.get('omp_account_id_hash')
        if expected and hashlib.sha256(str(data.get('accountId')).encode()).hexdigest() != expected:
            raise OmpError('credential_account_changed')
        expected_key = config.get('omp_account_hash')
        if expected_key:
            key = f"oauth|account:{data.get('accountId')}|email:{data.get('email')}"
            if isinstance(data.get('orgId'), str) and data['orgId']:
                key += '|org:' + data['orgId']
            if hashlib.sha256(key.encode()).hexdigest() != expected_key:
                raise OmpError('credential_account_changed')
        allowed = ('access', 'expires', 'accountId', 'email', 'orgId', 'orgName', 'authorizedAt',
                   'projectId', 'enterpriseUrl', 'apiEndpoint', 'subscriptionType', 'rateLimitTier', 'planType')
        access_only = {key: data[key] for key in allowed if key in data}
        access_only['refresh'] = ''
        copied = dict(credential)
        copied['data'] = _json(access_only)
        _table_snapshot(auth_source, directories['agent'] / 'agent.db', 'auth_credentials', [copied])
        models_source = _readonly(models_path)
        rows = [dict(row) for row in models_source.execute(
            'SELECT * FROM model_cache WHERE provider_id=? OR substr(provider_id,1,?)=?',
            (provider, len(provider) + 1, provider + ':'))]
        exact_model = False
        for row in rows:
            specifications = json.loads(row['models'])
            if isinstance(specifications, list) and any(isinstance(item, dict) and item.get('id') == model
                                                       for item in specifications):
                exact_model = True
        if not rows or not exact_model:
            raise OmpError('exact_native_model_cache_unavailable')
        _table_snapshot(models_source, directories['agent'] / 'models.db', 'model_cache', rows)
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        raise OmpError('readonly_snapshot_unavailable') from None
    finally:
        if auth_source is not None:
            auth_source.close()
        if models_source is not None:
            models_source.close()
    settings = {'retry': {'enabled': False, 'maxRetries': 0, 'modelFallback': False,
                          'fallbackChains': {}, 'waitForUsageReset': False, 'usageAwareFallback': False},
                'providers': {'anthropic': {'serverSideFallback': False}},
                'telemetry': {'otlpExportEnabled': False},
                'compaction': {'enabled': False, 'midTurnEnabled': False, 'experimentalContextManagement': False},
                'ttsr': {'enabled': False},
                'autolearn': {'enabled': False, 'autoContinue': False}, 'memory': {'backend': 'off'},
                'hindsight': {'autoRecall': False, 'autoRetain': False, 'mentalModelsEnabled': False},
                'memories': {'enabled': False}, 'mcp': {'enableProjectConfig': False},
                'startup': {'setupWizard': False}, 'prewalk': {'enabled': False}}
    _private_file(directories['agent'] / 'config.yml', _json(settings))
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'TERM': 'dumb', 'NO_COLOR': '1', 'HOME': str(directories['home']),
           'PI_CODING_AGENT_DIR': str(directories['agent']), 'OMP_AGENT_DIR': str(directories['agent']),
           'TMPDIR': str(directories['tmp']), 'XDG_DATA_HOME': str(directories['xdg-data']),
           'XDG_STATE_HOME': str(directories['xdg-state']), 'XDG_CACHE_HOME': str(directories['xdg-cache']),
           'XDG_CONFIG_HOME': str(directories['xdg-config']), 'XDG_RUNTIME_DIR': str(directories['xdg-runtime']),
           'PI_NO_TITLE': '1', 'OTEL_SDK_DISABLED': 'true', 'DO_NOT_TRACK': '1'}
    effort = config.get('omp_effort', 'low')
    if effort not in ('off', 'minimal', 'low', 'medium', 'high', 'xhigh'):
        raise OmpError('invalid_effort')
    selector = provider + '/' + model + ':' + effort
    argv = [str(config.get('omp_binary', DEFAULT_BINARY)), *FLAGS, '--model', selector,
            '--max-time', str(timeout)]
    return {'call_dir': str(call), 'agent_dir': str(directories['agent']), 'cwd': str(directories['cwd']),
            'env': env, 'selector': selector, 'argv': argv,
            'source_auth_db': str(auth_path), 'source_models_db': str(models_path),
            'auth_snapshot': 'latest_enabled_oauth_access_only_no_refresh',
            'expected_omp_version': config.get('omp_expected_version', '18.12.6')}


def credential_available(provider, config=None, *, timeout_s=120):
    """Check latest enabled OAuth without refresh, writes, or discovery."""
    connection = None
    try:
        auth, _ = _source_paths(config or {})
        connection = _readonly(auth)
        row = connection.execute(
            "SELECT data FROM auth_credentials WHERE provider=? AND credential_type='oauth' "
            "AND disabled_cause IS NULL ORDER BY updated_at DESC,id DESC LIMIT 1", (provider,)).fetchone()
        value = json.loads(row[0]) if row else {}
        return (isinstance(value.get('access'), str) and bool(value['access'])
                and _positive(value.get('expires')) and _positive(timeout_s)
                and value['expires'] > (time.time() + timeout_s + 60) * 1000
                and (provider != 'anthropic' or value['access'].startswith('sk-ant-oat'))
                and (provider != 'openai-codex' or bool(value.get('accountId'))))
    except (sqlite3.Error, ValueError, AttributeError, TypeError):
        return False
    finally:
        if connection is not None:
            connection.close()


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


class ReceiptAccumulator:
    """OMP 18.12.6 JSON contract grounded by the real print fixture.

    Only assistant message_end supplies authoritative usage; placeholder starts
    and duplicated turn_end/agent_end never add tokens.
    """

    def __init__(self, provider, model, max_tokens):
        self.provider, self.model, self.limit = provider, model, max_tokens
        self.message = None
        self.starts = 0
        self.text = []
        self.output_bytes = 0
        self.stop = None
        self.tool_call = False

    def feed(self, event):
        if not isinstance(event, dict):
            raise OmpError('invalid_omp_json_event')
        kind = event.get('type')
        message = event.get('message', {})
        if kind == 'message_start' and isinstance(message, dict) and message.get('role') == 'assistant':
            self.starts += 1
            if self.starts > 1:
                self.stop = 'unexpected_additional_model_turn'
        if kind in ('tool_execution_start', 'tool_execution_update', 'tool_execution_end'):
            self.tool_call = True
            self.stop = 'tool_call_rejected'
        if kind == 'message_update':
            stream = event.get('assistantMessageEvent', {})
            if not isinstance(stream, dict):
                raise OmpError('invalid_omp_stream_event')
            stream_type = stream.get('type', '')
            if stream_type.startswith('toolcall_'):
                self.tool_call = True
                self.stop = 'tool_call_rejected'
            if stream_type in ('text_delta', 'thinking_delta') and isinstance(stream.get('delta'), str):
                delta = stream['delta']
                self.output_bytes += len(delta.encode('utf-8'))
                if stream_type == 'text_delta':
                    self.text.append(delta)
                # Printable events omit partial usage. Bytes are a conservative
                # token upper bound, never a claimed provider token observation.
                if self.output_bytes >= self.limit:
                    self.stop = 'client_byte_bound_stop'
        if kind == 'message_end' and isinstance(message, dict) and message.get('role') == 'assistant':
            if self.message is not None:
                self.stop = 'unexpected_additional_model_turn'
            else:
                self.message = message
            if (message.get('provider'), message.get('model')) != (self.provider, self.model):
                self.stop = 'native_model_identity_mismatch'
            content = message.get('content', [])
            if not isinstance(content, list):
                raise OmpError('invalid_omp_content')
            if any(isinstance(block, dict) and block.get('type') in ('toolCall', 'tool_call') for block in content):
                self.tool_call = True
                self.stop = 'tool_call_rejected'
            usage = message.get('usage', {})
            if isinstance(usage, dict) and _tokens(usage.get('output')) is not None and usage['output'] > self.limit:
                self.stop = 'native_output_limit_overrun'
        return self.stop is not None

    def result(self):
        message = self.message or {}
        content = message.get('content', [])
        text = ''.join(block.get('text', '') for block in content
                       if isinstance(block, dict) and block.get('type') == 'text'
                       and isinstance(block.get('text'), str)) if self.message else ''.join(self.text)
        usage = message.get('usage', {})
        usage = usage if isinstance(usage, dict) else {}
        failed = message.get('stopReason') in ('error', 'aborted')
        if failed and not any(_number(usage.get(key)) for key in ('input', 'output', 'cacheRead', 'cacheWrite')):
            usage = {}
        ttft, duration = _number(message.get('ttft')), _number(message.get('duration'))
        gen_ms = duration - ttft if ttft is not None and duration is not None and duration > ttft else None
        output = _tokens(usage.get('output'))
        status = message.get('errorStatus')
        error = {'code': self.stop} if self.stop else None
        if failed:
            error = {'code': 'omp_provider_error'}
            if type(status) is int:
                error['http_status'] = status
        elif not self.message and not self.stop:
            error = {'code': 'omp_missing_terminal_receipt'}
        price = usage.get('cost', {})
        return {'content': text, 'reported_provider': message.get('provider'),
                'reported_model': message.get('model'), 'response_id': message.get('responseId'),
                'input_tokens': _tokens(usage.get('input')), 'output_tokens': output,
                'cached_input_tokens': _tokens(usage.get('cacheRead')),
                'cache_write_input_tokens': _tokens(usage.get('cacheWrite')),
                'total_tokens': _tokens(usage.get('totalTokens')),
                'cost': None, 'list_price_usd': _number(price.get('total')) if isinstance(price, dict) else None,
                'cost_basis': 'omp_computed_list_price_not_actual_billed_spend',
                'ttft_ms': ttft, 'duration_ms': duration, 'gen_ms': gen_ms,
                'generation_basis': 'native_duration_minus_native_ttft' if gen_ms is not None else None,
                'tokens_per_second': output * 1000 / gen_ms if output is not None and gen_ms is not None else None,
                'finish_reason': self.stop or message.get('stopReason'), 'tool_call': self.tool_call,
                'output_limit_enforced': False, 'output_bytes_observed': self.output_bytes,
                'client_limit_overrun_bytes': max(0, self.output_bytes - self.limit),
                'server_overrun_tokens': max(0, output - self.limit) if output is not None else None,
                'error': error, 'quota_headers': {}, 'terminal_assistant_receipts': int(self.message is not None)}


def _safe_receipt_event(event):
    """Persist reviewed receipt fields, never provider errors or payloads."""
    if event.get('type') == 'message_end' and isinstance(event.get('message'), dict):
        message = event['message']
        if message.get('role') != 'assistant':
            return None
        safe = {key: message[key] for key in ('role', 'provider', 'model', 'usage', 'stopReason',
                 'ttft', 'duration', 'responseId', 'errorStatus') if key in message}
        safe['content'] = [{'type': 'text', 'text': block['text']} for block in message.get('content', [])
                           if isinstance(block, dict) and block.get('type') == 'text'
                           and isinstance(block.get('text'), str)]
        return {'type': 'message_end', 'message': safe}
    if event.get('type') == 'message_update':
        stream = event.get('assistantMessageEvent', {})
        if isinstance(stream, dict):
            return {'type': 'message_update', 'assistantMessageEvent':
                    {key: stream[key] for key in ('type', 'contentIndex', 'delta') if key in stream}}
    return None


def _prompt(messages):
    if (not isinstance(messages, (list, tuple)) or not messages or any(not isinstance(row, dict)
            or set(row) != {'role', 'content'} or row['role'] not in ('system', 'user', 'assistant')
            or not isinstance(row['content'], str) for row in messages)):
        raise OmpError('invalid_text_only_messages')
    if len(messages) == 1 and messages[0]['role'] == 'user':
        return messages[0]['content']
    return 'Respond to this ordered text-only conversation. Do not use tools.\n' + _json({'messages': messages})


def _terminate(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute(provider, model, messages, max_tokens, *, timeout_s=120, config=None):
    """One native invocation. Caller owns authorization and budget admission."""
    if type(max_tokens) is not int or not 1 <= max_tokens <= 128000 or not _positive(timeout_s):
        raise OmpError('invalid_completion_limits')
    prompt = _prompt(messages)
    options = {**(config or {}), 'timeout_s': timeout_s}
    prepared = prepare_agent_dir({'provider': provider, 'model': model}, options)
    call = Path(prepared['call_dir'])
    _private_file(call / 'prompt.txt', prompt)
    prompt_bytes = prompt.encode('utf-8')
    accumulator = ReceiptAccumulator(provider, model, max_tokens)
    started = time.monotonic()
    deadline = started + timeout_s
    process = subprocess.Popen(prepared['argv'], cwd=prepared['cwd'], env=prepared['env'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    selector = selectors.DefaultSelector()
    buffer, total_output, stderr_bytes, offset = bytearray(), 0, 0, 0
    completed_reads = False
    receipt = call / 'receipt.jsonl'
    receipt_fd = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        for stream, name, mask in ((process.stdin, 'stdin', selectors.EVENT_WRITE),
                                   (process.stdout, 'stdout', selectors.EVENT_READ),
                                   (process.stderr, 'stderr', selectors.EVENT_READ)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, mask, name)
        with os.fdopen(receipt_fd, 'w', encoding='utf-8') as log:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    accumulator.stop = 'omp_deadline_exceeded'
                    break
                for key, _ in selector.select(min(remaining, .25)):
                    if key.data == 'stdin':
                        try:
                            count = os.write(key.fd, prompt_bytes[offset:offset + 65536])
                            offset += count
                        except BrokenPipeError:
                            offset = len(prompt_bytes)
                        if offset >= len(prompt_bytes):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 'stderr':
                        stderr_bytes += len(chunk)
                        continue
                    total_output += len(chunk)
                    if total_output > 32 * 1024 * 1024:
                        accumulator.stop = 'omp_receipt_output_limit'
                        break
                    buffer.extend(chunk)
                    while b'\n' in buffer:
                        line, _, rest = buffer.partition(b'\n')
                        buffer = bytearray(rest)
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeError):
                            raise OmpError('invalid_omp_json_event') from None
                        stop = accumulator.feed(event)
                        safe = _safe_receipt_event(event)
                        if safe is not None:
                            log.write(_json(safe) + '\n')
                            log.flush()
                        if stop:
                            break
                    if accumulator.stop:
                        break
                if accumulator.stop:
                    break
            if buffer.strip() and not accumulator.stop:
                try:
                    event = json.loads(buffer)
                except (ValueError, UnicodeError):
                    raise OmpError('invalid_omp_json_event') from None
                accumulator.feed(event)
                safe = _safe_receipt_event(event)
                if safe is not None:
                    log.write(_json(safe) + '\n')
            completed_reads = True
    finally:
        selector.close()
        if completed_reads and not accumulator.stop and process.poll() is None:
            try:
                process.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                accumulator.stop = 'omp_deadline_exceeded'
        if process.poll() is None:
            _terminate(process)
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    value = accumulator.result()
    value.update(provider=provider, model=model, receipt_path=str(receipt), call_dir=str(call),
                 elapsed_ms=(time.monotonic() - started) * 1000, exit_code=process.returncode,
                 stderr_bytes=stderr_bytes, prompt_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
                 omp_expected_version=prepared['expected_omp_version'])
    if process.returncode and value['error'] is None:
        value['error'] = {'code': 'omp_process_failed', 'exit_code': process.returncode}
    _private_file(call / 'result.json', _json(value))
    return value
