from __future__ import annotations

import platform
import re
import shutil
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from quant_agent.artifacts import write_csv_rows, write_json
from quant_agent.config import StrategyConfig
from quant_agent.metrics import enrich_metrics
from quant_agent.models.manifest import RunManifest
from quant_agent.models.run import BacktestResult
from quant_agent.utils.git import current_git_commit
from quant_agent.utils.hashing import file_sha256


def save_run(
    config: StrategyConfig,
    result: BacktestResult,
    flags: list[dict[str, str]] | None = None,
    runs_root: Path = Path("runs"),
) -> tuple[Path, RunManifest]:
    run_dir = create_run_dir(config.strategy_name, runs_root)
    shutil.copyfile(config.path, run_dir / "config.yaml")
    metrics = enrich_metrics(
        result.metrics,
        equity_curve=result.equity_curve,
        trades=result.trades,
        positions=result.positions,
    )

    artifacts = {
        "config": "config.yaml",
        "manifest": "manifest.json",
        "metrics": "metrics.json",
        "metrics_csv": "metrics.csv",
        "trades": "trades.csv",
        "positions": "positions.csv",
        "equity_curve": "equity_curve.csv",
        "decision_log": "decision_log.csv",
        "flags": "flags.json",
        "short_report": "short_report.md",
        "engine_metadata": "engine_metadata.json",
    }

    write_json(run_dir / "metrics.json", metrics)
    write_csv_rows(run_dir / "metrics.csv", [metrics])
    write_csv_rows(run_dir / "trades.csv", result.trades)
    write_csv_rows(run_dir / "positions.csv", result.positions)
    write_csv_rows(run_dir / "equity_curve.csv", result.equity_curve)
    write_csv_rows(run_dir / "decision_log.csv", result.decision_log)
    write_json(run_dir / "flags.json", flags or [])
    write_json(run_dir / "engine_metadata.json", result.metadata)
    artifacts.update(copy_source_artifacts(run_dir, result.metadata.get("source_artifacts", {})))

    manifest = RunManifest(
        run_id=run_dir.name,
        strategy_name=config.strategy_name,
        engine=config.engine,
        config_hash=file_sha256(config.path),
        git_commit=current_git_commit(),
        data_version=str(config.raw.get("data", {}).get("version", "unknown")),
        universe_version=str(config.raw.get("data", {}).get("universe_version", "unknown")),
        backtest_start=str(config.raw.get("backtest", {}).get("start_date", "unknown")),
        backtest_end=str(config.raw.get("backtest", {}).get("end_date", "unknown")),
        rebalance_rule=str(config.raw.get("backtest", {}).get("rebalance_rule", "unknown")),
        transaction_cost_bps=config.raw.get("costs", {}).get("transaction_cost_bps"),
        slippage_bps=config.raw.get("costs", {}).get("slippage_bps"),
        metrics=metrics,
        artifacts=artifacts,
        run_metadata=result.metadata,
        reproducibility=build_reproducibility_metadata(config, result, metrics),
    )
    write_json(run_dir / "manifest.json", manifest.to_dict())
    return run_dir, manifest


def build_reproducibility_metadata(
    config: StrategyConfig,
    result: BacktestResult,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    subprocess_config = config.raw.get("subprocess", {}) if isinstance(config.raw.get("subprocess"), dict) else {}
    data_config = config.raw.get("data", {}) if isinstance(config.raw.get("data"), dict) else {}
    backtest_config = config.raw.get("backtest", {}) if isinstance(config.raw.get("backtest"), dict) else {}
    costs_config = config.raw.get("costs", {}) if isinstance(config.raw.get("costs"), dict) else {}
    source_metadata = result.metadata.get("source_metadata", {}) if isinstance(result.metadata.get("source_metadata"), dict) else {}
    return {
        "captured_at_local": datetime.now().isoformat(timespec="seconds"),
        "quant_agent_version": package_version(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "config_path": str(config.path),
        "config_hash": file_sha256(config.path),
        "git_commit": current_git_commit(),
        "engine": config.engine,
        "adapter_version": "subprocess_csv_v1" if config.engine == "subprocess_csv" else f"{config.engine}_v1",
        "subprocess_working_dir": subprocess_config.get("working_dir"),
        "subprocess_command": subprocess_config.get("command"),
        "source_script": source_metadata.get("source_script"),
        "data_version": data_config.get("version", "unknown"),
        "universe_version": data_config.get("universe_version", "unknown"),
        "backtest_start": backtest_config.get("start_date", "unknown"),
        "backtest_end": backtest_config.get("end_date", "unknown"),
        "rebalance_rule": backtest_config.get("rebalance_rule", "unknown"),
        "transaction_cost_bps": costs_config.get("transaction_cost_bps"),
        "slippage_bps": costs_config.get("slippage_bps"),
        "metrics_keys": sorted(metrics),
        "artifact_row_counts": {
            "trades": len(result.trades),
            "positions": len(result.positions),
            "equity_curve": len(result.equity_curve),
            "decision_log": len(result.decision_log),
        },
    }


def package_version() -> str:
    try:
        return version("quant-agent")
    except PackageNotFoundError:
        return "unknown"


def create_run_dir(strategy_name: str, runs_root: Path) -> Path:
    runs_root.mkdir(parents=True, exist_ok=True)
    date_part = datetime.now().strftime("%Y%m%d")
    safe_name = safe_run_component(strategy_name)
    prefix = f"{safe_name}_{date_part}"
    existing = sorted(runs_root.glob(f"{prefix}_*"))
    next_index = len(existing) + 1
    run_dir = runs_root / f"{prefix}_{next_index:03d}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "audits").mkdir(parents=True, exist_ok=True)
    return run_dir


def latest_run_dir(runs_root: Path = Path("runs")) -> Path:
    candidates = [path for path in runs_root.iterdir() if path.is_dir()]
    if not candidates:
        raise FileNotFoundError("No run folders found under runs/.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def safe_run_component(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "unnamed_strategy"


def copy_source_artifacts(run_dir: Path, source_artifacts: dict[str, str]) -> dict[str, str]:
    copied = {}
    if not source_artifacts:
        return copied

    target_dir = run_dir / "source_artifacts"
    target_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for key, source in sorted(source_artifacts.items()):
        source_path = Path(source)
        if not source_path.exists() or not source_path.is_file():
            continue
        target_name = source_path.name
        if target_name in used_names:
            target_name = f"{safe_run_component(key)}_{target_name}"
        used_names.add(target_name)
        target_path = target_dir / target_name
        shutil.copyfile(source_path, target_path)
        copied[f"source_{safe_run_component(key)}"] = target_path.relative_to(run_dir).as_posix()
    return copied
