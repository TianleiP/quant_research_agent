from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    strategy_name: str
    engine: str
    config_hash: str
    git_commit: str | None
    data_version: str
    universe_version: str
    backtest_start: str
    backtest_end: str
    rebalance_rule: str
    transaction_cost_bps: Any
    slippage_bps: Any
    metrics: dict[str, Any]
    artifacts: dict[str, str]
    run_metadata: dict[str, Any]
    reproducibility: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
