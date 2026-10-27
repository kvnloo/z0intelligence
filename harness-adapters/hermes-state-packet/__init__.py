"""Independent read-only orientation. Leave the routing adapter untouched."""
import importlib.util
from pathlib import Path

_reader = Path(__file__).resolve().parents[2] / 'src/z0int/orientation.py'
_spec = importlib.util.spec_from_file_location('z0_state_packet_orientation', _reader)
_orientation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_orientation)


def callback(index):
    def before_turn(**_):
        parts = _orientation.chunks(_orientation.context())
        return {'context': parts[index]} if index < len(parts) else None
    return before_turn


def _text(value):
    """User message text from a str, a content-part list ({'type': 'text', 'text': ...}) or a message dict."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _text(value.get('content') if 'content' in value else value.get('text'))
    if isinstance(value, (list, tuple)):
        return ' '.join(_text(part) for part in value)
    return ''


def corrections_hook(user_message=None, conversation_history=None, **_):
    prompt = _text(user_message)
    if not prompt.strip() and conversation_history:
        last = [m for m in conversation_history if isinstance(m, dict) and m.get('role') == 'user']
        prompt = _text(last[-1]) if last else ''
    text = _orientation.corrections(prompt)
    return {'context': text} if text else None


def register(ctx):
    for index in range(10):
        ctx.register_hook('pre_llm_call', callback(index))
    ctx.register_hook('pre_llm_call', corrections_hook)
