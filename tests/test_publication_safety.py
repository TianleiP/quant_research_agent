from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_machine_local_files_are_ignored() -> None:
    ignore_lines = {
        line.strip()
        for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "/.codex/config.toml" in ignore_lines
    assert "/configs/xgboost_live_strategy.yaml" in ignore_lines
    assert "/state/project_state.json" in ignore_lines
    assert ".env" in ignore_lines
    assert "!.env.example" in ignore_lines


def test_publication_candidates_pass_safety_audit() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "publication_audit.py"),
            "--root",
            str(PROJECT_ROOT),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
