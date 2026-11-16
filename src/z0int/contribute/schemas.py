"""Load and check the contributor-layer JSON Schemas (``schemas/*.schema.json``).

The validator is a strict stdlib subset of JSON Schema draft 2020-12. It implements
only the keywords the contributor schemas use. Any other keyword raises
:class:`SchemaError` instead of being ignored, so an unsupported rule can never pass
silently. ``tests/test_contribute_schemas.py`` checks every verdict against the
reference ``jsonschema`` package when it is installed.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_NAMES = ("priorities", "vocab", "contribute.state", "suggestion", "contrib.receipt", "profile")

# Annotation-only keywords: allowed, no effect on the verdict.
_ANNOTATIONS = {"$schema", "$id", "$defs", "title", "description", "default", "examples", "$comment"}
_SUPPORTED = _ANNOTATIONS | {
    "type", "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum",
    "exclusiveMinimum", "required", "properties", "additionalProperties", "items", "minItems",
    "maxItems", "uniqueItems", "$ref", "oneOf", "anyOf", "allOf", "if", "then", "else", "not",
}


class SchemaError(ValueError):
    """The schema itself uses something this validator does not implement."""


def schemas_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "schemas"
        if (cand / "priorities.schema.json").is_file():
            return cand
    cand = Path.cwd() / "schemas"
    if (cand / "priorities.schema.json").is_file():
        return cand
    raise FileNotFoundError("schemas/priorities.schema.json not found (use an editable install of the checkout)")


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    if name not in SCHEMA_NAMES:
        raise KeyError(f"unknown contributor schema: {name}")
    return (schemas_dir() / f"{name}.schema.json").read_text(encoding="utf-8")


def load_schema(name: str) -> dict[str, Any]:
    return json.loads(_load(name))


def _type_ok(value: Any, t: str) -> bool:
    if t == "null":
        return value is None
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool) or (
            isinstance(value, float) and value.is_integer()
        )
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    raise SchemaError(f"unsupported type {t!r}")


def _json_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"only local $ref supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(value: Any, schema: Any, root: dict[str, Any], path: str, errors: list[str]) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append(f"{path}: not allowed")
        return
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise SchemaError(f"unsupported schema keyword(s) {sorted(unknown)} at {path}")
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), root, path, errors)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{path}: expected {'|'.join(types)}")
            return
    if "const" in schema and not _json_eq(value, schema["const"]):
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and not any(_json_eq(value, e) for e in schema["enum"]):
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                _check(value[key], sub, root, f"{path}.{key}", errors)
        if "additionalProperties" in schema:
            extra = [k for k in value if k not in props]
            ap = schema["additionalProperties"]
            for key in extra:
                if ap is False:
                    errors.append(f"{path}: unexpected key {key!r}")
                elif ap is not True:
                    _check(value[key], ap, root, f"{path}.{key}", errors)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen: list[str] = []
            for item in value:
                key = json.dumps(item, sort_keys=True)
                if key in seen:
                    errors.append(f"{path}: duplicate item {item!r}")
                seen.append(key)
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], root, f"{path}[{i}]", errors)
    for sub in schema.get("allOf", []):
        _check(value, sub, root, path, errors)
    if "anyOf" in schema:
        if not any(not _collect(value, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{path}: matches none of anyOf")
    if "oneOf" in schema:
        hits = sum(1 for s in schema["oneOf"] if not _collect(value, s, root, path))
        if hits != 1:
            errors.append(f"{path}: matches {hits} of oneOf (need exactly 1)")
    if "not" in schema and not _collect(value, schema["not"], root, path):
        errors.append(f"{path}: matches a forbidden schema")
    if "if" in schema:
        branch = "then" if not _collect(value, schema["if"], root, path) else "else"
        if branch in schema:
            _check(value, schema[branch], root, path, errors)


def _collect(value: Any, schema: Any, root: dict[str, Any], path: str) -> list[str]:
    errs: list[str] = []
    _check(value, schema, root, path, errs)
    return errs


def validate_instance(value: Any, schema: dict[str, Any]) -> list[str]:
    """Errors for ``value`` against an in-memory ``schema`` (empty list = valid)."""
    return _collect(value, schema, schema, "$")


def validate(value: Any, name: str) -> list[str]:
    """Errors for ``value`` against ``schemas/<name>.schema.json`` (empty list = valid)."""
    return validate_instance(value, load_schema(name))
