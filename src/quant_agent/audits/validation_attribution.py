from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.artifacts import read_csv_rows
from quant_agent.metrics import load_metrics


DEFAULT_VALIDATION_START = "2022-01-03"
DEFAULT_TOP_DAYS = 10
MIN_VALIDATION_DAYS = 252
MAX_DRAWDOWN_LIMIT = -0.40
TOP_10_POSITIVE_SHARE_WARN = 0.35
RARE_STATE_DAY_SHARE = 0.25
RARE_STATE_POSITIVE_SHARE = 0.60

STATE_COLUMNS = [
    "decision_label",
    "feedback_stage",
    "top_leverage_active",
    "top_weight_signal",
    "cash_active",
    "cash_entry",
    "stage0_guard_active",
    "stage1_guard_active",
    "stage2_guard_active",
    "stage2_permission",
    "outer_active",
    "outer_cap_applied",
    "rollover_active",
    "spy_below200_lag1",
    "spy_macd_neg_lag1",
    "breadth_weak_lag1",
]


def run_validation_attribution_audit(
    run_dir: Path,
    start_date: str = DEFAULT_VALIDATION_START,
    end_date: str | None = None,
    top_days: int = DEFAULT_TOP_DAYS,
) -> Path:
    audit = build_validation_attribution_audit(
        run_dir=run_dir,
        start_date=start_date,
        end_date=end_date,
        top_days=top_days,
    )
    audits_dir = run_dir / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    json_path = audits_dir / "validation_attribution.json"
    markdown_path = audits_dir / "validation_attribution.md"
    json_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_validation_attribution_markdown(audit), encoding="utf-8")
    return markdown_path


def build_validation_attribution_audit(
    run_dir: Path,
    start_date: str = DEFAULT_VALIDATION_START,
    end_date: str | None = None,
    top_days: int = DEFAULT_TOP_DAYS,
) -> dict[str, Any]:
    metrics = load_metrics(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    daily_path = daily_curve_path(run_dir, manifest)
    end_date = end_date or str(metrics.get("source_daily_end") or manifest.get("backtest_end") or "")

    all_rows = read_csv_rows(daily_path) if daily_path.exists() else []
    rows = [
        row
        for row in all_rows
        if row.get("date") and row["date"] >= start_date and (not end_date or row["date"] <= end_date)
    ]

    checks: list[dict[str, str]] = []
    add_check(
        checks,
        "pass" if all_rows else "fail",
        "daily_curve_available",
        f"Loaded {len(all_rows)} daily rows from {relative_path(daily_path, run_dir)}."
        if all_rows
        else "Daily curve CSV is missing or empty.",
    )
    add_check(
        checks,
        "pass" if len(rows) >= MIN_VALIDATION_DAYS else "fail",
        "validation_window_rows",
        f"Validation window contains {len(rows)} rows."
        if rows
        else f"No rows found for validation window {start_date} to {end_date or 'latest'}.",
    )

    window_summary = summarize_window(rows)
    evaluate_window_checks(checks, rows, window_summary)

    top_positive_days = top_return_days(rows, descending=True, limit=top_days)
    top_negative_days = top_return_days(rows, descending=False, limit=top_days)
    concentration = concentration_summary(rows, top_positive_days, top_negative_days)
    evaluate_concentration_checks(checks, concentration)

    state_attribution = build_state_attribution(rows)
    evaluate_state_checks(checks, state_attribution)

    exposure_attribution = attribution_for_column(rows, "exposure_bucket")
    year_attribution = attribution_for_column(rows, "year")

    return {
        "run_id": run_dir.name,
        "variant_id": str(metrics.get("variant_id") or manifest.get("strategy_name") or run_dir.name),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "recommendation": recommendation_from_checks(checks),
        "window": {
            "start_date": start_date,
            "end_date": end_date or (rows[-1].get("date") if rows else ""),
            "rows": len(rows),
        },
        "checks": checks,
        "summary": window_summary,
        "concentration": concentration,
        "top_positive_days": [summarize_day(row) for row in top_positive_days],
        "top_negative_days": [summarize_day(row) for row in top_negative_days],
        "state_attribution": state_attribution,
        "exposure_attribution": exposure_attribution,
        "year_attribution": year_attribution,
        "artifacts": {
            "daily_curve": relative_path(daily_path, run_dir),
            "validation_attribution_json": "audits/validation_attribution.json",
            "validation_attribution_markdown": "audits/validation_attribution.md",
        },
    }


def evaluate_window_checks(
    checks: list[dict[str, str]],
    rows: list[dict[str, str]],
    summary: dict[str, Any],
) -> None:
    if not rows:
        return
    max_drawdown = numeric(summary.get("max_drawdown"))
    if max_drawdown is None:
        add_check(checks, "warn", "validation_drawdown_available", "Validation max drawdown could not be calculated.")
    elif max_drawdown <= MAX_DRAWDOWN_LIMIT:
        add_check(
            checks,
            "fail",
            "validation_drawdown_threshold",
            f"Validation max drawdown {max_drawdown:.4f} breaches {MAX_DRAWDOWN_LIMIT:.0%}.",
        )
    else:
        add_check(
            checks,
            "pass",
            "validation_drawdown_threshold",
            f"Validation max drawdown {max_drawdown:.4f} is within threshold.",
        )

    cagr = numeric(summary.get("cagr"))
    add_check(
        checks,
        "pass" if cagr is not None and cagr > 0 else "warn",
        "validation_cagr_positive",
        f"Validation CAGR is {cagr:.4f}." if cagr is not None else "Validation CAGR is unavailable.",
    )


def evaluate_concentration_checks(checks: list[dict[str, str]], concentration: dict[str, Any]) -> None:
    top_10_share = numeric(concentration.get("top_10_positive_share_of_positive_returns"))
    if top_10_share is None:
        add_check(checks, "warn", "top_day_concentration_available", "Top-day concentration could not be calculated.")
    elif top_10_share > TOP_10_POSITIVE_SHARE_WARN:
        add_check(
            checks,
            "warn",
            "top_day_concentration",
            f"Top 10 positive days explain {top_10_share:.1%} of positive validation returns.",
        )
    else:
        add_check(
            checks,
            "pass",
            "top_day_concentration",
            f"Top 10 positive days explain {top_10_share:.1%} of positive validation returns.",
        )


def evaluate_state_checks(checks: list[dict[str, str]], state_attribution: dict[str, list[dict[str, Any]]]) -> None:
    if not state_attribution:
        add_check(checks, "warn", "state_attribution_available", "No state columns were available for attribution.")
        return
    add_check(checks, "pass", "state_attribution_available", f"Built attribution for {len(state_attribution)} state columns.")

    rare_concentrations = []
    for column, groups in state_attribution.items():
        for group in groups:
            day_share = numeric(group.get("day_share")) or 0.0
            positive_share = numeric(group.get("positive_return_share")) or 0.0
            if day_share <= RARE_STATE_DAY_SHARE and positive_share >= RARE_STATE_POSITIVE_SHARE:
                rare_concentrations.append(f"{column}={group.get('value')}")
    if rare_concentrations:
        add_check(
            checks,
            "warn",
            "rare_state_return_concentration",
            "Positive validation returns are concentrated in rare states: "
            + ", ".join(rare_concentrations[:5])
            + ("." if len(rare_concentrations) <= 5 else ", ..."),
        )
    else:
        add_check(checks, "pass", "rare_state_return_concentration", "Positive returns are not concentrated in rare state buckets.")


def summarize_window(rows: list[dict[str, str]]) -> dict[str, Any]:
    returns = [daily_return(row) for row in rows]
    compounded = compounded_return(returns)
    summary = {
        "days": len(rows),
        "start_date": rows[0].get("date") if rows else "",
        "end_date": rows[-1].get("date") if rows else "",
        "compounded_return": compounded,
        "cagr": annualized_return(compounded, len(rows)),
        "arithmetic_return_sum": sum(returns),
        "mean_daily_return": sum(returns) / len(returns) if returns else None,
        "positive_days": len([value for value in returns if value > 0]),
        "negative_days": len([value for value in returns if value < 0]),
        "positive_return_sum": sum(value for value in returns if value > 0),
        "negative_return_sum": sum(value for value in returns if value < 0),
        "mean_gross_exposure": mean_numeric(row.get("gross_exposure") for row in rows),
        "mean_cash_weight": mean_numeric(row.get("cash_weight") for row in rows),
        "mean_turnover": mean_numeric(row.get("turnover") for row in rows),
    }
    summary.update(drawdown_summary(rows))
    return summary


def concentration_summary(
    rows: list[dict[str, str]],
    top_positive_days: list[dict[str, str]],
    top_negative_days: list[dict[str, str]],
) -> dict[str, Any]:
    positive_sum = sum(max(daily_return(row), 0.0) for row in rows)
    negative_abs_sum = abs(sum(min(daily_return(row), 0.0) for row in rows))
    top_5_positive = sum(daily_return(row) for row in top_positive_days[:5])
    top_10_positive = sum(daily_return(row) for row in top_positive_days[:10])
    top_5_negative_abs = abs(sum(daily_return(row) for row in top_negative_days[:5]))
    top_10_negative_abs = abs(sum(daily_return(row) for row in top_negative_days[:10]))
    return {
        "positive_return_sum": positive_sum,
        "negative_abs_return_sum": negative_abs_sum,
        "top_5_positive_return_sum": top_5_positive,
        "top_10_positive_return_sum": top_10_positive,
        "top_5_positive_share_of_positive_returns": safe_ratio(top_5_positive, positive_sum),
        "top_10_positive_share_of_positive_returns": safe_ratio(top_10_positive, positive_sum),
        "top_5_negative_abs_return_sum": top_5_negative_abs,
        "top_10_negative_abs_return_sum": top_10_negative_abs,
        "top_5_negative_share_of_negative_returns": safe_ratio(top_5_negative_abs, negative_abs_sum),
        "top_10_negative_share_of_negative_returns": safe_ratio(top_10_negative_abs, negative_abs_sum),
    }


def build_state_attribution(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for column in STATE_COLUMNS:
        if any(column in row for row in rows):
            result[column] = attribution_for_column(rows, column)
    return result


def attribution_for_column(rows: list[dict[str, str]], column: str) -> list[dict[str, Any]]:
    total_positive = sum(max(daily_return(row), 0.0) for row in rows)
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        value = derived_value(row, column)
        groups.setdefault(value, []).append(row)

    summaries = []
    for value, group_rows in groups.items():
        returns = [daily_return(row) for row in group_rows]
        positive_sum = sum(value for value in returns if value > 0)
        summaries.append(
            {
                "value": value,
                "days": len(group_rows),
                "day_share": safe_ratio(len(group_rows), len(rows)),
                "arithmetic_return_sum": sum(returns),
                "mean_daily_return": sum(returns) / len(returns) if returns else None,
                "compounded_return": compounded_return(returns),
                "positive_return_sum": positive_sum,
                "positive_return_share": safe_ratio(positive_sum, total_positive),
                "negative_return_sum": sum(value for value in returns if value < 0),
                "mean_gross_exposure": mean_numeric(row.get("gross_exposure") for row in group_rows),
                "mean_cash_weight": mean_numeric(row.get("cash_weight") for row in group_rows),
            }
        )
    return sorted(summaries, key=lambda item: abs(float(item["arithmetic_return_sum"] or 0.0)), reverse=True)


def top_return_days(rows: list[dict[str, str]], descending: bool, limit: int) -> list[dict[str, str]]:
    filtered = [row for row in rows if daily_return(row) > 0] if descending else [row for row in rows if daily_return(row) < 0]
    return sorted(filtered, key=daily_return, reverse=descending)[:limit]


def summarize_day(row: dict[str, str]) -> dict[str, Any]:
    keys = [
        "date",
        "daily_return",
        "equity",
        "decision_label",
        "feedback_stage",
        "gross_exposure",
        "cash_weight",
        "top_leverage_active",
        "top_weight_signal",
        "cash_active",
        "stage0_guard_active",
        "stage1_guard_active",
        "stage2_guard_active",
        "outer_active",
        "spy_below200_lag1",
        "spy_macd_neg_lag1",
        "breadth_weak_lag1",
    ]
    return {key: convert_value(row[key]) for key in keys if key in row}


def drawdown_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        return {"max_drawdown": None, "max_drawdown_date": ""}

    equity = []
    if all(row.get("equity") not in {None, ""} for row in rows):
        equity = [numeric(row.get("equity")) for row in rows]
    if not equity or any(value is None for value in equity):
        level = 1.0
        equity = []
        for row in rows:
            level *= 1.0 + daily_return(row)
            equity.append(level)

    peak = equity[0]
    max_dd = 0.0
    max_dd_date = rows[0].get("date", "")
    underwater_days = 0
    for row, level in zip(rows, equity):
        if level is None:
            continue
        peak = max(peak, level)
        drawdown = level / peak - 1.0 if peak else 0.0
        if drawdown < 0:
            underwater_days += 1
        if drawdown < max_dd:
            max_dd = drawdown
            max_dd_date = row.get("date", "")
    return {
        "max_drawdown": max_dd,
        "max_drawdown_date": max_dd_date,
        "underwater_days": underwater_days,
        "underwater_day_share": safe_ratio(underwater_days, len(rows)),
    }


def render_validation_attribution_markdown(audit: dict[str, Any]) -> str:
    summary = audit.get("summary", {})
    concentration = audit.get("concentration", {})
    lines = [
        "# Validation Attribution",
        "",
        f"Run ID: `{audit['run_id']}`",
        f"Variant: `{audit['variant_id']}`",
        f"Recommendation: `{audit['recommendation']}`",
        f"Window: `{audit['window']['start_date']}` to `{audit['window']['end_date']}`",
        "",
        "## Summary",
        "",
        f"- Days: `{summary.get('days')}`",
        f"- Compounded return: `{format_decimal(summary.get('compounded_return'))}`",
        f"- CAGR: `{format_decimal(summary.get('cagr'))}`",
        f"- Max drawdown: `{format_decimal(summary.get('max_drawdown'))}` on `{summary.get('max_drawdown_date')}`",
        f"- Mean gross exposure: `{format_decimal(summary.get('mean_gross_exposure'))}`",
        f"- Mean cash weight: `{format_decimal(summary.get('mean_cash_weight'))}`",
        "",
        "## Checks",
        "",
    ]
    for check in audit["checks"]:
        lines.append(f"- {check['status'].upper()}: {check['code']} - {check['message']}")

    lines.extend(
        [
            "",
            "## Concentration",
            "",
            f"- Top 5 positive share: `{format_decimal(concentration.get('top_5_positive_share_of_positive_returns'))}`",
            f"- Top 10 positive share: `{format_decimal(concentration.get('top_10_positive_share_of_positive_returns'))}`",
            f"- Top 5 negative share: `{format_decimal(concentration.get('top_5_negative_share_of_negative_returns'))}`",
            f"- Top 10 negative share: `{format_decimal(concentration.get('top_10_negative_share_of_negative_returns'))}`",
            "",
            "## Top Positive Days",
            "",
        ]
    )
    lines.extend(render_days(audit.get("top_positive_days", [])))
    lines.extend(["", "## Top Negative Days", ""])
    lines.extend(render_days(audit.get("top_negative_days", [])))

    lines.extend(["", "## Decision Attribution", ""])
    lines.extend(render_groups(audit.get("state_attribution", {}).get("decision_label", [])))
    lines.extend(["", "## Exposure Attribution", ""])
    lines.extend(render_groups(audit.get("exposure_attribution", [])))
    lines.extend(["", "## Year Attribution", ""])
    lines.extend(render_groups(audit.get("year_attribution", [])))

    lines.extend(["", "## Artifacts", ""])
    for name, path in sorted(audit.get("artifacts", {}).items()):
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def render_days(days: list[dict[str, Any]]) -> list[str]:
    if not days:
        return ["- None."]
    return [
        f"- `{day.get('date')}`: return `{format_decimal(day.get('daily_return'))}`, "
        f"decision `{day.get('decision_label', '')}`, exposure `{format_decimal(day.get('gross_exposure'))}`"
        for day in days
    ]


def render_groups(groups: list[dict[str, Any]]) -> list[str]:
    if not groups:
        return ["- None."]
    return [
        f"- `{group.get('value')}`: days `{group.get('days')}`, "
        f"return sum `{format_decimal(group.get('arithmetic_return_sum'))}`, "
        f"positive share `{format_decimal(group.get('positive_return_share'))}`"
        for group in groups
    ]


def daily_curve_path(run_dir: Path, manifest: dict[str, Any]) -> Path:
    artifacts = manifest.get("artifacts", {}) if isinstance(manifest.get("artifacts"), dict) else {}
    source_daily = artifacts.get("source_daily_curve")
    if source_daily:
        return run_dir / source_daily
    source_dir = run_dir / "source_artifacts"
    candidates = sorted(path for path in source_dir.glob("*.csv") if path.name not in {
        "summary_by_window.csv",
        "rolling_1y_summary.csv",
        "bucket_summary.csv",
        "changed_day_summary.csv",
        "variant_specs.csv",
    })
    if candidates:
        return candidates[0]
    return run_dir / "equity_curve.csv"


def validation_audit_source_exists(run_dir: Path) -> bool:
    manifest = read_json(run_dir / "manifest.json")
    return daily_curve_path(run_dir, manifest).exists()


def derived_value(row: dict[str, str], column: str) -> str:
    if column == "exposure_bucket":
        exposure = numeric(row.get("gross_exposure"))
        if exposure is None:
            return "unknown"
        if exposure <= 0.10:
            return "cash"
        if exposure < 0.95:
            return "partial"
        if exposure <= 1.05:
            return "full"
        return "leveraged"
    if column == "year":
        return str(row.get("date", ""))[:4] or "unknown"
    return str(row.get(column, "unknown") or "blank")


def recommendation_from_checks(checks: list[dict[str, str]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "blocked"
    if any(check["status"] == "warn" for check in checks):
        return "review_required"
    return "explained"


def add_check(checks: list[dict[str, str]], status: str, code: str, message: str) -> None:
    checks.append({"status": status, "code": code, "message": message})


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def daily_return(row: dict[str, str]) -> float:
    return numeric(row.get("daily_return")) or 0.0


def compounded_return(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = 1.0
    for value in returns:
        equity *= 1.0 + value
    return equity - 1.0


def annualized_return(compounded: float | None, days: int) -> float | None:
    if compounded is None or days <= 0 or compounded <= -1.0:
        return None
    return (1.0 + compounded) ** (252.0 / days) - 1.0


def mean_numeric(values: Any) -> float | None:
    parsed = [numeric(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return sum(parsed) / len(parsed) if parsed else None


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    numerator_value = numeric(numerator)
    denominator_value = numeric(denominator)
    if numerator_value is None or denominator_value in {None, 0.0}:
        return None
    return numerator_value / denominator_value


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
