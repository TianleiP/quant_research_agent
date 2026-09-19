import json
from datetime import date, timedelta

from quant_agent.audits.validation_attribution import (
    build_validation_attribution_audit,
    run_validation_attribution_audit,
)


def test_run_validation_attribution_writes_json_and_markdown(tmp_path):
    run_dir = make_run_dir(tmp_path, [0.001] * 300)

    markdown_path = run_validation_attribution_audit(run_dir)
    json_path = run_dir / "audits" / "validation_attribution.json"

    assert markdown_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["recommendation"] == "explained"
    assert payload["summary"]["days"] == 300
    assert "top_day_concentration" in check_codes(payload, "pass")
    assert "decision_label" in payload["state_attribution"]


def test_validation_attribution_warns_when_top_days_dominate(tmp_path):
    returns = [0.0] * 290 + [0.02] * 10
    run_dir = make_run_dir(tmp_path, returns)

    audit = build_validation_attribution_audit(run_dir)

    assert audit["recommendation"] == "review_required"
    assert "top_day_concentration" in check_codes(audit, "warn")
    assert audit["concentration"]["top_10_positive_share_of_positive_returns"] == 1.0


def test_validation_attribution_blocks_large_validation_drawdown(tmp_path):
    returns = [0.001] * 300
    returns[20] = -0.45
    run_dir = make_run_dir(tmp_path, returns)

    audit = build_validation_attribution_audit(run_dir)

    assert audit["recommendation"] == "blocked"
    assert "validation_drawdown_threshold" in check_codes(audit, "fail")


def make_run_dir(tmp_path, returns):
    run_dir = tmp_path / "target_variant_20260608_001"
    source_dir = run_dir / "source_artifacts"
    source_dir.mkdir(parents=True)
    write_json(
        run_dir / "metrics.json",
        {
            "variant_id": "target_variant",
            "source_daily_end": "2022-12-31",
            "cagr": 0.10,
            "max_drawdown": -0.20,
            "calmar": 0.50,
        },
    )
    write_json(
        run_dir / "manifest.json",
        {
            "strategy_name": "target_variant",
            "backtest_end": "2022-12-31",
            "artifacts": {"source_daily_curve": "source_artifacts/target_variant.csv"},
        },
    )
    rows = [
        "date,daily_return,decision_label,feedback_stage,gross_exposure,cash_weight,top_leverage_active,top_weight_signal,cash_active"
    ]
    start = date(2022, 1, 3)
    for offset, value in enumerate(returns):
        current = start + timedelta(days=offset)
        rows.append(
            f"{current.isoformat()},{value},stage0_top_full_or_partial,0,1.0,0.0,0,1.0,0"
        )
    (source_dir / "target_variant.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return run_dir


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def check_codes(audit, status):
    return {check["code"] for check in audit["checks"] if check["status"] == status}
