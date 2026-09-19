from __future__ import annotations

from quant_agent.models.run import BacktestResult


def basic_risk_flags(result: BacktestResult) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    metrics = result.metrics

    for warning in result.metadata.get("warnings", []):
        flags.append(
            {
                "level": "WARN",
                "code": str(warning.get("code", "adapter_warning")),
                "message": str(warning.get("message", warning)),
            }
        )

    if metrics.get("max_drawdown", 0) <= -0.35:
        flags.append(
            {
                "level": "WARN",
                "code": "large_drawdown",
                "message": "Max drawdown is near or below the review threshold.",
            }
        )

    if metrics.get("top2_contribution", 0) >= 0.6:
        flags.append(
            {
                "level": "WARN",
                "code": "sleeve_concentration",
                "message": "Top2 sleeve contribution appears highly concentrated.",
            }
        )

    if not result.decision_log:
        flags.append(
            {
                "level": "WARN",
                "code": "missing_decision_log",
                "message": "Decision log is missing, so replay and lag-safety checks are limited.",
            }
        )
    else:
        flags.append(
            {
                "level": "PASS",
                "code": "decision_log_present",
                "message": "Decision log is present for replay and timing checks.",
            }
        )

    return flags
