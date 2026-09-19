from __future__ import annotations

import json
from typing import Any


class MockLLMClient:
    provider = "mock"
    model = "mock-diagnosis"

    def complete(self, instructions: str, prompt: str) -> str:
        if "quant-agent agent-session planner" in instructions.lower():
            return mock_agent_planner_response(prompt)
        if "generalized fix proposal drafter" in instructions.lower():
            return json.dumps(
                {
                    "summary": "Mock generalized fix draft created from the provided source context.",
                    "problem_statement": "The requested behavior needs a reviewed source change outside deterministic generators.",
                    "intended_behavior": [
                        "Preserve existing behavior while adding the requested source-level change.",
                        "Keep the final implementation small and directly testable.",
                    ],
                    "patch_plan": [
                        "Identify the smallest source block that owns the requested behavior.",
                        "Draft the change near the existing behavior boundary.",
                        "Run focused tests or a fresh strategy verification after review.",
                    ],
                    "candidate_unified_diff": "--- a/script.py\n+++ b/script.py\n@@ -1,1 +1,2 @@\n # mock candidate diff\n+# review before applying\n",
                    "verification_commands": [
                        "quant-agent apply-fix --proposal <deterministic-proposal.json>",
                        "quant-agent run --config <strategy-config> --fresh",
                    ],
                    "risks": [
                        "Mock draft is not source-aware enough to apply directly.",
                    ],
                    "confidence": "low",
                }
            )
        if "single strict json object" in instructions.lower():
            return """
{
  "summary": "The run artifact bundle is readable and suitable for structured diagnosis.",
  "findings": [
    {
      "severity": "medium",
      "category": "missing_artifact",
      "title": "Position and trade artifacts are unavailable",
      "evidence": [
        "known_missing_artifacts includes per-symbol positions",
        "known_missing_artifacts includes trades"
      ],
      "recommended_action": "Add per-symbol position, trade, and ranking exports to the source backtest."
    }
  ],
  "missing_artifacts": [
    "positions",
    "trades",
    "ranking_tables"
  ],
  "next_actions": [
    "Run with a real provider for a model-backed diagnosis",
    "Add richer decision logs when the research simulator can export them"
  ]
}
""".strip()
        if "codebase entrypoint" in instructions.lower():
            return "\n".join(
                [
                    "# Entrypoint Explanation",
                    "",
                    "Provider: mock",
                    "",
                    "## Execution Flow",
                    "",
                    "- The indexed file and source excerpt were loaded successfully.",
                    "",
                    "## Inputs And Outputs",
                    "",
                    "- Artifact references from discovery are available for review.",
                    "",
                    "## Edit Points",
                    "",
                    "- Treat this as a smoke test only; no real model call was made.",
                    "",
                ]
            )
        return "\n".join(
            [
                "# LLM Diagnosis",
                "",
                "Provider: mock",
                "",
                "## Main Read",
                "",
                "The run artifact bundle is readable and suitable for LLM diagnosis.",
                "",
                "## Risks",
                "",
                "- Treat this as a smoke test only; no real model call was made.",
                "- Per-symbol positions, trades, and ranking tables are not available yet.",
                "",
                "## Next Checks",
                "",
                "- Run with `--provider openai` after setting `OPENAI_API_KEY`.",
                "- Add richer decision logs when the research simulator can export them.",
                "",
            ]
        )

    def decide_with_tools(
        self,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del instructions, tools
        user_prompt = next(
            (str(item.get("content") or "") for item in messages if item.get("role") == "user"),
            "{}",
        )
        payload = json.loads(mock_agent_planner_response(user_prompt))
        if payload.get("status") == "complete":
            return {
                "type": "final",
                "content": "Mock ReAct executor completed after gathering the requested evidence.",
            }
        action = payload["action"]
        context = extract_prompt_json(user_prompt)
        return {
            "type": "tool_call",
            "call_id": f"mock_call_{len(context.get('completed_actions', [])) + 1}",
            "name": action["name"],
            "args": action.get("args", {}),
        }

    def complete_structured(
        self,
        instructions: str,
        prompt: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        del schema, schema_name
        lowered = instructions.lower()
        if "bounded progress evaluator" in lowered:
            return mock_evaluation_response(prompt)
        if "bounded replanner" in lowered:
            return mock_replan_response(prompt)
        if "structured planner for quant-agent" in lowered:
            return mock_plan_response(prompt)
        raise RuntimeError("Mock provider does not recognize this structured-output task.")


def mock_agent_planner_response(prompt: str) -> str:
    context = extract_prompt_json(prompt)
    completed = set(context.get("completed_actions", []))
    task = str(context.get("task", "")).lower()
    allowed_items = {
        item.get("name"): item
        for item in context.get("allowed_actions", [])
        if isinstance(item, dict)
    }
    allowed = set(allowed_items)

    preferences = []
    if "memory" in task or "search code" in task or "source context" in task or "relevant source" in task:
        preferences.append("query_code_memory")
    if "inspect proposal" in task or "latest proposal" in task:
        preferences.append("inspect_proposal")
    if "trace" in task or "source" in task:
        preferences.append("trace_source")
    if "suggest" in task or "proposal" in task:
        preferences.append("suggest_fix")
    if "dry" in task and "fix" in task:
        preferences.append("apply_fix_dry_run")
    if "apply" in task and "fix" in task:
        preferences.append("apply_fix_yes")
    if "fresh" in task or "rerun" in task:
        preferences.append("run_fresh")
    if any(token in task for token in ["artifact", "position", "trade", "ranking", "contract", "inspect"]):
        preferences.append("inspect_artifacts_latest")
    if any(token in task for token in ["promotion", "promote", "candidate", "review"]):
        preferences.append("promote_latest")
    if any(token in task for token in ["metric", "cagr", "drawdown", "performance", "status", "summary"]):
        preferences.append("metrics_latest")
    preferences.append("inspect_latest_run")

    for name in preferences:
        if name in allowed and name not in completed:
            requires_approval = bool(allowed_items.get(name, {}).get("requires_approval"))
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": name,
                        "args": {},
                        "rationale": f"Mock planner selected {name} for the requested task.",
                        "requires_approval": requires_approval,
                    },
                }
            )
    return json.dumps({"status": "complete"})


def mock_plan_response(prompt: str) -> dict[str, Any]:
    context = extract_prompt_json(prompt)
    task = str(context.get("task", "")).lower()
    allowed = {
        item.get("name")
        for item in context.get("allowed_tools", [])
        if isinstance(item, dict)
    }
    limit = max(1, int(context.get("max_plan_steps", 1)))
    names: list[str] = []

    if "lifecycle" in task and "proposal" in task:
        names.extend(
            [
                "inspect_proposal",
                "apply_fix_dry_run",
                "apply_fix_yes",
                "run_fresh",
                "inspect_artifacts_latest",
            ]
        )
    else:
        if any(token in task for token in ["memory", "search code", "source context", "relevant source"]):
            names.append("query_code_memory")
        if "inspect proposal" in task or "latest proposal" in task:
            names.append("inspect_proposal")
        if "trace" in task or "source" in task:
            names.append("trace_source")
        if "suggest" in task or "proposal" in task:
            names.append("suggest_fix")
        if "dry" in task and "fix" in task:
            names.append("apply_fix_dry_run")
        if "apply" in task and "fix" in task:
            names.append("apply_fix_yes")
        if "fresh" in task or "rerun" in task:
            names.append("run_fresh")
        if any(token in task for token in ["artifact", "position", "trade", "ranking", "contract", "inspect"]):
            names.append("inspect_artifacts_latest")
        if any(token in task for token in ["promotion", "promote", "candidate", "review"]):
            names.append("promote_latest")
        if any(token in task for token in ["metric", "cagr", "drawdown", "performance", "status", "summary"]):
            names.append("metrics_latest")
        names.append("inspect_latest_run")

    names = [name for name in dict.fromkeys(names) if name in allowed][:limit]
    if not names:
        names = ["inspect_latest_run"]
    return {
        "goal": str(context.get("task") or "Inspect the current quant-agent state."),
        "steps": [
            {
                "title": name.replace("_", " ").title(),
                "description": f"Use {name} to gather the next required piece of evidence.",
                "tool_names": [name],
                "success_criteria": [f"{name} returns a successful structured result."],
            }
            for name in names
        ],
    }


def mock_evaluation_response(prompt: str) -> dict[str, Any]:
    context = extract_prompt_json(prompt)
    current = context.get("current_step", {})
    last = context.get("last_action", {})
    action = last.get("action", {}) if isinstance(last, dict) else {}
    matches = (
        isinstance(current, dict)
        and isinstance(action, dict)
        and last.get("status") == "ok"
        and action.get("name") in current.get("tool_names", [])
    )
    completed = [current.get("id")] if matches and current.get("id") else []
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
        "feedback": "The latest tool result satisfies the current step." if matches else "More matching tool evidence is needed.",
        "summary": "All planned evidence has been gathered." if matches and not remaining else "Continue with the next planned step.",
    }


def mock_replan_response(prompt: str) -> dict[str, Any]:
    context = extract_prompt_json(prompt)
    plan = context.get("current_plan", {})
    remaining = [
        step
        for step in plan.get("steps", [])
        if isinstance(step, dict) and step.get("status") != "complete"
    ] if isinstance(plan, dict) else []
    limit = max(1, int(context.get("max_plan_steps", 1)))
    steps = [
        {
            "title": str(step.get("title") or "Inspect latest run"),
            "description": str(step.get("description") or "Gather remaining evidence."),
            "tool_names": list(step.get("tool_names") or ["inspect_latest_run"]),
            "success_criteria": list(step.get("success_criteria") or ["A structured result is returned."]),
        }
        for step in remaining[:limit]
    ]
    if not steps:
        steps = [
            {
                "title": "Inspect Latest Run",
                "description": "Gather a final run summary after replanning.",
                "tool_names": ["inspect_latest_run"],
                "success_criteria": ["The latest run summary is returned."],
            }
        ]
    return {
        "goal": str(context.get("task") or "Complete the remaining work."),
        "steps": steps,
    }


def extract_prompt_json(prompt: str) -> dict:
    start = prompt.find("{")
    if start < 0:
        return {}
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
