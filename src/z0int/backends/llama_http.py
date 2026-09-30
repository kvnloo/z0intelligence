"""Portable generation-free decisions over any host's llama.cpp server.

The OpenJev direct-readout pattern without torch: format the question through the
server's own chat template, end the prompt at "Answer:", read the next-token
log-probabilities of the option letters and renormalize over exactly those letters.
Works with any GGUF on any device the host has (CUDA, Vulkan, Metal, CPU), which is
what makes z0 decisions available regardless of machine. Stdlib only.
"""

from __future__ import annotations

import json
import math
import os
import string
import time
import urllib.request
from typing import Any

from .base import (BackendCapabilities, BackendHealth, DecisionAnswer, DecisionRequest,
                   DecisionResult)

LETTERS = string.ascii_uppercase
TOP_K = 64


def _post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _options(question) -> list[tuple[str, str]]:
    if question.type == 'boolean':
        return [('false', question.false_criterion or 'no'), ('true', question.true_criterion or 'yes')]
    if question.type == 'choice':
        return [(o.id, o.description) for o in question.options]
    return [(str(i), level) for i, level in enumerate(question.levels)]


def _letter_distribution(top: list[dict[str, Any]], n: int) -> dict[str, float]:
    """Renormalize over the first n letters; unseen letters get the tail floor."""
    mass = {LETTERS[i]: 0.0 for i in range(n)}
    floor = min((t['logprob'] for t in top), default=-30.0) - math.log(2)
    for tok in top:
        key = tok['token'].strip()
        if key in mass:
            mass[key] += math.exp(tok['logprob'])
    for key, value in mass.items():
        if value == 0.0:
            mass[key] = math.exp(floor)
    total = math.fsum(mass.values())
    return {k: v / total for k, v in mass.items()}


class LlamaHttpBackend:
    def __init__(self, url: str | None = None, timeout: float = 30.0):
        self.url = (url or os.environ.get('Z0INT_SLM_URL') or 'http://127.0.0.1:11510').rstrip('/')
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> 'LlamaHttpBackend':
        return cls()

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(id='llama_http', local=True, supports_boolean=True, supports_choice=True,
                                   supports_score=True, max_choice_options=len(LETTERS), max_score_levels=10,
                                   kind='direct_readout',
                                   description='Next-token option-letter readout over the host llama.cpp server',
                                   supports_batch_questions=True)

    def _model(self) -> str | None:
        try:
            with urllib.request.urlopen(self.url + '/v1/models', timeout=5) as resp:
                data = json.load(resp).get('data') or [{}]
            return data[0].get('id')
        except Exception:
            return None

    def health(self, *, load: bool = False) -> BackendHealth:
        try:
            with urllib.request.urlopen(self.url + '/health', timeout=5) as resp:
                ok = json.load(resp).get('status') == 'ok'
        except Exception as exc:
            return BackendHealth(id='llama_http', configured=True, ready=False, detail=f'{self.url}: {type(exc).__name__}')
        return BackendHealth(id='llama_http', configured=True, ready=ok, loaded=ok, model=self._model(),
                             detail=self.url, diagnostics={'url': self.url})

    def _prompt(self, state: Any, question, options: list[tuple[str, str]]) -> str:
        body = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        lines = '\n'.join(f'{LETTERS[i]}: {desc}' for i, (_, desc) in enumerate(options))
        content = f'{body}\n\nQuestion: {question.instructions}\n{lines}\nReply with the letter only.'
        tmpl = _post(self.url + '/apply-template', {'messages': [{'role': 'user', 'content': content}],
                                                    'chat_template_kwargs': {'enable_thinking': False}}, self.timeout)
        return tmpl['prompt'] + 'Answer:'

    def _yes_no(self, state: Any, question) -> dict[str, float]:
        body = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        criteria = ''
        if question.true_criterion or question.false_criterion:
            criteria = f"\nyes means: {question.true_criterion or 'true'}\nno means: {question.false_criterion or 'false'}"
        content = f'{body}\n\nQuestion: {question.instructions}{criteria}\nAnswer yes or no.'
        tmpl = _post(self.url + '/apply-template', {'messages': [{'role': 'user', 'content': content}],
                                                    'chat_template_kwargs': {'enable_thinking': False}}, self.timeout)
        out = _post(self.url + '/completion', {'prompt': tmpl['prompt'], 'n_predict': 1, 'n_probs': TOP_K,
                                                'temperature': 0, 'cache_prompt': True}, self.timeout)
        top = (out.get('completion_probabilities') or [{}])[0].get('top_logprobs') or []
        mass = {'true': 0.0, 'false': 0.0}
        for tok in top:
            word = tok['token'].strip().lower()
            if word == 'yes':
                mass['true'] += math.exp(tok['logprob'])
            elif word == 'no':
                mass['false'] += math.exp(tok['logprob'])
        floor = math.exp(min((t['logprob'] for t in top), default=-30.0) - math.log(2))
        mass = {k: v or floor for k, v in mass.items()}
        total = mass['true'] + mass['false']
        return {k: v / total for k, v in mass.items()}

    def evaluate(self, request: DecisionRequest) -> DecisionResult:
        started = time.perf_counter()
        answers = []
        for question in request.questions:
            options = _options(question)
            if len(options) > len(LETTERS):
                raise ValueError(f'llama_http supports at most {len(LETTERS)} options')
            # Small models carry strong letter-position bias; average the forward and
            # reversed option orders (score keeps its natural order).
            orders = [list(range(len(options)))]
            if question.type != 'score' and os.environ.get('Z0INT_SLM_DEBIAS', '1') != '0':
                orders.append(orders[0][::-1])
            probs = {oid: 0.0 for oid, _ in options}
            if question.type == 'boolean':
                # Letter framing carries no yes/no signal in small models (both orders
                # collapse to the same position); read the native yes/no tokens instead.
                probs = self._yes_no(request.state, question)
                orders = []
            for order in orders:
                shown = [options[i] for i in order]
                out = _post(self.url + '/completion', {'prompt': self._prompt(request.state, question, shown),
                                                        'n_predict': 1, 'n_probs': TOP_K, 'temperature': 0,
                                                        'cache_prompt': True}, self.timeout)
                top = (out.get('completion_probabilities') or [{}])[0].get('top_logprobs') or []
                letters = _letter_distribution(top, len(shown))
                for pos, (oid, _) in enumerate(shown):
                    probs[oid] += letters[LETTERS[pos]] / len(orders)
            best = max(probs, key=probs.get)
            value: bool | str | float
            if question.type == 'boolean':
                value = best == 'true'
            elif question.type == 'choice':
                value = best
            else:
                value = sum(int(k) * p for k, p in probs.items()) / max(1, len(options) - 1)
            answers.append(DecisionAnswer(question_id=question.id, type=question.type, probabilities=probs,
                                          value=value, confidence=probs[best]))
        return DecisionResult(backend='llama_http', model=self._model(), revision=None, answers=tuple(answers),
                              latency_ms=(time.perf_counter() - started) * 1000, diagnostics={'url': self.url})
