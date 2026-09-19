from pathlib import Path

from quant_agent.config import load_config, with_run_before_ingest


def test_load_config_reads_strategy_name():
    config = load_config(Path("configs/example_strategy.yaml"))

    assert config.strategy_name == "example_momentum"
    assert config.engine == "dummy"


def test_with_run_before_ingest_returns_overridden_copy():
    config = load_config(Path("configs/xgboost_live_strategy.yaml"))

    overridden = with_run_before_ingest(config, True)

    assert config.raw["subprocess"]["run_before_ingest"] is False
    assert overridden.raw["subprocess"]["run_before_ingest"] is True
