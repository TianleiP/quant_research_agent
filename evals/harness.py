from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager

from quant_agent.agent.runner import resume_agent_session, run_agent_session

from evals.contracts import ScenarioContract
from evals.graders import approval_violations, grade_common


@dataclass(frozen=True)
class WorkPaths:
    base: Path
    project: Path
    external_repo: Path
    runtime: Path


@dataclass
class PreparedScenario:
    paths: WorkPaths
    context: dict[str, Any]
    patch_context: ContextManager[Any] = nullcontext()


class ScenarioDriver:
    def prepare(self, contract: ScenarioContract, base: Path) -> PreparedScenario:
        raise NotImplementedError

    def custom_grade(
        self,
        contract: ScenarioContract,
        prepared: PreparedScenario,
        result: dict[str, Any],
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        return result.get("status") == "complete", infer_terminal_reason(result), [], {}


def run_scenario(
    contract: ScenarioContract,
    driver: ScenarioDriver,
    *,
    provider_override: str | None = None,
    keep_workdir: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    base = Path(tempfile.mkdtemp(prefix=f"quant_agent_eval_{contract.id}_"))
    try:
        prepared = driver.prepare(contract, base)
        before = snapshot_tree(prepared.paths.base, ignored_prefixes={"runtime/"})
        approval_records: list[dict[str, Any]] = []
        unexpected_approvals: list[str] = []
        rejected_selections: list[str] = []
        provider = provider_override or contract.provider
        if provider != "mock" and not contract.supports_real_provider:
            raise ValueError(f"Scenario {contract.id} does not support provider override {provider!r}.")
        with prepared.patch_context:
            result = run_agent_session(
                task=contract.task,
                max_steps=contract.max_steps,
                planner=contract.planner,
                provider=provider,
                root=prepared.paths.project,
                session_dir=prepared.paths.runtime / "sessions",
                max_replans=contract.max_replans,
            )
            approval_index = 0
            while result.get("status") == "waiting_approval":
                prepared.context.setdefault("approval_interrupts", []).append(result.get("interrupts", []))
                action = result.get("state", {}).get("current_action", {})
                action_name = action.get("name") if isinstance(action, dict) else None
                expected = contract.approvals[approval_index] if approval_index < len(contract.approvals) else None
                if expected is None or expected.action != action_name:
                    unexpected_approvals.append(str(action_name))
                    decision = "reject"
                else:
                    decision = expected.decision
                    approval_index += 1
                approval_records.append({"action": action_name, "decision": decision})
                if decision == "reject" and isinstance(action_name, str):
                    rejected_selections.append(action_name)
                result = resume_agent_session(
                    session=str(result["session_id"]),
                    approve=decision == "approve",
                    reject=decision == "reject",
                    root=prepared.paths.project,
                    session_dir=prepared.paths.runtime / "sessions",
                )
        if approval_index != len(contract.approvals):
            unexpected_approvals.append(
                "missing expected approvals: "
                + ", ".join(item.action for item in contract.approvals[approval_index:])
            )

        action_records = result.get("actions", [])
        executed_tools = [
            str(record.get("action", {}).get("name"))
            for record in action_records
            if isinstance(record.get("action"), dict)
        ]
        selected_tools = [*executed_tools, *rejected_selections]
        task_success, terminal_reason, custom_checks, evidence = driver.custom_grade(
            contract, prepared, result
        )
        after = snapshot_tree(prepared.paths.base, ignored_prefixes={"runtime/"})
        mutations = changed_paths(before, after)
        common_checks = grade_common(
            contract,
            graph_status=str(result.get("status")),
            terminal_reason=terminal_reason,
            task_success=task_success,
            selected_tools=selected_tools,
            executed_tools=executed_tools,
            action_records=action_records,
            approval_records=approval_records,
            mutations=mutations,
            steps=int(result.get("step") or 0),
        )
        common_checks.append(
            {
                "name": "approval_script_complete",
                "pass": not unexpected_approvals,
                "expected": [],
                "actual": unexpected_approvals,
            }
        )
        checks = [*common_checks, *custom_checks]
        unauthorized_violations = approval_violations(action_records, approval_records)
        return {
            "scenario_id": contract.id,
            "title": contract.title,
            "pass": all(item.get("pass") is True for item in checks),
            "graph_status": result.get("status"),
            "terminal_reason": terminal_reason,
            "task_success": task_success,
            "selected_tools": selected_tools,
            "executed_tools": executed_tools,
            "approval_records": approval_records,
            "unauthorized_action_violations": unauthorized_violations,
            "steps": result.get("step"),
            "evaluation_count": len(result.get("evaluation_history", [])),
            "replan_count": result.get("replan_count", 0),
            "mutations": mutations,
            "checks": checks,
            "evidence": evidence,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "token_usage_available": False,
            "token_usage": None,
            "session_id": result.get("session_id"),
            "workdir": str(base) if keep_workdir else None,
        }
    except Exception as exc:
        return {
            "scenario_id": contract.id,
            "title": contract.title,
            "pass": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "workdir": str(base) if keep_workdir else None,
        }
    finally:
        if not keep_workdir:
            shutil.rmtree(base, ignore_errors=True)


def make_work_paths(base: Path) -> WorkPaths:
    paths = WorkPaths(
        base=base,
        project=base / "project",
        external_repo=base / "external_repo",
        runtime=base / "runtime",
    )
    for path in [paths.project, paths.external_repo, paths.runtime]:
        path.mkdir(parents=True, exist_ok=True)
    return paths


def snapshot_tree(root: Path, *, ignored_prefixes: set[str] | None = None) -> dict[str, str]:
    ignored_prefixes = ignored_prefixes or set()
    snapshot: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if any(relative.startswith(prefix) for prefix in ignored_prefixes):
            continue
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def infer_terminal_reason(result: dict[str, Any]) -> str:
    state = result.get("state", {})
    if state.get("approval_decision") == "rejected":
        return "approval_rejected"
    errors = "\n".join(str(item) for item in result.get("errors", []))
    if "revision loop guard" in errors.lower():
        return "revision_loop_guard"
    if result.get("status") == "blocked" and "max_steps" in str(result.get("final_message")):
        return "step_budget_exhausted"
    if result.get("status") == "complete":
        return "completed"
    return str(result.get("status") or "unknown")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
