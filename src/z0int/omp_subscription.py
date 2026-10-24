"""Text-only Responses/Messages adapters, using Unii's native provider wires.

OMP owns subscription credentials and refresh. Read its latest enabled OAuth
row on each call, never write a credential store, and never fall back to keys
or another account. Protocol references: uniichat/{Subscription,Codex,Claude,
OpenAI,Anthropic}.ts. Subscription pins are in the current OMP model_cache.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MODELS = {
    'openai-codex': ('gpt-6-luna',),
    'anthropic': ('claude-haiku-5-5',),
}
_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


class SubscriptionError(RuntimeError):
    """Safe error: no provider body, credential, URL, or original exception."""

    def __init__(self, message, *, code=None, headers=None):
        super().__init__(message)
        self.code = code
        self.headers = headers or {}


@dataclass
class Completion:
    content: str
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    model: str | None
    id: str | None
    cost: float | None
    tool_call: bool = False
    quota_headers: dict = field(default_factory=dict)
    output_limit_enforced: bool = True

    @property
    def usage(self):
        return {'prompt_tokens': self.input_tokens,
                'completion_tokens': self.output_tokens,
                'prompt_tokens_details': {'cached_tokens': self.cached_input_tokens},
                'cost': self.cost}

    @property
    def raw(self):
        # Only receipt fields, never the full provider payload.
        return {'model': self.model, 'id': self.id,
                'choices': [{'finish_reason': self.finish_reason}]}


def _login(provider):
    if provider not in ('openai-codex', 'anthropic'):
        raise SubscriptionError('Unsupported OMP subscription provider.')
    directory = Path(os.environ.get('OMP_AGENT_DIR') or Path.home() / '.omp' / 'agent')
    db = None
    try:
        db = sqlite3.connect((directory / 'agent.db').resolve().as_uri() + '?mode=ro', uri=True)
        row = db.execute("SELECT data FROM auth_credentials WHERE provider = ? "
                         "AND credential_type = 'oauth' AND disabled_cause IS NULL "
                         "ORDER BY updated_at DESC, id DESC LIMIT 1", (provider,)).fetchone()
        data = json.loads(row[0]) if row else None
    except Exception:
        raise SubscriptionError('Cannot read the existing OMP subscription login.') from None
    finally:
        if db is not None:
            db.close()
    if (not isinstance(data, dict) or not isinstance(data.get('access'), str)
            or not data['access'] or type(data.get('expires')) not in (int, float)
            or not math.isfinite(data['expires'])):
        raise SubscriptionError('No valid existing OMP OAuth subscription login.')
    if provider == 'anthropic' and not data['access'].startswith('sk-ant-oat'):
        raise SubscriptionError('The OMP Anthropic login is not subscription OAuth.')
    if provider == 'openai-codex' and (not isinstance(data.get('accountId'), str) or not data['accountId']):
        raise SubscriptionError('The OMP Codex subscription login has no account ID.')
    if data['expires'] <= time.time() * 1000:
        raise SubscriptionError('The OMP subscription login expired. Let OMP refresh it before retrying.')
    return {'access': data['access'], 'accountId': data.get('accountId'), 'expires': data['expires']}


def credential_available(provider):
    try:
        _login(provider)
        return True
    except SubscriptionError:
        return False


def _messages(messages):
    if not isinstance(messages, (list, tuple)) or not messages:
        raise SubscriptionError('Messages must be a nonempty text-message sequence.')
    system, turns = [], []
    for message in messages:
        if (not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant')
                or not isinstance(message.get('content'), str)):
            raise SubscriptionError('Only system, user, and assistant text messages are supported.')
        if message['role'] == 'system':
            if turns:
                raise SubscriptionError('System messages must precede conversation messages.')
            system.append(message['content'])
        else:
            turns.append({'role': message['role'], 'content': message['content']})
    if not turns or turns[0]['role'] != 'user':
        raise SubscriptionError('The conversation must start with a user message.')
    return '\n\n'.join(system), turns


def _request(provider, model, messages, max_tokens):
    if provider not in MODELS or model not in MODELS[provider]:
        raise SubscriptionError('Unsupported exact provider/model subscription route.')
    if type(max_tokens) is not int or not 1 <= max_tokens <= 128000:
        raise SubscriptionError('max_tokens must be an integer from 1 to 128000.')
    system, turns = _messages(messages)
    anthropic = provider == 'anthropic'
    headers = {'content-type': 'application/json', 'accept': 'text/event-stream'}
    login = _login(provider)
    headers['authorization'] = 'Bearer ' + login['access']
    if anthropic:
        url = 'https://api.anthropic.com/v1/messages?beta=true'
        headers.update({'anthropic-beta': 'claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14',
                        'user-agent': 'claude-cli/2.1.280 (external, cli)', 'x-app': 'cli'})
    else:
        url = 'https://chatgpt.com/backend-api/codex/responses'
        headers.update({'chatgpt-account-id': login['accountId'], 'originator': 'codex_cli_rs',
                        'user-agent': 'codex_cli_rs/0.162.1'})
    if anthropic:
        headers['anthropic-version'] = '2023-06-01'
        merged = []
        for turn in turns:
            block = {'type': 'text', 'text': turn['content']}
            if merged and merged[-1]['role'] == turn['role']:
                merged[-1]['content'].append(block)
            else:
                merged.append({'role': turn['role'], 'content': [block]})
        body = {'model': model, 'stream': True, 'max_tokens': max_tokens, 'messages': merged,
                'system': [{'type': 'text', 'text': system}] if system else []}
        body['system'].insert(0, {'type': 'text', 'text': _IDENTITY})
        body.update(thinking={'type': 'adaptive', 'display': 'summarized'}, output_config={'effort': 'low'})
    else:
        inputs = [{'role': t['role'], 'content': [{'type': 'output_text' if t['role'] == 'assistant'
                                                 else 'input_text', 'text': t['content']}]} for t in turns]
        body = {'model': model, 'instructions': system, 'input': inputs, 'stream': True, 'store': False,
                'tools': [], 'reasoning': {'effort': 'low', 'summary': 'auto', 'context': 'all_turns'}}
        # Codex's native endpoint does not support max_output_tokens.
        # Match Unii Codex.body: do not invent an unsupported hard limit.
        body['include'] = ['reasoning.encrypted_content']
    return Request(url, json.dumps(body).encode(), headers, method='POST'), anthropic


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _cost(usage):
    value = usage.get('cost')
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _text(value):
    return value if isinstance(value, str) and value else None


def _events(response, deadline):
    data = []
    for raw in response:
        if time.monotonic() >= deadline:
            raise SubscriptionError('The provider stream exceeded the request deadline.')
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            if data:
                payload = '\n'.join(data)
                data.clear()
                if payload != '[DONE]':
                    event = json.loads(payload)
                    if not isinstance(event, dict):
                        raise SubscriptionError('The provider returned an invalid stream event.')
                    yield event
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
    if data:
        payload = '\n'.join(data)
        if payload != '[DONE]':
            event = json.loads(payload)
            if not isinstance(event, dict):
                raise SubscriptionError('The provider returned an invalid stream event.')
            yield event


def _responses(events):
    items, deltas = {}, []
    for event in events:
        kind = event.get('type')
        if kind == 'error' or kind == 'response.failed':
            raise SubscriptionError('The Responses provider rejected the completion.')
        if kind == 'response.output_text.delta' and isinstance(event.get('delta'), str):
            deltas.append(event['delta'])
        if kind == 'response.output_item.done' and type(event.get('output_index')) is int:
            items[event['output_index']] = event['item']
        if kind in ('response.completed', 'response.done', 'response.incomplete'):
            response = event.get('response')
            if not isinstance(response, dict) or response.get('status') == 'failed':
                raise SubscriptionError('The Responses provider returned an invalid completion.')
            output = response.get('output') or [items[i] for i in sorted(items)]
            texts, tool, refused = [], False, False
            for item in output:
                if item.get('type') in ('function_call', 'custom_tool_call'):
                    tool = True
                if item.get('type') == 'message':
                    for block in item.get('content', []):
                        if block.get('type') == 'output_text':
                            texts.append(block.get('text', ''))
                        elif block.get('type') == 'refusal':
                            refused = True
            incomplete = kind == 'response.incomplete' or response.get('status') == 'incomplete'
            reason = 'length' if (response.get('incomplete_details') or {}).get('reason') == 'max_output_tokens' else 'incomplete'
            finish = reason if incomplete else 'tool_calls' if tool else 'refusal' if refused else 'stop'
            usage = response.get('usage') or {}
            return Completion(''.join(texts) if texts else ''.join(deltas), finish,
                              _number(usage.get('input_tokens')), _number(usage.get('output_tokens')),
                              _number((usage.get('input_tokens_details') or {}).get('cached_tokens')),
                              _text(response.get('model')), _text(response.get('id')), _cost(usage), tool)
    raise SubscriptionError('The Responses stream ended without a terminal completion.')


def _anthropic(events):
    blocks, usage, model, identity, stop = {}, {}, None, None, None
    for event in events:
        kind = event.get('type')
        if kind == 'error':
            raise SubscriptionError('The Messages provider rejected the completion.')
        if kind == 'message_start':
            message = event['message']
            model, identity = _text(message.get('model')), _text(message.get('id'))
            usage.update(message.get('usage') or {})
        elif kind == 'content_block_start':
            blocks[event['index']] = dict(event['content_block'])
        elif kind == 'content_block_delta':
            delta = event['delta']
            if delta.get('type') == 'text_delta':
                block = blocks[event['index']]
                block['text'] = block.get('text', '') + delta['text']
        elif kind == 'message_delta':
            stop = event.get('delta', {}).get('stop_reason') or stop
            usage.update(event.get('usage') or {})
        elif kind == 'message_stop':
            tool = any(b.get('type') in ('tool_use', 'server_tool_use') for b in blocks.values())
            finish = 'stop' if stop == 'end_turn' else 'length' if stop == 'max_tokens' else 'tool_calls' if stop == 'tool_use' else stop
            inputs = _number(usage.get('input_tokens'))
            cached = _number(usage.get('cache_read_input_tokens'))
            if inputs is not None:
                inputs += (cached or 0) + (_number(usage.get('cache_creation_input_tokens')) or 0)
            return Completion(''.join(blocks[i].get('text', '') for i in sorted(blocks)
                                      if blocks[i].get('type') == 'text'), finish, inputs,
                              _number(usage.get('output_tokens')), cached, model, identity, _cost(usage), tool)
    raise SubscriptionError('The Messages stream ended without a terminal completion.')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward subscription authorization to another endpoint.
        return None


def _safe_headers(headers):
    allowed = ('retry-after', 'x-ratelimit-', 'anthropic-ratelimit-')
    return {k.lower(): v for k, v in headers.items()
            if any(k.lower().startswith(prefix) for prefix in allowed)}


def execute(provider, model, messages, max_tokens, *, timeout_s=120):
    """Execute once, without retries or refresh; absent usage/cost stays None.

    Native Codex ignores max_tokens, as in Unii. Other routes enforce it in
    their request. Never treat subscription pricing as provider-reported $0.
    """
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise SubscriptionError('timeout_s must be a positive finite number.')
    request, anthropic = _request(provider, model, messages, max_tokens)
    try:
        deadline = time.monotonic() + timeout_s
        with build_opener(_NoRedirect()).open(request, timeout=timeout_s) as response:
            if 'text/event-stream' not in response.headers.get('content-type', '').lower():
                raise SubscriptionError('The provider did not return an event stream.')
            result = (_anthropic if anthropic else _responses)(_events(response, deadline))
            result.quota_headers.update(_safe_headers(response.headers))
            result.output_limit_enforced = provider != 'openai-codex'
            return result
    except HTTPError as exc:
        code = exc.code
        headers = _safe_headers(exc.headers) if exc.headers is not None else {}
        exc.close()
        raise SubscriptionError('The provider rejected the HTTP request.', code=code, headers=headers) from None
    except SubscriptionError:
        raise
    except Exception:
        raise SubscriptionError('The provider request or stream failed.') from None
