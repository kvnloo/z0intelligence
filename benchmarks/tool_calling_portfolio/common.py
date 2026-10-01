"""Shared pieces for the tool-calling portfolio bench (z0intelligence#20).

Adapters: one parser chain turns a llama.cpp /v1/chat/completions response into a
list of {name, arguments} calls. Native ``message.tool_calls`` (the GGUF's own jinja
template + llama.cpp's parser) wins; only when it is empty do model-format fallbacks
read ``message.content``. Which path produced the calls is recorded per row.

Scoring is pure: ``score_item(item, calls)`` never looks at latency or model name.
"""
from __future__ import annotations

import difflib
import itertools
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = Path('~/.z0int/research/tool_calling_portfolio').expanduser()
ITEMS_PRIVATE = PRIVATE / 'items_v0.jsonl'

# ------------------------------------------------------------------ schemas

_BFCL_TYPES = {'dict': 'object', 'float': 'number', 'tuple': 'array', 'integer': 'integer', 'string': 'string',
               'boolean': 'boolean', 'array': 'array', 'object': 'object', 'number': 'number'}


def bfcl_schema(node):
    """BFCL parameter dialect -> JSON Schema (same mapping BFCL applies for OpenAI FC models)."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == 'type':
                t = _BFCL_TYPES.get(v)
                if t:
                    out['type'] = t
            elif k in ('properties',):
                out[k] = {pk: bfcl_schema(pv) for pk, pv in v.items()}
            elif k in ('items', 'additionalProperties'):
                out[k] = bfcl_schema(v)
            else:
                out[k] = v
        return out
    return node


def tool_name(name):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)


def bfcl_tools(functions):
    tools, back = [], {}
    for f in functions:
        n = tool_name(f['name'])
        back[n] = f['name']
        tools.append({'type': 'function', 'function': {'name': n, 'description': f.get('description', ''),
                                                       'parameters': bfcl_schema(f['parameters'])}})
    return tools, back


# ------------------------------------------------------------------ adapters

def _loads(s):
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


def _norm_call(obj):
    if not isinstance(obj, dict):
        return None
    if 'function' in obj and isinstance(obj['function'], dict):
        obj = obj['function']
    name = obj.get('name')
    args = obj.get('arguments', obj.get('parameters', {}))
    if isinstance(args, str):
        args = _loads(args)
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    return {'name': name, 'arguments': args}


def _json_values(text):
    dec = json.JSONDecoder()
    i = 0
    while i < len(text):
        j = min([p for p in (text.find('{', i), text.find('[', i)) if p >= 0], default=-1)
        if j < 0:
            return
        try:
            v, end = dec.raw_decode(text, j)
            yield v
            i = end
        except ValueError:
            i = j + 1


def _fg_value(s):
    s = s.strip()
    m = re.fullmatch(r'<escape>(.*?)<escape>', s, re.S)
    if m:
        return m.group(1)
    try:
        return json.loads(s)
    except ValueError:
        return s


def _fg_args(body):
    """FunctionGemma: call:name{key:<escape>str<escape>,n:3}."""
    out, depth, cur, parts, esc = {}, 0, '', [], False
    for ch_i, ch in enumerate(body):
        if body.startswith('<escape>', ch_i):
            esc = not esc
        if ch in '{[' and not esc:
            depth += 1
        elif ch in '}]' and not esc:
            depth -= 1
        if ch == ',' and depth == 0 and not esc:
            parts.append(cur)
            cur = ''
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    for p in parts:
        if ':' in p:
            k, v = p.split(':', 1)
            out[k.strip()] = _fg_value(v)
    return out


def parse_content(content):
    """Fallback parsers over raw text, in a fixed order. Returns (calls, path)."""
    text = re.sub(r'<think>.*?</think>', '', content or '', flags=re.S).strip()
    if not text:
        return [], 'none'
    calls = []
    for m in re.finditer(r'<tool_call>\s*(.*?)\s*(?:</tool_call>|$)', text, re.S):  # hermes / qwen
        for v in _json_values(m.group(1)):
            c = _norm_call(v)
            if c:
                calls.append(c)
    if calls:
        return calls, 'content_hermes'
    for m in re.finditer(r'<start_function_call>\s*call:([\w.\-]+)\{(.*?)\}\s*<end_function_call>', text, re.S):
        calls.append({'name': m.group(1), 'arguments': _fg_args(m.group(2))})
    if calls:
        return calls, 'content_functiongemma'
    body = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    for v in _json_values(body):  # hammer: JSON array of {name, arguments}; also bare objects
        items = v if isinstance(v, list) else [v.get('tool_calls', v)] if isinstance(v, dict) else []
        flat = []
        for it in items:
            flat.extend(it if isinstance(it, list) else [it])
        for it in flat:
            c = _norm_call(it)
            if c:
                calls.append(c)
        if calls:
            return calls, 'content_json'
    return [], 'none'


def extract_calls(response):
    msg = (response.get('choices') or [{}])[0].get('message') or {}
    native = [c for c in (_norm_call(tc) for tc in (msg.get('tool_calls') or [])) if c]
    if native:
        return native, 'native'
    return parse_content(msg.get('content') or '')


# ------------------------------------------------------------------ validation

_PY = {'object': dict, 'array': list, 'string': str, 'boolean': bool}


def _type_ok(t, v):
    if t == 'integer':
        return isinstance(v, int) and not isinstance(v, bool)
    if t == 'number':
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == 'null':
        return v is None
    return isinstance(v, _PY.get(t, object))


def validate(schema, v):
    """Minimal JSON-schema check: type, required, properties, additionalProperties=false, enum, items, min/max."""
    t = schema.get('type')
    if isinstance(t, list):
        if not any(_type_ok(x, v) for x in t):
            return False
    elif t and not _type_ok(t, v):
        return False
    if 'enum' in schema and v not in schema['enum']:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 'minimum' in schema and v < schema['minimum']:
            return False
        if 'maximum' in schema and v > schema['maximum']:
            return False
    if isinstance(v, dict):
        props = schema.get('properties', {})
        if any(r not in v for r in schema.get('required', [])):
            return False
        if schema.get('additionalProperties') is False and any(k not in props for k in v):
            return False
        for k, sub in props.items():
            if k in v and isinstance(sub, dict) and not validate(sub, v[k]):
                return False
    if isinstance(v, list) and isinstance(schema.get('items'), dict):
        return all(validate(schema['items'], x) for x in v)
    return True


def args_valid(calls, tools):
    """Every emitted call names an offered tool and its arguments validate against that tool's schema."""
    by = {t['function']['name']: t['function']['parameters'] for t in tools}
    return bool(calls) and all(c['name'] in by and validate(by[c['name']], c['arguments']) for c in calls)


# ------------------------------------------------------------------ BFCL AST check (subset of upstream ast_checker)

def _std(s):
    return re.sub(r"[ ,./\-_*^]", '', str(s)).lower().replace("'", '"')


def _eq(v, acceptable):
    for a in acceptable:
        if a == '' and v is None:
            return True
        if isinstance(a, bool) or isinstance(v, bool):
            if a is v or a == v and type(a) is type(v):
                return True
            continue
        if isinstance(a, (int, float)) and isinstance(v, (int, float)):
            if float(a) == float(v):
                return True
        elif isinstance(a, str) and isinstance(v, str):
            if _std(a) == _std(v):
                return True
        elif isinstance(a, list) and isinstance(v, (list, tuple)):
            if len(a) == len(v) and all(_eq(x, [y]) for x, y in zip(v, a)):
                return True
        elif isinstance(a, dict) and isinstance(v, dict):
            if set(v) <= set(a) and all(_eq(v.get(k), a[k] if isinstance(a[k], list) else [a[k]]) for k in a
                                        if k in v or '' not in (a[k] if isinstance(a[k], list) else [a[k]])):
                return True
    return False


def _call_matches(call, gt, back, functions):
    (gname, gparams), = gt.items()
    if back.get(call['name'], call['name']) != gname:
        return False
    fdef = next((f for f in functions if f['name'] == gname), None)
    props = (fdef or {}).get('parameters', {}).get('properties', {})
    req = (fdef or {}).get('parameters', {}).get('required', [])
    args = call['arguments']
    if any(r not in args for r in req) or any(k not in props for k in args):
        return False
    for k, acc in gparams.items():
        if k in args:
            if not _eq(args[k], acc):
                return False
        elif '' not in acc:
            return False
    return True


def bfcl_correct(item, calls):
    cat = item['category']
    if cat == 'irrelevance':
        return len(calls) == 0
    gts = item['ground_truth']
    if len(calls) != len(gts):
        return False
    back = item['name_back']
    for perm in itertools.permutations(range(len(gts))):
        if all(_call_matches(calls[i], gts[j], back, item['functions']) for i, j in enumerate(perm)):
            return True
    return False


# ------------------------------------------------------------------ z0 route_worker formation

def _ws(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def task_faithful(request, task):
    r, t = _ws(request), _ws(task)
    if r and r in t:
        return True
    return difflib.SequenceMatcher(None, r, t, autojunk=False).ratio() >= 0.9


def z0_route_score(item, calls):
    g = item['gold']
    out = {'one_call': len(calls) == 1}
    c = calls[0] if calls else {'name': None, 'arguments': {}}
    a = c['arguments']
    out['tool_ok'] = len(calls) == 1 and c['name'] == g['tool']
    out['ids_ok'] = a.get('trace_id') == g['trace_id'] and a.get('parent_agent') == g['parent_agent']
    out['task_ok'] = task_faithful(item['request'], a.get('task'))
    out['remote_ok'] = a.get('allow_remote', False) is False
    if g['tool'] == 'route_worker':
        out['function_ok'] = a.get('function') in g['functions']
    else:
        out['function_ok'] = a.get('provider') == g['provider'] and a.get('model') == g['model'] and bool(_ws(a.get('reason')))
    out['form_exact'] = all(out[k] for k in ('tool_ok', 'ids_ok', 'task_ok', 'remote_ok'))
    return out


def score_item(item, calls):
    """Returns {'exact': bool, ...per-suite detail}. Validity is computed separately by args_valid."""
    s = item['suite']
    if s == 'bfcl':
        ok = bfcl_correct(item, calls)
        return {'exact': ok, 'called': bool(calls)}
    if s == 'z0_route':
        d = z0_route_score(item, calls)
        d['exact'] = d['form_exact'] and d['function_ok']
        return d
    if s == 'action_selector':
        ok = len(calls) == 1 and calls[0]['name'] == item['gold']
        return {'exact': ok, 'pred': calls[0]['name'] if len(calls) == 1 else None}
    raise ValueError(s)


def load_items(path=ITEMS_PRIVATE):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
