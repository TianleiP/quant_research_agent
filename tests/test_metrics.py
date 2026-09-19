import pytest

from quant_agent.metrics import enrich_metrics, format_metrics_summary


def test_format_metrics_summary_contains_core_metrics():
    summary = format_metrics_summary({"cagr": 0.1, "max_drawdown": -0.2, "calmar": 0.5})

    assert "CAGR: 10.0%" in summary
    assert "Max Drawdown: -20.0%" in summary
    assert "Calmar: 0.50" in summary


def test_enrich_metrics_adds_distribution_trade_and_exposure_fields():
    enriched = enrich_metrics(
        {"cagr": 0.2},
        equity_curve=[
            {"date": "2024-01-01", "daily_return": 0.00, "equity": 100.0},
            {"date": "2024-01-02", "daily_return": 0.10, "equity": 110.0},
            {"date": "2024-01-03", "daily_return": -0.10, "equity": 99.0},
            {"date": "2024-02-01", "daily_return": 0.05, "equity": 103.95},
        ],
        trades=[
            {"date": "2024-01-02", "symbol": "AAA"},
            {"date": "2024-01-03", "symbol": "BBB"},
        ],
        positions=[
            {"date": "2024-01-02", "symbol": "AAA", "weight": 0.6},
            {"date": "2024-01-02", "symbol": "BBB", "weight": -0.2},
            {"date": "2024-01-03", "symbol": "AAA", "weight": 0.5},
        ],
    )

    assert enriched["observations"] == 4
    assert enriched["number_of_trades"] == 2
    assert enriched["number_of_position_rows"] == 3
    assert enriched["max_drawdown"] == pytest.approx(-0.10)
    assert enriched["max_drawdown_date"] == "2024-01-03T00:00:00"
    assert enriched["monthly_returns"]["2024-01"] == pytest.approx(-0.01)
    assert enriched["best_day"]["date"] == "2024-01-02T00:00:00"
    assert enriched["average_position_gross_exposure"] == pytest.approx(0.65)
    assert "ann_vol" in enriched
    assert "sortino" in enriched
