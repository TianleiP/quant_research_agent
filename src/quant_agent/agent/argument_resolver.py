from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quant_agent.agent.state import AgentAction, AgentState
from quant_agent.memory import find_latest_code_memory_index
from quant_agent.registry import latest_run_dir


SUPPORTED_SUGGEST_ISSUES = {"missing-position-exports", "stale-metadata", "generalized-fix"}
TRACE_ISSUE = "missing-position-exports"


def argument_context_for_prompt(root: Path, task: str | None = None) -> dict[str, Any]:
    resolved = resolve_agent_arguments(root, task=task)
    context = {
        "repo": resolved.get("repo"),
        "file": resolved.get("file"),
        "artifact_report": resolved.get("artifact_report"),
        "proposal": resolved.get("proposal"),
        "config": resolved.get("config"),
        "promoted_variant_id": resolved.get("promoted_variant_id"),
        "latest_run_id": resolved.get("latest_run_id"),
        "latest_proposal_issue": resolved.get("latest_proposal_issue"),
        "action_defaults": action_defaults(resolved, task=task),
        "sources": resolved.get("sources", {}),
    }
    return prune_empty(context)


def apply_resolved_args(
    action: AgentAction,
    root: Path,
    task: str | None = None,
) -> AgentAction:
    name = action.get("name")
    if not isinstance(name, str):
        return action

    resolved = resolve_agent_arguments(root, task=task)
    defaults = args_for_action(name, resolved, task=task, existing_args=action.get("args", {}))
    if not defaults:
        return action

    args = dict(action.get("args") or {})
    for key, value in defaults.items():
        if value is None:
            continue
        if key not in args or args[key] in {"", None}:
            args[key] = value

    updated: AgentAction = dict(action)
    updated["args"] = args
    return updated


def resolve_agent_arguments(root: Path, task: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    state = read_json_if_exists(root / "state" / "project_state.json")
    run_dir = safe_latest_run_dir(root)
    engine_metadata = read_json_if_exists(run_dir / "engine_metadata.json") if run_dir else {}
    artifact_report_path = run_dir / "audits" / "artifact_contract.json" if run_dir else None
    artifact_report = read_json_if_exists(artifact_report_path) if artifact_report_path else {}
    proposal_path, proposal = latest_proposal(root)
    external_repo = first_external_repo(state) or {}

    repo = first_text(
        engine_metadata.get("working_dir"),
        external_repo.get("path"),
        proposal.get("repo"),
    )
    memory_index = latest_memory_index(root)
    repo_memory_index = latest_memory_index(root, repo=repo)
    file_path = first_text(
        file_from_command(engine_metadata.get("command"), repo),
        normalize_repo_file(nested_get(engine_metadata, ["source_metadata", "source_script"]), repo),
        normalize_repo_file(external_repo.get("entrypoint"), external_repo.get("path")),
        normalize_repo_file(proposal.get("file"), repo),
    )
    promoted_variant = first_text(
        engine_metadata.get("expected_variant_id"),
        nested_get(engine_metadata, ["source_metadata", "promoted_live_variant_id"]),
        nested_get(state, ["latest_verified_run", "expected_variant_id"]),
        external_repo.get("expected_variant_id"),
    )
    config = first_text(
        nested_get(state, ["latest_verified_run", "config"]),
        path_arg(root, run_dir / "config.yaml") if run_dir and (run_dir / "config.yaml").exists() else None,
    )
    suggested_fix = artifact_report.get("suggested_fix") if isinstance(artifact_report.get("suggested_fix"), dict) else {}
    suggested_issue = suggested_fix.get("issue") if isinstance(suggested_fix, dict) else None

    sources = {
        "state": "state/project_state.json" if state else None,
        "latest_run": path_arg(root, run_dir) if run_dir else None,
        "engine_metadata": path_arg(root, run_dir / "engine_metadata.json") if run_dir else None,
        "artifact_report": path_arg(root, artifact_report_path) if artifact_report_path and artifact_report_path.exists() else None,
        "latest_proposal": path_arg(root, proposal_path) if proposal_path else None,
    }
    return prune_empty(
        {
            "repo": repo,
            "file": file_path,
            "artifact_report": path_arg(root, artifact_report_path)
            if artifact_report_path and artifact_report_path.exists()
            else None,
            "proposal": path_arg(root, proposal_path) if proposal_path else None,
            "memory_index": path_arg(root, memory_index) if memory_index else None,
            "repo_memory_index": path_arg(root, repo_memory_index) if repo_memory_index else None,
            "config": config,
            "promoted_variant_id": promoted_variant,
            "latest_run_id": run_dir.name if run_dir else None,
            "artifact_suggested_issue": suggested_issue,
            "latest_proposal_issue": proposal.get("issue"),
            "task_issue": issue_from_task(task),
            "sources": prune_empty(sources),
        }
    )


def action_defaults(resolved: dict[str, Any], task: str | None = None) -> dict[str, dict[str, Any]]:
    defaults = {}
    for name in [
        "trace_source",
        "suggest_fix",
        "inspect_proposal",
        "apply_fix_dry_run",
        "apply_fix_yes",
        "run_fresh",
        "query_code_memory",
        "suggest_revision",
        "synthesize_revision",
    ]:
        args = args_for_action(name, resolved, task=task)
        if args:
            defaults[name] = args
    return defaults


def args_for_action(
    name: str,
    resolved: dict[str, Any],
    task: str | None = None,
    existing_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = existing_args or {}
    if name == "trace_source":
        return prune_empty(
            {
                "repo": resolved.get("repo"),
                "file": resolved.get("file"),
                "issue": TRACE_ISSUE,
            }
        )
    if name == "suggest_fix":
        issue = first_text(existing.get("issue"), issue_from_task(task), resolved.get("artifact_suggested_issue"))
        if issue not in SUPPORTED_SUGGEST_ISSUES:
            issue = "missing-position-exports"
        payload = {
            "repo": resolved.get("repo"),
            "file": resolved.get("file"),
            "issue": issue,
            "memory_index": resolved.get("repo_memory_index"),
        }
        if issue == "missing-position-exports":
            payload["artifact_report"] = resolved.get("artifact_report")
        if issue == "stale-metadata":
            payload["promoted_variant_id"] = resolved.get("promoted_variant_id")
        if issue == "generalized-fix":
            payload["problem"] = task
        return prune_empty(payload)
    if name in {"inspect_proposal", "apply_fix_dry_run", "apply_fix_yes"}:
        return prune_empty({"proposal": resolved.get("proposal")})
    if name == "suggest_revision":
        return prune_empty({"proposal": resolved.get("proposal")})
    if name == "synthesize_revision":
        return prune_empty({"revision_proposal": resolved.get("proposal")})
    if name == "run_fresh":
        return prune_empty({"config": resolved.get("config")})
    if name == "query_code_memory":
        return prune_empty(
            {
                "index": resolved.get("memory_index"),
                "query": clean_memory_query(task),
                "top_k": 6,
            }
        )
    return {}


def safe_latest_run_dir(root: Path) -> Path | None:
    try:
        return latest_run_dir(root / "runs")
    except (FileNotFoundError, OSError):
        return None


def latest_proposal(root: Path) -> tuple[Path | None, dict[str, Any]]:
    proposal_root = root / ".fix_proposals"
    if not proposal_root.exists():
        return None, {}
    candidates = sorted(
        proposal_root.glob("**/proposal.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        payload = read_json_if_exists(path)
        if payload:
            return path, payload
    return None, {}


def latest_memory_index(root: Path, repo: str | None = None) -> Path | None:
    return find_latest_code_memory_index(root, repo=Path(repo) if repo else None)


def should_retrieve_code_context(state: AgentState, successful_tools: set[str]) -> bool:
    if "query_code_memory" in successful_tools:
        return False
    root = Path(str(state.get("root") or Path.cwd()))
    if not latest_memory_index(root):
        return False
    task = str(state.get("task") or "").lower()
    if any(term in task for term in ("apply", "run fresh", "rerun", "re-run")):
        return False
    return any(
        term in task
        for term in (
            "fix",
            "trace",
            "source",
            "export",
            "codebase",
            "repo",
            "repository",
            "where",
            "memory",
        )
    )


def file_from_command(command: Any, repo: str | None = None) -> str | None:
    if not isinstance(command, list):
        return None
    for item in command:
        if not isinstance(item, str):
            continue
        normalized = item.replace("\\", "/")
        if not normalized.lower().endswith(".py"):
            continue
        return normalize_repo_file(normalized, repo)
    return None


def normalize_repo_file(value: Any, repo: str | None = None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute() and repo:
        try:
            return path.resolve().relative_to(Path(repo).resolve()).as_posix()
        except ValueError:
            return None
    if path.is_absolute():
        return None
    return value.replace("\\", "/")


def first_external_repo(state: dict[str, Any]) -> dict[str, Any] | None:
    repos = state.get("external_repos")
    if not isinstance(repos, list):
        return None
    for repo in repos:
        if isinstance(repo, dict) and repo.get("path"):
            return repo
    return None


def issue_from_task(task: str | None) -> str | None:
    text = str(task or "").lower()
    if "stale" in text or "metadata" in text:
        return "stale-metadata"
    if "missing" in text or "position" in text or "trade" in text or "ranking" in text or "export" in text:
        return "missing-position-exports"
    if "generalized" in text or "candidate diff" in text or "llm fix" in text or "draft fix" in text:
        return "generalized-fix"
    return None


def clean_memory_query(task: str | None) -> str:
    text = str(task or "").strip()
    lowered = text.lower()
    prefixes = [
        "search code memory for",
        "query code memory for",
        "search memory for",
        "query memory for",
        "find source context for",
        "find relevant source for",
        "source context for",
        "relevant source for",
    ]
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip() or text
    return text


def path_arg(root: Path, path: Path | None) -> str | None:
    if not path:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def nested_get(payload: dict[str, Any], keys: list[str]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def prune_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", {}, [])}
