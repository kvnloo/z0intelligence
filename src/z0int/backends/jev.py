"""TypeSafe Jev via the existing host credential and typed-decision path."""
import importlib.util
import os
import time
from pathlib import Path
from .base import BackendCapabilities, BackendHealth, DecisionResult
from .laya import _question_to_laya, _answer_from_laya


def existing_transport():
    path = Path(__file__).resolve().parents[3] / 'scripts/race-omp-backends.py'
    spec = importlib.util.spec_from_file_location('z0int_existing_typesafe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.load_env(module.ENV_PATH)
    return module


class JevBackend:
    def __init__(self):
        self.transport = existing_transport()

    @property
    def capabilities(self):
        return BackendCapabilities(id='jev_typesafe', local=False, trainable=False,
            supports_boolean=True, supports_choice=True, supports_score=True,
            max_choice_options=255, max_score_levels=10)

    def health(self, *, load=False):
        configured = bool(os.environ.get('TYPESAFE_API_KEY'))
        return BackendHealth(id='jev_typesafe', configured=configured, ready=configured,
            model='jev-1.13.0', detail='Credential presence only; live readiness requires execution')

    def evaluate(self, request):
        if not self.health().configured:
            raise RuntimeError('TypeSafe credential unavailable in existing host config')
        start = time.monotonic()
        raw = self.transport.post(self.transport.TYPESAFE + '/v1/systemone',
            {'model': 'jev-1.13.0', 'state': request.state,
             'questions': {q.id: _question_to_laya(q) for q in request.questions}},
            {'Authorization': 'Bearer ' + os.environ['TYPESAFE_API_KEY']}, 15)
        if raw.get('model') != 'jev-1.13.0':
            raise ValueError('Unexpected TypeSafe model revision')
        answers = []
        for q in request.questions:
            answer = raw['answers'][q.id]
            if q.type == 'choice' and set(answer.get('probabilities', {})) != {o.id for o in q.options}:
                raise ValueError('Invalid choice distribution')
            if q.type == 'boolean' and 'noul' not in answer:
                raise ValueError('Missing boolean probability')
            answers.append(_answer_from_laya(q, answer))
        return DecisionResult(backend='jev_typesafe', model=raw['model'], revision=raw['model'],
            answers=tuple(answers), latency_ms=(time.monotonic()-start)*1000,
            diagnostics={'input_tokens': raw.get('usage', {}).get('input_tokens'),
                         'output_tokens': raw.get('usage', {}).get('output_tokens'),
                         'usage_source': 'provider_response'})
