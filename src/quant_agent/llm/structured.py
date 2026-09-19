from __future__ import annotations

import json
from typing import Any


def parse_structured_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        value = json.loads(extract_json_object(stripped))
    if not isinstance(value, dict):
        raise RuntimeError("Structured model output must be a JSON object.")
    return value


def extract_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise RuntimeError("Structured model output did not contain a JSON object.")
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise RuntimeError("Structured model output contained an unclosed JSON object.")
