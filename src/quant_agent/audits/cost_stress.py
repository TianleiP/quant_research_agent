from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from quant_agent.artifacts import read_csv_rows
from quant_agent.metrics import load_metrics


DEFAULT_SCENARIOS = [
    {"name": "base_costs", "transaction_cost_bps": 1.0, "slippage_bps": 1.0},
    {"name": "moderate_stress", "transaction_cost_bps": 5.0, "slippage_bps": 5.0},
    {"name": "high_stress", "transaction_cost_bps": 10.0, "slippage_bps": 10.0},
]
MIN_STRESSED_CAGR = 0.0
MAX_STRESSED_DRAWDOWN = -0.45


def run_cost_stress_audit(run_dir: Path) -> Path:
    audit = build_cost_stress_audit(run_dir)
    audits_dir = run_dir / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    json_path = audits_dir / "cost_stress.json"
    markdown_path = audits_dir / "cost_stress.md"
    json_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_cost_stress_markdown(audit), encoding="utf-8")
    return markdown_path


def build_cost_stress_audit(
    run_dir: Path,
    scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metrics = load_metrics(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    equity_path = artifact_path(run_dir, manifest, "equity_curve", run_dir / "equity_curve.csv")
    equity_rows = read_csv_rows(equity_path) if equity_path.exists() else []
    scenario_results = [
        stress_scenario(metrics, equity_rows, scenario)
        for scenario in (scenarios or DEFAULT_SCENARIOS)
    ]
    checks: list[dict[str, str]] = []
    add_check(
        checks,
        "pass" if equity_rows else "warn",
        "daily_curve_available",
        f"Loaded {len(equity_rows)} daily rows." if equity_rows else "Daily equity curve is missing; using aggregate fallback.",
    )
    if not any(result.get("uses_daily_turnover") for result in scenario_results):
        add_check(
            checks,
            "warn",
            "daily_turnover_missing",
            "Daily turnover was unavailable; stress used turnover_per_year fallback when present.",
        )
    worst = scenario_results[-1] if scenario_results else {}
    stressed_cagr = numeric(worst.get("cagr"))
    stressed_drawdown = numeric(worst.get("max_drawdown"))
    add_check(
        checks,
        "pass" if stressed_cagr is not None and stressed_cagr > MIN_STRESSED_CAGR else "warn",
        "high_stress_cagr_positive",
        f"High-stress CAGR is {stressed_cagr:.4f}." if stressed_cagr is not None else "High-stress CAGR could not be estimated.",
    )
    add_check(
        checks,
        "pass" if stressed_drawdown is not None and stressed_drawdown > MAX_STRESSED_DRAWDOWN else "warn",
        "high_stress_drawdown_threshold",
        f"High-stress max drawdown is {stressed_drawdown:.4f}." if stressed_drawdown is not None else "High-stress drawdown could not be estimated.",
    )
    recommendation = recommendation_from_checks(checks)
    return {
        "run_id": run_dir.name,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "recommendation": recommendation,
        "baseline": {
            key: metrics[key]
            for key in ["cagr", "max_drawdown", "sharpe", "turnover_per_year", "number_of_trades"]
            if key in metrics
        },
        "scenarios": scenario_results,
        "checks": checks,
        "artifacts": {
            "cost_stress_json": "audits/cost_stress.json",
            "cost_stress_markdown": "audits/cost_stress.md",
            "equity_curve": relative_path(equity_path, run_dir),
        },
    }


def stress_scenario(
    metrics: dict[str, Any],
    equity_rows: list[dict[str, str]],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    cost_bps = numeric(scenario.get("transaction_cost_bps")) or 0.0
    slippage_bps = numeric(scenario.get("slippage_bps")) or 0.0
    total_bps = cost_bps + slippage_bps
    returns: list[float] = []
    uses_daily_turnover = False
    for row in equity_rows:
        daily_return = numeric(row.get("daily_return"))
        if daily_return is None:
            continue
        turnover = numeric(row.get("turnover"))
        if turnover is not None:
            uses_daily_turnover = True
        turnover = turnover or 0.0
        returns.append(daily_return - turnover * total_bps / 10_000.0)
    if returns:
        return {
            "name": scenario.get("name", f"{total_bps:g}bps"),
            "transaction_cost_bps": cost_bps,
            "slippage_bps": slippage_bps,
            "total_cost_bps": total_bps,
            "uses_daily_turnover": uses_daily_turnover,
            **metrics_from_returns(returns),
        }

    turnover_per_year = numeric(metrics.get("turnover_per_year")) or 0.0
    cagr = numeric(metrics.get("cagr"))
    stressed_cagr = cagr - turnover_per_year * total_bps / 10_000.0 if cagr is not None else None
    return {
        "name": scenario.get("name", f"{total_bps:g}bps"),
        "transaction_cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
        "total_cost_bps": total_bps,
        "uses_daily_turnover": False,
        "cagr": stressed_cagr,
        "max_drawdown": metrics.get("max_drawdown"),
        "sharpe": metrics.get("sharpe"),
        "method": "aggregate_turnover_fallback",
    }


def metrics_from_returns(returns: list[float]) -> dict[str, Any]:
    total = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for ret in returns:
        total *= 1.0 + ret
        peak = max(peak, total)
        if peak:
            max_drawdown = min(max_drawdown, total / peak - 1.0)
    years = max(len(returns) / 252.0, 1 / 252)
    cagr = total ** (1.0 / years) - 1.0
    vol = pstdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    sharpe = mean(returns) * 252 / vol if vol else 0.0
    return {
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "ann_vol": vol,
        "method": "daily_turnover_adjustment",
    }


def render_cost_stress_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Cost And Slippage Stress Audit",
        "",
        f"Run ID: `{audit['run_id']}`",
        f"Recommendation: `{audit['recommendation']}`",
        "",
        "## Checks",
        "",
    ]
    for check in audit["checks"]:
        lines.append(f"- {check['status'].upper()}: {check['code']} - {check['message']}")
    lines.extend(["", "## Scenarios", ""])
    for row in audit["scenarios"]:
        cagr = format_number(row.get("cagr"), "{:.2%}")
        maxdd = format_number(row.get("max_drawdown"), "{:.2%}")
        sharpe = format_number(row.get("sharpe"), "{:.2f}")
        lines.append(
            f"- `{row['name']}` total_cost={row['total_cost_bps']:.1f} bps: "
            f"CAGR {cagr}, MaxDD {maxdd}, Sharpe {sharpe}"
        )
    lines.extend(["", "## Artifacts", ""])
    for name, path in sorted(audit.get("artifacts", {}).items()):
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def recommendation_from_checks(checks: list[dict[str, str]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "blocked"
    if any(check["status"] == "warn" for check in checks):
        return "review_required"
    return "passed"


def add_check(checks: list[dict[str, str]], status: str, code: str, message: str) -> None:
    checks.append({"status": status, "code": code, "message": message})


def artifact_path(run_dir: Path, manifest: dict[str, Any], key: str, fallback: Path) -> Path:
    artifacts = manifest.get("artifacts", {}) if isinstance(manifest.get("artifacts"), dict) else {}
    return run_dir / artifacts[key] if artifacts.get(key) else fallback


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def format_number(value: Any, fmt: str) -> str:
    parsed = numeric(value)
    return fmt.format(parsed) if parsed is not None else "n/a"


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
