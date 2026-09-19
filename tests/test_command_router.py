import json

from quant_agent.cli import main
from quant_agent.command_router import plan_command_route, render_route_plan


def test_router_plans_metrics_command():
    plan = plan_command_route("show me the latest CAGR and drawdown")

    assert plan.status == "planned"
    assert plan.intent == "metrics"
    assert plan.safety == "safe_read"
    assert plan.command == ["quant-agent", "metrics", "--run", "latest"]


def test_router_plans_replay_command_with_date():
    plan = plan_command_route("why did it trade on 2026-01-03")

    assert plan.status == "planned"
    assert plan.intent == "replay"
    assert plan.command == ["quant-agent", "replay", "--run", "latest", "--date", "2026-01-03"]


def test_router_requires_date_for_replay():
    plan = plan_command_route("replay the latest trade")

    assert plan.status == "needs_input"
    assert plan.intent == "replay"
    assert plan.missing_args == ["date"]
    assert "Missing arguments: date" in render_route_plan(plan)


def test_router_sends_risky_fix_to_agent_session():
    plan = plan_command_route("apply the latest fix proposal", provider="mock", max_steps=4)

    assert plan.status == "planned"
    assert plan.intent == "approval_gated_agent_action"
    assert plan.safety == "approval_gated"
    assert plan.execution_kind == "agent_session"
    assert plan.command[:4] == ["quant-agent", "agent-session", "--task", "apply the latest fix proposal"]
    assert "--provider" in plan.command
    assert "mock" in plan.command


def test_router_gives_patch_lifecycle_enough_steps():
    plan = plan_command_route("complete latest proposal patch-run-inspect lifecycle", provider="mock", max_steps=4)

    assert plan.status == "planned"
    assert plan.intent == "patch_run_inspect_lifecycle"
    assert plan.execution_kind == "agent_session"
    assert plan.command[:4] == [
        "quant-agent",
        "agent-session",
        "--task",
        "complete latest proposal patch-run-inspect lifecycle",
    ]
    assert plan.command[-1] == "10"


def test_router_handles_memory_build_and_query_requests():
    build_plan = plan_command_route("build a code memory index")
    query_plan = plan_command_route("search code memory for replay exports")

    assert build_plan.status == "needs_input"
    assert build_plan.intent == "memory_build"
    assert build_plan.missing_args == ["repo"]
    assert query_plan.status == "planned"
    assert query_plan.intent == "query_code_memory"
    assert query_plan.command[:4] == ["quant-agent", "agent-session", "--task", "search code memory for replay exports"]


def test_router_plans_cost_stress_audit():
    plan = plan_command_route("run a cost and slippage stress audit")

    assert plan.status == "planned"
    assert plan.intent == "audit_costs"
    assert plan.command == ["quant-agent", "audit", "costs", "--run", "latest"]


def test_router_plans_promotion_warning_closure():
    plan = plan_command_route("close the remaining promotion warnings", provider="mock")

    assert plan.status == "planned"
    assert plan.intent == "promotion_closure"
    assert plan.command == ["quant-agent", "close-promotion", "--run", "latest"]


def test_ask_command_prints_plan_without_execution(capsys):
    exit_code = main(["ask", "show", "latest", "metrics"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Route plan:" in captured.out
    assert "quant-agent metrics --run latest" in captured.out


def test_ask_command_executes_safe_metrics_route(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "runs" / "demo_20260610_001"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps({"cagr": 0.12, "max_drawdown": -0.2, "calmar": 0.6}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = main(["ask", "show", "latest", "metrics", "--execute"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Executing routed command:" in captured.out
    assert "Run: demo_20260610_001" in captured.out
    assert "CAGR: 12.0%" in captured.out
