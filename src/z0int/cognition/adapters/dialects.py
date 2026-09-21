"""Per-model tool-call dialects.

Every model-specific quirk (prompt shape, tool schema rendering, response
parsing, structured-output mode) lives here and nowhere else. The rest of the
stack talks to :class:`~z0int.cognition.adapters.local_slm.LocalSLMBackend` and
never sees a Qwen/Nemotron/Hammer/FunctionGemma template.

Adding a model means adding a dialect, not editing the controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import json
import re


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of one JSON object from free-form model output."""
    if not text:
        return None
    candidates: list[str] = []
    candidates.extend(_FENCE_RE.findall(text))
    candidates.append(text)
    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        # Balanced-brace scan.
        depth = 0
        start = -1
        for i, ch in enumerate(cand):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        parsed = json.loads(cand[start : i + 1])
                    except json.JSONDecodeError:
                        start = -1
                        continue
                    if isinstance(parsed, dict):
                        return parsed
                    start = -1
    return None


def render_action_tools(actions: Sequence[Any]) -> list[dict[str, Any]]:
    """Render legal actions as OpenAI-style tool schemas.

    Non-tool actions (the model/control rungs of the cascade) are also advertised
    as callable pseudo-tools, so one tool-calling surface covers the whole
    cascade. That is what "model-as-tool orchestration" means operationally.
    """
    tools: list[dict[str, Any]] = []
    for action in actions:
        schema = getattr(action, "arguments_schema", None)
        if schema is None:
            schema = {"type": "object", "properties": {}, "additionalProperties": False}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": getattr(action, "action_id"),
                    "description": getattr(action, "description"),
                    "parameters": schema,
                },
            }
        )
    return tools


def render_choice_prompt(
    *,
    state: str,
    actions: Sequence[Any],
    objective: str | None = None,
    risk_class: str | None = None,
    constraints: Mapping[str, Any] | None = None,
) -> str:
    lines: list[str] = [
        "You are a bounded decision component. You may ONLY choose one of the "
        "action ids listed below. You may not invent, rename or combine actions.",
        "",
    ]
    if objective:
        lines.append(f"Objective: {objective}")
    if risk_class:
        lines.append(f"Risk class: {risk_class}")
    if constraints:
        lines.append(f"Constraints: {json.dumps(dict(constraints), sort_keys=True)}")
    lines.extend(["", "Situation:", state.strip(), "", "Legal actions:"])
    for action in actions:
        fam = getattr(action, "family", None)
        suffix = f" [family={fam}]" if fam else ""
        lines.append(
            f"- {getattr(action, 'action_id')}: {getattr(action, 'description')}{suffix}"
        )
    ids = ", ".join(str(getattr(a, "action_id")) for a in actions)
    lines.extend(
        [
            "",
            f"Allowed ids: {ids}",
            'Reply with ONLY a JSON object: {"action_id": "<one allowed id>", '
            '"confidence": <0..1>}',
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class ParsedInvocation:
    tool: str
    arguments: dict[str, Any]
    confidence: float | None = None
    source: str = "tool_call"
    extra_calls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "confidence": self.confidence,
            "source": self.source,
        }


def _parse_arg_blob(blob: str | None) -> dict[str, Any]:
    if not blob:
        return {}
    text = blob.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    # FunctionGemma: call:NAME{key:<escape>value<escape>, ...}
    args: dict[str, Any] = {}
    for key, value in re.findall(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:<escape>)?(.*?)(?:<escape>)?\s*(?=,|}|$)", text
    ):
        args[key] = value
    return args


@dataclass(frozen=True)
class ToolDialect:
    """How one model family exposes tool calls."""

    name: str
    use_native_tools: bool = True
    use_json_schema: bool = False
    tool_choice: str | None = "required"
    temperature: float = 0.0
    max_tokens: int = 256
    # Ordered text patterns used when the server does NOT return a parsed
    # ``tool_calls`` array. Group 1 must be the tool name; optional group 2 is
    # the raw argument blob.
    call_patterns: tuple[re.Pattern[str], ...] = ()
    # Patterns whose group 1 is a *JSON object* holding the call
    # (Hermes/Nemotron emit ``<tool_call>{"name": ..., "arguments": {...}}</tool_call>``).
    json_blob_patterns: tuple[re.Pattern[str], ...] = ()
    # Server-side stop sequences. Without these a small function-calling model
    # keeps generating after its call and burns the latency budget.
    stop: tuple[str, ...] = ()

    def render(self, *, state: str, actions, objective=None, risk_class=None, constraints=None):
        return render_choice_prompt(
            state=state,
            actions=actions,
            objective=objective,
            risk_class=risk_class,
            constraints=constraints,
        )

    def tools_payload(self, actions) -> list[dict[str, Any]] | None:
        return render_action_tools(actions) if self.use_native_tools else None

    def response_format(self, schema: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not self.use_json_schema or schema is None:
            return None
        return {"type": "json_schema", "json_schema": {"name": "decision", "schema": dict(schema)}}

    def parse(
        self,
        *,
        content: str,
        tool_call: dict[str, Any] | None,
        allowed_ids: Sequence[str],
    ) -> ParsedInvocation | None:
        allowed = set(allowed_ids)
        text = content or ""

        # 1. A server-parsed native tool call is authoritative.
        if tool_call:
            fn = (tool_call.get("function") or {}) if isinstance(tool_call, dict) else {}
            name = str(fn.get("name") or "")
            raw_args = fn.get("arguments")
            args: dict[str, Any] = {}
            if isinstance(raw_args, str) and raw_args.strip():
                try:
                    parsed = json.loads(raw_args)
                    if isinstance(parsed, dict):
                        args = parsed
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(raw_args, dict):
                args = dict(raw_args)
            if name:
                return ParsedInvocation(tool=name, arguments=args, source="tool_call")

        # 2. Model-family text syntax. The FIRST emitted call wins: a bounded
        #    single-choice decision cannot be answered by a later call, and
        #    preferring the longest id would silently favour verbose names.
        for pattern in self.call_patterns:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            first = matches[0]
            groups = first.groups()
            name = (groups[0] or "").strip() if groups else ""
            if not name:
                continue
            args = _parse_arg_blob(groups[1] if len(groups) > 1 else None)
            others = tuple(
                m.group(1) for m in matches[1:] if m.groups() and m.group(1) and m.group(1) != name
            )
            return ParsedInvocation(
                tool=name, arguments=args, source=f"text:{self.name}", extra_calls=others
            )

        # 3. Hermes/Nemotron-style ``<tool_call>{...}</tool_call>`` JSON blob.
        for pattern in self.json_blob_patterns:
            for match in pattern.finditer(text):
                obj = extract_json_object(match.group(1) or "")
                if not obj:
                    continue
                name = obj.get("name") or obj.get("action_id") or obj.get("tool")
                if not name:
                    continue
                args = obj.get("arguments") if isinstance(obj.get("arguments"), dict) else {}
                conf = obj.get("confidence")
                return ParsedInvocation(
                    tool=str(name),
                    arguments=dict(args),
                    confidence=float(conf) if isinstance(conf, (int, float)) else None,
                    source=f"text:{self.name}",
                )

        # 4. A JSON object naming an allowed action.
        obj = extract_json_object(text)
        if obj:
            name = obj.get("action_id") or obj.get("tool") or obj.get("name")
            conf = obj.get("confidence")
            if name is not None and str(name) in allowed:
                args = obj.get("arguments") if isinstance(obj.get("arguments"), dict) else {}
                confidence = float(conf) if isinstance(conf, (int, float)) else None
                return ParsedInvocation(
                    tool=str(name), arguments=dict(args), confidence=confidence, source="json"
                )

        # 5. Last resort: an allowed id mentioned as bare text; earliest wins.
        best: tuple[int, str] | None = None
        for candidate in allowed:
            match = re.search(re.escape(candidate), text)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), candidate)
        if best is not None:
            return ParsedInvocation(tool=best[1], arguments={}, source="substring")
        return None


# --- concrete dialects -------------------------------------------------

# Hermes / Qwen3-style XML wrapper around a JSON object (Nemotron-Orchestrator).
_HERMES_BLOB = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
# Qwen3.5 native XML: <tool_call><function=NAME><parameter=K>v</parameter></function></tool_call>
_QWEN35_CALL = re.compile(r"<function=([\w.\-]+)\s*>", re.DOTALL)
# FunctionGemma: <start_function_call>call:NAME{...}<end_function_call>
_FUNCTIONGEMMA_CALL = re.compile(
    r"<start_function_call>\s*call:\s*([A-Za-z_][\w.\-]*)\s*(\{.*?\})?\s*<end_function_call>",
    re.DOTALL,
)
# Hammer2.1 emits a bare JSON array: [{"name": ..., "arguments": {...}}]
_HAMMER_ARRAY = re.compile(
    r'\[\s*\{\s*"name"\s*:\s*"([^"]+)"[^}]*?"arguments"\s*:\s*(\{.*?\})\s*\}', re.DOTALL
)

HERMES_DIALECT = ToolDialect(
    name="hermes",
    use_native_tools=True,
    tool_choice="required",
    call_patterns=(_HAMMER_ARRAY,),
    json_blob_patterns=(_HERMES_BLOB,),
    stop=("</tool_call>", "<start_function_response>"),
)
QWEN_DIALECT = ToolDialect(
    name="qwen",
    use_native_tools=True,
    tool_choice="required",
    # Qwen3.5 has thinking on by default.
    max_tokens=1024,
    call_patterns=(_QWEN35_CALL,),
    json_blob_patterns=(_HERMES_BLOB,),
    stop=("</tool_call>", "<start_function_response>"),
)
NEMOTRON_DIALECT = ToolDialect(
    name="nemotron",
    use_native_tools=True,
    tool_choice="required",
    # Nemotron-Orchestrator emits a <think> block before its call; the measured
    # failure mode at 256 tokens was an EMPTY content field with the whole
    # budget spent on reasoning.
    max_tokens=1024,
    json_blob_patterns=(_HERMES_BLOB,),
    stop=("</tool_call>", "<start_function_response>", "<tool_response>"),
)
HAMMER_DIALECT = ToolDialect(
    name="hammer",
    use_native_tools=True,
    tool_choice="auto",
    max_tokens=384,
    call_patterns=(_HAMMER_ARRAY,),
    json_blob_patterns=(_HERMES_BLOB,),
    stop=("</tool_call>", "\n```"),
)
FUNCTIONGEMMA_DIALECT = ToolDialect(
    name="functiongemma",
    use_native_tools=True,
    use_json_schema=False,
    tool_choice="required",
    max_tokens=128,
    call_patterns=(_FUNCTIONGEMMA_CALL,),
    stop=("<end_function_call>", "<start_function_response>"),
)
PLAIN_JSON_DIALECT = ToolDialect(
    name="plain_json", use_native_tools=False, use_json_schema=True, max_tokens=128
)

DIALECTS: dict[str, ToolDialect] = {
    d.name: d
    for d in (
        HERMES_DIALECT,
        QWEN_DIALECT,
        NEMOTRON_DIALECT,
        HAMMER_DIALECT,
        FUNCTIONGEMMA_DIALECT,
        PLAIN_JSON_DIALECT,
    )
}


def dialect_for(tool_parser: str | None, tool_call_template: str | None = None) -> ToolDialect:
    """Resolve a dialect from a manifest row, defaulting to the Hermes shape."""
    for key in (tool_parser, tool_call_template):
        if not key:
            continue
        lowered = str(key).strip().lower()
        for name, dialect in DIALECTS.items():
            if name in lowered:
                return dialect
    return HERMES_DIALECT
