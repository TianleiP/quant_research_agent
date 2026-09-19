from __future__ import annotations

from pathlib import Path
from typing import Any

from quant_agent.metrics import format_metrics_summary


def write_short_report(
    run_dir: Path,
    metrics: dict[str, Any],
    flags: list[dict[str, str]],
    artifacts: dict[str, str],
) -> Path:
    report_path = run_dir / "short_report.md"
    lines = [
        f"# Short Report: {run_dir.name}",
        "",
        "## Core Metrics",
        "",
        "```text",
        format_metrics_summary(metrics),
        "```",
        "",
        "## Flags",
        "",
    ]
    lines.extend(f"- {flag['level']}: {flag['message']}" for flag in flags)
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
        ]
    )
    lines.extend(f"- {name}: `{path}`" for name, path in sorted(artifacts.items()))
    lines.extend(
        [
            "",
            "## Suggested Next Actions",
            "",
            "- View detailed metrics.",
            "- Run lag-safety audit.",
            "- Replay a suspicious decision date.",
            "- Generate a promotion report before changing the live baseline.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path

