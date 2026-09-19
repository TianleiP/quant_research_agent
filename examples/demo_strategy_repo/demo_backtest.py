from __future__ import annotations

import json
import sys
from pathlib import Path


REPAIR_MARKERS = [
    'POSITIONS_DIR = OUT_DIR / "positions"',
    "realized_weights = _simulate_with_weight_capture(",
    "_trades_from_weight_capture",
    "_rankings_from_weight_capture",
]


def main() -> int:
    source_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    source = source_path.read_text(encoding="utf-8")
    repaired = all(marker in source for marker in REPAIR_MARKERS)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregate_outputs(output_dir, source_path)
    clear_position_outputs(output_dir)
    if repaired:
        write_position_outputs(output_dir)
        print("repair detected: wrote positions, trades, and rankings")
    else:
        print("missing export path: wrote aggregate outputs only")
    return 0


def write_aggregate_outputs(output_dir: Path, source_path: Path) -> None:
    (output_dir / "summary_by_window.csv").write_text(
        "window,variant_id,days,ann_return,max_drawdown,sharpe,ann_vol,cum_return,mean_cash_weight,mean_gross_exposure\n"
        "full_2000_2026,target_variant,2,0.20,-0.10,1.2,0.15,0.25,0.10,0.90\n",
        encoding="utf-8",
    )
    (output_dir / "daily_curve.csv").write_text(
        "date,variant_id,daily_return,equity,decision_label,turnover,gross_exposure,cash_weight\n"
        "2026-01-01,target_variant,0.0,1.0,start,0.0,0.0,1.0\n"
        "2026-01-02,target_variant,0.1,1.1,risk_on,0.5,0.9,0.1\n",
        encoding="utf-8",
    )
    (output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "current_variant_id": "target_variant",
                "source_script": source_path.as_posix(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def clear_position_outputs(output_dir: Path) -> None:
    for name in ["positions.csv", "trades.csv", "rankings.csv"]:
        path = output_dir / name
        if path.exists():
            path.unlink()


def write_position_outputs(output_dir: Path) -> None:
    (output_dir / "positions.csv").write_text(
        "date,variant_id,symbol,weight,sleeve,rank,score,price\n"
        "2026-01-02,target_variant,AAPL,0.6,risk_on,1,0.9,200.0\n",
        encoding="utf-8",
    )
    (output_dir / "trades.csv").write_text(
        "date,variant_id,symbol,prev_weight,target_weight,weight_change,turnover\n"
        "2026-01-02,target_variant,AAPL,0.0,0.6,0.6,0.6\n",
        encoding="utf-8",
    )
    (output_dir / "rankings.csv").write_text(
        "date,variant_id,symbol,rank,score,eligible,sleeve\n"
        "2026-01-02,target_variant,AAPL,1,0.9,true,risk_on\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
