from __future__ import annotations

from quant_agent.backtest.dummy_engine import run_dummy_backtest
from quant_agent.backtest.subprocess_csv_engine import run_subprocess_csv_backtest
from quant_agent.config import StrategyConfig
from quant_agent.models.run import BacktestResult


def run_backtest(config: StrategyConfig) -> BacktestResult:
    """Normalize a configured backtest engine into quant-agent artifacts."""
    if config.engine == "dummy":
        return run_dummy_backtest(config)
    if config.engine == "subprocess_csv":
        return run_subprocess_csv_backtest(config)
    raise NotImplementedError(
        f"No adapter is registered for engine={config.engine!r}. "
        "Add one in quant_agent.backtest.adapter."
    )
