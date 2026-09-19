from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

OUT_DIR = Path("outputs")
DAILY_DIR = OUT_DIR / "daily_curves"
PROMOTED_LIVE_VARIANT_ID = "target_variant"


class rob:
    class last:
        @staticmethod
        def _simulate(vin, panel, spec, weights):
            return pd.DataFrame()

    @staticmethod
    def _to_last_spec(spec):
        return spec

    class RobustSpec:
        def __init__(self, variant_id, family, notes, top2_key=None):
            self.variant_id = variant_id
            self.family = family
            self.notes = notes


class Spec:
    variant_id = "target_variant"
    notes = "self-contained repair demo"
    base_top_n = 5
    base_score = "combo"
    accel_top_n = 2
    accel_score = "mom12"


def _make_specs() -> list[Any]:
    return [Spec()]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    specs = _make_specs()
    curves = {}
    for i, spec in enumerate(specs, start=1):
        qbase_weights = {}
        top2_weights = {}
        partial_base = {}
        vin = {}
        current_panel = pd.DataFrame({"date": []})
        daily = rob.last._simulate(
            vin,
            current_panel,
            rob._to_last_spec(rob.RobustSpec(spec.variant_id, "current_strategy_clean_ablation", spec.notes, top2_key="top2_h1")),
            {"top10_lq19": partial_base, "top10_noliq": partial_base},
        )
        daily["variant_id"] = spec.variant_id
        daily.to_csv(DAILY_DIR / f"{spec.variant_id}.csv", index=False)
        curves[spec.variant_id] = daily
    return 0
