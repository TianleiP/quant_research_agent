from __future__ import annotations

from typing import Any, Literal, TypedDict


AgentStatus = Literal["running", "complete", "blocked", "waiting_approval"]
ActionStatus = Literal["ok", "error"]


class AgentAction(TypedDict, total=False):
    name: str
    args: dict[str, Any]
    rationale: str
    requires_approval: bool
    tool_call_id: str
    tool_call_args: dict[str, Any]
    provider_output: list[dict[str, Any]]


class AgentActionRecord(TypedDict, total=False):
    step: int
    action: AgentAction
    status: ActionStatus
    result: dict[str, Any]
    error: str
    approved: bool


class AgentState(TypedDict, total=False):
    task: str
    planner: str
    llm_provider: str
    llm_model: str | None
    root: str
    session_id: str
    thread_id: str
    session_path: str
    state_path: str
    approval_path: str
    checkpoint_backend: str
    checkpoint_path: str | None
    checkpoint_reference: str
    max_steps: int
    step: int
    status: AgentStatus
    current_action: AgentAction | None
    approval_request: dict[str, Any] | None
    approval_decision: str | None
    observations: list[dict[str, Any]]
    actions: list[AgentActionRecord]
    react_trace: list[dict[str, Any]]
    react_final_message: str
    plan: dict[str, Any]
    plan_version: int
    plan_status: str
    current_plan_step_id: str | None
    plan_history: list[dict[str, Any]]
    evaluation_count: int
    max_evaluations: int
    evaluation_history: list[dict[str, Any]]
    replan_count: int
    max_replans: int
    errors: list[str]
    final_message: str


def successful_tool_names(state: AgentState) -> set[str]:
    names: set[str] = set()
    for record in state.get("actions", []):
        if record.get("status") != "ok":
            continue
        action = record.get("action")
        if action and action.get("name"):
            names.add(action["name"])
    return names
