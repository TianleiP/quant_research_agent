from __future__ import annotations

import argparse
import json
import os
from functools import partial
from pathlib import Path
from typing import Any, Sequence

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from quant_agent.agent.tool_catalog import TOOL_SPECS, ToolSpec, get_tool_spec
from quant_agent.agent.tools import invoke_tool


SERVER_NAME = "quant-agent"
SERVER_VERSION = "0.1.0"
SERVER_INSTRUCTIONS = (
    "Inspect and repair the configured quantitative-research project through its shared tool registry. "
    "Read evidence before proposing changes. Approval-gated tools are disabled unless the server was "
    "started with --enable-approval-gated; the MCP host must still ask for approval before write tools."
)


def _title(name: str) -> str:
    return name.replace("_", " ").title()


def _annotations(spec: ToolSpec) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        title=_title(spec.name),
        readOnlyHint=spec.read_only,
        destructiveHint=spec.destructive,
        idempotentHint=spec.read_only or spec.name == "apply_fix_dry_run",
        openWorldHint=spec.open_world,
    )


def mcp_tool_specs() -> list[types.Tool]:
    """Translate the canonical internal catalog into MCP tool definitions."""
    return [
        types.Tool(
            name=spec.name,
            title=_title(spec.name),
            description=spec.description,
            inputSchema=spec.parameters,
            outputSchema={
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
                "additionalProperties": True,
            },
            annotations=_annotations(spec),
        )
        for spec in TOOL_SPECS.values()
    ]


def invoke_mcp_tool(
    name: str,
    arguments: dict[str, Any] | None,
    root: Path,
    allow_approval_gated: bool,
) -> dict[str, Any]:
    """Invoke one canonical tool while keeping deterministic approval enforcement."""
    spec = get_tool_spec(name)
    if spec.requires_approval and not allow_approval_gated:
        raise PermissionError(
            f"Tool '{name}' is approval-gated and this MCP server has it disabled. "
            "Restart with --enable-approval-gated and let the MCP host request approval."
        )
    return invoke_tool(
        name=name,
        args=arguments or {},
        root=root,
        approved=spec.requires_approval,
        rationale="MCP tool invocation.",
    )


def _success_result(payload: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))],
        structuredContent=payload,
        isError=False,
    )


def _error_result(name: str, exc: Exception) -> types.CallToolResult:
    payload = {
        "action": name,
        "error": type(exc).__name__,
        "message": str(exc),
    }
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))],
        structuredContent=payload,
        isError=True,
    )


def create_mcp_server(root: Path, allow_approval_gated: bool = False) -> Server:
    project_root = root.resolve()

    async def list_tools(_context: Any, _params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=mcp_tool_specs())

    async def call_tool(_context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        # Tool handlers may fail in domain-specific ways; the stdio protocol must stay alive.
        try:
            payload = await anyio.to_thread.run_sync(
                partial(
                    invoke_mcp_tool,
                    name=name,
                    arguments=arguments,
                    root=project_root,
                    allow_approval_gated=allow_approval_gated,
                )
            )
        except Exception as exc:
            return _error_result(name, exc)
        return _success_result(payload)

    return Server(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        instructions=SERVER_INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_stdio(root: Path, allow_approval_gated: bool = False) -> None:
    server = create_mcp_server(root, allow_approval_gated=allow_approval_gated)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expose quant-agent tools over MCP stdio.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("QUANT_AGENT_ROOT", Path.cwd())),
        help="Quant-agent project root (default: QUANT_AGENT_ROOT or current directory).",
    )
    parser.add_argument(
        "--enable-approval-gated",
        action="store_true",
        default=os.environ.get("QUANT_AGENT_MCP_ENABLE_APPROVAL_GATED", "").lower() in {"1", "true", "yes"},
        help="Allow approval-gated tools after the MCP host grants its own approval.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    anyio.run(serve_stdio, args.root, args.enable_approval_gated)


if __name__ == "__main__":
    main()
