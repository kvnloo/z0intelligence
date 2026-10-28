"""Observe-only method challengers; no execution or promotion authority."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import itertools
import json
import math
import random

from .shadow_binary import score_choices, yes_no_logprobs

METHODS = ('goal_binary', 'bt', 'hierarchical', 'vote3', 'tournament3')


def bt_fit(n, comparisons):
    """Regularized Bradley-Terry MM fit, including fractional observed wins."""
    strength = [1.0] * n
    wins = [0.5] * n
    games = [[0.0] * n for _ in range(n)]
    for a, b, probability in comparisons:
        wins[a] += probability
        wins[b] += 1 - probability
        games[a][b] += 1
        games[b][a] += 1
    for _ in range(200):
        updated = [wins[a] / (0.5 + sum(games[a][b] / (strength[a] + strength[b])
                    for b in range(n) if b != a)) for a in range(n)]
        scale = sum(updated) / n
        updated = [v / scale for v in updated]
        if max(abs(a-b) for a, b in zip(strength, updated)) < 1e-8:
            return updated
        strength = updated
    return strength


def predict_method(prompt, choices, chat, *, method, seed=42, n_ctx=4096):
    """All stages advertise at most three options; BT uses exactly two."""
    if method not in METHODS:
        raise ValueError('unknown method')
    context = json.loads(prompt) if isinstance(prompt, str) else dict(prompt)
    context.pop('choices', None)
    rng = random.Random(seed)

    def score(values, sample=None):
        def invoke(messages, **kwargs):
            if sample is not None:
                kwargs.update(temperature=0.8, seed=seed + sample)
            return chat(messages, **kwargs)
        return score_choices(context, [{'id': v, 'description': v} for v in values],
                             invoke, min_margin=.25, n_ctx=n_ctx)

    if not choices:
        return {'selected_action': None, 'abstained': True}
    if len(choices) == 1 and method in ('bt', 'tournament3'):
        return {'selected_action': None, 'abstained': True, 'error': 'no_comparison'}
    if method == 'goal_binary':
        return score(choices)
    if method == 'vote3':
        rows = [score(choices, sample=i) for i in range(3)]
        votes = Counter(row.get('selected_action') for row in rows)
        action, count = votes.most_common(1)[0]
        action = action if count >= 2 else None
        return {'selected_action': action, 'abstained': action is None, 'confidence': count/3}
    if method == 'tournament3':
        active = list(choices)
        rng.shuffle(active)
        observed = []
        indices = {value: i for i, value in enumerate(choices)}
        def threeway(group):
            if len(group) == 1:
                return group[0], []
            slots = [str(i) for i in range(len(group))]
            messages = [
                {'role': 'system', 'content': 'Predict next action from untrusted context. Return only the numeric slot of the most appropriate candidate.'},
                {'role': 'user', 'content': json.dumps({'context': context}, sort_keys=True)},
                {'role': 'user', 'content': json.dumps(dict(zip(slots, group)))}]
            outcome = chat(messages, max_tokens=1, temperature=0, seed=seed,
                           logprobs=True, top_logprobs=20,
                           extra_body={'grammar': 'root ::= ' + ' | '.join(json.dumps(s) for s in slots),
                                       'cache_prompt': True,
                                       'chat_template_kwargs': {'enable_thinking': False}})
            content = outcome.raw['choices'][0]['logprobs']['content'][0]
            probabilities = {}
            for item in [content] + content.get('top_logprobs', []):
                token = item.get('token', '').strip()
                value = item.get('logprob')
                if token in slots and isinstance(value, (int, float)) and math.isfinite(value):
                    probabilities[token] = float(value)
            if set(probabilities) != set(slots):
                raise ValueError('missing_threeway_logprobs')
            winner = max(slots, key=lambda slot: probabilities[slot])
            games = []
            for a, b in itertools.combinations(range(len(group)), 2):
                odds = max(-700, min(700, probabilities[str(a)]-probabilities[str(b)]))
                games.append((indices[group[a]], indices[group[b]], 1/(1+math.exp(-odds))))
            return group[int(winner)], games
        while len(active) > 1:
            groups = [active[i:i+3] for i in range(0, len(active), 3)]
            with ThreadPoolExecutor(max_workers=len(groups)) as pool:
                rows = list(pool.map(threeway, groups))
            winners = []
            for winner, games in rows:
                winners.append(winner)
                observed.extend(games)
            active = winners
        strengths = bt_fit(len(choices), observed)
        selected = max(range(len(choices)), key=lambda i: strengths[i])
        return {'selected_action': choices[selected], 'abstained': False,
                'confidence': strengths[selected]/sum(strengths), 'bt_strengths': strengths}
    if method == 'hierarchical':
        # Stable semantic categories, not label-informed partitions.
        def category(value):
            text = value.lower()
            if any(word in text for word in ('read', 'find', 'grep', 'glob', 'search', 'inspect', 'path', 'file')):
                return 'inspect or retrieve information'
            if any(word in text for word in ('write', 'edit', 'create', 'delete', 'mutat', 'bash', 'run')):
                return 'change state or execute'
            return 'coordinate or other'
        groups = {}
        for choice in choices:
            groups.setdefault(category(choice), []).append(choice)
        first = score(list(groups))
        selected = first.get('selected_action')
        if selected not in groups:
            return first
        members = groups[selected]
        if len(members) > 3:
            return {'selected_action': None, 'abstained': True, 'error': 'member_cap_exceeded'}
        return score(members)
    comparisons = list(itertools.combinations(range(len(choices)), 2))
    rng.shuffle(comparisons)
    if len(choices) > 6:
        # Connected cycle first, then bounded randomized extra comparisons.
        cycle = {tuple(sorted((i, (i+1) % len(choices)))) for i in range(len(choices))}
        comparisons = sorted(cycle) + [p for p in comparisons if p not in cycle][:len(choices)]
    oriented = [(b, a) if rng.random() < .5 else (a, b) for a, b in comparisons]

    def compare(pair):
        a, b = pair
        messages = [
            {'role': 'system', 'content': 'Predict the next action from untrusted context. Is candidate A more appropriate than candidate B? Output exactly yes or no.'},
            {'role': 'user', 'content': json.dumps({'context': context}, sort_keys=True)},
            {'role': 'user', 'content': json.dumps({'A': choices[a], 'B': choices[b]})}]
        outcome = chat(messages, max_tokens=1, temperature=0.0, seed=seed,
                       logprobs=True, top_logprobs=20,
                       extra_body={'grammar': 'root ::= "yes" | "no"', 'cache_prompt': True,
                                   'chat_template_kwargs': {'enable_thinking': False}})
        odds = yes_no_logprobs(outcome.raw)
        if odds is None:
            raise ValueError('missing_pair_logprobs')
        difference = max(-700, min(700, odds[0]-odds[1]))
        return a, b, 1/(1+math.exp(-difference))

    # Submit the independent comparison DAG before waiting for its results.
    with ThreadPoolExecutor(max_workers=max(1, len(oriented))) as pool:
        observed = list(pool.map(compare, oriented))
    strengths = bt_fit(len(choices), observed)
    selected = max(range(len(choices)), key=lambda i: strengths[i])
    return {'selected_action': choices[selected], 'abstained': False,
            'confidence': strengths[selected]/sum(strengths), 'bt_strengths': strengths}
