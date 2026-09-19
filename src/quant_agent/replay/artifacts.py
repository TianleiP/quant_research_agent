from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant_agent.artifacts import read_csv_rows


@dataclass(frozen=True)
class ReplayArtifacts:
    decision_log: list[dict[str, str]]
    positions: list[dict[str, str]]
    trades: list[dict[str, str]]
    rankings: list[dict[str, str]]


def load_replay_artifacts(run_dir: Path) -> ReplayArtifacts:
    manifest = read_json_if_exists(run_dir / "manifest.json")
    artifact_map = manifest.get("artifacts", {}) if isinstance(manifest.get("artifacts"), dict) else {}
    return ReplayArtifacts(
        decision_log=read_artifact_rows(run_dir, artifact_map, "decision_log", "decision_log.csv"),
        positions=read_artifact_rows(run_dir, artifact_map, "positions", "positions.csv"),
        trades=read_artifact_rows(run_dir, artifact_map, "trades", "trades.csv"),
        rankings=read_artifact_rows(run_dir, artifact_map, "rankings", "rankings.csv"),
    )


def rows_for_date(rows: list[dict[str, str]], date: str, *date_keys: str) -> list[dict[str, str]]:
    keys = date_keys or ("date",)
    return [row for row in rows if any(row.get(key) == date for key in keys)]


def first_row_for_date(rows: list[dict[str, str]], date: str, *date_keys: str) -> dict[str, str] | None:
    matches = rows_for_date(rows, date, *date_keys)
    return matches[0] if matches else None


def read_artifact_rows(
    run_dir: Path,
    artifact_map: dict[str, str],
    name: str,
    fallback: str,
) -> list[dict[str, str]]:
    path = resolve_artifact_path(run_dir, artifact_map, name, fallback)
    if not path or not path.exists() or not path.is_file():
        return []
    try:
        return read_csv_rows(path)
    except (OSError, UnicodeDecodeError):
        return []


def resolve_artifact_path(
    run_dir: Path,
    artifact_map: dict[str, str],
    name: str,
    fallback: str,
) -> Path | None:
    candidates = []
    value = artifact_map.get(name)
    if value:
        candidates.append(run_dir / value)

    source_value = artifact_map.get(f"source_{name}_csv")
    if source_value:
        candidates.append(run_dir / source_value)

    candidates.append(run_dir / fallback)
    if name == "rankings":
        candidates.extend(sorted((run_dir / "source_artifacts").glob("*rankings*.csv")))
        candidates.extend(sorted(run_dir.glob("*rankings*.csv")))

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
