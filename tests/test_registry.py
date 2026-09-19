import json

from quant_agent.config import StrategyConfig
from quant_agent.models.run import BacktestResult
from quant_agent.registry import save_run


def test_save_run_enriches_metrics_and_records_reproducibility(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("strategy:\n  name: demo_strategy\nengine: dummy\n", encoding="utf-8")
    config = StrategyConfig(
        path=config_path,
        raw={
            "strategy": {"name": "demo_strategy"},
            "engine": "dummy",
            "data": {"version": "fixture_data", "universe_version": "fixture_universe"},
            "backtest": {
                "start_date": "2024-01-01",
                "end_date": "2024-01-03",
                "rebalance_rule": "daily",
            },
            "costs": {"transaction_cost_bps": 1, "slippage_bps": 2},
            "subprocess": {"working_dir": "research", "command": "python run.py"},
        },
    )
    result = BacktestResult(
        metrics={"cagr": 0.12, "max_drawdown": -0.08, "calmar": 1.5},
        equity_curve=[
            {"date": "2024-01-01", "daily_return": 0.0, "equity": 100.0},
            {"date": "2024-01-02", "daily_return": 0.01, "equity": 101.0},
        ],
        trades=[{"date": "2024-01-02", "symbol": "AAA", "quantity": 1}],
        positions=[{"date": "2024-01-02", "symbol": "AAA", "weight": 1.0}],
        decision_log=[{"decision_date": "2024-01-02"}],
        metadata={"source_metadata": {"source_script": "research/run.py"}},
    )

    run_dir, manifest = save_run(config, result, flags=[], runs_root=tmp_path / "runs")

    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    manifest_payload = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert metrics["number_of_trades"] == 1
    assert metrics["observations"] == 2
    assert "ann_vol" in metrics
    assert manifest.metrics == metrics
    assert manifest_payload["reproducibility"]["python_version"]
    assert manifest_payload["reproducibility"]["source_script"] == "research/run.py"
    assert manifest_payload["reproducibility"]["artifact_row_counts"] == {
        "decision_log": 1,
        "equity_curve": 2,
        "positions": 1,
        "trades": 1,
    }
    assert manifest_payload["reproducibility"]["transaction_cost_bps"] == 1
