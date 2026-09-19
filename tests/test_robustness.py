import json

from quant_agent.audits.robustness import build_robustness_audit, run_robustness_sweep


def test_run_robustness_sweep_writes_structured_audit(tmp_path):
    run_dir = make_run_dir(tmp_path)

    markdown_path = run_robustness_sweep(run_dir)
    json_path = run_dir / "audits" / "robustness.json"

    assert markdown_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["recommendation"] == "robust"
    assert payload["variant_id"] == "target_variant"
    assert len(payload["local_neighbors"]) == 4
    assert "local_cagr_stability" in check_codes(payload, "pass")


def test_robustness_audit_warns_on_validation_concentration(tmp_path):
    run_dir = make_run_dir(tmp_path, validation_return=0.50)

    audit = build_robustness_audit(run_dir)

    assert audit["recommendation"] == "review_required"
    assert "validation_cagr_concentration" in check_codes(audit, "warn")


def test_robustness_audit_blocks_drawdown_breach(tmp_path):
    run_dir = make_run_dir(tmp_path, target_drawdown=-0.41)

    audit = build_robustness_audit(run_dir)

    assert audit["recommendation"] == "blocked"
    assert "window_drawdown_threshold" in check_codes(audit, "fail")


def make_run_dir(tmp_path, *, validation_return=0.30, target_drawdown=-0.25):
    run_dir = tmp_path / "target_variant_20260608_001"
    source_dir = run_dir / "source_artifacts"
    source_dir.mkdir(parents=True)
    write_json(
        run_dir / "metrics.json",
        {
            "variant_id": "target_variant",
            "primary_window": "full_2000_2026",
            "cagr": 0.20,
            "max_drawdown": target_drawdown,
            "calmar": 0.20 / abs(target_drawdown),
            "sharpe": 1.0,
        },
    )
    write_json(
        run_dir / "manifest.json",
        {
            "strategy_name": "target_variant",
            "artifacts": {
                "source_summary_by_window": "source_artifacts/summary_by_window.csv",
                "source_rolling_1y_summary_csv": "source_artifacts/rolling_1y_summary.csv",
            },
        },
    )
    write_json(
        source_dir / "metadata.json",
        {"promoted_live_variant_id": "target_variant"},
    )
    (source_dir / "summary_by_window.csv").write_text(
        "\n".join(
            [
                "window,variant_id,base_top_n,base_score,accel_top_n,accel_score,ann_return,max_drawdown,sharpe,ann_vol,mean_cash_weight,mean_gross_exposure",
                csv_row("full_2000_2026", "target_variant", 5, "combo", 2, "mom12", 0.20, target_drawdown, 1.0),
                csv_row("full_2000_2026", "base6_combo_accel2_mom12", 6, "combo", 2, "mom12", 0.21, -0.24, 1.1),
                csv_row("full_2000_2026", "base5_raw_accel2_mom12", 5, "raw", 2, "mom12", 0.19, -0.26, 0.9),
                csv_row("full_2000_2026", "base5_combo_accel3_mom12", 5, "combo", 3, "mom12", 0.18, -0.27, 0.8),
                csv_row("full_2000_2026", "base5_combo_accel2_combo", 5, "combo", 2, "combo", 0.19, -0.23, 0.9),
                csv_row("pre_2013", "target_variant", 5, "combo", 2, "mom12", 0.08, -0.30, 0.5),
                csv_row("train_2013_2021", "target_variant", 5, "combo", 2, "mom12", 0.18, -0.22, 0.9),
                csv_row("validation_2022_2026", "target_variant", 5, "combo", 2, "mom12", validation_return, -0.20, 1.4),
                csv_row("post_2013", "target_variant", 5, "combo", 2, "mom12", 0.26, -0.23, 1.2),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (source_dir / "rolling_1y_summary.csv").write_text(
        "\n".join(
            [
                "variant_id,base_top_n,base_score,accel_top_n,accel_score,mean_1y_cagr,median_1y_cagr,mean_1y_maxdd,median_1y_maxdd",
                "target_variant,5,combo,2,mom12,0.14,0.10,-0.12,-0.10",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def csv_row(window, variant_id, base_top_n, base_score, accel_top_n, accel_score, ann_return, max_drawdown, sharpe):
    return (
        f"{window},{variant_id},{base_top_n},{base_score},{accel_top_n},{accel_score},"
        f"{ann_return},{max_drawdown},{sharpe},0.20,0.10,0.90"
    )


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def check_codes(audit, status):
    return {check["code"] for check in audit["checks"] if check["status"] == status}
