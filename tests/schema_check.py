"""A minimal JSON Schema (2020-12) checker for the keywords ``worker/wiki_index.schema.json`` uses.

``jsonschema`` is not installed in this project. This covers exactly the keywords of that
schema and raises on any other keyword, so a later schema change cannot be ignored silently.
Patterns are searched (not anchored), as JSON Schema specifies.
"""

from __future__ import annotations

import re
from typing import Any

_ANNOTATIONS = {"$schema", "$id", "title", "description", "$defs"}
_KEYWORDS = {
    "$ref", "type", "const", "enum", "pattern", "minLength", "minimum", "required",
    "properties", "additionalProperties", "items", "uniqueItems",
}  # fmt: skip
_TYPES: dict[str, Any] = {
    "object": dict, "array": list, "string": str, "null": type(None), "boolean": bool,
}  # fmt: skip


def _is_type(value: Any, name: str) -> bool:
    if name in ("integer", "number"):
        numeric = int if name == "integer" else (int, float)
        return isinstance(value, numeric) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], where: str = "$") -> list[str]:
    """Every violation of ``schema`` (resolved against ``root``) by ``value``."""
    unknown = set(schema) - _ANNOTATIONS - _KEYWORDS
    if unknown:
        raise ValueError(f"schema_check does not support the keyword(s) {sorted(unknown)} at {where}")
    errors: list[str] = []
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        errors += schema_errors(value, target, root, where)
    if "type" in schema:
        types = [schema["type"]] if isinstance(schema["type"], str) else schema["type"]
        if not any(_is_type(value, name) for name in types):
            return [*errors, f"{where}: expected type {types}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: shorter than {schema['minLength']}")
    if _is_type(value, "number") and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{where}: below {schema['minimum']}")
    if isinstance(value, dict):
        errors += [f"{where}: missing {key!r}" for key in schema.get("required", []) if key not in value]
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors += schema_errors(item, properties[key], root, f"{where}.{key}")
            elif extra is False:
                errors.append(f"{where}: unexpected key {key!r}")
            elif isinstance(extra, dict):
                errors += schema_errors(item, extra, root, f"{where}.{key}")
    if isinstance(value, list):
        if "items" in schema:
            for index, item in enumerate(value):
                errors += schema_errors(item, schema["items"], root, f"{where}[{index}]")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            errors.append(f"{where}: items are not unique")
    return errors
