from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quant_agent.agent.argument_resolver import argument_context_for_prompt, should_retrieve_code_context
from quant_agent.agent.actions import APPROVAL_GATED_ACTIONS, SAFE_ACTIONS, make_action, validate_action
from quant_agent.agent.lifecycle import choose_patch_lifecycle_action, wants_patch_lifecycle
from quant_agent.agent.state import AgentAction, AgentState, successful_tool_names
from quant_agent.agent.tool_catalog import get_tool_spec
from quant_agent.llm.client import create_llm_client


INSTRUCTIONS = """You are the quant-agent agent-session planner.
Return one strict JSON object and no markdown.
You may only choose one action from the allowed_actions list.
Safe actions execute immediately.
Approval-gated actions must set requires_approval=true and will pause for user approval before execution.
The planner context includes argument_defaults inferred from project state, latest run artifacts, and latest proposals.
If an action has matching argument_defaults, you may omit those args or use them exactly.
Never choose blocked actions, raw file edits, dependency installs, or tools outside the allowed_actions list.
If enough information has been gathered, return {"status": "complete"}.
If more information is needed, return:
{
  "status": "continue",
  "action": {
    "name": "<allowed action>",
    "args": {},
    "rationale": "<brief concrete reason>",
    "requires_approval": false
  }
}
"""


def choose_llm_action(state: AgentState) -> AgentAction | None:
    successful_tools = successful_tool_names(state)
    if "read_project_state" not in successful_tools:
        return make_action("read_project_state", "Load centralized implementation state before LLM planning.")
    if "read_capabilities" not in successful_tools:
        return make_action("read_capabilities", "Load available safe actions before LLM planning.")
    lifecycle_action = choose_patch_lifecycle_action(state, successful_tools)
    if lifecycle_action:
        return lifecycle_action
    if wants_patch_lifecycle(str(state.get("task") or "")):
        return None
    if should_retrieve_code_context(state, successful_tools):
        return make_action("query_code_memory", "Retrieve source context from code memory before planning trace/fix work.")

    client = create_llm_client(
        provider=str(state.get("llm_provider") or "auto"),
        model=state.get("llm_model"),
    )
    response = client.complete(INSTRUCTIONS, build_planner_prompt(state, successful_tools))
    payload = parse_planner_response(response)
    status = payload.get("status")
    if status == "complete":
        return None
    if status != "continue":
        raise ValueError("LLM planner response status must be 'continue' or 'complete'.")
    raw_action = payload.get("action")
    if not isinstance(raw_action, dict):
        raise ValueError("LLM planner response missing action object.")
    action = normalize_action(raw_action)
    validate_action(action)
    if action["name"] in successful_tools:
        raise ValueError(f"LLM planner repeated already completed action: {action['name']}")
    return action


def build_planner_prompt(state: AgentState, successful_tools: set[str]) -> str:
    context = {
        "task": state.get("task"),
        "step": state.get("step", 0),
        "max_steps": state.get("max_steps", 0),
        "completed_actions": sorted(successful_tools),
        "allowed_actions": allowed_action_payload(),
        "argument_defaults": argument_context_for_prompt(
            Path(str(state.get("root") or Path.cwd())),
            task=str(state.get("task") or ""),
        ).get("action_defaults", {}),
        "recent_observations": compact_observations(state.get("observations", [])),
    }
    return "Planner context JSON:\n" + json.dumps(context, indent=2, sort_keys=True)


def allowed_action_payload() -> list[dict[str, Any]]:
    actions = []
    for name in sorted(SAFE_ACTIONS | APPROVAL_GATED_ACTIONS):
        if name in {"read_project_state", "read_capabilities"}:
            continue
        actions.append(
            {
                "name": name,
                "description": get_tool_spec(name).description,
                "requires_approval": get_tool_spec(name).requires_approval,
            }
        )
    return actions


def parse_planner_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = json.loads(extract_json_object(stripped))
    if not isinstance(payload, dict):
        raise ValueError("LLM planner response must be a JSON object.")
    return payload


def normalize_action(raw_action: dict[str, Any]) -> AgentAction:
    name = raw_action.get("name")
    if not isinstance(name, str):
        raise ValueError("LLM planner action name must be a string.")
    args = raw_action.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("LLM planner action args must be an object.")
    rationale = raw_action.get("rationale", "")
    return {
        "name": name,
        "args": args,
        "rationale": str(rationale),
        "requires_approval": bool(raw_action.get("requires_approval", False)),
    }


def extract_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in LLM planner response.")
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("Unclosed JSON object in LLM planner response.")


def compact_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent = []
    for item in observations[-6:]:
        current = {
            key: value
            for key, value in item.items()
            if key in {"type", "action", "run_id", "recommendation", "metrics", "project", "latest_verified_run", "known_gaps", "next_recommended_steps", "promotion_recommendation", "artifact_contract_recommendation", "query", "result_count", "results", "verification_status", "error_type", "error", "quality_gate", "proposal_dir", "failure_context_path", "synthesis_status", "synthesized_issue", "synthesis_reason", "apply_supported"}
        }
        recent.append(current)
    return recent
