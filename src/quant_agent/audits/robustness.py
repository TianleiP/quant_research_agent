from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from quant_agent.artifacts import read_csv_rows
from quant_agent.metrics import load_metrics


PARAMETER_COLUMNS = ["base_top_n", "base_score", "accel_top_n", "accel_score"]
MAX_DRAWDOWN_LIMIT = -0.40
MAX_LOCAL_CAGR_GAP = 0.03
MAX_LOCAL_DRAWDOWN_GAP = 0.05
MIN_LOCAL_NEIGHBORS = 3


def run_robustness_sweep(run_dir: Path) -> Path:
    audit = build_robustness_audit(run_dir)
    audits_dir = run_dir / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)

    json_path = audits_dir / "robustness.json"
    markdown_path = audits_dir / "robustness.md"
    json_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_robustness_markdown(audit), encoding="utf-8")
    return markdown_path


def build_robustness_audit(run_dir: Path) -> dict[str, Any]:
    metrics = load_metrics(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    source_metadata = read_json(run_dir / "source_artifacts" / "metadata.json")
    variant_id = (
        metrics.get("variant_id")
        or source_metadata.get("promoted_live_variant_id")
        or manifest.get("strategy_name")
        or run_dir.name
    )
    primary_window = str(metrics.get("primary_window") or "full_2000_2026")

    summary_path = artifact_path(
        run_dir,
        manifest,
        "source_summary_by_window",
        run_dir / "source_artifacts" / "summary_by_window.csv",
    )
    rolling_path = artifact_path(
        run_dir,
        manifest,
        "source_rolling_1y_summary_csv",
        run_dir / "source_artifacts" / "rolling_1y_summary.csv",
    )

    summary_rows = read_csv_rows(summary_path) if summary_path.exists() else []
    rolling_rows = read_csv_rows(rolling_path) if rolling_path.exists() else []
    checks: list[dict[str, str]] = []

    add_check(
        checks,
        "pass" if summary_rows else "fail",
        "summary_by_window_available",
        f"Loaded {len(summary_rows)} summary rows from {relative_path(summary_path, run_dir)}."
        if summary_rows
        else "source summary_by_window.csv is missing or empty.",
    )

    primary_rows = [row for row in summary_rows if row.get("window") == primary_window]
    target_primary = find_row(primary_rows, variant_id)
    add_check(
        checks,
        "pass" if target_primary else "fail",
        "target_primary_window_present",
        f"Found target variant {variant_id} in {primary_window}."
        if target_primary
        else f"Target variant {variant_id} is missing from {primary_window}.",
    )

    parameter_values = collect_parameter_values(primary_rows)
    local_neighbors = local_neighbor_rows(primary_rows, target_primary, parameter_values) if target_primary else []
    add_check(
        checks,
        "pass" if len(local_neighbors) >= MIN_LOCAL_NEIGHBORS else "warn",
        "local_neighbor_count",
        f"Found {len(local_neighbors)} one-step local neighbors."
        if local_neighbors
        else "No one-step local neighbors found for the target variant.",
    )

    target_metrics = summarize_row(target_primary) if target_primary else core_metrics_fallback(metrics)
    neighbor_summaries = [summarize_neighbor(row, target_primary, parameter_values) for row in local_neighbors]
    evaluate_neighbor_stability(checks, target_primary, local_neighbors)

    window_rows = [row for row in summary_rows if row.get("variant_id") == variant_id]
    window_summaries = [summarize_row(row) for row in sorted(window_rows, key=lambda item: item.get("window", ""))]
    evaluate_window_stability(checks, window_rows)

    rolling_target = find_row(rolling_rows, variant_id)
    rolling_summary = summarize_rolling_row(rolling_target) if rolling_target else {}
    evaluate_rolling_stability(checks, rolling_target)

    recommendation = recommendation_from_checks(checks)
    return {
        "run_id": run_dir.name,
        "variant_id": str(variant_id),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "recommendation": recommendation,
        "primary_window": primary_window,
        "checks": checks,
        "target": target_metrics,
        "local_neighbors": neighbor_summaries,
        "window_metrics": window_summaries,
        "rolling_1y": rolling_summary,
        "artifacts": {
            "summary_by_window": relative_path(summary_path, run_dir),
            "rolling_1y_summary": relative_path(rolling_path, run_dir),
            "robustness_json": "audits/robustness.json",
            "robustness_markdown": "audits/robustness.md",
        },
    }


def evaluate_neighbor_stability(
    checks: list[dict[str, str]],
    target_row: dict[str, str] | None,
    neighbors: list[dict[str, str]],
) -> None:
    if not target_row or not neighbors:
        return

    target_cagr = numeric(target_row.get("ann_return"))
    neighbor_cagrs = numeric_values(row.get("ann_return") for row in neighbors)
    if target_cagr is not None and neighbor_cagrs:
        neighbor_median = median(neighbor_cagrs)
        best_neighbor = max(neighbor_cagrs)
        if target_cagr >= neighbor_median - MAX_LOCAL_CAGR_GAP:
            add_check(
                checks,
                "pass",
                "local_cagr_stability",
                f"Target CAGR {target_cagr:.4f} is within {MAX_LOCAL_CAGR_GAP:.2%} of local median {neighbor_median:.4f}.",
            )
        else:
            add_check(
                checks,
                "warn",
                "local_cagr_stability",
                f"Target CAGR {target_cagr:.4f} is more than {MAX_LOCAL_CAGR_GAP:.2%} below local median {neighbor_median:.4f}.",
            )
        if best_neighbor - target_cagr <= MAX_LOCAL_CAGR_GAP:
            add_check(
                checks,
                "pass",
                "local_best_cagr_gap",
                f"Best local neighbor exceeds target by {best_neighbor - target_cagr:.4f}.",
            )
        else:
            add_check(
                checks,
                "warn",
                "local_best_cagr_gap",
                f"Best local neighbor exceeds target by {best_neighbor - target_cagr:.4f}.",
            )

    target_drawdown = numeric(target_row.get("max_drawdown"))
    neighbor_drawdowns = numeric_values(row.get("max_drawdown") for row in neighbors)
    if target_drawdown is not None and neighbor_drawdowns:
        neighbor_median = median(neighbor_drawdowns)
        if target_drawdown >= neighbor_median - MAX_LOCAL_DRAWDOWN_GAP:
            add_check(
                checks,
                "pass",
                "local_drawdown_stability",
                f"Target max drawdown {target_drawdown:.4f} is close to local median {neighbor_median:.4f}.",
            )
        else:
            add_check(
                checks,
                "warn",
                "local_drawdown_stability",
                f"Target max drawdown {target_drawdown:.4f} is more than {MAX_LOCAL_DRAWDOWN_GAP:.2%} worse than local median {neighbor_median:.4f}.",
            )


def evaluate_window_stability(checks: list[dict[str, str]], rows: list[dict[str, str]]) -> None:
    if not rows:
        add_check(checks, "fail", "window_rows_present", "No target window rows are available.")
        return
    add_check(checks, "pass", "window_rows_present", f"Found {len(rows)} target window rows.")

    non_positive = [
        row.get("window", "unknown")
        for row in rows
        if numeric(row.get("ann_return")) is None or numeric(row.get("ann_return")) <= 0
    ]
    add_check(
        checks,
        "pass" if not non_positive else "warn",
        "window_returns_positive",
        "All target windows have positive annualized return."
        if not non_positive
        else f"Non-positive annualized return in windows: {', '.join(non_positive)}.",
    )

    drawdown_breaches = [
        row.get("window", "unknown")
        for row in rows
        if numeric(row.get("max_drawdown")) is None or numeric(row.get("max_drawdown")) <= MAX_DRAWDOWN_LIMIT
    ]
    add_check(
        checks,
        "pass" if not drawdown_breaches else "fail",
        "window_drawdown_threshold",
        f"All target windows stay above {MAX_DRAWDOWN_LIMIT:.0%} max drawdown."
        if not drawdown_breaches
        else f"Max drawdown breaches {MAX_DRAWDOWN_LIMIT:.0%} in windows: {', '.join(drawdown_breaches)}.",
    )

    by_window = {row.get("window"): row for row in rows}
    full_cagr = numeric(value_from_window(by_window, "full_2000_2026", "ann_return"))
    validation_cagr = numeric(value_from_window(by_window, "validation_2022_2026", "ann_return"))
    if full_cagr is not None and validation_cagr is not None and full_cagr > 0:
        if validation_cagr <= 2 * full_cagr:
            add_check(
                checks,
                "pass",
                "validation_cagr_concentration",
                f"Validation CAGR {validation_cagr:.4f} is within 2x full-window CAGR {full_cagr:.4f}.",
            )
        else:
            add_check(
                checks,
                "warn",
                "validation_cagr_concentration",
                f"Validation CAGR {validation_cagr:.4f} is more than 2x full-window CAGR {full_cagr:.4f}.",
            )


def evaluate_rolling_stability(checks: list[dict[str, str]], rolling_row: dict[str, str] | None) -> None:
    if not rolling_row:
        add_check(checks, "warn", "rolling_1y_available", "rolling_1y_summary.csv is missing the target variant.")
        return
    add_check(checks, "pass", "rolling_1y_available", "Found target row in rolling_1y_summary.csv.")

    median_cagr = numeric(rolling_row.get("median_1y_cagr"))
    add_check(
        checks,
        "pass" if median_cagr is not None and median_cagr > 0 else "warn",
        "rolling_median_cagr_positive",
        f"Median rolling 1Y CAGR is {median_cagr:.4f}."
        if median_cagr is not None
        else "Median rolling 1Y CAGR is missing.",
    )

    mean_drawdown = numeric(rolling_row.get("mean_1y_maxdd"))
    if mean_drawdown is not None:
        add_check(
            checks,
            "pass" if mean_drawdown > -0.25 else "warn",
            "rolling_mean_drawdown",
            f"Mean rolling 1Y max drawdown is {mean_drawdown:.4f}.",
        )


def render_robustness_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Robustness Summary",
        "",
        f"Run ID: `{audit['run_id']}`",
        f"Variant: `{audit['variant_id']}`",
        f"Recommendation: `{audit['recommendation']}`",
        f"Primary window: `{audit['primary_window']}`",
        "",
        "## Checks",
        "",
    ]
    for check in audit["checks"]:
        lines.append(f"- {check['status'].upper()}: {check['code']} - {check['message']}")

    lines.extend(["", "## Target Metrics", ""])
    lines.extend(render_key_values(audit.get("target", {})))

    lines.extend(["", "## Local Neighbors", ""])
    neighbors = audit.get("local_neighbors", [])
    if neighbors:
        for row in neighbors:
            diff = ", ".join(row.get("differences", []))
            lines.append(
                f"- `{row.get('variant_id')}` ({diff}): "
                f"CAGR {format_decimal(row.get('ann_return'))}, "
                f"MaxDD {format_decimal(row.get('max_drawdown'))}, "
                f"Sharpe {format_decimal(row.get('sharpe'))}"
            )
    else:
        lines.append("- None found.")

    lines.extend(["", "## Window Metrics", ""])
    for row in audit.get("window_metrics", []):
        lines.append(
            f"- `{row.get('window')}`: CAGR {format_decimal(row.get('ann_return'))}, "
            f"MaxDD {format_decimal(row.get('max_drawdown'))}, Sharpe {format_decimal(row.get('sharpe'))}"
        )

    lines.extend(["", "## Rolling 1Y", ""])
    rolling = audit.get("rolling_1y", {})
    lines.extend(render_key_values(rolling) if rolling else ["- No rolling 1Y target row available."])

    lines.extend(["", "## Artifacts", ""])
    for name, path in sorted(audit.get("artifacts", {}).items()):
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def render_key_values(payload: dict[str, Any]) -> list[str]:
    if not payload:
        return ["- None."]
    return [f"- {key}: `{value}`" for key, value in sorted(payload.items())]


def summarize_neighbor(
    row: dict[str, str],
    target_row: dict[str, str] | None,
    parameter_values: dict[str, list[Any]],
) -> dict[str, Any]:
    summary = summarize_row(row)
    summary["differences"] = parameter_differences(row, target_row, parameter_values) if target_row else []
    return summary


def summarize_row(row: dict[str, str] | None) -> dict[str, Any]:
    if not row:
        return {}
    keys = [
        "window",
        "variant_id",
        "base_top_n",
        "base_score",
        "accel_top_n",
        "accel_score",
        "ann_return",
        "max_drawdown",
        "sharpe",
        "ann_vol",
        "mean_cash_weight",
        "mean_gross_exposure",
    ]
    return {key: convert_value(row[key]) for key in keys if key in row}


def summarize_rolling_row(row: dict[str, str] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {key: convert_value(value) for key, value in row.items()}


def core_metrics_fallback(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = ["variant_id", "cagr", "max_drawdown", "calmar", "sharpe", "ann_vol", "exposure"]
    return {key: metrics[key] for key in keys if key in metrics}


def local_neighbor_rows(
    rows: list[dict[str, str]],
    target_row: dict[str, str],
    parameter_values: dict[str, list[Any]],
) -> list[dict[str, str]]:
    neighbors = [
        row
        for row in rows
        if row.get("variant_id") != target_row.get("variant_id")
        and parameter_distance(row, target_row, parameter_values) == 1
    ]
    return sorted(neighbors, key=lambda row: row.get("variant_id", ""))


def parameter_distance(
    row: dict[str, str],
    target_row: dict[str, str],
    parameter_values: dict[str, list[Any]],
) -> int:
    distance = 0
    for column in PARAMETER_COLUMNS:
        if row.get(column) == target_row.get(column):
            continue
        if column.endswith("_top_n"):
            distance += numeric_step_distance(row.get(column), target_row.get(column), parameter_values.get(column, []))
        else:
            distance += 1
    return distance


def parameter_differences(
    row: dict[str, str],
    target_row: dict[str, str],
    parameter_values: dict[str, list[Any]],
) -> list[str]:
    differences = []
    for column in PARAMETER_COLUMNS:
        if row.get(column) == target_row.get(column):
            continue
        if column.endswith("_top_n"):
            step = numeric_step_distance(row.get(column), target_row.get(column), parameter_values.get(column, []))
            differences.append(f"{column}{'' if step == 1 else ' nonlocal'}")
        else:
            differences.append(column)
    return differences


def numeric_step_distance(value: Any, target: Any, values: list[Any]) -> int:
    value_num = numeric(value)
    target_num = numeric(target)
    numeric_values_only = sorted({item for item in (numeric(candidate) for candidate in values) if item is not None})
    if value_num is None or target_num is None or not numeric_values_only:
        return 2
    try:
        return abs(numeric_values_only.index(value_num) - numeric_values_only.index(target_num))
    except ValueError:
        return 2


def collect_parameter_values(rows: list[dict[str, str]]) -> dict[str, list[Any]]:
    return {column: [row.get(column) for row in rows if row.get(column) not in {None, ""}] for column in PARAMETER_COLUMNS}


def find_row(rows: list[dict[str, str]], variant_id: Any) -> dict[str, str] | None:
    for row in rows:
        if row.get("variant_id") == variant_id:
            return row
    return None


def value_from_window(rows_by_window: dict[str | None, dict[str, str]], window: str, key: str) -> Any:
    row = rows_by_window.get(window)
    return row.get(key) if row else None


def recommendation_from_checks(checks: list[dict[str, str]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "blocked"
    if any(check["status"] == "warn" for check in checks):
        return "review_required"
    return "robust"


def add_check(checks: list[dict[str, str]], status: str, code: str, message: str) -> None:
    checks.append({"status": status, "code": code, "message": message})


def artifact_path(run_dir: Path, manifest: dict[str, Any], key: str, fallback: Path) -> Path:
    relative = manifest.get("artifacts", {}).get(key) if isinstance(manifest.get("artifacts"), dict) else None
    return run_dir / relative if relative else fallback


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def numeric_values(values: Any) -> list[float]:
    result = []
    for value in values:
        parsed = numeric(value)
        if parsed is not None:
            result.append(parsed)
    return result


def numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def convert_value(value: Any) -> Any:
    parsed = numeric(value)
    return parsed if parsed is not None else value


def format_decimal(value: Any) -> str:
    parsed = numeric(value)
    return f"{parsed:.4f}" if parsed is not None else str(value)


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
