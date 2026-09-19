from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_self_contained_repair_demo_completes_without_mutating_template(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "run_repair_demo.py"
    template = root / "examples" / "demo_strategy_repo" / "research" / "strategy.py"
    before = template.read_bytes()
    workdir = tmp_path / "repair_demo"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--approve-demo-actions",
            "--workdir",
            str(workdir),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((workdir / "demo_report.json").read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["graph_status"] == "complete"
    assert report["initial_artifact_recommendation"] == "needs_export_patch"
    assert report["initial_missing_artifacts"] == ["positions", "trades", "rankings"]
    assert report["artifact_recommendation"] == "satisfied"
    assert [item["action"] for item in report["approvals"]] == [
        "trace_source",
        "suggest_fix",
        "apply_fix_dry_run",
        "apply_fix_yes",
        "run_fresh",
    ]
    assert all(item["interrupt_count"] > 0 for item in report["approvals"])
    assert template.read_bytes() == before
