from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Union


class SchemaValidationError(ValueError):
    pass


def load_json(path: Union[str, Path]) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_event(event: dict[str, Any]) -> None:
    schema = load_json(Path(__file__).resolve().parents[1] / "schemas" / "event.schema.json")
    _validate_object(event, schema, path="$")


def validate_ticket(ticket: dict[str, Any]) -> None:
    schema = load_json(Path(__file__).resolve().parents[1] / "schemas" / "ticket.schema.json")
    _validate_object(ticket, schema, path="$")


def _validate_object(value: Any, schema: dict[str, Any], path: str) -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            raise SchemaValidationError(f"{path} must be an object")
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            extra = set(value.keys()) - allowed
            if extra:
                raise SchemaValidationError(f"{path} has unexpected keys: {sorted(extra)}")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _validate_object(value[key], child, f"{path}.{key}")
        return
    if expected_type == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(f"{path} must be an array")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            _validate_object(item, item_schema, f"{path}[{index}]")
        return
    if isinstance(expected_type, list):
        if not any(_matches_type(value, item_type) for item_type in expected_type):
            raise SchemaValidationError(f"{path} must be one of {expected_type}")
    elif expected_type and not _matches_type(value, expected_type):
        raise SchemaValidationError(f"{path} must be {expected_type}")
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path} must be {schema['const']}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path} must be one of {schema['enum']}")
    if "pattern" in schema and not re.match(schema["pattern"], str(value)):
        raise SchemaValidationError(f"{path} does not match {schema['pattern']}")
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        raise SchemaValidationError(f"{path} must be >= {schema['minimum']}")
    if schema.get("format") == "date-time":
        _validate_datetime(value, path)


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    return True


def _validate_datetime(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be an ISO8601 datetime string")
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchemaValidationError(f"{path} must be an ISO8601 datetime string") from exc
