from __future__ import annotations

from typing import Any

from quant_agent.agent.state import AgentAction
from quant_agent.agent.tool_catalog import get_tool_spec, tool_names


SAFE_ACTIONS = tool_names("safe")

APPROVAL_GATED_ACTIONS = tool_names("approval_gated")

BLOCKED_ACTIONS = {
    "edit_external_repo",
    "install_package",
}


def make_action(
    name: str,
    rationale: str,
    args: dict[str, Any] | None = None,
    requires_approval: bool = False,
) -> AgentAction:
    return {
        "name": name,
        "args": args or {},
        "rationale": rationale,
        "requires_approval": requires_approval,
    }


def validate_action(action: AgentAction) -> None:
    name = action.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("Agent action is missing a name.")
    if name in BLOCKED_ACTIONS:
        raise PermissionError(f"Action {name!r} is blocked.")
    spec = get_tool_spec(name)
    if spec.requires_approval and not action.get("requires_approval"):
        raise PermissionError(f"Action {name!r} must be marked requires_approval=true.")
    if action.get("requires_approval") and not spec.requires_approval:
        raise PermissionError(f"Action {name!r} requires approval and cannot be auto-executed.")


def action_requires_approval(action: AgentAction) -> bool:
    name = action.get("name")
    if not isinstance(name, str) or not name:
        return bool(action.get("requires_approval"))
    return bool(get_tool_spec(name).requires_approval or action.get("requires_approval"))


def validate_action_for_execution(action: AgentAction, approved: bool = False) -> None:
    validate_action(action)
    if action_requires_approval(action) and not approved:
        raise PermissionError(f"Action {action.get('name')!r} requires approval before execution.")
