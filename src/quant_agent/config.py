from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StrategyConfig:
    path: Path
    raw: dict[str, Any]

    @property
    def strategy_name(self) -> str:
        strategy = self.raw.get("strategy", {})
        return str(strategy.get("name", "unnamed_strategy"))

    @property
    def engine(self) -> str:
        return str(self.raw.get("engine", "dummy"))

    @property
    def parameters(self) -> dict[str, Any]:
        return dict(self.raw.get("parameters", {}))


def load_config(path: Path) -> StrategyConfig:
    path = path.resolve()
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config must contain a YAML mapping: {path}")
    return StrategyConfig(path=path, raw=raw)


def with_run_before_ingest(config: StrategyConfig, value: bool) -> StrategyConfig:
    raw = copy.deepcopy(config.raw)
    subprocess_config = raw.setdefault("subprocess", {})
    if not isinstance(subprocess_config, dict):
        raise ValueError("Config field 'subprocess' must be a mapping.")
    subprocess_config["run_before_ingest"] = value
    return StrategyConfig(path=config.path, raw=raw)
