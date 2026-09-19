from __future__ import annotations

from pathlib import Path


def run_docs_consistency_audit(run_dir: Path) -> Path:
    lines = [
        "# Documentation Consistency Audit",
        "",
        "UNKNOWN: Documentation consistency rules have not been configured yet.",
        f"INFO: Run under review: {run_dir.name}",
    ]
    report_path = run_dir / "audits" / "docs_consistency.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path

