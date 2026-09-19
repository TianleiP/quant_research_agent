from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quant_agent.agent.argument_resolver import argument_context_for_prompt, should_retrieve_code_context
from quant_agent.agent.actions import make_action, validate_action
from quant_agent.agent.lifecycle import choose_patch_lifecycle_action, wants_patch_lifecycle
from quant_agent.agent.planning import PLAN_REACT, current_plan_tool_names
from quant_agent.agent.state import AgentAction, AgentState, successful_tool_names
from quant_agent.agent.tool_catalog import TOOL_SPECS, get_tool_spec
from quant_agent.llm.client import create_llm_client


REACT_INSTRUCTIONS = """You are the tool-calling executor for quant-agent.
Use the supplied function tools to gather evidence and carry out the user's task.
When a structured plan is present, work only on its current step using the tools exposed for that step.
Choose at most one tool per model turn. The application will execute it and return its result as a tool message.
When the available evidence is enough, return a concise final answer instead of calling another tool.
Never invent tools, claim a tool ran before receiving its result, or request raw file edits or dependency installs.
Tool risk and approval are enforced by application code; approval-gated tools will pause before execution.
Do not reveal hidden chain-of-thought. Use tool calls for actions and the final response for conclusions.
"""


def choose_react_action(state: AgentState) -> AgentAction | None:
    successful_tools = successful_tool_names(state)
    if "read_project_state" not in successful_tools:
        return make_action("read_project_state", "Load centralized implementation state before ReAct execution.")
    if "read_capabilities" not in successful_tools:
        return make_action("read_capabilities", "Load the capability map before exposing tools to the model.")

    lifecycle_action = choose_patch_lifecycle_action(state, successful_tools)
    if lifecycle_action:
        return lifecycle_action
    if wants_patch_lifecycle(str(state.get("task") or "")):
        if state.get("planner") == PLAN_REACT and current_plan_tool_names(state):
            raise RuntimeError("Deterministic patch lifecycle ended while structured plan steps remain.")
        return None
    if state.get("planner") != PLAN_REACT and should_retrieve_code_context(state, successful_tools):
        return make_action("query_code_memory", "Retrieve source context before model-directed trace or fix work.")

    client = create_llm_client(
        provider=str(state.get("llm_provider") or "auto"),
        model=state.get("llm_model"),
    )
    tools = react_tools_for_state(state)
    decision = client.decide_with_tools(
        REACT_INSTRUCTIONS,
        build_react_messages(state, successful_tools),
        tools,
    )
    if not isinstance(decision, dict):
        raise ValueError("ReAct model decision must be an object.")

    decision_type = decision.get("type")
    if decision_type == "final":
        if state.get("planner") == PLAN_REACT and current_plan_tool_names(state):
            raise ValueError("Plan-ReAct executor returned a final answer before the structured plan completed.")
        content = decision.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("ReAct final response must contain text.")
        state["react_final_message"] = content.strip()
        state.setdefault("react_trace", []).append(
            {"type": "final", "content": content.strip()}
        )
        return None
    if decision_type != "tool_call":
        raise ValueError("ReAct model decision type must be 'tool_call' or 'final'.")

    call_id = decision.get("call_id")
    name = decision.get("name")
    args = decision.get("args", {})
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("ReAct tool call is missing call_id.")
    if not isinstance(name, str) or not name:
        raise ValueError("ReAct tool call is missing a tool name.")
    if not isinstance(args, dict):
        raise ValueError("ReAct tool-call args must be an object.")

    spec = get_tool_spec(name)
    action = make_action(
        name,
        f"Model requested {name} through native tool calling.",
        args=args,
        requires_approval=spec.requires_approval,
    )
    action["tool_call_id"] = call_id
    action["tool_call_args"] = dict(args)
    provider_output = decision.get("provider_output")
    if isinstance(provider_output, list):
        action["provider_output"] = [item for item in provider_output if isinstance(item, dict)]
    validate_action(action)
    state.setdefault("react_trace", []).append(
        {
            "type": "tool_call",
            "call_id": call_id,
            "name": name,
            "args": args,
            "risk": spec.risk,
        }
    )
    return action


def build_react_messages(
    state: AgentState,
    successful_tools: set[str] | None = None,
) -> list[dict[str, Any]]:
    if successful_tools is None:
        successful_tools = successful_tool_names(state)
    root = Path(str(state.get("root") or Path.cwd()))
    context = {
        "task": state.get("task"),
        "step": state.get("step", 0),
        "max_steps": state.get("max_steps", 0),
        "completed_actions": sorted(successful_tools),
        "allowed_actions": react_tools_for_state(state, include_parameters=False),
        "argument_defaults": argument_context_for_prompt(
            root,
            task=str(state.get("task") or ""),
        ).get("action_defaults", {}),
        "preflight_observations": compact_preflight_observations(state),
        "plan": state.get("plan") if state.get("planner") == PLAN_REACT else None,
        "current_plan_step_id": state.get("current_plan_step_id"),
        "latest_evaluation": state.get("evaluation_history", [])[-1]
        if state.get("evaluation_history")
        else None,
    }
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "ReAct execution context JSON:\n" + json.dumps(context, indent=2, sort_keys=True),
        }
    ]

    for record in state.get("actions", []):
        action = record.get("action", {})
        if not isinstance(action, dict):
            continue
        call_id = action.get("tool_call_id")
        name = action.get("name")
        if not isinstance(call_id, str) or not call_id or not isinstance(name, str):
            continue
        args = action.get("tool_call_args", action.get("args", {}))
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, sort_keys=True, default=str),
                    },
                }
            ],
        }
        if action.get("provider_output"):
            assistant_message["provider_output"] = action["provider_output"]
        messages.append(assistant_message)
        output = record.get("result") if record.get("status") == "ok" else {
            "error": record.get("error", "tool execution failed")
        }
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(output, sort_keys=True, default=str),
            }
        )
    return messages


def react_tool_payload(
    include_parameters: bool = True,
    names: set[str] | None = None,
) -> list[dict[str, Any]]:
    tool_payloads = []
    for name, spec in sorted(TOOL_SPECS.items()):
        if name in {"read_project_state", "read_capabilities"}:
            continue
        if names is not None and name not in names:
            continue
        description = spec.description
        if spec.requires_approval:
            description += " Execution pauses for explicit user approval."
        tool_payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "requires_approval": spec.requires_approval,
        }
        if include_parameters:
            tool_payload["parameters"] = spec.parameters
        tool_payloads.append(tool_payload)
    return tool_payloads


def react_tools_for_state(
    state: AgentState,
    include_parameters: bool = True,
) -> list[dict[str, Any]]:
    names = current_plan_tool_names(state) if state.get("planner") == PLAN_REACT else None
    tools = react_tool_payload(include_parameters=include_parameters, names=names)
    if state.get("planner") == PLAN_REACT and not tools:
        raise RuntimeError("Plan-ReAct has no tools available for the current plan step.")
    return tools


def compact_preflight_observations(state: AgentState) -> list[dict[str, Any]]:
    preflight = []
    for item in state.get("observations", [])[-6:]:
        if item.get("action") not in {"read_project_state", "read_capabilities"}:
            continue
        preflight.append(
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "type",
                    "action",
                    "project",
                    "latest_verified_run",
                    "known_gaps",
                    "next_recommended_steps",
                    "capabilities",
                }
            }
        )
    return preflight
