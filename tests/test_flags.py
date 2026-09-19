from quant_agent.flags import basic_risk_flags
from quant_agent.models.run import BacktestResult


def test_basic_risk_flags_pass_when_decision_log_present():
    result = BacktestResult(metrics={"max_drawdown": -0.1}, decision_log=[{"decision_date": "2021-01-01"}])

    flags = basic_risk_flags(result)

    assert any(flag["code"] == "decision_log_present" for flag in flags)

