from __future__ import annotations

import os
from pathlib import Path


def get_secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    return read_dotenv_value(Path.cwd() / ".env", name)


def read_dotenv_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != name:
            continue
        return clean_dotenv_value(value.strip())
    return None


def clean_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
