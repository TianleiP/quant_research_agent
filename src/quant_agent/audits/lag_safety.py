from __future__ import annotations

from pathlib import Path

from quant_agent.artifacts import read_csv_rows


def run_lag_safety_audit(run_dir: Path) -> Path:
    decision_log_path = run_dir / "decision_log.csv"
    rows = read_csv_rows(decision_log_path) if decision_log_path.exists() else []

    lines = [
        "# Lag-Safety Audit",
        "",
    ]
    if not rows:
        lines.append("WARN: Decision log is missing, so lag-safety cannot be verified.")
    else:
        for row in rows:
            decision_date = row.get("decision_date", "unknown")
            available_through = row.get("available_through", "unknown")
            execution_date = row.get("execution_date", "unknown")
            if available_through <= decision_date <= execution_date:
                lines.append(
                    f"PASS: {decision_date} decision used data available through {available_through}."
                )
            else:
                lines.append(
                    f"WARN: {decision_date} timing fields need review: available_through={available_through}, execution_date={execution_date}."
                )

    report_path = run_dir / "audits" / "lag_safety.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path

