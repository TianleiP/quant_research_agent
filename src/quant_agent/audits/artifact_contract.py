from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_ARTIFACTS = [
    ("manifest", "manifest.json", "json", 0),
    ("metrics", "metrics.json", "json", 0),
    ("decision_log", "decision_log.csv", "csv", 1),
    ("equity_curve", "equity_curve.csv", "csv", 1),
]

SOURCE_ARTIFACTS = [
    ("source_daily_curve", None, "csv", 1),
    ("source_summary_by_window", "source_artifacts/summary_by_window.csv", "csv", 1),
]

POSITION_LEVEL_ARTIFACTS = [
    ("positions", "positions.csv", "csv", 1),
    ("trades", "trades.csv", "csv", 1),
    ("rankings", "rankings.csv", "csv", 1),
]

EXPORT_CONTRACT = {
    "positions.csv": [
        "date",
        "variant_id",
        "symbol",
        "weight",
        "sleeve",
        "rank",
        "score",
        "price",
    ],
    "trades.csv": [
        "date",
        "variant_id",
        "symbol",
        "prev_weight",
        "target_weight",
        "weight_change",
        "turnover",
    ],
    "rankings.csv": [
        "date",
        "variant_id",
        "symbol",
        "rank",
        "score",
        "eligible",
        "sleeve",
    ],
}


def run_artifact_contract_inspection(run_dir: Path) -> Path:
    inspection = build_artifact_contract_inspection(run_dir)
    audits_dir = run_dir / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    json_path = audits_dir / "artifact_contract.json"
    markdown_path = audits_dir / "artifact_contract.md"
    inspection["artifacts"]["artifact_contract_json"] = "audits/artifact_contract.json"
    inspection["artifacts"]["artifact_contract_markdown"] = "audits/artifact_contract.md"
    if inspection.get("suggested_fix"):
        inspection["suggested_fix"]["artifact_report"] = relative_path(json_path, Path.cwd())
        inspection["suggested_fix"]["command"] = suggest_fix_command(inspection["suggested_fix"])
    json_path.write_text(json.dumps(inspection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_artifact_contract_markdown(inspection), encoding="utf-8")
    return markdown_path


def build_artifact_contract_inspection(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "manifest.json")
    engine_metadata = read_json(run_dir / "engine_metadata.json")
    artifacts = manifest.get("artifacts", {}) if isinstance(manifest.get("artifacts"), dict) else {}
    checks = []

    for name, fallback, kind, min_rows in CORE_ARTIFACTS:
        checks.append(check_artifact(run_dir, artifacts, name, fallback, kind, min_rows, required=True))
    for name, fallback, kind, min_rows in SOURCE_ARTIFACTS:
        checks.append(check_artifact(run_dir, artifacts, name, fallback, kind, min_rows, required=True))
    for name, fallback, kind, min_rows in POSITION_LEVEL_ARTIFACTS:
        checks.append(check_artifact(run_dir, artifacts, name, fallback, kind, min_rows, required=False))

    missing_position_level = [
        check
        for check in checks
        if check["name"] in {name for name, *_rest in POSITION_LEVEL_ARTIFACTS}
        and check["status"] != "pass"
    ]
    suggested_fix = build_suggested_missing_export_fix(engine_metadata, missing_position_level)
    return {
        "run_id": run_dir.name,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "recommendation": recommendation_from_checks(checks),
        "checks": checks,
        "missing_position_level_artifacts": missing_position_level,
        "export_contract": EXPORT_CONTRACT,
        "suggested_fix": suggested_fix,
        "artifacts": {},
    }


def check_artifact(
    run_dir: Path,
    artifacts: dict[str, str],
    name: str,
    fallback: str | None,
    kind: str,
    min_rows: int,
    required: bool,
) -> dict[str, Any]:
    path = resolve_artifact_path(run_dir, artifacts, name, fallback)
    exists = path.exists() if path else False
    rows = csv_row_count(path) if exists and kind == "csv" else None
    has_rows = rows is None or rows >= min_rows
    status = "pass" if exists and has_rows else ("fail" if required else "warn")
    message = artifact_message(name, path, exists, rows, min_rows, required)
    return {
        "name": name,
        "status": status,
        "required": required,
        "kind": kind,
        "path": relative_path(path, run_dir) if path else "",
        "exists": exists,
        "rows": rows,
        "min_rows": min_rows,
        "message": message,
    }


def artifact_message(
    name: str,
    path: Path | None,
    exists: bool,
    rows: int | None,
    min_rows: int,
    required: bool,
) -> str:
    label = "required" if required else "position-level"
    if not exists:
        return f"{label} artifact {name} is missing."
    if rows is not None and rows < min_rows:
        return f"{label} artifact {name} exists but has {rows} rows; expected at least {min_rows}."
    if rows is not None:
        return f"{label} artifact {name} is present with {rows} rows."
    return f"{label} artifact {name} is present."


def build_suggested_missing_export_fix(
    engine_metadata: dict[str, Any],
    missing_position_level: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not missing_position_level:
        return None
    repo = engine_metadata.get("working_dir")
    script = source_script_from_command(engine_metadata.get("command", []))
    if not isinstance(repo, str) or not repo or not script:
        return {
            "issue": "missing-position-exports",
            "reason": "Position-level artifacts are missing, but the source repo/script could not be inferred from engine metadata.",
            "missing": [item["name"] for item in missing_position_level],
        }
    return {
        "issue": "missing-position-exports",
        "repo": repo,
        "file": script,
        "missing": [item["name"] for item in missing_position_level],
        "reason": "The run has strategy-level artifacts but lacks position-level exports needed for holdings/trade/ranking replay.",
    }


def source_script_from_command(command: Any) -> str | None:
    if not isinstance(command, list):
        return None
    for item in command:
        text = str(item)
        if text.lower().endswith(".py"):
            path = Path(text)
            if not path.is_absolute():
                return path.as_posix()
    return None


def suggest_fix_command(suggested_fix: dict[str, Any]) -> str | None:
    repo = suggested_fix.get("repo")
    file_path = suggested_fix.get("file")
    artifact_report = suggested_fix.get("artifact_report")
    if not repo or not file_path or not artifact_report:
        return None
    return (
        "quant-agent suggest-fix --issue missing-position-exports "
        f"--repo \"{repo}\" --file \"{file_path}\" --artifact-report \"{artifact_report}\""
    )


def render_artifact_contract_markdown(inspection: dict[str, Any]) -> str:
    lines = [
        "# Artifact Contract Inspection",
        "",
        f"Run ID: `{inspection['run_id']}`",
        f"Recommendation: `{inspection['recommendation']}`",
        "",
        "## Checks",
        "",
    ]
    for check in inspection["checks"]:
        lines.append(f"- {check['status'].upper()}: `{check['name']}` - {check['message']}")

    lines.extend(["", "## Position-Level Export Contract", ""])
    for file_name, columns in inspection["export_contract"].items():
        lines.append(f"- `{file_name}`: `{', '.join(columns)}`")

    lines.extend(["", "## Suggested Fix", ""])
    suggested = inspection.get("suggested_fix")
    if suggested:
        lines.append(f"- Issue: `{suggested.get('issue')}`")
        lines.append(f"- Missing: `{', '.join(suggested.get('missing', []))}`")
        if suggested.get("repo") and suggested.get("file"):
            lines.append(f"- Repo: `{suggested['repo']}`")
            lines.append(f"- File: `{suggested['file']}`")
        if suggested.get("command"):
            lines.append(f"- Command: `{suggested['command']}`")
        lines.append(f"- Reason: {suggested.get('reason')}")
    else:
        lines.append("- None. Position-level artifact contract is satisfied.")
    lines.append("")
    return "\n".join(lines)


def recommendation_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "blocked"
    if any(check["status"] == "warn" for check in checks):
        return "needs_export_patch"
    return "satisfied"


def resolve_artifact_path(
    run_dir: Path,
    artifacts: dict[str, str],
    name: str,
    fallback: str | None,
) -> Path | None:
    value = artifacts.get(name)
    if value:
        return run_dir / value
    source_value = artifacts.get(f"source_{name}_csv")
    if source_value:
        return run_dir / source_value
    if name == "rankings":
        ranking_candidates = sorted(run_dir.glob("rankings*.csv"))
        if ranking_candidates:
            return ranking_candidates[0]
    return run_dir / fallback if fallback else None


def csv_row_count(path: Path) -> int:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
