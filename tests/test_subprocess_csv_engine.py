from pathlib import Path

import yaml

from quant_agent.config import load_config
from quant_agent.backtest.subprocess_csv_engine import run_subprocess_csv_backtest


def test_subprocess_csv_engine_ingests_expected_variant(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    summary_path = output_dir / "summary_by_window.csv"
    daily_path = output_dir / "daily_curve.csv"
    metadata_path = output_dir / "metadata.json"
    positions_path = output_dir / "positions.csv"
    trades_path = output_dir / "trades.csv"
    rankings_path = output_dir / "rankings.csv"
    summary_path.write_text(
        "\n".join(
            [
                "window,variant_id,days,ann_return,max_drawdown,sharpe,ann_vol,cum_return,mean_cash_weight,mean_gross_exposure",
                "full_2000_2026,target_variant,2,0.25,-0.30,1.1,0.2,10,0.1,0.9",
                "post_2013,target_variant,2,0.40,-0.20,1.5,0.25,5,0.05,0.95",
            ]
        ),
        encoding="utf-8",
    )
    daily_path.write_text(
        "\n".join(
            [
                "date,variant_id,daily_return,equity,decision_label,turnover,gross_exposure,cash_weight",
                "2020-01-01,target_variant,0.0,1.0,start,0,0,1",
                "2020-01-02,target_variant,0.1,1.1,risk_on,1,1,0",
            ]
        ),
        encoding="utf-8",
    )
    metadata_path.write_text(
        '{"current_variant_id": "stale_variant"}',
        encoding="utf-8",
    )
    positions_path.write_text(
        "date,symbol,weight\n2020-01-02,AAPL,0.5\n",
        encoding="utf-8",
    )
    trades_path.write_text(
        "date,symbol,weight_change\n2020-01-02,AAPL,0.5\n",
        encoding="utf-8",
    )
    rankings_path.write_text(
        "date,symbol,rank\n2020-01-02,AAPL,1\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "strategy": {"name": "target_variant"},
                "engine": "subprocess_csv",
                "subprocess": {
                    "working_dir": str(tmp_path),
                    "command": ["python", "unused.py"],
                    "run_before_ingest": False,
                },
                "outputs": {
                    "expected_variant_id": "target_variant",
                    "primary_window": "full_2000_2026",
                    "summary_csv": str(summary_path),
                    "daily_curve_csv": str(daily_path),
                    "metadata_json": str(metadata_path),
                    "positions_csv": str(positions_path),
                    "trades_csv": str(trades_path),
                    "rankings_csv": str(rankings_path),
                },
            }
        ),
        encoding="utf-8",
    )

    result = run_subprocess_csv_backtest(load_config(Path(config_path)))

    assert result.metrics["cagr"] == 0.25
    assert result.metrics["max_drawdown"] == -0.3
    assert result.metrics["calmar"] == 0.25 / 0.3
    assert len(result.equity_curve) == 2
    assert len(result.decision_log) == 1
    assert result.positions == [{"date": "2020-01-02", "symbol": "AAPL", "weight": 0.5}]
    assert result.trades == [{"date": "2020-01-02", "symbol": "AAPL", "weight_change": 0.5}]
    assert "rankings_csv" in result.metadata["source_artifacts"]
    assert any(
        warning["code"] == "source_current_variant_mismatch"
        for warning in result.metadata["warnings"]
    )
