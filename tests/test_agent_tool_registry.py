import json

import pytest

from quant_agent.agent.actions import (
    APPROVAL_GATED_ACTIONS,
    SAFE_ACTIONS,
    action_requires_approval,
)
from quant_agent.agent.planner import allowed_action_payload
from quant_agent.agent.tool_catalog import TOOL_SPECS
from quant_agent.agent.tools import TOOL_HANDLERS, execute_agent_action, invoke_tool
from quant_agent.cli import main


def test_tool_catalog_handlers_and_safety_sets_stay_in_sync():
    assert set(TOOL_SPECS) == set(TOOL_HANDLERS)
    assert SAFE_ACTIONS == {
        name for name, spec in TOOL_SPECS.items() if spec.risk == "safe"
    }
    assert APPROVAL_GATED_ACTIONS == {
        name for name, spec in TOOL_SPECS.items() if spec.risk == "approval_gated"
    }


def test_planner_tool_payload_comes_from_catalog():
    payload = {item["name"]: item for item in allowed_action_payload()}

    assert payload["metrics_latest"]["description"] == TOOL_SPECS["metrics_latest"].description
    assert payload["trace_source"]["requires_approval"] is True


def test_tool_catalog_exposes_json_schemas_for_model_and_future_mcp_adapters():
    assert TOOL_SPECS["metrics_latest"].parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    trace_properties = TOOL_SPECS["trace_source"].parameters["properties"]
    assert trace_properties["repo"]["type"] == "string"
    assert trace_properties["file"]["type"] == "string"


def test_invoke_tool_runs_safe_handler_and_legacy_action_still_works(tmp_path):
    run_dir = make_run(tmp_path)

    direct = invoke_tool("metrics_latest", {"run": run_dir.name}, root=tmp_path)
    legacy = execute_agent_action(
        {
            "name": "metrics_latest",
            "args": {"run": run_dir.name},
            "rationale": "Compatibility check.",
            "requires_approval": False,
        },
        root=tmp_path,
    )

    assert direct == legacy
    assert direct["run_id"] == run_dir.name
    assert direct["metrics"]["cagr"] == 0.12


def test_invoke_tool_cannot_bypass_approval_gate(tmp_path):
    with pytest.raises(PermissionError, match="requires approval before execution"):
        invoke_tool("run_fresh", {"config": "configs/demo.yaml"}, root=tmp_path)


def test_unknown_tool_never_enters_the_approval_workflow():
    model_action = {
        "name": "shell_command",
        "args": {"command": "do something unsafe"},
        "rationale": "The model invented a tool.",
        "requires_approval": True,
    }

    with pytest.raises(ValueError, match="Unsupported agent action"):
        action_requires_approval(model_action)


def test_metrics_cli_uses_shared_tool_entrypoint(monkeypatch, capsys):
    calls = []

    def fake_invoke_tool(name, args, root, approved=False, rationale=""):
        calls.append({"name": name, "args": args, "root": root, "approved": approved})
        return {
            "action": "metrics_latest",
            "run_id": "demo_run",
            "metrics": {"cagr": 0.12, "max_drawdown": -0.2, "calmar": 0.6},
        }

    monkeypatch.setattr("quant_agent.cli.invoke_tool", fake_invoke_tool)

    exit_code = main(["metrics", "--run", "demo_run"])

    assert exit_code == 0
    assert calls[0]["name"] == "metrics_latest"
    assert calls[0]["args"] == {"run": "demo_run"}
    assert "Run: demo_run" in capsys.readouterr().out


def make_run(root):
    run_dir = root / "runs" / "demo_20260913_001"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "variant_id": "demo",
                "cagr": 0.12,
                "max_drawdown": -0.2,
                "calmar": 0.6,
            }
        ),
        encoding="utf-8",
    )
    return run_dir
