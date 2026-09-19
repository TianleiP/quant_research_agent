from __future__ import annotations

from pathlib import Path

from quant_agent.artifacts import read_csv_rows


def load_decision_log(run_dir: Path) -> list[dict[str, str]]:
    path = run_dir / "decision_log.csv"
    if not path.exists():
        return []
    return read_csv_rows(path)

