import json

from quant_agent.state_updater import update_project_state


def test_update_project_state_refreshes_latest_run_and_test_status(tmp_path):
    make_project(tmp_path)
    make_run(tmp_path, "demo_20260610_001")

    state = update_project_state(
        root=tmp_path,
        state_path=tmp_path / "state" / "project_state.json",
        test_status="99 passed",
    )

    written = json.loads((tmp_path / "state" / "project_state.json").read_text(encoding="utf-8"))
    latest = state["latest_verified_run"]
    assert written == state
    assert state["project"]["requires_python"] == ">=3.11"
    assert state["environment"]["latest_test_status"] == "99 passed"
    assert latest["run_id"] == "demo_20260610_001"
    assert latest["fresh_subprocess_run"] is True
    assert latest["metrics"]["cagr_percent_display"] == "12.0%"
    assert latest["metrics"]["max_drawdown_percent_display"] == "-20.0%"
    assert latest["position_level_artifacts"]["positions_rows"] == 2
    assert latest["position_level_artifacts"]["artifact_contract_recommendation"] == "satisfied"
    assert latest["promotion"]["recommendation"] == "review_required"
    assert latest["promotion"]["warnings"] == ["diagnosis_missing"]
    assert latest["promotion_closure"]["recommendation"] == "needs_action"
    assert latest["promotion_closure"]["items"] == [{"code": "diagnosis_missing", "status": "needs_action"}]
    assert state["capability_status"]["implemented"] == ["run"]


def test_update_project_state_dry_run_does_not_write(tmp_path):
    make_project(tmp_path)
    make_run(tmp_path, "demo_20260610_001")
    original = {"schema_version": 1, "environment": {"latest_test_status": "old"}}
    state_path = tmp_path / "state" / "project_state.json"
    state_path.write_text(json.dumps(original), encoding="utf-8")

    state = update_project_state(
        root=tmp_path,
        state_path=state_path,
        test_status="new",
        write=False,
    )

    assert state["environment"]["latest_test_status"] == "new"
    assert json.loads(state_path.read_text(encoding="utf-8")) == original


def make_project(root):
    (root / "state").mkdir()
    (root / "pyproject.toml").write_text(
        """
[project]
name = "quant-agent"
requires-python = ">=3.11"
dependencies = ["langgraph>=1.0.0", "pyyaml>=6.0"]
""".strip(),
        encoding="utf-8",
    )
    (root / "state" / "project_state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "latest_verified_run": {
                    "run_id": "demo_20260610_001",
                    "config": "configs/original.yaml",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "state" / "capabilities.json").write_text(
        json.dumps(
            {
                "capabilities": [
                    {"name": "run", "status": "implemented"},
                    {"name": "replay", "status": "partial"},
                    {"name": "future", "status": "planned"},
                ]
            }
        ),
        encoding="utf-8",
    )


def make_run(root, run_id):
    run_dir = root / "runs" / run_id
    audits_dir = run_dir / "audits"
    audits_dir.mkdir(parents=True)
    write_json(
        run_dir / "manifest.json",
        {
            "strategy_name": "demo",
            "engine": "subprocess_csv",
        },
    )
    write_json(
        run_dir / "metrics.json",
        {
            "variant_id": "demo",
            "primary_window": "full",
            "cagr": 0.12,
            "max_drawdown": -0.2,
            "calmar": 0.6,
            "turnover_per_year": 4.22,
            "exposure": 0.8,
        },
    )
    write_json(
        run_dir / "engine_metadata.json",
        {
            "subprocess_ran": True,
            "elapsed_seconds": 12.3,
            "expected_variant_id": "demo",
            "primary_window": "full",
        },
    )
    write_json(
        audits_dir / "artifact_contract.json",
        {
            "recommendation": "satisfied",
            "checks": [
                {"name": "positions", "rows": 2},
                {"name": "trades", "rows": 3},
                {"name": "rankings", "rows": 4},
            ],
        },
    )
    write_json(
        audits_dir / "PROMOTION_AUDIT_demo.json",
        {
            "recommendation": "review_required",
            "blocking_issues": [],
            "warnings": [{"code": "diagnosis_missing"}],
        },
    )
    (audits_dir / "PROMOTION_AUDIT_demo.md").write_text("# Promotion\n", encoding="utf-8")
    write_json(
        audits_dir / "promotion_closure.json",
        {
            "closure_recommendation": "needs_action",
            "summary": {"needs_action": 1},
            "items": [{"code": "diagnosis_missing", "status": "needs_action"}],
        },
    )
    (audits_dir / "promotion_closure.md").write_text("# Closure\n", encoding="utf-8")
    (run_dir / "config.yaml").write_text("strategy:\n  name: demo\n", encoding="utf-8")


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
