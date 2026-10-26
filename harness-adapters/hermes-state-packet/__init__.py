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


def corrections_hook(user_message=None, **_):
    text = _orientation.corrections(user_message if isinstance(user_message, str) else '')
    return {'context': text} if text else None


def register(ctx):
    for index in range(10):
        ctx.register_hook('pre_llm_call', callback(index))
    ctx.register_hook('pre_llm_call', corrections_hook)
