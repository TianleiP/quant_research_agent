import json

from quant_agent.audits.promotion_closure import (
    build_promotion_closure,
    generate_promotion_closure_report,
)
from quant_agent.lineage import update_strategy_lineage


def test_generate_promotion_closure_report_classifies_remaining_warnings(tmp_path):
    run_dir = make_run_dir(tmp_path)
    update_strategy_lineage(
        strategy="target_variant",
        previous="control_variant",
        reason="Candidate improves validation drawdown while preserving guard logic.",
        changed_rules=["Use Top5 combo partial pocket."],
        status="candidate",
        path=tmp_path / "state" / "strategy_lineage.json",
    )

    result = generate_promotion_closure_report(run_dir, diagnose=True, provider="mock")
    closure = result["closure"]
    by_code = {item["code"]: item for item in closure["items"]}

    assert result["json_path"].exists()
    assert result["markdown_path"].exists()
    assert closure["closure_recommendation"] == "needs_human_decision"
    assert "diagnosis_missing" not in by_code
    assert by_code["source_current_variant_is_control"]["status"] == "closed"
    assert by_code["cost_stress_review_required"]["status"] == "needs_decision"
    assert by_code["robustness_review_required"]["status"] == "needs_decision"
    assert any(item["code"] == "diagnosis_present" for item in closure["already_closed"])
    assert "Promotion Warning Closure" in result["markdown_path"].read_text(encoding="utf-8")


def test_build_promotion_closure_blocks_failed_promotion_check(tmp_path):
    run_dir = tmp_path / "failed_variant_20260611_001"
    run_dir.mkdir()
    write_json(run_dir / "metrics.json", {"variant_id": "failed_variant", "cagr": 0.1})

    closure = build_promotion_closure(
        run_dir,
        {
            "strategy_name": "failed_variant",
            "recommendation": "blocked",
            "blocking_issues": [
                {"status": "fail", "code": "max_drawdown_threshold", "message": "Too much drawdown."}
            ],
            "warnings": [],
            "checks": [],
            "artifacts": {},
        },
    )

    assert closure["closure_recommendation"] == "blocked"
    assert closure["items"][0]["status"] == "blocked"


def make_run_dir(tmp_path):
    run_dir = tmp_path / "target_variant_20260608_001"
    source_dir = run_dir / "source_artifacts"
    source_dir.mkdir(parents=True)
    metrics = {
        "variant_id": "target_variant",
        "cagr": 0.20,
        "max_drawdown": -0.30,
        "calmar": 0.20 / 0.30,
        "sharpe": 1.1,
        "turnover_per_year": 50.0,
        "full_2000_2026_ann_return": 0.20,
        "validation_2022_2026_ann_return": 0.30,
    }
    source_metadata = {
        "current_variant_id": "control_variant",
        "control_variant_id": "control_variant",
        "promoted_live_variant_id": "target_variant",
        "generated_at_utc": "2026-06-08T00:00:00+00:00",
        "source_script": "research/run_strategy.py",
    }
    write_json(run_dir / "metrics.json", metrics)
    write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_dir.name,
            "strategy_name": "target_variant",
            "engine": "subprocess_csv",
            "artifacts": {},
            "run_metadata": {"source_metadata": source_metadata},
        },
    )
    write_json(run_dir / "engine_metadata.json", {"subprocess_ran": True, "exit_code": 0})
    write_json(run_dir / "flags.json", [])
    write_json(source_dir / "metadata.json", source_metadata)
    (source_dir / "summary_by_window.csv").write_text(
        "window,variant_id,base_top_n,base_score,accel_top_n,accel_score,ann_return,max_drawdown,sharpe\n"
        "full_2000_2026,target_variant,5,combo,2,mom12,0.20,-0.30,1.1\n"
        "validation_2022_2026,target_variant,5,combo,2,mom12,0.30,-0.20,1.3\n",
        encoding="utf-8",
    )
    (run_dir / "decision_log.csv").write_text(
        "decision_date,available_through,execution_date\n2020-01-02,2020-01-01,2020-01-03\n",
        encoding="utf-8",
    )
    (run_dir / "trades.csv").write_text("date,symbol,quantity\n2020-01-03,AAA,1\n", encoding="utf-8")
    (run_dir / "positions.csv").write_text("date,symbol,weight\n2020-01-03,AAA,1.0\n", encoding="utf-8")
    return run_dir


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
