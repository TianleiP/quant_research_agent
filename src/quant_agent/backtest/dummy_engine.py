from __future__ import annotations

from quant_agent.config import StrategyConfig
from quant_agent.models.run import BacktestResult


def run_dummy_backtest(config: StrategyConfig) -> BacktestResult:
    top_n = int(config.parameters.get("top_n", 8))
    top2_weight = float(config.parameters.get("top2_weight", 0.20))
    metrics = {
        "cagr": 0.362,
        "max_drawdown": -0.348,
        "calmar": 1.04,
        "turnover_per_year": 18.5,
        "exposure": 0.924,
        "top2_contribution": 0.55,
        "top_n": top_n,
        "top2_weight": top2_weight,
    }
    trades = [
        {
            "date": "2021-03-08",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 100,
            "price": 116.20,
            "execution_session": "2021-03-08",
        },
        {
            "date": "2021-03-08",
            "symbol": "NVDA",
            "side": "buy",
            "quantity": 50,
            "price": 129.75,
            "execution_session": "2021-03-08",
        },
    ]
    positions = [
        {"date": "2021-03-08", "symbol": "AAPL", "weight": 0.18},
        {"date": "2021-03-08", "symbol": "NVDA", "weight": 0.16},
        {"date": "2021-03-08", "symbol": "MSFT", "weight": 0.12},
    ]
    equity_curve = [
        {"date": "2021-03-05", "equity": 1000000.00},
        {"date": "2021-03-08", "equity": 1006500.00},
    ]
    decision_log = [
        {
            "decision_date": "2021-03-08",
            "available_through": "2021-03-05",
            "signal_date": "2021-03-05",
            "execution_date": "2021-03-08",
            "market_gate": "risk_on",
            "selected_symbols": "AAPL,NVDA,MSFT,AMD,GOOGL,AMZN,META,AVGO",
            "reason": "Momentum ranks were computed using prior-session data.",
        }
    ]
    metadata = {
        "engine": "dummy",
        "note": "Demo engine output for scaffolding and tests.",
    }
    return BacktestResult(
        metrics=metrics,
        trades=trades,
        positions=positions,
        equity_curve=equity_curve,
        decision_log=decision_log,
        metadata=metadata,
    )

