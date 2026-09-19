from __future__ import annotations

from pathlib import Path
from typing import Any

from quant_agent.replay.artifacts import (
    ReplayArtifacts,
    first_row_for_date,
    load_replay_artifacts,
    rows_for_date,
)


def replay_date(run_dir: Path, date: str) -> str:
    artifacts = load_replay_artifacts(run_dir)
    row = first_row_for_date(artifacts.decision_log, date, "decision_date", "date")
    if not row:
        return f"No decision log entry found for {date} in {run_dir.name}."

    positions = rows_for_date(artifacts.positions, date, "date", "decision_date")
    trades = rows_for_date(artifacts.trades, date, "date", "execution_date", "decision_date")
    rankings = rows_for_date(artifacts.rankings, date, "date", "decision_date")

    sections = [
        f"Date: {date}",
        "",
        "Information available before decision:",
        f"- Prices/signals through {row.get('available_through', 'unknown')}",
        f"- Signal date: {row.get('signal_date', row.get('available_through', 'unknown'))}",
        "",
        "Strategy state:",
        f"- Market gate: {row.get('market_gate', row.get('decision', 'unknown'))}",
        f"- Feedback stage: {row.get('feedback_stage', 'unknown')}",
        f"- Gross exposure: {format_percent(row.get('gross_exposure'))}",
        f"- Cash weight: {format_percent(row.get('cash_weight'))}",
        "",
        "Holdings:",
        *render_holdings(positions),
        "",
        "Trade changes:",
        *render_trades(trades),
        "",
        "Ranking context:",
        *render_rankings(rankings),
        "",
        "Reason:",
        f"- {decision_reason(row, bool(positions or rankings))}",
        "",
        "Execution:",
        f"- Orders executed on {row.get('execution_date', date)}.",
    ]
    return "\n".join(sections)


def render_holdings(rows: list[dict[str, str]], limit: int = 10) -> list[str]:
    if not rows:
        return ["- Position-level holdings are not available for this date."]

    cash_rows = [row for row in rows if is_cash_symbol(row.get("symbol", ""))]
    security_rows = [row for row in rows if not is_cash_symbol(row.get("symbol", ""))]
    ordered = sorted(
        security_rows,
        key=lambda row: (
            parse_float(row.get("rank")) is None,
            parse_float(row.get("rank")) or 0,
            -abs(parse_float(row.get("weight")) or 0),
            row.get("symbol", ""),
        ),
    )
    total_weight = sum(abs(parse_float(row.get("weight")) or 0) for row in security_rows)
    lines = [
        f"- Securities held: {len(security_rows)}",
        f"- Gross security weight from holdings: {format_percent(total_weight)}",
    ]
    if cash_rows:
        cash_weight = sum(parse_float(row.get("weight")) or 0 for row in cash_rows)
        lines.append(f"- Cash position: {format_percent(cash_weight)}")
    lines.append("- Top holdings:")
    for row in ordered[:limit]:
        lines.append(f"  - {holding_line(row)}")
    if len(ordered) > limit:
        lines.append(f"  - ... {len(ordered) - limit} more")
    return lines


def render_trades(rows: list[dict[str, str]], limit: int = 10) -> list[str]:
    if not rows:
        return ["- No trade changes recorded for this date."]

    ordered = sorted(
        rows,
        key=lambda row: (
            is_cash_symbol(row.get("symbol", "")),
            -abs(parse_float(row.get("weight_change")) or parse_float(row.get("turnover")) or 0),
            row.get("symbol", ""),
        ),
    )
    total_turnover = sum(abs(parse_float(row.get("turnover")) or parse_float(row.get("weight_change")) or 0) for row in rows)
    lines = [
        f"- Trade rows: {len(rows)}",
        f"- Total turnover: {format_percent(total_turnover)}",
    ]
    for row in ordered[:limit]:
        lines.append(f"  - {trade_line(row)}")
    if len(ordered) > limit:
        lines.append(f"  - ... {len(ordered) - limit} more")
    return lines


def render_rankings(rows: list[dict[str, str]], limit: int = 10) -> list[str]:
    if not rows:
        return ["- Ranking rows are not available for this date."]

    ordered = sorted(
        rows,
        key=lambda row: (
            parse_float(row.get("rank")) is None,
            parse_float(row.get("rank")) or 0,
            row.get("symbol", ""),
        ),
    )
    lines = [f"- Ranking rows: {len(rows)}", "- Top ranks:"]
    for row in ordered[:limit]:
        lines.append(f"  - {ranking_line(row)}")
    if len(ordered) > limit:
        lines.append(f"  - ... {len(ordered) - limit} more")
    return lines


def holding_line(row: dict[str, str]) -> str:
    parts = [str(row.get("symbol", "unknown")), format_percent(row.get("weight"))]
    if row.get("rank"):
        parts.append(f"rank {format_number(row.get('rank'))}")
    if row.get("sleeve"):
        parts.append(f"sleeve {row['sleeve']}")
    if row.get("score"):
        parts.append(f"score {format_number(row.get('score'))}")
    return " | ".join(parts)


def trade_line(row: dict[str, str]) -> str:
    symbol = str(row.get("symbol", "unknown"))
    change = parse_float(row.get("weight_change"))
    direction = "increase" if change is not None and change > 0 else "decrease" if change is not None and change < 0 else "change"
    return (
        f"{symbol} {direction} {format_percent(change)} "
        f"from {format_percent(row.get('prev_weight'))} to {format_percent(row.get('target_weight'))}"
    )


def ranking_line(row: dict[str, str]) -> str:
    parts = [f"rank {format_number(row.get('rank'))}", str(row.get("symbol", "unknown"))]
    if row.get("weight"):
        parts.append(f"weight {format_percent(row.get('weight'))}")
    if row.get("sleeve"):
        parts.append(f"sleeve {row['sleeve']}")
    if row.get("eligible"):
        parts.append(f"eligible {row['eligible']}")
    if row.get("score"):
        parts.append(f"score {format_number(row.get('score'))}")
    return " | ".join(parts)


def decision_reason(row: dict[str, str], has_position_context: bool) -> str:
    reason = row.get("reason") or "No reason recorded."
    if has_position_context:
        reason = reason.replace("; per-symbol ranks are not saved", "; position-level artifacts are available below")
        reason = reason.replace("per-symbol ranks are not saved", "position-level artifacts are available below")
    return reason


def is_cash_symbol(symbol: str) -> bool:
    return symbol.upper() in {"__CASH__", "CASH", "USD"}


def format_percent(value: Any) -> str:
    number = parse_float(value)
    if number is None:
        return "unknown"
    return f"{number:.1%}"


def format_number(value: Any) -> str:
    number = parse_float(value)
    if number is None:
        return str(value or "unknown")
    if number.is_integer():
        return str(int(number))
    return f"{number:.4g}"


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None
