"""JSON Schema validation for the subset RSI's contracts use.

The contracts in ``RSI/schemas/`` are the frozen interface between skills: a step's output
must pass schema validation before the next step starts, and a failed validation retries in
place rather than passing bad data downstream. That makes the validator load-bearing, so it
is dependency-free and reports every failure with a path.

Supported keywords: ``$schema``/``$id``/``title``/``description`` (metadata ignored),
``type``, ``enum``, ``const``, ``required``, ``properties``,
``additionalProperties`` (bool or schema), ``items``, ``minItems``, ``maxItems``,
``minimum``, ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``, ``minLength``,
``pattern``, ``oneOf``, ``anyOf``, ``allOf``, ``$ref`` to a local definition.

Anything else is rejected loudly at contract-load time, so a contract can never silently
degrade into a weaker check.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_KNOWN_KEYWORDS = {
    "type", "enum", "const", "required", "properties", "additionalProperties", "items",
    "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "oneOf", "anyOf", "allOf", "not", "$ref",
    "title", "description", "$schema", "$id", "default", "examples", "definitions", "$defs",
    "format", "uniqueItems", "propertyNames",
}

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
    "null": type(None),
}


class SchemaError(ValueError):
    """Raised when a contract file itself is outside the supported subset."""


class ValidationError(ValueError):
    """Raised when an instance violates one of its contract's clauses."""

    def __init__(self, errors: List[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _resolve(root: Dict[str, Any], ref: str) -> Dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"only local $ref is supported, got {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        if part not in node:
            raise SchemaError(f"unresolvable $ref {ref!r}")
        node = node[part]
    return node


def _check_keywords(schema: Any, path: str = "#") -> None:
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: a schema must be an object")
    for key in schema:
        if key not in _KNOWN_KEYWORDS:
            raise SchemaError(f"{path}: unsupported keyword {key!r}; extend jsonschema_lite deliberately")
    for key in ("properties", "$defs", "definitions"):
        if key in schema:
            for name, child in schema[key].items():
                _check_keywords(child, f"{path}/{key}/{name}")
    if isinstance(schema.get("items"), dict):
        _check_keywords(schema["items"], f"{path}/items")
    if isinstance(schema.get("additionalProperties"), dict):
        _check_keywords(schema["additionalProperties"], f"{path}/additionalProperties")
    for key in ("oneOf", "anyOf", "allOf"):
        if key in schema:
            for index, child in enumerate(schema[key]):
                _check_keywords(child, f"{path}/{key}/{index}")


def validate(instance: Any, schema: Dict[str, Any], root: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return a list of human-readable errors, empty when the instance is valid."""
    root = root if root is not None else schema
    return _validate(instance, schema, root, "$")


def _validate(instance: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str) -> List[str]:
    errors: List[str] = []

    if "$ref" in schema:
        errors.extend(_validate(instance, _resolve(root, schema["$ref"]), root, path))
        if schema.keys() == {"$ref"} | {k for k in ("title", "description") if k in schema}:
            return errors

    if "type" in schema:
        declared = schema["type"]
        types = declared if isinstance(declared, list) else [declared]
        matched = False
        for name in types:
            expected = _TYPE_MAP.get(name)
            if expected is None:
                raise SchemaError(f"unsupported type {name!r}")
            if name in ("number", "integer"):
                if isinstance(instance, bool):
                    continue
                if isinstance(instance, int):
                    matched = True
                    break
                if isinstance(instance, float) and name == "number":
                    matched = True
                    break
                continue
            if isinstance(instance, expected):
                matched = True
                break
        if not matched:
            errors.append(f"{path}: expected type {declared!r}, got {_type_name(instance)}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, value in instance.items():
            if name in properties:
                errors.extend(_validate(value, properties[name], root, f"{path}.{name}"))
            elif additional is False:
                errors.append(f"{path}: unexpected property {name!r}")
            elif isinstance(additional, dict):
                errors.extend(_validate(value, additional, root, f"{path}.{name}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items, got {len(instance)}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items, got {len(instance)}")
        if "uniqueItems" in schema and schema["uniqueItems"]:
            seen = [json.dumps(item, sort_keys=True) for item in instance]
            if len(seen) != len(set(seen)):
                errors.append(f"{path}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                errors.extend(_validate(item, schema["items"], root, f"{path}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {instance} must exceed {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {instance} must be below {schema['exclusiveMaximum']}")

    if "allOf" in schema:
        for child in schema["allOf"]:
            errors.extend(_validate(instance, child, root, path))
    if "anyOf" in schema:
        branch_errors = [_validate(instance, child, root, path) for child in schema["anyOf"]]
        if all(branch for branch in branch_errors):
            errors.append(f"{path}: matches none of {len(schema['anyOf'])} allowed forms")
    if "oneOf" in schema:
        matches = sum(1 for child in schema["oneOf"] if not _validate(instance, child, root, path))
        if matches != 1:
            errors.append(f"{path}: matches {matches} of the allowed forms, expected exactly 1")
    if "not" in schema:
        if not _validate(instance, schema["not"], root, path):
            errors.append(f"{path}: must not match the excluded form")
    return errors


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def load_schema(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    _check_keywords(schema)
    return schema


def check(instance: Any, schema: Dict[str, Any], label: str = "instance") -> None:
    """Raise ``ValidationError`` naming every violation."""
    errors = validate(instance, schema)
    if errors:
        raise ValidationError([f"{label}: {error}" for error in errors])
