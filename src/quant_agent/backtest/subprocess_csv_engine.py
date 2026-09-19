from __future__ import annotations

import csv
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.config import StrategyConfig
from quant_agent.models.run import BacktestResult
from quant_agent.utils.hashing import file_sha256


SUMMARY_NUMERIC_FIELDS = [
    "days",
    "ann_return",
    "max_drawdown",
    "sharpe",
    "ann_vol",
    "cum_return",
    "delta_cagr_vs_control",
    "delta_maxdd_vs_control",
    "mean_cash_weight",
    "mean_gross_exposure",
    "top2_active_share",
    "top2_partial_share",
    "top2_full_share",
    "top2_leverage_share",
]

OPTIONAL_OUTPUT_KEYS = [
    "metadata_json",
    "rolling_1y_summary_csv",
    "bucket_summary_csv",
    "changed_day_summary_csv",
    "variant_specs_csv",
    "report_md",
    "positions_csv",
    "trades_csv",
    "rankings_csv",
]


def run_subprocess_csv_backtest(config: StrategyConfig) -> BacktestResult:
    subprocess_config = dict(config.raw.get("subprocess", {}))
    outputs_config = dict(config.raw.get("outputs", {}))

    working_dir = resolve_path(subprocess_config.get("working_dir", "."))
    command = subprocess_config.get("command", [])
    if not isinstance(command, list) or not command:
        raise ValueError("subprocess.command must be a non-empty YAML list.")
    command = [str(part) for part in command]

    run_before_ingest = bool(subprocess_config.get("run_before_ingest", False))
    timeout_seconds = subprocess_config.get("timeout_seconds")

    started_at = utc_now()
    start_epoch = time.time()
    completed = None
    if run_before_ingest:
        completed = subprocess.run(
            command,
            cwd=working_dir,
            env=external_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "External backtest failed with exit code "
                f"{completed.returncode}.\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

    expected_variant_id = require_string(outputs_config, "expected_variant_id")
    primary_window = str(outputs_config.get("primary_window", "full_2000_2026"))
    summary_path = resolve_output_path(working_dir, outputs_config, "summary_csv")
    daily_curve_path = resolve_output_path(working_dir, outputs_config, "daily_curve_csv")
    source_metadata_path = optional_output_path(working_dir, outputs_config, "metadata_json")
    positions_path = optional_output_path(working_dir, outputs_config, "positions_csv")
    trades_path = optional_output_path(working_dir, outputs_config, "trades_csv")

    validate_source_file(summary_path, run_before_ingest, start_epoch)
    validate_source_file(daily_curve_path, run_before_ingest, start_epoch)

    summary_rows = read_csv_dicts(summary_path)
    variant_summary_rows = [
        row for row in summary_rows if row.get("variant_id") == expected_variant_id
    ]
    if not variant_summary_rows:
        raise ValueError(
            f"Variant {expected_variant_id!r} was not found in {summary_path}."
        )

    primary_row = find_primary_window(variant_summary_rows, primary_window)
    daily_rows = read_csv_dicts(daily_curve_path)
    if not daily_rows:
        raise ValueError(f"Daily curve is empty: {daily_curve_path}")

    warnings = build_warnings(
        run_before_ingest=run_before_ingest,
        source_metadata_path=source_metadata_path,
        expected_variant_id=expected_variant_id,
    )
    source_metadata = read_json_if_exists(source_metadata_path)

    metrics = build_metrics(primary_row, variant_summary_rows)
    metrics["source_daily_start"] = daily_rows[0].get("date")
    metrics["source_daily_end"] = daily_rows[-1].get("date")
    metrics["source_daily_rows"] = len(daily_rows)
    metrics["turnover_per_year"] = estimate_turnover_per_year(daily_rows)

    equity_curve = normalize_daily_curve(daily_rows)
    decision_log = build_decision_log(daily_rows)
    positions = normalize_daily_curve(read_csv_dicts(positions_path)) if positions_path and positions_path.exists() else []
    trades = normalize_daily_curve(read_csv_dicts(trades_path)) if trades_path and trades_path.exists() else []
    source_artifacts = collect_source_artifacts(
        working_dir=working_dir,
        outputs_config=outputs_config,
        required_paths={
            "summary_by_window": summary_path,
            "daily_curve": daily_curve_path,
        },
    )
    finished_at = utc_now()

    metadata = {
        "engine": "subprocess_csv",
        "working_dir": str(working_dir),
        "command": command,
        "subprocess_ran": run_before_ingest,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(time.time() - start_epoch, 3),
        "exit_code": completed.returncode if completed else None,
        "expected_variant_id": expected_variant_id,
        "primary_window": primary_window,
        "source_metadata": source_metadata,
        "source_metadata_trusted_for_live_variant": False,
        "warnings": warnings,
        "source_artifacts": {key: str(path) for key, path in source_artifacts.items()},
        "source_artifact_mtimes": {
            key: datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            for key, path in source_artifacts.items()
        },
        "source_artifact_checksums": {
            key: file_sha256(path) for key, path in source_artifacts.items()
        },
    }
    if completed:
        metadata["stdout_tail"] = tail_text(completed.stdout)
        metadata["stderr_tail"] = tail_text(completed.stderr)

    return BacktestResult(
        metrics=metrics,
        trades=trades,
        positions=positions,
        equity_curve=equity_curve,
        decision_log=decision_log,
        metadata=metadata,
    )


def build_metrics(
    primary_row: dict[str, str],
    variant_summary_rows: list[dict[str, str]],
) -> dict[str, Any]:
    ann_return = parse_number(primary_row.get("ann_return"))
    max_drawdown = parse_number(primary_row.get("max_drawdown"))
    ann_vol = parse_number(primary_row.get("ann_vol"))
    metrics: dict[str, Any] = {
        "cagr": ann_return,
        "max_drawdown": max_drawdown,
        "sharpe": parse_number(primary_row.get("sharpe")),
        "ann_vol": ann_vol,
        "cum_return": parse_number(primary_row.get("cum_return")),
        "exposure": parse_number(primary_row.get("mean_gross_exposure")),
        "mean_cash_weight": parse_number(primary_row.get("mean_cash_weight")),
        "top2_active_share": parse_number(primary_row.get("top2_active_share")),
        "top2_partial_share": parse_number(primary_row.get("top2_partial_share")),
        "top2_full_share": parse_number(primary_row.get("top2_full_share")),
        "primary_window": primary_row.get("window"),
        "variant_id": primary_row.get("variant_id"),
    }
    if ann_return is not None and max_drawdown not in (None, 0):
        metrics["calmar"] = ann_return / abs(max_drawdown)

    for row in variant_summary_rows:
        window = safe_metric_key(row.get("window", "unknown_window"))
        for field in SUMMARY_NUMERIC_FIELDS:
            value = parse_number(row.get(field))
            if value is not None:
                metrics[f"{window}_{field}"] = value

    return {key: value for key, value in metrics.items() if value is not None}


def build_decision_log(daily_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    decision_rows = []
    previous_date: str | None = None
    for row in daily_rows:
        decision_date = row.get("date", "")
        if not previous_date:
            previous_date = decision_date
            continue
        decision_rows.append(
            {
                "decision_date": decision_date,
                "available_through": previous_date,
                "signal_date": previous_date,
                "execution_date": decision_date,
                "market_gate": row.get("decision_label", ""),
                "feedback_stage": row.get("feedback_stage", ""),
                "gross_exposure": row.get("gross_exposure", ""),
                "cash_weight": row.get("cash_weight", ""),
                "reason": (
                    "Daily state replay from source curve. Lagged market/risk fields "
                    "are stored in *_lag1 columns; per-symbol ranks are not saved."
                ),
            }
        )
        previous_date = decision_date
    return decision_rows


def normalize_daily_curve(daily_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    normalized = []
    for row in daily_rows:
        normalized.append({key: parse_scalar(value) for key, value in row.items()})
    return normalized


def estimate_turnover_per_year(daily_rows: list[dict[str, str]]) -> float | None:
    turnovers = [
        value
        for value in (parse_number(row.get("turnover")) for row in daily_rows)
        if value is not None
    ]
    if not turnovers or len(daily_rows) < 2:
        return None
    years = len(daily_rows) / 252
    if years <= 0:
        return None
    return sum(abs(value) for value in turnovers) / years


def build_warnings(
    run_before_ingest: bool,
    source_metadata_path: Path | None,
    expected_variant_id: str,
) -> list[dict[str, str]]:
    warnings = []
    if not run_before_ingest:
        warnings.append(
            {
                "code": "ingested_existing_outputs",
                "message": (
                    "run_before_ingest is false, so quant-agent ingested existing "
                    "research outputs without a fresh subprocess run."
                ),
            }
        )

    source_metadata = read_json_if_exists(source_metadata_path)
    source_variant = source_metadata.get("current_variant_id")
    if source_variant and source_variant != expected_variant_id:
        warnings.append(
            {
                "code": "source_current_variant_mismatch",
                "message": (
                    "Source metadata current_variant_id="
                    f"{source_variant} differs from configured expected_variant_id="
                    f"{expected_variant_id}; using the configured variant."
                ),
            }
        )
    return warnings


def collect_source_artifacts(
    working_dir: Path,
    outputs_config: dict[str, Any],
    required_paths: dict[str, Path],
) -> dict[str, Path]:
    artifacts = dict(required_paths)
    for key in OPTIONAL_OUTPUT_KEYS:
        path = optional_output_path(working_dir, outputs_config, key)
        if path and path.exists():
            artifacts[key] = path
    return artifacts


def validate_source_file(path: Path, run_before_ingest: bool, start_epoch: float) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Expected output file does not exist: {path}")
    if run_before_ingest and path.stat().st_mtime < start_epoch - 1:
        raise ValueError(
            f"Expected output file was not refreshed by the subprocess: {path}"
        )


def find_primary_window(
    rows: list[dict[str, str]],
    primary_window: str,
) -> dict[str, str]:
    for row in rows:
        if row.get("window") == primary_window:
            return row
    return rows[0]


def resolve_output_path(
    working_dir: Path,
    outputs_config: dict[str, Any],
    key: str,
) -> Path:
    return resolve_path(require_string(outputs_config, key), base=working_dir)


def optional_output_path(
    working_dir: Path,
    outputs_config: dict[str, Any],
    key: str,
) -> Path | None:
    value = outputs_config.get(key)
    if not value:
        return None
    return resolve_path(str(value), base=working_dir)


def require_string(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required config value: {key}")
    return value


def resolve_path(value: Any, base: Path | None = None) -> Path:
    path = Path(str(value))
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.resolve()


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read_json_if_exists(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {"value": payload}


def parse_scalar(value: str) -> Any:
    parsed = parse_number(value)
    if parsed is not None:
        return parsed
    return value


def parse_number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def safe_metric_key(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def tail_text(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def external_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    return env
