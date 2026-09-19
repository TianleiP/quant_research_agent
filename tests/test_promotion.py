import json

from quant_agent.audits.promotion import generate_promotion_report
from quant_agent.lineage import update_strategy_lineage


def test_generate_promotion_report_writes_json_and_markdown(tmp_path):
    run_dir = make_run_dir(tmp_path)

    result = generate_promotion_report(run_dir)
    promotion = result["promotion"]

    assert result["json_path"].exists()
    assert result["markdown_path"].exists()
    assert promotion["recommendation"] == "review_required"
    assert promotion["strategy_name"] == "target_variant"
    assert check_codes(promotion, "warn") >= {
        "diagnosis_missing",
        "trade_position_artifacts_empty",
        "robustness_review_required",
    }
    assert not promotion["blocking_issues"]

    payload = json.loads(result["json_path"].read_text(encoding="utf-8"))
    assert payload["recommendation"] == "review_required"
    assert "Promotion Audit: target_variant" in result["markdown_path"].read_text(encoding="utf-8")


def test_generate_promotion_report_blocks_large_drawdown(tmp_path):
    run_dir = make_run_dir(tmp_path, max_drawdown=-0.41)

    result = generate_promotion_report(run_dir)

    assert result["promotion"]["recommendation"] == "blocked"
    assert "max_drawdown_threshold" in check_codes(result["promotion"], "fail")


def test_generate_promotion_report_creates_prerequisite_audits(tmp_path):
    run_dir = make_run_dir(tmp_path)

    assert not (run_dir / "audits" / "lag_safety.md").exists()
    assert not (run_dir / "audits" / "robustness.md").exists()
    assert not (run_dir / "audits" / "cost_stress.md").exists()

    generate_promotion_report(run_dir)

    assert (run_dir / "audits" / "lag_safety.md").exists()
    assert (run_dir / "audits" / "robustness.md").exists()
    assert (run_dir / "audits" / "cost_stress.md").exists()


def test_generate_promotion_report_can_generate_missing_diagnosis(tmp_path):
    run_dir = make_run_dir(tmp_path)

    result = generate_promotion_report(run_dir, diagnose=True, provider="mock")

    assert (run_dir / "diagnosis.json").exists()
    assert "diagnosis_present" in check_codes(result["promotion"], "pass")
    assert "diagnosis_missing" not in check_codes(result["promotion"], "warn")


def test_generate_promotion_report_includes_cost_stress_and_lineage(tmp_path):
    run_dir = make_run_dir(tmp_path)
    update_strategy_lineage(
        strategy="target_variant",
        previous="control_variant",
        reason="Candidate improves validation drawdown while preserving guard logic.",
        changed_rules=["Use Top5 combo partial pocket."],
        status="candidate",
        path=tmp_path / "state" / "strategy_lineage.json",
    )

    result = generate_promotion_report(run_dir)
    promotion = result["promotion"]
    markdown = result["markdown_path"].read_text(encoding="utf-8")

    assert promotion["artifacts"]["cost_stress"] == "audits/cost_stress.md"
    assert promotion["cost_stress"]["scenarios"]
    assert promotion["lineage"]["previous_baseline"] == "control_variant"
    assert "cost_stress_review_required" in check_codes(promotion, "warn")
    assert "strategy_lineage_present" in check_codes(promotion, "pass")
    assert "## Cost Stress" in markdown
    assert "## Strategy Lineage" in markdown


def make_run_dir(
    tmp_path,
    *,
    max_drawdown=-0.30,
    validation_ann_return=0.30,
    full_ann_return=0.20,
):
    run_dir = tmp_path / "target_variant_20260608_001"
    source_dir = run_dir / "source_artifacts"
    source_dir.mkdir(parents=True)

    metrics = {
        "variant_id": "target_variant",
        "cagr": full_ann_return,
        "max_drawdown": max_drawdown,
        "calmar": full_ann_return / abs(max_drawdown),
        "sharpe": 1.1,
        "full_2000_2026_ann_return": full_ann_return,
        "validation_2022_2026_ann_return": validation_ann_return,
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
        "window,variant_id,ann_return,max_drawdown\nfull_2000_2026,target_variant,0.20,-0.30\n",
        encoding="utf-8",
    )
    (run_dir / "decision_log.csv").write_text(
        "decision_date,available_through,execution_date\n2020-01-02,2020-01-01,2020-01-03\n",
        encoding="utf-8",
    )
    (run_dir / "trades.csv").write_text("date,symbol,quantity\n", encoding="utf-8")
    (run_dir / "positions.csv").write_text("date,symbol,weight\n", encoding="utf-8")
    (run_dir / "short_report.md").write_text("# Short Report\n", encoding="utf-8")
    return run_dir


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def check_codes(promotion, status):
    return {check["code"] for check in promotion["checks"] if check["status"] == status}
