from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RoutePlan:
    status: str
    intent: str
    command: list[str]
    summary: str
    safety: str
    execution_kind: str
    confidence: float
    missing_args: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def plan_command_route(
    task: str,
    *,
    run: str = "latest",
    provider: str = "auto",
    planner: str = "llm",
    max_steps: int = 6,
) -> RoutePlan:
    text = " ".join(task.split())
    lowered = text.lower()
    date = extract_date(text)

    if not text:
        return needs_input("unknown", "A natural-language request is required.", ["task"])

    if wants_memory_build(lowered):
        return needs_input("memory_build", "Code memory build requests need a repo path.", ["repo"])

    if wants_memory_query(lowered):
        return agent_session_plan(
            task=text,
            intent="query_code_memory",
            provider=provider,
            planner="rule",
            max_steps=max_steps,
            safety="safe_read",
            summary="Use agent-session to query the latest local code memory index.",
            confidence=0.82,
        )

    if wants_patch_lifecycle(lowered):
        return agent_session_plan(
            task=text,
            intent="patch_run_inspect_lifecycle",
            provider=provider,
            planner=planner,
            max_steps=max(max_steps, 10),
            safety="approval_gated",
            summary="Run the proposal patch lifecycle through agent-session with approval gates.",
            confidence=0.88,
        )

    if wants_risky_agent_action(lowered):
        return agent_session_plan(
            task=text,
            intent="approval_gated_agent_action",
            provider=provider,
            planner=planner,
            max_steps=max_steps,
            safety="approval_gated",
            summary="Route through agent-session so trace/fix/run actions keep approval gates.",
            confidence=0.86,
        )

    if wants_replay(lowered):
        if not date:
            return needs_input("replay", "Replay requests need a date in YYYY-MM-DD format.", ["date"])
        return command_plan(
            intent="replay",
            command=["quant-agent", "replay", "--run", run, "--date", date],
            summary=f"Replay the strategy decision for {date}.",
            safety="safe_read",
            execution_kind="direct",
            confidence=0.94,
        )

    if wants_metrics(lowered):
        return command_plan(
            intent="metrics",
            command=["quant-agent", "metrics", "--run", run],
            summary="Show latest core performance metrics.",
            safety="safe_read",
            execution_kind="direct",
            confidence=0.93,
        )

    if wants_artifact_inspection(lowered):
        return command_plan(
            intent="inspect_artifacts",
            command=["quant-agent", "inspect-artifacts", "--run", run],
            summary="Inspect artifact contract status for the selected run.",
            safety="safe_local_write",
            execution_kind="direct",
            confidence=0.9,
            notes=["Writes/refreshes artifact_contract reports under the run audit folder."],
        )

    if wants_promotion_closure(lowered):
        command = ["quant-agent", "close-promotion", "--run", run]
        if "diagnose" in lowered or "diagnosis" in lowered:
            command.extend(["--diagnose", "--provider", provider])
        return command_plan(
            intent="promotion_closure",
            command=command,
            summary="Regenerate promotion evidence and classify remaining promotion warnings.",
            safety="safe_local_write",
            execution_kind="direct",
            confidence=0.9,
            notes=["Writes/refreshes promotion audit and promotion_closure reports under the run audit folder."],
        )

    if wants_promotion(lowered):
        return command_plan(
            intent="promotion",
            command=["quant-agent", "promote", "--run", run],
            summary="Generate or refresh the promotion audit for the selected run.",
            safety="safe_local_write",
            execution_kind="direct",
            confidence=0.88,
            notes=["Writes/refreshes promotion audit files under the run audit folder."],
        )

    audit_name = audit_intent(lowered)
    if audit_name:
        return command_plan(
            intent=f"audit_{audit_name}",
            command=["quant-agent", "audit", audit_name, "--run", run],
            summary=f"Run the {audit_name} audit for the selected run.",
            safety="safe_local_write",
            execution_kind="direct",
            confidence=0.87,
            notes=["Writes/refreshes audit files under the run audit folder."],
        )

    if wants_diagnosis(lowered):
        return command_plan(
            intent="diagnose",
            command=["quant-agent", "diagnose", "--run", run, "--provider", provider],
            summary="Generate an LLM diagnosis for the selected run.",
            safety="safe_local_write",
            execution_kind="direct",
            confidence=0.82,
            notes=["May call an LLM provider and writes diagnosis artifacts unless the diagnose command is changed manually."],
        )

    if wants_discovery(lowered):
        return agent_session_plan(
            task=text,
            intent="repo_discovery_or_explanation",
            provider=provider,
            planner=planner,
            max_steps=max_steps,
            safety="safe_local_write",
            summary="Route through agent-session because repo/file arguments may need to be inferred.",
            confidence=0.72,
        )

    if wants_state_update(lowered):
        return command_plan(
            intent="update_state",
            command=["quant-agent", "update-state"],
            summary="Refresh machine-readable project state from current artifacts.",
            safety="safe_local_write",
            execution_kind="direct",
            confidence=0.86,
            notes=["Writes state/project_state.json."],
        )

    if wants_status_or_next(lowered):
        return agent_session_plan(
            task=text,
            intent="project_status",
            provider=provider,
            planner="rule",
            max_steps=max_steps,
            safety="safe_local_write",
            summary="Use agent-session to summarize project state and latest run context.",
            confidence=0.84,
        )

    return agent_session_plan(
        task=text,
        intent="general_agent_session",
        provider=provider,
        planner=planner,
        max_steps=max_steps,
        safety="approval_gated",
        summary="No direct command route was specific enough; use agent-session with existing action registry and approval gates.",
        confidence=0.55,
        notes=["Review the proposed route before executing if the request could modify code, run backtests, or apply fixes."],
    )


def render_route_plan(plan: RoutePlan) -> str:
    lines = [
        "Route plan:",
        f"- Status: {plan.status}",
        f"- Intent: {plan.intent}",
        f"- Safety: {plan.safety}",
        f"- Execution: {plan.execution_kind}",
        f"- Confidence: {plan.confidence:.2f}",
        f"- Summary: {plan.summary}",
    ]
    if plan.missing_args:
        lines.append("- Missing arguments: " + ", ".join(plan.missing_args))
    if plan.command:
        lines.extend(["", "Command:", format_command(plan.command)])
    if plan.notes:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {note}" for note in plan.notes)
    return "\n".join(lines)


def extract_date(task: str) -> str | None:
    match = DATE_PATTERN.search(task)
    return match.group(0) if match else None


def command_plan(
    *,
    intent: str,
    command: list[str],
    summary: str,
    safety: str,
    execution_kind: str,
    confidence: float,
    notes: list[str] | None = None,
) -> RoutePlan:
    return RoutePlan(
        status="planned",
        intent=intent,
        command=command,
        summary=summary,
        safety=safety,
        execution_kind=execution_kind,
        confidence=confidence,
        notes=notes or [],
    )


def agent_session_plan(
    *,
    task: str,
    intent: str,
    provider: str,
    planner: str,
    max_steps: int,
    safety: str,
    summary: str,
    confidence: float,
    notes: list[str] | None = None,
) -> RoutePlan:
    return command_plan(
        intent=intent,
        command=[
            "quant-agent",
            "agent-session",
            "--task",
            task,
            "--planner",
            planner,
            "--provider",
            provider,
            "--max-steps",
            str(max_steps),
        ],
        summary=summary,
        safety=safety,
        execution_kind="agent_session",
        confidence=confidence,
        notes=notes,
    )


def needs_input(intent: str, summary: str, missing_args: list[str]) -> RoutePlan:
    return RoutePlan(
        status="needs_input",
        intent=intent,
        command=[],
        summary=summary,
        safety="none",
        execution_kind="none",
        confidence=0.0,
        missing_args=missing_args,
    )


def wants_risky_agent_action(text: str) -> bool:
    if "apply" in text and any(token in text for token in ["fix", "proposal", "patch"]):
        return True
    if "run" in text and any(token in text for token in ["backtest", "external", "fresh"]):
        return True
    if "trace" in text and any(token in text for token in ["source", "code", "file"]):
        return True
    if "suggest" in text and "fix" in text:
        return True
    risky_tokens = [
        "apply fix",
        "apply patch",
        "modify",
        "edit",
        "write code",
        "fix code",
        "patch",
        "suggest fix",
        "trace source",
        "trace code",
        "fresh run",
        "rerun",
        "re-run",
        "run backtest",
        "run the backtest",
        "run external",
        "install",
    ]
    return any(token in text for token in risky_tokens)


def wants_replay(text: str) -> bool:
    tokens = [
        "replay",
        "why did",
        "what happened",
        "decision",
        "holding",
        "holdings",
        "trade",
        "trades",
        "rank",
        "ranking",
    ]
    return any(token in text for token in tokens)


def wants_metrics(text: str) -> bool:
    tokens = ["metric", "metrics", "performance", "cagr", "drawdown", "calmar", "sharpe", "return"]
    return any(token in text for token in tokens)


def wants_artifact_inspection(text: str) -> bool:
    if "artifact" not in text and "contract" not in text and "position-level" not in text:
        return False
    return any(token in text for token in ["inspect", "check", "status", "missing", "validate", "verify"])


def wants_promotion(text: str) -> bool:
    return any(token in text for token in ["promote", "promotion", "candidate", "review readiness"])


def wants_promotion_closure(text: str) -> bool:
    if "promotion" not in text and "promote" not in text:
        return False
    return any(token in text for token in ["close", "closure", "resolve", "remaining warning", "warnings"])


def audit_intent(text: str) -> str | None:
    if "lag" in text:
        return "lag"
    if "robust" in text:
        return "robustness"
    if "cost" in text or "slippage" in text:
        return "costs"
    if "validation" in text or "attribution" in text:
        return "validation"
    if "doc" in text and ("audit" in text or "consistency" in text):
        return "docs"
    return None


def wants_diagnosis(text: str) -> bool:
    return any(token in text for token in ["diagnose", "diagnosis", "llm diagnosis", "anomaly"])


def wants_discovery(text: str) -> bool:
    return any(token in text for token in ["discover", "scan repo", "scan repository", "explain entrypoint", "explain repo"])


def wants_state_update(text: str) -> bool:
    return "update state" in text or "refresh state" in text or "sync state" in text


def wants_memory_build(text: str) -> bool:
    memory_terms = ["code memory", "semantic memory", "vector index", "memory index"]
    return any(term in text for term in memory_terms) and any(token in text for token in ["build", "create", "index"])


def wants_memory_query(text: str) -> bool:
    memory_terms = ["code memory", "semantic memory", "memory index"]
    return any(term in text for term in memory_terms) and any(token in text for token in ["query", "search", "find"])


def wants_patch_lifecycle(text: str) -> bool:
    lifecycle_terms = [
        "patch-run-inspect",
        "patch run inspect",
        "proposal lifecycle",
        "fix lifecycle",
        "complete latest proposal",
        "complete fix proposal",
        "apply and verify",
        "dry run and apply",
        "run verification",
    ]
    if any(term in text for term in lifecycle_terms):
        return True
    return (
        "proposal" in text
        and "apply" in text
        and any(token in text for token in ["verify", "verification", "inspect", "run"])
    )


def wants_status_or_next(text: str) -> bool:
    tokens = ["status", "current state", "where are we", "next step", "what next", "summary"]
    return any(token in text for token in tokens)


def format_command(parts: list[str]) -> str:
    return " ".join(quote_part(part) for part in parts)


def quote_part(part: Any) -> str:
    text = str(part)
    if not text:
        return '""'
    if re.search(r"\s", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text
