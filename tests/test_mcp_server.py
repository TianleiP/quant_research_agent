from __future__ import annotations

import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from quant_agent.agent.tool_catalog import TOOL_SPECS
from quant_agent.mcp_server import create_mcp_server, invoke_mcp_tool, mcp_tool_specs


def test_mcp_catalog_is_derived_from_shared_tool_catalog() -> None:
    tools = {tool.name: tool for tool in mcp_tool_specs()}

    assert set(tools) == set(TOOL_SPECS)
    assert tools["query_code_memory"].input_schema == TOOL_SPECS["query_code_memory"].parameters
    assert tools["read_project_state"].annotations.read_only_hint is True
    assert tools["query_code_memory"].annotations.open_world_hint is True
    assert tools["apply_fix_yes"].annotations.destructive_hint is True


def test_in_process_mcp_client_lists_and_calls_safe_tool(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with Client(create_mcp_server(tmp_path)) as client:
            listing = await client.list_tools()
            assert {tool.name for tool in listing.tools} == set(TOOL_SPECS)

            result = await client.call_tool("read_project_state", {})
            assert result.is_error is False
            assert result.structured_content == {
                "action": "read_project_state",
                "path": "state/project_state.json",
                "exists": False,
                "project": {
                    "name": None,
                    "current_stage": None,
                    "requires_python": None,
                    "agent_runtime": None,
                    "behavior_model": None,
                },
                "latest_verified_run": {
                    "run_id": None,
                    "config": None,
                    "artifact_contract_recommendation": None,
                    "promotion_recommendation": None,
                },
                "known_gaps": [],
                "next_recommended_steps": [],
            }

    anyio.run(exercise)


def test_approval_gated_tool_returns_protocol_error_when_disabled(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with Client(create_mcp_server(tmp_path, allow_approval_gated=False)) as client:
            result = await client.call_tool("trace_source", {})
            assert result.is_error is True
            assert result.structured_content["error"] == "PermissionError"
            assert "approval-gated" in result.structured_content["message"]

    anyio.run(exercise)


def test_enabled_gate_still_uses_shared_tool_registry(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_invoke_tool(**kwargs):
        captured.update(kwargs)
        return {"action": kwargs["name"], "status": "ok"}

    monkeypatch.setattr("quant_agent.mcp_server.invoke_tool", fake_invoke_tool)

    result = invoke_mcp_tool(
        "apply_fix_yes",
        {"proposal": "proposal.json"},
        root=tmp_path,
        allow_approval_gated=True,
    )

    assert result == {"action": "apply_fix_yes", "status": "ok"}
    assert captured == {
        "name": "apply_fix_yes",
        "args": {"proposal": "proposal.json"},
        "root": tmp_path,
        "approved": True,
        "rationale": "MCP tool invocation.",
    }


def test_stdio_server_handshake_and_safe_call(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "quant_agent.mcp_server", "--root", str(tmp_path)],
            cwd=str(repo_root),
        )
        async with Client(parameters) as client:
            listing = await client.list_tools()
            result = await client.call_tool("read_capabilities", {})

            assert len(listing.tools) == len(TOOL_SPECS)
            assert result.is_error is False
            assert result.structured_content["action"] == "read_capabilities"

    anyio.run(exercise)
