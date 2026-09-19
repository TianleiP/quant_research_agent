import json

from quant_agent.audits.cost_stress import build_cost_stress_audit, run_cost_stress_audit


def test_run_cost_stress_audit_uses_daily_turnover_and_writes_reports(tmp_path):
    run_dir = tmp_path / "runs" / "demo_20260611_001"
    run_dir.mkdir(parents=True)
    write_json(
        run_dir / "metrics.json",
        {"cagr": 0.2, "max_drawdown": -0.1, "sharpe": 1.2, "turnover_per_year": 30},
    )
    write_json(run_dir / "manifest.json", {"artifacts": {"equity_curve": "equity_curve.csv"}})
    (run_dir / "equity_curve.csv").write_text(
        "date,daily_return,equity,turnover\n"
        "2024-01-01,0.0100,101,0.5\n"
        "2024-01-02,0.0080,101.808,0.4\n"
        "2024-01-03,-0.0030,101.502576,0.3\n",
        encoding="utf-8",
    )

    markdown_path = run_cost_stress_audit(run_dir)

    payload = json.loads((run_dir / "audits" / "cost_stress.json").read_text(encoding="utf-8"))
    assert markdown_path == run_dir / "audits" / "cost_stress.md"
    assert payload["recommendation"] == "passed"
    assert all(item["uses_daily_turnover"] for item in payload["scenarios"])
    assert "daily_turnover_missing" not in check_codes(payload, "warn")
    assert "Cost And Slippage Stress Audit" in markdown_path.read_text(encoding="utf-8")


def test_cost_stress_audit_falls_back_to_aggregate_turnover(tmp_path):
    run_dir = tmp_path / "demo_20260611_001"
    run_dir.mkdir()
    write_json(
        run_dir / "metrics.json",
        {"cagr": 0.1, "max_drawdown": -0.2, "sharpe": 0.8, "turnover_per_year": 50},
    )
    write_json(run_dir / "manifest.json", {"artifacts": {}})

    audit = build_cost_stress_audit(run_dir)

    assert audit["recommendation"] == "review_required"
    assert audit["scenarios"][-1]["method"] == "aggregate_turnover_fallback"
    assert "daily_curve_available" in check_codes(audit, "warn")
    assert "daily_turnover_missing" in check_codes(audit, "warn")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def check_codes(audit, status):
    return {check["code"] for check in audit["checks"] if check["status"] == status}
