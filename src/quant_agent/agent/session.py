from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def create_session_id() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def session_log_path(session_id: str, session_dir: Path) -> Path:
    return session_dir / f"{session_id}.jsonl"


def session_state_path(session_id: str, session_dir: Path) -> Path:
    return session_dir / f"{session_id}.state.json"


def approval_request_path(session_id: str, session_dir: Path) -> Path:
    return session_dir / f"{session_id}.approval.json"


def session_checkpoint_path(session_dir: Path) -> Path:
    return session_dir / "checkpoints.sqlite"


def append_session_event(path: str | Path, event_type: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def save_session_state(path: str | Path, state: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_session_state(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Session state must be a JSON object: {path}")
    return payload


def write_approval_request(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_approval_request(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Approval request must be a JSON object: {path}")
    return payload
