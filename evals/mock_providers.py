from __future__ import annotations

import json
from typing import Any


class FailureAwarePlanReactClient:
    """Eval-only scripted model that replans after a failed fresh verification."""

    provider = "eval-scripted"
    model = "failure-aware-v1"

    def complete_structured(
        self,
        instructions: str,
        prompt: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        del schema, schema_name
        lowered = instructions.lower()
        context = _extract_json(prompt)
        if "structured planner for quant-agent" in lowered:
            return _plan(
                context,
                ["inspect_proposal", "apply_fix_dry_run", "apply_fix_yes", "run_fresh"],
            )
        if "bounded replanner" in lowered:
            return _plan(context, ["suggest_revision", "synthesize_revision"])
        if "bounded progress evaluator" in lowered:
            return _evaluate(context)
        raise RuntimeError("FailureAwarePlanReactClient received an unsupported structured task.")

    def decide_with_tools(
        self,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del instructions, messages
        if not tools:
            return {"type": "final", "content": "No further tool action is available."}
        return {
            "type": "tool_call",
            "call_id": "eval_fallback_call",
            "name": tools[0]["name"],
            "args": {},
        }


def _plan(context: dict[str, Any], names: list[str]) -> dict[str, Any]:
    limit = max(1, int(context.get("max_plan_steps", len(names))))
    selected = names[:limit]
    return {
        "goal": str(context.get("task") or "Evaluate verification failure handling."),
        "steps": [
            {
                "title": name.replace("_", " ").title(),
                "description": f"Run {name} and record its structured evidence.",
                "tool_names": [name],
                "success_criteria": [f"A structured {name} result is recorded."],
            }
            for name in selected
        ],
    }


def _evaluate(context: dict[str, Any]) -> dict[str, Any]:
    current = context.get("current_step", {})
    last = context.get("last_action", {})
    action = last.get("action", {}) if isinstance(last, dict) else {}
    result = last.get("result", {}) if isinstance(last, dict) else {}
    current_id = current.get("id") if isinstance(current, dict) else None
    matches = (
        isinstance(action, dict)
        and last.get("status") == "ok"
        and action.get("name") in current.get("tool_names", [])
    )
    completed = [current_id] if matches and current_id else []
    if action.get("name") == "run_fresh" and result.get("verification_status") == "failed":
        return {
            "verdict": "replan",
            "completed_step_ids": completed,
            "feedback": "Fresh verification failed; preserve failure evidence and revise the remaining plan.",
            "summary": "Verification failed and needs a bounded revision path.",
        }
    plan = context.get("plan", {})
    remaining = [
        step
        for step in plan.get("steps", [])
        if isinstance(step, dict)
        and step.get("status") != "complete"
        and step.get("id") not in completed
    ] if isinstance(plan, dict) else []
    return {
        "verdict": "complete" if matches and not remaining else "continue",
        "completed_step_ids": completed,
        "feedback": "The matching tool result was recorded." if matches else "More matching evidence is required.",
        "summary": "The bounded revision path is complete." if matches and not remaining else "Continue the current plan.",
    }


def _extract_json(prompt: str) -> dict[str, Any]:
    start = prompt.find("{")
    if start < 0:
        return {}
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}

