from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from quant_agent.agent.argument_resolver import argument_context_for_prompt
from quant_agent.agent.state import AgentState
from quant_agent.agent.tool_catalog import TOOL_SPECS
from quant_agent.llm.client import create_llm_client


PLAN_REACT = "plan-react"
MAX_PLAN_STEPS = 6

PLANNER_INSTRUCTIONS = """You are the structured planner for quant-agent.
Return a compact executable plan that satisfies the supplied JSON schema.
Use only tool names from allowed_tools. Do not include read_project_state or read_capabilities; the runtime performs those preflight reads automatically.
Each step should describe an observable result, list one or more acceptable tools for that step, and include concrete success criteria.
Keep steps ordered, non-duplicative, and small enough for the supplied execution budget.
Never plan raw file edits, dependency installation, or actions outside the tool catalog.
"""

EVALUATOR_INSTRUCTIONS = """You are the bounded progress evaluator for quant-agent.
Evaluate only the current plan step using the actual last tool result.
Mark the current step complete only when its success criteria have evidence in that result.
Return continue when more tool evidence is needed, replan when the remaining plan no longer fits the evidence, complete only when no planned work remains, and blocked when progress is impossible.
Keep feedback and the user-facing summary concise. Return only the required structured object.
"""

REPLANNER_INSTRUCTIONS = """You are the bounded replanner for quant-agent.
Return only the remaining executable steps after considering evaluator feedback and completed evidence.
Use only tool names from allowed_tools. Do not repeat completed work, and stay within the remaining execution budget.
Never plan raw file edits, dependency installation, or actions outside the tool catalog.
"""


def plan_schema() -> dict[str, Any]:
    allowed_names = sorted(name for name in TOOL_SPECS if name not in preflight_tool_names())
    return {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "tool_names": {
                            "type": "array",
                            "items": {"type": "string", "enum": allowed_names},
                        },
                        "success_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["title", "description", "tool_names", "success_criteria"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["goal", "steps"],
        "additionalProperties": False,
    }


EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["continue", "replan", "complete", "blocked"],
        },
        "completed_step_ids": {"type": "array", "items": {"type": "string"}},
        "feedback": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["verdict", "completed_step_ids", "feedback", "summary"],
    "additionalProperties": False,
}


def create_initial_plan(state: AgentState) -> dict[str, Any]:
    max_steps = initial_plan_budget(state)
    client = create_llm_client(
        provider=str(state.get("llm_provider") or "auto"),
        model=state.get("llm_model"),
    )
    payload = client.complete_structured(
        PLANNER_INSTRUCTIONS,
        build_plan_prompt(state, max_steps=max_steps),
        plan_schema(),
        "quant_agent_plan",
    )
    return normalize_plan(payload, version=1, max_steps=max_steps)


def evaluate_current_step(state: AgentState) -> dict[str, Any]:
    current = current_plan_step(state)
    if current is None:
        return {
            "verdict": "complete",
            "completed_step_ids": [],
            "feedback": "No plan steps remain.",
            "summary": "All planned work is complete.",
        }
    client = create_llm_client(
        provider=str(state.get("llm_provider") or "auto"),
        model=state.get("llm_model"),
    )
    payload = client.complete_structured(
        EVALUATOR_INSTRUCTIONS,
        build_evaluation_prompt(state, current),
        EVALUATION_SCHEMA,
        "quant_agent_evaluation",
    )
    return normalize_evaluation(payload, current_step=current, last_action=latest_action_record(state))


def create_revised_plan(state: AgentState, feedback: str) -> dict[str, Any]:
    version = int(state.get("plan_version", 1)) + 1
    remaining_budget = max(1, min(MAX_PLAN_STEPS, int(state.get("max_steps", 1)) - int(state.get("step", 0))))
    client = create_llm_client(
        provider=str(state.get("llm_provider") or "auto"),
        model=state.get("llm_model"),
    )
    payload = client.complete_structured(
        REPLANNER_INSTRUCTIONS,
        build_replan_prompt(state, feedback=feedback, max_steps=remaining_budget),
        plan_schema(),
        "quant_agent_replan",
    )
    return normalize_plan(payload, version=version, max_steps=remaining_budget)


def normalize_plan(payload: dict[str, Any], *, version: int, max_steps: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Structured planner output must be an object.")
    goal = payload.get("goal")
    raw_steps = payload.get("steps")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Structured plan requires a non-empty goal.")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Structured plan requires at least one step.")
    if len(raw_steps) > max_steps:
        raise ValueError(f"Structured plan has {len(raw_steps)} steps but only {max_steps} fit the budget.")

    plan_steps = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            raise ValueError("Every structured plan step must be an object.")
        title = require_text(raw_step.get("title"), "plan step title")
        description = require_text(raw_step.get("description"), "plan step description")
        tool_names = require_text_list(raw_step.get("tool_names"), "plan step tool_names")
        criteria = require_text_list(raw_step.get("success_criteria"), "plan step success_criteria")
        unknown = set(tool_names) - set(TOOL_SPECS)
        if unknown:
            raise ValueError("Structured plan contains unknown tools: " + ", ".join(sorted(unknown)))
        forbidden = set(tool_names) & preflight_tool_names()
        if forbidden:
            raise ValueError("Structured plan must not include automatic preflight tools.")
        plan_steps.append(
            {
                "id": f"v{version}_step_{index}",
                "title": title,
                "description": description,
                "tool_names": list(dict.fromkeys(tool_names)),
                "success_criteria": criteria,
                "status": "in_progress" if index == 1 else "pending",
            }
        )
    return {
        "goal": goal.strip(),
        "version": version,
        "status": "active",
        "steps": plan_steps,
    }


def normalize_evaluation(
    payload: dict[str, Any],
    *,
    current_step: dict[str, Any],
    last_action: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Structured evaluator output must be an object.")
    verdict = payload.get("verdict")
    if verdict not in {"continue", "replan", "complete", "blocked"}:
        raise ValueError("Evaluator verdict must be continue, replan, complete, or blocked.")
    completed_step_ids = payload.get("completed_step_ids")
    if not isinstance(completed_step_ids, list) or any(not isinstance(item, str) for item in completed_step_ids):
        raise ValueError("Evaluator completed_step_ids must be a list of strings.")
    completed_step_ids = list(dict.fromkeys(completed_step_ids))
    current_id = current_step.get("id")
    if any(item != current_id for item in completed_step_ids):
        raise ValueError("Evaluator may complete only the current plan step.")
    if completed_step_ids:
        action = (last_action or {}).get("action", {})
        action_name = action.get("name") if isinstance(action, dict) else None
        if (last_action or {}).get("status") != "ok" or action_name not in current_step.get("tool_names", []):
            raise ValueError("Evaluator cannot complete a step without a successful matching tool result.")
    return {
        "verdict": verdict,
        "completed_step_ids": completed_step_ids,
        "feedback": require_text(payload.get("feedback"), "evaluator feedback"),
        "summary": require_text(payload.get("summary"), "evaluator summary"),
    }


def mark_completed_steps(plan: dict[str, Any], completed_step_ids: list[str]) -> dict[str, Any]:
    next_plan = copy.deepcopy(plan)
    for step in next_plan.get("steps", []):
        if step.get("id") in completed_step_ids:
            step["status"] = "complete"
    remaining_steps = [step for step in next_plan.get("steps", []) if step.get("status") != "complete"]
    for step in remaining_steps:
        step["status"] = "pending"
    if remaining_steps:
        remaining_steps[0]["status"] = "in_progress"
        next_plan["status"] = "active"
    else:
        next_plan["status"] = "complete"
    return next_plan


def merge_revised_plan(state: AgentState, revised: dict[str, Any]) -> dict[str, Any]:
    old_plan = state.get("plan", {})
    completed_steps = [
        copy.deepcopy(step)
        for step in old_plan.get("steps", [])
        if isinstance(step, dict) and step.get("status") == "complete"
    ] if isinstance(old_plan, dict) else []
    next_plan = copy.deepcopy(revised)
    next_plan["steps"] = [*completed_steps, *next_plan.get("steps", [])]
    return next_plan


def current_plan_step(state: AgentState) -> dict[str, Any] | None:
    plan = state.get("plan")
    if not isinstance(plan, dict):
        return None
    for step in plan.get("steps", []):
        if isinstance(step, dict) and step.get("status") == "in_progress":
            return step
    for step in plan.get("steps", []):
        if isinstance(step, dict) and step.get("status") == "pending":
            return step
    return None


def current_plan_tool_names(state: AgentState) -> set[str]:
    step = current_plan_step(state)
    if step is None:
        return set()
    return {str(name) for name in step.get("tool_names", [])}


def initial_plan_budget(state: AgentState) -> int:
    return max(1, min(MAX_PLAN_STEPS, int(state.get("max_steps", 3)) - 2))


def build_plan_prompt(state: AgentState, *, max_steps: int) -> str:
    root = Path(str(state.get("root") or Path.cwd()))
    context = {
        "task": state.get("task"),
        "max_plan_steps": max_steps,
        "allowed_tools": tool_context(),
        "argument_defaults": argument_context_for_prompt(root, task=str(state.get("task") or "")),
    }
    return "Planning context JSON:\n" + json.dumps(context, indent=2, sort_keys=True)


def build_evaluation_prompt(state: AgentState, current_step: dict[str, Any]) -> str:
    context = {
        "task": state.get("task"),
        "plan": state.get("plan"),
        "current_step": current_step,
        "last_action": latest_action_record(state),
        "evaluation_count": state.get("evaluation_count", 0),
        "max_evaluations": state.get("max_evaluations", 0),
        "replan_count": state.get("replan_count", 0),
        "max_replans": state.get("max_replans", 0),
    }
    return "Evaluation context JSON:\n" + json.dumps(context, indent=2, sort_keys=True, default=str)


def build_replan_prompt(state: AgentState, *, feedback: str, max_steps: int) -> str:
    context = {
        "task": state.get("task"),
        "current_plan": state.get("plan"),
        "completed_actions": state.get("actions", []),
        "evaluator_feedback": feedback,
        "max_plan_steps": max_steps,
        "allowed_tools": tool_context(),
    }
    return "Replanning context JSON:\n" + json.dumps(context, indent=2, sort_keys=True, default=str)


def tool_context() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec.description,
            "requires_approval": spec.requires_approval,
        }
        for name, spec in sorted(TOOL_SPECS.items())
        if name not in preflight_tool_names()
    ]


def latest_action_record(state: AgentState) -> dict[str, Any] | None:
    actions = state.get("actions", [])
    return actions[-1] if actions else None


def preflight_tool_names() -> set[str]:
    return {"read_project_state", "read_capabilities"}


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Structured {label} must be non-empty text.")
    return value.strip()


def require_text_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Structured {label} must be a non-empty list.")
    text_values = []
    for item in value:
        text_values.append(require_text(item, label))
    return text_values
