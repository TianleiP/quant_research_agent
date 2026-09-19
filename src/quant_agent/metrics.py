from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from quant_agent.artifacts import read_json


CORE_METRICS = [
    ("cagr", "CAGR", "{:.1%}"),
    ("max_drawdown", "Max Drawdown", "{:.1%}"),
    ("calmar", "Calmar", "{:.2f}"),
    ("ann_vol", "Annualized Vol", "{:.1%}"),
    ("sharpe", "Sharpe", "{:.2f}"),
    ("sortino", "Sortino", "{:.2f}"),
    ("turnover_per_year", "Turnover", "{:.1f} / year"),
    ("exposure", "Exposure", "{:.1%}"),
    ("number_of_trades", "Trades", "{:.0f}"),
]


def load_metrics(run_dir: Path) -> dict[str, Any]:
    return dict(read_json(run_dir / "metrics.json"))


def format_metrics_summary(metrics: dict[str, Any]) -> str:
    lines = []
    for key, label, fmt in CORE_METRICS:
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, (int, float)):
            rendered = fmt.format(value)
        else:
            rendered = str(value)
        lines.append(f"{label}: {rendered}")
    return "\n".join(lines) if lines else "No core metrics available."


def enrich_metrics(
    metrics: dict[str, Any],
    equity_curve: list[dict[str, Any]] | None = None,
    trades: list[dict[str, Any]] | None = None,
    positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = dict(metrics)
    equity_rows = normalize_equity_rows(equity_curve or [])
    daily_returns = [row["daily_return"] for row in equity_rows if row.get("daily_return") is not None]

    if equity_rows:
        enriched.setdefault("observations", len(equity_rows))
        first_equity = equity_rows[0].get("equity")
        last_equity = equity_rows[-1].get("equity")
        if first_equity and last_equity:
            enriched.setdefault("total_return", last_equity / first_equity - 1.0)
        drawdown = drawdown_summary(equity_rows)
        for key, value in drawdown.items():
            enriched.setdefault(key, value)
        period_returns = period_return_summary(equity_rows)
        enriched.setdefault("yearly_returns", period_returns["yearly_returns"])
        enriched.setdefault("monthly_returns", period_returns["monthly_returns"])
        enriched.setdefault("best_day", best_worst_period(equity_rows, "daily_return", best=True))
        enriched.setdefault("worst_day", best_worst_period(equity_rows, "daily_return", best=False))
        enriched.setdefault("best_month", period_returns["best_month"])
        enriched.setdefault("worst_month", period_returns["worst_month"])

    if daily_returns:
        enriched.setdefault("ann_vol", annualized_volatility(daily_returns))
        if "sharpe" not in enriched:
            enriched["sharpe"] = annualized_sharpe(daily_returns)
        if "sortino" not in enriched:
            enriched["sortino"] = annualized_sortino(daily_returns)

    trades = trades or []
    positions = positions or []
    enriched.setdefault("number_of_trades", len(trades))
    enriched.setdefault("number_of_position_rows", len(positions))
    exposure = average_position_exposure(positions)
    if exposure is not None:
        enriched.setdefault("average_position_gross_exposure", exposure)
    return enriched


def normalize_equity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    previous_equity: float | None = None
    for row in rows:
        dt = parse_date(row.get("date") or row.get("decision_date"))
        equity = numeric(row.get("equity"))
        daily_return = numeric(row.get("daily_return"))
        if daily_return is None and equity is not None and previous_equity not in {None, 0}:
            daily_return = equity / float(previous_equity) - 1.0
        if equity is not None:
            previous_equity = equity
        normalized.append({"date": dt, "equity": equity, "daily_return": daily_return})
    return [row for row in normalized if row.get("date") is not None or row.get("equity") is not None]


def annualized_volatility(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    return pstdev(returns) * math.sqrt(252)


def annualized_sharpe(returns: list[float]) -> float:
    vol = annualized_volatility(returns)
    if vol == 0:
        return 0.0
    return mean(returns) * 252 / vol


def annualized_sortino(returns: list[float]) -> float:
    downside = [min(value, 0.0) for value in returns]
    if len(downside) < 2:
        return 0.0
    downside_vol = pstdev(downside) * math.sqrt(252)
    if downside_vol == 0:
        return 0.0
    return mean(returns) * 252 / downside_vol


def drawdown_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    peak = None
    peak_date = None
    worst = 0.0
    worst_date = None
    recovery_days = None
    in_drawdown_from = None
    worst_unrecovered_peak = None
    for row in rows:
        equity = row.get("equity")
        dt = row.get("date")
        if equity is None:
            continue
        if peak is None or equity > peak:
            if worst_unrecovered_peak and recovery_days is None and dt and worst_date:
                recovery_days = max((dt - worst_date).days, 0)
            peak = equity
            peak_date = dt
            in_drawdown_from = None
        if not peak:
            continue
        drawdown = equity / peak - 1.0
        if drawdown < 0 and in_drawdown_from is None:
            in_drawdown_from = peak_date
        if drawdown < worst:
            worst = drawdown
            worst_date = dt
            worst_unrecovered_peak = in_drawdown_from
            recovery_days = None
    return {
        "max_drawdown": worst,
        "max_drawdown_date": worst_date.isoformat() if worst_date else None,
        "drawdown_recovery_days": recovery_days,
    }


def period_return_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    monthly: dict[str, list[float]] = defaultdict(list)
    yearly: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        dt = row.get("date")
        ret = row.get("daily_return")
        if dt is None or ret is None:
            continue
        monthly[dt.strftime("%Y-%m")].append(ret)
        yearly[dt.strftime("%Y")].append(ret)
    monthly_returns = {key: compound_returns(values) for key, values in sorted(monthly.items())}
    yearly_returns = {key: compound_returns(values) for key, values in sorted(yearly.items())}
    return {
        "monthly_returns": monthly_returns,
        "yearly_returns": yearly_returns,
        "best_month": best_worst_mapping(monthly_returns, best=True),
        "worst_month": best_worst_mapping(monthly_returns, best=False),
    }


def compound_returns(values: list[float]) -> float:
    total = 1.0
    for value in values:
        total *= 1.0 + value
    return total - 1.0


def best_worst_mapping(values: dict[str, float], best: bool) -> dict[str, Any]:
    if not values:
        return {}
    key = max(values, key=values.get) if best else min(values, key=values.get)
    return {"period": key, "return": values[key]}


def best_worst_period(rows: list[dict[str, Any]], key: str, best: bool) -> dict[str, Any]:
    candidates = [row for row in rows if row.get(key) is not None]
    if not candidates:
        return {}
    selected = max(candidates, key=lambda row: row[key]) if best else min(candidates, key=lambda row: row[key])
    dt = selected.get("date")
    return {"date": dt.isoformat() if dt else None, "return": selected.get(key)}


def average_position_exposure(positions: list[dict[str, Any]]) -> float | None:
    by_date: dict[str, float] = defaultdict(float)
    for row in positions:
        date = str(row.get("date") or row.get("decision_date") or "")
        weight = numeric(row.get("weight") or row.get("target_weight"))
        if not date or weight is None:
            continue
        by_date[date] += abs(weight)
    if not by_date:
        return None
    return mean(by_date.values())


def parse_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except ValueError:
        return None
