from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.types import Command

from quant_agent.agent.graph import build_agent_graph
from quant_agent.agent.persistence import (
    CheckpointConfig,
    create_checkpointer,
    resolve_checkpoint_config,
)
from quant_agent.agent.session import (
    append_session_event,
    approval_request_path,
    create_session_id,
    load_session_state,
    save_session_state,
    session_checkpoint_path,
    session_log_path,
    session_state_path,
)
from quant_agent.agent.state import AgentState


def run_agent_session(
    task: str,
    max_steps: int = 6,
    planner: str = "rule",
    provider: str = "auto",
    model: str | None = None,
    root: Path | None = None,
    session_dir: Path | None = None,
    max_replans: int = 2,
    checkpoint_backend: str | None = None,
    checkpoint_postgres_uri: str | None = None,
) -> dict[str, Any]:
    if planner not in {"rule", "llm", "react", "plan-react"}:
        raise ValueError("planner must be 'rule', 'llm', 'react', or 'plan-react'.")
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1.")
    if planner == "plan-react" and max_steps < 3:
        raise ValueError("plan-react requires max_steps of at least 3 for preflight plus planned work.")
    if max_replans < 0:
        raise ValueError("max_replans must be zero or greater.")
    resolved_root = (root or Path.cwd()).resolve()
    resolved_session_dir = (session_dir or (resolved_root / "state" / "sessions")).resolve()
    resolved_session_dir.mkdir(parents=True, exist_ok=True)
    session_id = create_session_id()
    thread_id = session_id
    log_path = session_log_path(session_id, resolved_session_dir)
    state_path = session_state_path(session_id, resolved_session_dir)
    approval_path = approval_request_path(session_id, resolved_session_dir)
    persistence = resolve_checkpoint_config(
        root=resolved_root,
        session_dir=resolved_session_dir,
        backend=checkpoint_backend,
        postgres_uri=checkpoint_postgres_uri,
    )
    initial_state: AgentState = {
        "task": task,
        "planner": planner,
        "llm_provider": provider,
        "llm_model": model,
        "root": str(resolved_root),
        "session_id": session_id,
        "thread_id": thread_id,
        "session_path": str(log_path),
        "state_path": str(state_path),
        "approval_path": str(approval_path),
        "checkpoint_backend": persistence.backend,
        "checkpoint_path": persistence.checkpoint_path,
        "checkpoint_reference": persistence.safe_reference,
        "max_steps": max_steps,
        "step": 0,
        "status": "running",
        "current_action": None,
        "approval_request": None,
        "approval_decision": None,
        "observations": [],
        "actions": [],
        "react_trace": [],
        "react_final_message": "",
        "plan": {},
        "plan_version": 0,
        "plan_status": "not_started",
        "current_plan_step_id": None,
        "plan_history": [],
        "evaluation_count": 0,
        "max_evaluations": max_steps,
        "evaluation_history": [],
        "replan_count": 0,
        "max_replans": max_replans,
        "errors": [],
        "final_message": "",
    }
    append_session_event(
        log_path,
        "start",
        {
            "task": task,
            "planner": planner,
            "provider": provider,
            "model": model,
            "max_steps": max_steps,
            "max_replans": max_replans,
            "thread_id": thread_id,
            "checkpoint_backend": persistence.backend,
            "checkpoint_path": persistence.checkpoint_path,
            "checkpoint_reference": persistence.safe_reference,
        },
    )
    final_state, checkpoint_metadata = invoke_checkpointed_graph(
        initial_state,
        thread_id=thread_id,
        persistence=persistence,
    )
    save_session_state(state_path, final_state)
    return session_result(final_state, checkpoint_metadata)


def resume_agent_session(
    session: str,
    approve: bool = False,
    reject: bool = False,
    root: Path | None = None,
    session_dir: Path | None = None,
    checkpoint_backend: str | None = None,
    checkpoint_postgres_uri: str | None = None,
) -> dict[str, Any]:
    if approve == reject:
        raise ValueError("Choose exactly one of approve=True or reject=True.")
    resolved_root = (root or Path.cwd()).resolve()
    resolved_session_dir = (session_dir or (resolved_root / "state" / "sessions")).resolve()
    context = resolve_session_context(session, resolved_session_dir)
    thread_id = context["thread_id"]
    stored_backend = context.get("checkpoint_backend")
    if checkpoint_backend and stored_backend and checkpoint_backend != stored_backend:
        raise ValueError(
            f"Session {session!r} uses checkpoint backend {stored_backend!r}, "
            f"not {checkpoint_backend!r}."
        )
    persistence = resolve_checkpoint_config(
        root=resolved_root,
        session_dir=resolved_session_dir,
        backend=checkpoint_backend or stored_backend,
        postgres_uri=checkpoint_postgres_uri,
        sqlite_path=context.get("checkpoint_path"),
    )
    config = checkpoint_config(thread_id)

    with create_checkpointer(persistence) as checkpointer:
        graph = build_agent_graph(checkpointer=checkpointer)
        snapshot = graph.get_state(config)
        state = dict(snapshot.values)
        if not state:
            raise ValueError(f"No checkpointed agent session found for thread {thread_id!r}.")
        if state.get("status") != "waiting_approval":
            raise ValueError(f"Session {session!r} is not waiting for approval.")
        if not snapshot_interrupts(snapshot):
            raise ValueError(f"Session {session!r} has no pending LangGraph interrupt.")

        decision = "approved" if approve else "rejected"
        graph.invoke(Command(resume={"decision": decision}), config=config)
        final_snapshot = graph.get_state(config)
        final_state = dict(final_snapshot.values)
        checkpoint_metadata = build_checkpoint_metadata(final_snapshot)

    state_path = Path(final_state.get("state_path") or session_state_path(thread_id, resolved_session_dir))
    save_session_state(state_path, final_state)
    return session_result(final_state, checkpoint_metadata)


def invoke_checkpointed_graph(
    graph_input: AgentState | Command,
    thread_id: str,
    persistence: CheckpointConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = checkpoint_config(thread_id)
    with create_checkpointer(persistence) as checkpointer:
        graph = build_agent_graph(checkpointer=checkpointer)
        graph.invoke(graph_input, config=config)
        snapshot = graph.get_state(config)
        return dict(snapshot.values), build_checkpoint_metadata(snapshot)


def checkpoint_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def build_checkpoint_metadata(snapshot: Any) -> dict[str, Any]:
    configurable = snapshot.config.get("configurable", {}) if snapshot.config else {}
    return {
        "checkpoint_id": configurable.get("checkpoint_id"),
        "interrupts": snapshot_interrupts(snapshot),
    }


def snapshot_interrupts(snapshot: Any) -> list[dict[str, Any]]:
    interrupts = []
    for task in snapshot.tasks:
        for item in task.interrupts:
            interrupts.append(
                {
                    "id": getattr(item, "id", None),
                    "value": getattr(item, "value", None),
                }
            )
    return interrupts


def resolve_session_context(session: str, session_dir: Path) -> dict[str, Any]:
    path = Path(session)
    state_path = path if path.name.endswith(".state.json") and path.exists() else session_state_path(session, session_dir)
    if state_path.exists():
        state = load_session_state(state_path.resolve())
        thread_id = str(state.get("thread_id") or state.get("session_id") or "")
        if not thread_id:
            raise ValueError(f"Session state has no thread id: {state_path}")
        backend = str(state.get("checkpoint_backend") or "sqlite")
        checkpoint_path = state.get("checkpoint_path")
        if backend == "sqlite" and not checkpoint_path:
            checkpoint_path = str(session_checkpoint_path(session_dir))
        return {
            "thread_id": thread_id,
            "checkpoint_backend": backend,
            "checkpoint_path": checkpoint_path,
        }
    return {
        "thread_id": session,
        "checkpoint_backend": None,
        "checkpoint_path": str(session_checkpoint_path(session_dir)),
    }


def session_result(
    final_state: dict[str, Any],
    checkpoint_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checkpoint_metadata = checkpoint_metadata or {}
    return {
        "status": final_state.get("status"),
        "step": final_state.get("step"),
        "session_id": final_state.get("session_id"),
        "thread_id": final_state.get("thread_id") or final_state.get("session_id"),
        "session_path": final_state.get("session_path"),
        "state_path": final_state.get("state_path"),
        "approval_path": final_state.get("approval_path"),
        "checkpoint_backend": final_state.get("checkpoint_backend") or "sqlite",
        "checkpoint_path": final_state.get("checkpoint_path"),
        "checkpoint_reference": final_state.get("checkpoint_reference"),
        "checkpoint_id": checkpoint_metadata.get("checkpoint_id"),
        "interrupts": checkpoint_metadata.get("interrupts", []),
        "actions": final_state.get("actions", []),
        "react_trace": final_state.get("react_trace", []),
        "plan": final_state.get("plan", {}),
        "plan_history": final_state.get("plan_history", []),
        "evaluation_history": final_state.get("evaluation_history", []),
        "replan_count": final_state.get("replan_count", 0),
        "errors": final_state.get("errors", []),
        "final_message": final_state.get("final_message", ""),
        "state": final_state,
    }


def resolve_session_state_path(session: str, session_dir: Path) -> Path:
    path = Path(session)
    if path.name.endswith(".state.json") and path.exists():
        return path.resolve()
    return session_state_path(session, session_dir)
