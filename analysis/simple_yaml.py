from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple, Union


def load_yaml(path: Union[str, Path]) -> Any:
    """Parse the small YAML subset used by this repository's config files."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    parsed, index = _parse_block(_strip_comments(lines), 0, 0)
    if index != len(_strip_comments(lines)):
        raise ValueError(f"Could not parse all lines in {path}")
    return parsed


def _strip_comments(lines: List[str]) -> List[Tuple[int, str]]:
    stripped: List[Tuple[int, str]] = []
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        content = raw.split(" #", 1)[0].rstrip()
        stripped.append((len(raw) - len(raw.lstrip(" ")), content.lstrip(" ")))
    return stripped


def _parse_block(lines: List[Tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, text = lines[index]
    if current_indent < indent:
        return {}, index
    if text.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_dict(lines, index, indent)


def _parse_dict(lines: List[Tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"Unexpected indentation near: {text}")
        if text.startswith("- "):
            break
        if ":" not in text:
            raise ValueError(f"Expected key/value line: {text}")
        key, raw_value = text.split(":", 1)
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            result[key] = _parse_scalar(raw_value)
            continue
        value, index = _parse_block(lines, index, indent + 2)
        result[key] = value
    return result, index


def _parse_list(lines: List[Tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not text.startswith("- "):
            break
        item = text[2:].strip()
        index += 1
        if not item:
            value, index = _parse_block(lines, index, indent + 2)
            result.append(value)
            continue
        if ":" in item and not item.startswith(("'", '"')):
            key, raw_value = item.split(":", 1)
            entry: dict[str, Any] = {}
            if raw_value.strip():
                entry[key] = _parse_scalar(raw_value.strip())
            else:
                value, index = _parse_block(lines, index, indent + 2)
                entry[key] = value
            while index < len(lines):
                child_indent, child_text = lines[index]
                if child_indent < indent + 2:
                    break
                if child_indent != indent + 2 or child_text.startswith("- "):
                    break
                child_key, child_raw = child_text.split(":", 1)
                index += 1
                if child_raw.strip():
                    entry[child_key] = _parse_scalar(child_raw.strip())
                else:
                    value, index = _parse_block(lines, index, indent + 4)
                    entry[child_key] = value
            result.append(entry)
            continue
        result.append(_parse_scalar(item))
    return result, index


def _parse_scalar(value: str) -> Any:
    if value in {"null", "~"}:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    try:
        if any(char in value for char in [".", "e", "E"]):
            return float(value)
        return int(value)
    except ValueError:
        return value
