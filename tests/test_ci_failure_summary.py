from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_ci_failure_summary_reports_names_without_failure_details(tmp_path: Path) -> None:
    report = tmp_path / "pytest-results.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="1" failures="1">
  <testsuite name="pytest" tests="1" failures="1">
    <testcase classname="tests.test_demo" name="test_expected_behavior">
      <failure message="secret-looking failure detail">private diagnostic body</failure>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "ci_failure_summary.py"), str(report)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "tests.test_demo::test_expected_behavior" in completed.stdout
    assert "private diagnostic body" not in completed.stdout
    assert "secret-looking failure detail" not in completed.stdout
