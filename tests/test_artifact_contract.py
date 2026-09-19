import json

from quant_agent.audits.artifact_contract import (
    build_artifact_contract_inspection,
    run_artifact_contract_inspection,
)


def test_artifact_contract_detects_missing_position_level_exports(tmp_path):
    run_dir = make_run_dir(tmp_path, include_position_level=False)

    inspection = build_artifact_contract_inspection(run_dir)

    assert inspection["recommendation"] == "needs_export_patch"
    assert {item["name"] for item in inspection["missing_position_level_artifacts"]} == {
        "positions",
        "trades",
        "rankings",
    }
    assert inspection["suggested_fix"]["issue"] == "missing-position-exports"
    assert inspection["suggested_fix"]["repo"] == str(tmp_path / "repo")
    assert inspection["suggested_fix"]["file"] == "research/run_strategy.py"


def test_artifact_contract_writes_reports_and_suggested_command(tmp_path):
    run_dir = make_run_dir(tmp_path, include_position_level=False)

    report_path = run_artifact_contract_inspection(run_dir)
    json_path = run_dir / "audits" / "artifact_contract.json"

    assert report_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["suggested_fix"]["command"].startswith(
        "quant-agent suggest-fix --issue missing-position-exports"
    )


def test_artifact_contract_satisfied_when_position_level_exports_exist(tmp_path):
    run_dir = make_run_dir(tmp_path, include_position_level=True)

    inspection = build_artifact_contract_inspection(run_dir)

    assert inspection["recommendation"] == "satisfied"
    assert inspection["missing_position_level_artifacts"] == []
    assert inspection["suggested_fix"] is None


def make_run_dir(tmp_path, *, include_position_level):
    run_dir = tmp_path / "target_variant_20260609_001"
    source_dir = run_dir / "source_artifacts"
    source_dir.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "research").mkdir()
    (repo / "research" / "run_strategy.py").write_text("print('run')\n", encoding="utf-8")
    artifacts = {
        "manifest": "manifest.json",
        "metrics": "metrics.json",
        "decision_log": "decision_log.csv",
        "equity_curve": "equity_curve.csv",
        "source_daily_curve": "source_artifacts/target_variant.csv",
        "source_summary_by_window": "source_artifacts/summary_by_window.csv",
        "positions": "positions.csv",
        "trades": "trades.csv",
    }
    if include_position_level:
        artifacts["rankings"] = "rankings.csv"
    write_json(
        run_dir / "manifest.json",
        {"strategy_name": "target_variant", "artifacts": artifacts},
    )
    write_json(run_dir / "metrics.json", {"variant_id": "target_variant", "cagr": 0.1})
    write_json(
        run_dir / "engine_metadata.json",
        {"working_dir": str(repo), "command": ["python", "research/run_strategy.py"]},
    )
    write_csv(run_dir / "decision_log.csv", "date,decision\n2022-01-03,risk_on\n")
    write_csv(run_dir / "equity_curve.csv", "date,daily_return\n2022-01-03,0.01\n")
    write_csv(source_dir / "target_variant.csv", "date,daily_return\n2022-01-03,0.01\n")
    write_csv(source_dir / "summary_by_window.csv", "window,variant_id\nfull,target_variant\n")
    if include_position_level:
        write_csv(run_dir / "positions.csv", "date,symbol,weight\n2022-01-03,AAPL,0.5\n")
        write_csv(run_dir / "trades.csv", "date,symbol,weight_change\n2022-01-03,AAPL,0.5\n")
        write_csv(run_dir / "rankings.csv", "date,symbol,rank\n2022-01-03,AAPL,1\n")
    else:
        write_csv(run_dir / "positions.csv", "\n")
        write_csv(run_dir / "trades.csv", "\n")
    return run_dir


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_csv(path, text):
    path.write_text(text, encoding="utf-8")
