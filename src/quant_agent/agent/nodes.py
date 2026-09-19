from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from quant_agent.agent.argument_resolver import apply_resolved_args, latest_memory_index
from quant_agent.agent.actions import action_requires_approval, make_action
from quant_agent.agent.lifecycle import RevisionLoopBlocked, choose_patch_lifecycle_action, wants_patch_lifecycle
from quant_agent.agent.planner import choose_llm_action
from quant_agent.agent.planning import (
    PLAN_REACT,
    create_initial_plan,
    create_revised_plan,
    current_plan_step,
    evaluate_current_step,
    mark_completed_steps,
    merge_revised_plan,
    preflight_tool_names,
)
from quant_agent.agent.react import choose_react_action
from quant_agent.agent.session import append_session_event, write_approval_request
from quant_agent.agent.state import AgentAction, AgentState, successful_tool_names
from quant_agent.agent.tools import execute_agent_action


# Provider and tool failures are recorded so a resumable thread never looks
# "running" after one of its graph nodes failed.
def observe_node(state: AgentState) -> AgentState:
    updated = ensure_state_lists(dict(state))
    if not any(item.get("type") == "session_started" for item in updated["observations"]):
        observation = {
            "type": "session_started",
            "task": updated["task"],
            "planner": updated.get("planner", "rule"),
            "root": updated["root"],
        }
        updated["observations"].append(observation)
        append_session_event(updated["session_path"], "observe", observation)
    return updated


def plan_node(state: AgentState) -> AgentState:
    updated = ensure_state_lists(dict(state))
    if updated.get("planner") != PLAN_REACT or updated.get("plan") or updated.get("status") != "running":
        return updated
    try:
        plan = create_initial_plan(updated)
        updated["plan"] = plan
        updated["plan_version"] = int(plan.get("version", 1))
        updated["plan_status"] = str(plan.get("status", "active"))
        current = current_plan_step(updated)
        updated["current_plan_step_id"] = current.get("id") if current else None
        updated["plan_history"].append(copy.deepcopy(plan))
        append_session_event(updated["session_path"], "plan_created", {"plan": plan})
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        updated["errors"].append(error)
        updated["status"] = "blocked"
        updated["final_message"] = f"Agent session blocked during structured planning: {error}"
        append_session_event(updated["session_path"], "error", {"phase": "plan", "error": error})
    return updated


def decide_node(state: AgentState) -> AgentState:
    updated = ensure_state_lists(dict(state))
    if updated.get("status") != "running":
        return updated

    if updated.get("current_action") and updated.get("approval_decision") == "approved":
        append_session_event(
            updated["session_path"],
            "decide",
            {"action": updated["current_action"], "resume": "approved"},
        )
        return updated

    try:
        action = choose_next_action(updated)
    except RevisionLoopBlocked as exc:
        error = str(exc)
        updated["errors"].append(error)
        updated["status"] = "blocked"
        updated["final_message"] = f"Agent session blocked by revision loop guard: {error}"
        append_session_event(updated["session_path"], "blocked", {"phase": "decide", "reason": error})
        return updated
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        updated["errors"].append(error)
        updated["status"] = "blocked"
        updated["final_message"] = f"Agent session blocked during planning: {error}"
        append_session_event(updated["session_path"], "error", {"phase": "decide", "error": error})
        return updated
    if action is None:
        updated["status"] = "complete"
        updated["current_action"] = None
        updated["final_message"] = build_final_message(updated)
        append_session_event(updated["session_path"], "complete", {"final_message": updated["final_message"]})
        return updated
    action = apply_resolved_args(action, Path(updated["root"]), task=str(updated.get("task") or ""))
    if int(updated.get("step", 0)) >= int(updated.get("max_steps", 1)):
        updated["status"] = "blocked"
        updated["final_message"] = "Agent session stopped because max_steps was reached."
        append_session_event(updated["session_path"], "blocked", {"reason": "max_steps", "next_action": action})
        return updated

    if action_requires_approval(action):
        approval = build_approval_request(updated, action)
        updated["status"] = "waiting_approval"
        updated["current_action"] = action
        updated["approval_request"] = approval
        updated["final_message"] = (
            f"Agent session is waiting for approval to run {action['name']}. "
            f"Approval request: {updated['approval_path']}"
        )
        write_approval_request(updated["approval_path"], approval)
        append_session_event(updated["session_path"], "approval_required", approval)
        return updated

    updated["current_action"] = action
    append_session_event(updated["session_path"], "decide", {"action": action})
    return updated


def execute_node(state: AgentState) -> AgentState:
    updated = ensure_state_lists(dict(state))
    action = updated.get("current_action")
    if not action:
        return updated
    try:
        approved = updated.get("approval_decision") == "approved"
        result = execute_agent_action(action, Path(updated["root"]), approved=approved)
        action_record = {
            "step": int(updated.get("step", 0)) + 1,
            "action": action,
            "status": "ok",
            "result": result,
            "approved": approved,
        }
        updated["actions"].append(action_record)
        updated["observations"].append({"type": "action_result", **result})
        append_session_event(updated["session_path"], "execute", action_record)
    except Exception as exc:  # pragma: no cover - failure shape depends on the tool
        error = f"{type(exc).__name__}: {exc}"
        updated["errors"].append(error)
        updated["actions"].append(
            {
                "step": int(updated.get("step", 0)) + 1,
                "action": action,
                "status": "error",
                "error": error,
            }
        )
        append_session_event(updated["session_path"], "error", {"action": action, "error": error})
        updated["status"] = "blocked"
        updated["final_message"] = f"Agent session blocked while executing {action.get('name')}: {error}"
    updated["step"] = int(updated.get("step", 0)) + 1
    updated["current_action"] = None
    updated["approval_decision"] = None
    updated["approval_request"] = None
    return updated


def evaluate_node(state: AgentState) -> AgentState:
    updated = ensure_state_lists(dict(state))
    if updated.get("planner") != PLAN_REACT or updated.get("status") != "running":
        return updated
    last_record = updated["actions"][-1] if updated["actions"] else None
    last_action = last_record.get("action", {}) if isinstance(last_record, dict) else {}
    if isinstance(last_action, dict) and last_action.get("name") in preflight_tool_names():
        return updated

    try:
        if int(updated.get("evaluation_count", 0)) >= int(updated.get("max_evaluations", 0)):
            raise RuntimeError("Evaluator call limit reached before the plan completed.")
        evaluation = evaluate_current_step(updated)
        updated["evaluation_count"] = int(updated.get("evaluation_count", 0)) + 1
        evaluation_record = {
            **evaluation,
            "evaluation_number": updated["evaluation_count"],
            "plan_version": updated.get("plan_version", 1),
            "action": last_action.get("name") if isinstance(last_action, dict) else None,
        }
        updated["evaluation_history"].append(evaluation_record)
        updated["plan"] = mark_completed_steps(
            updated.get("plan", {}),
            evaluation.get("completed_step_ids", []),
        )
        append_session_event(updated["session_path"], "evaluate", evaluation_record)

        verdict = evaluation["verdict"]
        if verdict == "blocked":
            updated["status"] = "blocked"
            updated["plan_status"] = "blocked"
            updated["final_message"] = f"Agent session blocked by evaluator: {evaluation['feedback']}"
            return updated

        if verdict == "replan":
            if int(updated.get("replan_count", 0)) >= int(updated.get("max_replans", 0)):
                raise RuntimeError("Replan limit reached before the plan completed.")
            revised = create_revised_plan(updated, evaluation["feedback"])
            merged = merge_revised_plan(updated, revised)
            updated["plan"] = merged
            updated["plan_version"] = int(revised["version"])
            updated["plan_status"] = "active"
            updated["replan_count"] = int(updated.get("replan_count", 0)) + 1
            updated["plan_history"].append(copy.deepcopy(merged))
            append_session_event(
                updated["session_path"],
                "replan",
                {
                    "plan": merged,
                    "replan_count": updated["replan_count"],
                    "feedback": evaluation["feedback"],
                },
            )

        plan = updated.get("plan", {})
        plan_complete = isinstance(plan, dict) and plan.get("status") == "complete"
        if verdict == "complete" and not plan_complete:
            raise ValueError("Evaluator cannot complete the session while planned steps remain.")
        if plan_complete:
            updated["status"] = "complete"
            updated["plan_status"] = "complete"
            updated["current_plan_step_id"] = None
            updated["react_final_message"] = evaluation["summary"]
            updated["final_message"] = build_final_message(updated)
            append_session_event(updated["session_path"], "complete", {"final_message": updated["final_message"]})
            return updated

        current = current_plan_step(updated)
        updated["current_plan_step_id"] = current.get("id") if current else None
        updated["plan_status"] = "active"
        return updated
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        updated["errors"].append(error)
        updated["status"] = "blocked"
        updated["plan_status"] = "blocked"
        updated["final_message"] = f"Agent session blocked during evaluation: {error}"
        append_session_event(updated["session_path"], "error", {"phase": "evaluate", "error": error})
        return updated


def approval_node(state: AgentState) -> AgentState:
    updated = ensure_state_lists(dict(state))
    approval = updated.get("approval_request")
    if not isinstance(approval, dict):
        raise ValueError("Approval node requires an approval_request in graph state.")

    decision = normalize_approval_decision(interrupt(approval))
    approval = dict(approval)
    approval["status"] = decision
    updated["approval_request"] = approval
    write_approval_request(updated["approval_path"], approval)

    if decision == "rejected":
        action_name = approval.get("action", {}).get("name")
        updated["status"] = "complete"
        updated["approval_decision"] = "rejected"
        updated["current_action"] = None
        updated["final_message"] = f"Agent session approval rejected for {action_name}."
        append_session_event(
            updated["session_path"],
            "approval_rejected",
            {"approval_path": updated["approval_path"], "thread_id": updated.get("thread_id")},
        )
        return updated

    updated["status"] = "running"
    updated["approval_decision"] = "approved"
    append_session_event(
        updated["session_path"],
        "approval_approved",
        {"approval_path": updated["approval_path"], "thread_id": updated.get("thread_id")},
    )
    return updated


def normalize_approval_decision(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("decision")
    if value is True or (isinstance(value, str) and value in {"approve", "approved"}):
        return "approved"
    if value is False or (isinstance(value, str) and value in {"reject", "rejected"}):
        return "rejected"
    raise ValueError("Approval resume value must be approve/approved or reject/rejected.")


def route_after_decide(state: AgentState) -> str:
    if state.get("status") == "waiting_approval" and state.get("current_action"):
        return "approval"
    if state.get("status") == "running" and state.get("current_action"):
        return "execute"
    return "end"


def route_after_approval(state: AgentState) -> str:
    if state.get("status") == "running" and state.get("current_action"):
        return "execute"
    return "end"


def route_after_execute(state: AgentState) -> str:
    if state.get("status") == "running":
        return "evaluate"
    return "end"


def route_after_evaluate(state: AgentState) -> str:
    if state.get("status") == "running":
        return "decide"
    return "end"


def choose_next_action(state: AgentState) -> AgentAction | None:
    if state.get("planner") == "llm":
        return choose_llm_action(state)
    if state.get("planner") == "react":
        return choose_react_action(state)
    if state.get("planner") == PLAN_REACT:
        return choose_react_action(state)
    return choose_rule_action(state)


def build_approval_request(state: AgentState, action: AgentAction) -> dict[str, Any]:
    return {
        "session_id": state.get("session_id"),
        "thread_id": state.get("thread_id") or state.get("session_id"),
        "status": "pending",
        "task": state.get("task"),
        "step": state.get("step", 0),
        "action": action,
        "reason": action.get("rationale", ""),
        "resume_commands": {
            "approve": f"quant-agent agent-resume --session {state.get('session_id')} --approve",
            "reject": f"quant-agent agent-resume --session {state.get('session_id')} --reject",
        },
    }


def choose_rule_action(state: AgentState) -> AgentAction | None:
    successful_tools = successful_tool_names(state)
    task = str(state.get("task", "")).lower()

    if "read_project_state" not in successful_tools:
        return make_action("read_project_state", "Load centralized implementation state before deciding next steps.")
    if "read_capabilities" not in successful_tools:
        return make_action("read_capabilities", "Load the command/capability map so actions stay within known tools.")

    wants_metrics = any(token in task for token in ["metric", "cagr", "drawdown", "performance"])
    wants_artifacts = any(token in task for token in ["artifact", "position", "trade", "ranking", "contract", "inspect"])
    wants_promotion = any(token in task for token in ["promotion", "promote", "candidate", "review"])
    wants_status = any(token in task for token in ["status", "state", "next", "summary", "where", "current"])
    wants_memory = any(token in task for token in ["memory", "search code", "source context", "code context", "relevant source"])

    lifecycle_action = choose_patch_lifecycle_action(state, successful_tools)
    if lifecycle_action:
        return lifecycle_action
    if wants_patch_lifecycle(task):
        return None
    if wants_memory and "query_code_memory" not in successful_tools and latest_memory_index(Path(state["root"])):
        return make_action("query_code_memory", "Retrieve relevant source chunks from local code memory.")
    if wants_metrics and "metrics_latest" not in successful_tools:
        return make_action("metrics_latest", "The task asks about performance or metrics for the latest run.")
    if wants_artifacts and "inspect_artifacts_latest" not in successful_tools:
        return make_action("inspect_artifacts_latest", "The task asks about artifacts or position-level contract status.")
    if wants_promotion and "promote_latest" not in successful_tools:
        return make_action("promote_latest", "The task asks about promotion or review readiness.")
    if "inspect_latest_run" not in successful_tools:
        return make_action("inspect_latest_run", "Summarize the latest run before producing a final answer.")
    if wants_status and "metrics_latest" not in successful_tools:
        return make_action("metrics_latest", "Include latest core metrics in the status summary.")
    return None


def build_final_message(state: AgentState) -> str:
    summary = ["Agent session completed."]
    action_names = [record.get("action", {}).get("name") for record in state.get("actions", [])]
    if action_names:
        summary.append("Actions run: " + ", ".join(str(name) for name in action_names if name))

    react_final = state.get("react_final_message")
    if react_final:
        summary.append("Model conclusion: " + react_final)

    latest_run = latest_observation(state, "inspect_latest_run")
    if latest_run:
        summary.append(f"Latest run: {latest_run.get('run_id')}")
        metrics = latest_run.get("metrics", {})
        if isinstance(metrics, dict):
            summary.append(format_metric_line(metrics))
        if latest_run.get("artifact_contract_recommendation"):
            summary.append(f"Artifact contract: {latest_run['artifact_contract_recommendation']}")
        if latest_run.get("promotion_recommendation"):
            summary.append(f"Promotion: {latest_run['promotion_recommendation']}")

    artifact = latest_observation(state, "inspect_artifacts_latest")
    if artifact:
        summary.append(f"Artifact inspection: {artifact.get('recommendation')}")

    promotion = latest_observation(state, "promote_latest")
    if promotion:
        summary.append(f"Promotion audit: {promotion.get('recommendation')}")
        warnings = promotion.get("warnings", [])
        if warnings:
            summary.append("Promotion warnings: " + ", ".join(str(item) for item in warnings))

    memory = latest_observation(state, "query_code_memory")
    if memory:
        summary.append(f"Code memory results: {memory.get('result_count', 0)}")
        retrieved_chunks = memory.get("results", [])
        if isinstance(retrieved_chunks, list) and retrieved_chunks:
            top_chunk = retrieved_chunks[0]
            if isinstance(top_chunk, dict):
                summary.append(
                    f"Top source chunk: {top_chunk.get('path')}:{top_chunk.get('line_start')}-{top_chunk.get('line_end')}"
                )

    proposal = latest_observation(state, "inspect_proposal")
    if proposal:
        summary.append(
            f"Proposal: {proposal.get('issue')} apply_supported={proposal.get('apply_supported')} "
            f"quality_gate={proposal.get('quality_gate')}"
        )

    dry_run = latest_observation(state, "apply_fix_dry_run")
    if dry_run:
        summary.append(f"Patch dry-run: would_change={str(dry_run.get('would_change')).lower()}")

    applied = latest_observation(state, "apply_fix_yes")
    if applied:
        summary.append(f"Patch applied: {str(applied.get('applied')).lower()}")

    fresh = latest_observation(state, "run_fresh")
    if fresh:
        if fresh.get("verification_status") == "failed":
            summary.append(f"Verification failed: {fresh.get('error_type')} - {fresh.get('error')}")
        else:
            summary.append(f"Verification run: {fresh.get('run_id')}")

    revision = latest_observation(state, "suggest_revision")
    if revision:
        summary.append(
            f"Revision proposal: {revision.get('proposal_dir')} quality_gate={revision.get('quality_gate')}"
        )

    synthesis = latest_observation(state, "synthesize_revision")
    if synthesis:
        if synthesis.get("synthesis_status") == "synthesized":
            summary.append(
                f"Revised patch proposal: {synthesis.get('proposal_dir')} "
                f"apply_supported={synthesis.get('apply_supported')}"
            )
        else:
            summary.append(f"Revision synthesis: {synthesis.get('synthesis_status')} - {synthesis.get('synthesis_reason')}")

    project_state = latest_observation(state, "read_project_state")
    if project_state:
        next_steps = project_state.get("next_recommended_steps", [])
        if next_steps and isinstance(next_steps[0], dict):
            summary.append(f"Top recorded next step: {next_steps[0].get('name')}")

    return "\n".join(line for line in summary if line)


def latest_observation(state: AgentState, action_name: str) -> dict[str, Any] | None:
    for observation in reversed(state.get("observations", [])):
        if observation.get("action") == action_name:
            return observation
    return None


def format_metric_line(metrics: dict[str, Any]) -> str:
    fields = []
    if "cagr" in metrics:
        fields.append(f"CAGR={float(metrics['cagr']):.1%}")
    if "max_drawdown" in metrics:
        fields.append(f"MaxDD={float(metrics['max_drawdown']):.1%}")
    if "calmar" in metrics:
        fields.append(f"Calmar={float(metrics['calmar']):.2f}")
    return "Metrics: " + ", ".join(fields) if fields else ""


def ensure_state_lists(state: dict[str, Any]) -> AgentState:
    state.setdefault("observations", [])
    state.setdefault("actions", [])
    state.setdefault("errors", [])
    state.setdefault("react_trace", [])
    state.setdefault("plan_history", [])
    state.setdefault("evaluation_history", [])
    state.setdefault("step", 0)
    state.setdefault("status", "running")
    return state  # type: ignore[return-value]
