"""Law F: same-turn consultation before CHECK/CONTRACT actions. Report only."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


ACTIONS = frozenset(('design', 'delegation', 'edit'))


def evaluate(facts):
    """Finite Python twin of bend/consultation_fsm/consultation.bend."""
    if not isinstance(facts, dict) or facts.get('available') is not True:
        return 'unknown'
    applicable, consulted = facts.get('applicable'), facts.get('consulted')
    if type(applicable) is not bool or type(consulted) is not bool:
        return 'unknown'
    return 'violation' if applicable and not consulted else 'pass'


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Consultation timestamp must include a timezone')
    return parsed


def valid_receipt(row, action):
    try:
        return (row.get('schema') == 'z0.consultation.v1' and row.get('status') == 'ok'
                and all(isinstance(row.get(key), str) and row[key] for key in ('query', 'turn', 'session', 'sourceHash'))
                and all(row[key] == action[key] for key in ('turn', 'session', 'sourceHash'))
                and isinstance(row.get('hits'), list) and len(row['hits']) <= 12
                and all(isinstance(hit, dict) and isinstance(hit.get('source'), str) and hit['source'] for hit in row['hits'])
                and timestamp(row['ts']) <= timestamp(action['ts']))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def audit(rows):
    receipts = []
    checks = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('Consultation journal rows must be objects')
        if row.get('schema') == 'z0.consultation.v1':
            receipts.append(row)
        elif row.get('schema') == 'z0.consultation_action.v1':
            complete = (row.get('tier') in ('FAST', 'CHECK', 'CONTRACT') and row.get('action') in ACTIONS
                        and all(isinstance(row.get(key), str) and row[key] for key in ('session', 'turn', 'sourceHash', 'ts')))
            try:
                timestamp(row['ts'])
            except (KeyError, TypeError, ValueError, AttributeError):
                complete = False
            consulted = any(valid_receipt(receipt, row) for receipt in receipts)
            facts = {'available': bool(complete), 'applicable': row.get('tier') in ('CHECK', 'CONTRACT'), 'consulted': consulted}
            status = evaluate(facts)
            checks.append({'id': f"F:{index}:{row.get('turn')}", 'law': 'F', 'status': status,
                           'blocked': status != 'pass', 'facts': facts,
                           'reason': 'Same-turn consultation precedes the design/delegation/edit action',
                           'evidence': {'action': row, 'matching_receipt_count': sum(valid_receipt(receipt, row) for receipt in receipts)},
                           'escalate_to': 'Kevin' if status == 'unknown' else None})
    if not checks:
        checks.append({'id': 'F:empty-journal', 'law': 'F', 'status': 'unknown', 'blocked': True,
                       'facts': {'available': False}, 'reason': 'No action observations declared', 'evidence': {}, 'escalate_to': 'Kevin'})
    return checks


def command(args):
    from .state_laws import report
    try:
        path = Path(args.events).expanduser()
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        result = report(audit(rows), source=path)
    except (OSError, ValueError, TypeError) as exc:
        result = report([{'id': 'F:unavailable', 'law': 'F', 'status': 'unknown', 'blocked': True,
                          'facts': {'available': False}, 'reason': str(exc), 'evidence': {}, 'escalate_to': 'Kevin'}], source=args.events)
    print(json.dumps(result, sort_keys=True))
    return 3 if result['counts']['violation'] else 4 if result['counts']['unknown'] else 0
