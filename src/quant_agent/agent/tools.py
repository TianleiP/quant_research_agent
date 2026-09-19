from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from quant_agent.agent.argument_resolver import apply_resolved_args
from quant_agent.agent.actions import validate_action_for_execution
from quant_agent.agent.state import AgentAction
from quant_agent.agent.tool_catalog import get_tool_spec
from quant_agent.audits.artifact_contract import (
    build_artifact_contract_inspection,
    run_artifact_contract_inspection,
)
from quant_agent.audits.promotion import build_promotion_audit, generate_promotion_report
from quant_agent.backtest.adapter import run_backtest
from quant_agent.config import load_config, with_run_before_ingest
from quant_agent.flags import basic_risk_flags
from quant_agent.fixes import (
    apply_fix_proposal,
    suggest_generalized_fix,
    suggest_missing_export_fix,
    suggest_stale_metadata_fix,
    suggest_verification_revision,
    synthesize_revised_patch,
)
from quant_agent.fixes.source_trace import trace_source_file
from quant_agent.memory import query_code_memory as run_code_memory_query
from quant_agent.metrics import load_metrics
from quant_agent.registry import latest_run_dir, save_run
from quant_agent.reports import write_short_report


ToolHandler = Callable[[Path, dict[str, Any]], dict[str, Any]]


def invoke_tool(
    name: str,
    args: dict[str, Any] | None,
    root: Path,
    approved: bool = False,
    rationale: str = "Direct tool invocation.",
) -> dict[str, Any]:
    spec = get_tool_spec(name)
    action: AgentAction = {
        "name": name,
        "args": args or {},
        "rationale": rationale,
        "requires_approval": spec.requires_approval,
    }
    return execute_agent_action(action, root=root, approved=approved)


def execute_agent_action(action: AgentAction, root: Path, approved: bool = False) -> dict[str, Any]:
    action = apply_resolved_args(action, root)
    validate_action_for_execution(action, approved=approved)
    name = action["name"]
    try:
        handler = TOOL_HANDLERS[name]
    except KeyError as exc:
        raise ValueError(f"No handler registered for agent action: {name}") from exc
    return handler(root, action.get("args", {}))


def read_project_state(root: Path) -> dict[str, Any]:
    payload = read_json_if_exists(root / "state" / "project_state.json")
    project = payload.get("project", {}) if isinstance(payload.get("project"), dict) else {}
    latest = payload.get("latest_verified_run", {}) if isinstance(payload.get("latest_verified_run"), dict) else {}
    return {
        "action": "read_project_state",
        "path": "state/project_state.json",
        "exists": bool(payload),
        "project": {
            "name": project.get("name"),
            "current_stage": project.get("current_stage"),
            "requires_python": project.get("requires_python"),
            "agent_runtime": project.get("agent_runtime"),
            "behavior_model": project.get("behavior_model"),
        },
        "latest_verified_run": {
            "run_id": latest.get("run_id"),
            "config": latest.get("config"),
            "artifact_contract_recommendation": nested_get(latest, ["position_level_artifacts", "artifact_contract_recommendation"]),
            "promotion_recommendation": nested_get(latest, ["promotion", "recommendation"]),
        },
        "known_gaps": payload.get("known_gaps", [])[:8],
        "next_recommended_steps": payload.get("next_recommended_steps", [])[:5],
    }


def read_capabilities(root: Path) -> dict[str, Any]:
    payload = read_json_if_exists(root / "state" / "capabilities.json")
    capabilities = payload.get("capabilities", []) if isinstance(payload.get("capabilities"), list) else []
    return {
        "action": "read_capabilities",
        "path": "state/capabilities.json",
        "exists": bool(payload),
        "capability_count": len(capabilities),
        "implemented": [item.get("name") for item in capabilities if item.get("status") == "implemented"],
        "partial": [item.get("name") for item in capabilities if item.get("status") == "partial"],
        "planned": [item.get("name") for item in capabilities if item.get("status") == "planned"],
    }


def inspect_latest_run(root: Path) -> dict[str, Any]:
    run_dir = latest_run_dir(root / "runs")
    metrics = load_metrics(run_dir)
    manifest = read_json_if_exists(run_dir / "manifest.json")
    engine_metadata = read_json_if_exists(run_dir / "engine_metadata.json")
    artifact_contract = read_json_if_exists(run_dir / "audits" / "artifact_contract.json")
    promotion = latest_promotion_payload(run_dir)
    return {
        "action": "inspect_latest_run",
        "run_id": run_dir.name,
        "run_dir": relative_to_root(run_dir, root),
        "strategy_name": manifest.get("strategy_name"),
        "engine": manifest.get("engine"),
        "expected_variant_id": engine_metadata.get("expected_variant_id"),
        "subprocess_ran": engine_metadata.get("subprocess_ran"),
        "exit_code": engine_metadata.get("exit_code"),
        "metrics": compact_metrics(metrics),
        "artifact_contract_recommendation": artifact_contract.get("recommendation"),
        "promotion_recommendation": promotion.get("recommendation"),
    }


def metrics_latest(root: Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    run_ref = str((args or {}).get("run") or "latest")
    run_dir = resolve_run_ref(root, run_ref)
    return {
        "action": "metrics_latest",
        "run_id": run_dir.name,
        "metrics": compact_metrics(load_metrics(run_dir)),
    }


def inspect_artifacts_latest(root: Path) -> dict[str, Any]:
    run_dir = latest_run_dir(root / "runs")
    markdown_path = run_artifact_contract_inspection(run_dir)
    inspection = build_artifact_contract_inspection(run_dir)
    return {
        "action": "inspect_artifacts_latest",
        "run_id": run_dir.name,
        "recommendation": inspection.get("recommendation"),
        "missing_position_level_artifacts": [
            item.get("name") for item in inspection.get("missing_position_level_artifacts", [])
        ],
        "checks": summarize_checks(inspection.get("checks", [])),
        "markdown_path": relative_to_root(markdown_path, root),
        "json_path": relative_to_root(run_dir / "audits" / "artifact_contract.json", root),
    }


def promote_latest(root: Path) -> dict[str, Any]:
    run_dir = latest_run_dir(root / "runs")
    result = generate_promotion_report(run_dir)
    promotion = result["promotion"]
    return {
        "action": "promote_latest",
        "run_id": run_dir.name,
        "recommendation": promotion.get("recommendation"),
        "failures": [item.get("code") for item in promotion.get("blocking_issues", [])],
        "warnings": [item.get("code") for item in promotion.get("warnings", [])],
        "json_path": relative_to_root(Path(result["json_path"]), root),
        "markdown_path": relative_to_root(Path(result["markdown_path"]), root),
    }


def inspect_proposal(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    proposal_path = resolve_path_arg(root, args, "proposal")
    payload = read_json_if_exists(proposal_path)
    if not payload:
        raise FileNotFoundError(f"Proposal does not exist or is not JSON: {proposal_path}")
    patch_path = payload.get("patch_path")
    patch_exists = False
    if isinstance(patch_path, str) and patch_path:
        patch_exists = resolve_maybe_relative_path(root, Path(patch_path), proposal_path.parent).exists()
    apply_result = proposal_path.parent / "apply_result.json"
    apply_payload = read_json_if_exists(apply_result)
    return {
        "action": "inspect_proposal",
        "proposal_path": relative_to_root(proposal_path, root),
        "issue": payload.get("issue"),
        "status": payload.get("status"),
        "apply_supported": payload.get("apply_supported"),
        "manual_review_required": payload.get("manual_review_required"),
        "risk_level": payload.get("risk_level"),
        "repo": payload.get("repo"),
        "file": payload.get("file"),
        "patch_path": patch_path,
        "patch_exists": patch_exists,
        "proposal_schema_version": payload.get("proposal_schema_version"),
        "quality_gate": nested_get(payload, ["quality_gate", "status"]),
        "memory_context_status": nested_get(payload, ["memory_context", "status"]),
        "memory_context_results": nested_get(payload, ["memory_context", "result_count"]),
        "verification_commands": nested_get(payload, ["verification", "commands"]) or [],
        "target_files": payload.get("target_files", []),
        "patch_plan_steps": len(payload.get("patch_plan", [])) if isinstance(payload.get("patch_plan"), list) else 0,
        "apply_result_exists": bool(apply_payload),
        "last_apply_result": {
            key: apply_payload.get(key)
            for key in ["dry_run", "would_change", "applied", "target_path", "result_path"]
            if key in apply_payload
        },
    }


def query_code_memory(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    index_path = resolve_path_arg(root, args, "index")
    query = require_arg(args, "query")
    top_k = int(args.get("top_k") or 6)
    result = run_code_memory_query(index=index_path, query=query, top_k=top_k, max_excerpt_chars=700)
    return {
        "action": "query_code_memory",
        "index": relative_to_root(index_path, root),
        "query": query,
        "retrieval": result.get("retrieval", {}),
        "result_count": result.get("result_count", 0),
        "results": [
            {
                "score": item.get("score"),
                "semantic_score": item.get("semantic_score"),
                "lexical_score": item.get("lexical_score"),
                "symbol_score": item.get("symbol_score"),
                "path": item.get("path"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "citation": item.get("citation"),
                "symbols": item.get("symbols", [])[:5],
                "excerpt": item.get("excerpt"),
            }
            for item in result.get("results", [])[:top_k]
        ],
    }


def trace_source(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    repo = resolve_path_arg(root, args, "repo")
    file_path = require_arg(args, "file")
    issue = str(args.get("issue") or "missing-position-exports")
    output_dir = optional_path_arg(root, args, "output")
    if output_dir is None:
        output_dir = default_agent_output_dir(root, ".source_traces", issue, file_path)
    result = trace_source_file(repo=repo, file_path=file_path, issue=issue, output_dir=output_dir)
    readiness = result["trace"]["patch_readiness"]
    return {
        "action": "trace_source",
        "repo": str(repo),
        "file": file_path,
        "issue": issue,
        "patch_readiness": readiness,
        "trace_dir": relative_to_root(Path(result["trace_dir"]), root),
        "json_path": relative_to_root(Path(result["json_path"]), root),
        "markdown_path": relative_to_root(Path(result["markdown_path"]), root),
    }


def suggest_fix(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    repo = resolve_path_arg(root, args, "repo")
    file_path = require_arg(args, "file")
    issue = require_arg(args, "issue")
    memory_index = optional_path_arg(root, args, "memory_index")
    memory_query = args.get("memory_query") if isinstance(args.get("memory_query"), str) else None
    output_dir = optional_path_arg(root, args, "output")
    if output_dir is None:
        output_dir = default_agent_output_dir(root, ".fix_proposals", issue, file_path)
    if issue == "stale-metadata":
        promoted = require_arg(args, "promoted_variant_id")
        result = suggest_stale_metadata_fix(
            repo=repo,
            file_path=file_path,
            promoted_variant_id=promoted,
            output_dir=output_dir,
            memory_index=memory_index,
            memory_query=memory_query,
        )
    elif issue == "missing-position-exports":
        artifact_report = optional_path_arg(root, args, "artifact_report")
        result = suggest_missing_export_fix(
            repo=repo,
            file_path=file_path,
            artifact_report=artifact_report,
            output_dir=output_dir,
            memory_index=memory_index,
            memory_query=memory_query,
        )
    elif issue == "generalized-fix":
        result = suggest_generalized_fix(
            repo=repo,
            file_path=file_path,
            problem=str(args.get("problem") or "Draft a generalized fix proposal for this source file."),
            output_dir=output_dir,
            memory_index=memory_index,
            memory_query=memory_query,
            provider=str(args.get("provider") or "auto"),
            model=args.get("model") if isinstance(args.get("model"), str) else None,
        )
    else:
        raise ValueError(f"Unsupported suggest_fix issue: {issue}")
    proposal = result["proposal"]
    return {
        "action": "suggest_fix",
        "issue": issue,
        "proposal_dir": relative_to_root(Path(result["proposal_dir"]), root),
        "metadata_path": relative_to_root(Path(result["metadata_path"]), root),
        "patch_path": relative_to_root(Path(result["patch_path"]), root) if result.get("patch_path") else None,
        "candidate_patch_path": relative_to_root(Path(result["candidate_patch_path"]), root)
        if result.get("candidate_patch_path")
        else None,
        "draft_json_path": relative_to_root(Path(result["draft_json_path"]), root)
        if result.get("draft_json_path")
        else None,
        "apply_supported": proposal.get("apply_supported"),
        "risk_level": proposal.get("risk_level"),
        "manual_review_required": proposal.get("manual_review_required"),
        "quality_gate": nested_get(proposal, ["quality_gate", "status"]),
        "memory_context_status": nested_get(proposal, ["memory_context", "status"]),
        "memory_context_results": nested_get(proposal, ["memory_context", "result_count"]),
    }


def suggest_revision(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    proposal_path = resolve_path_arg(root, args, "proposal")
    output_dir = optional_path_arg(root, args, "output")
    failure = args.get("failure") if isinstance(args.get("failure"), dict) else None
    artifact_inspection = (
        args.get("artifact_inspection") if isinstance(args.get("artifact_inspection"), dict) else None
    )
    result = suggest_verification_revision(
        root=root,
        proposal_path=proposal_path,
        failure=failure,
        artifact_inspection=artifact_inspection,
        output_dir=output_dir,
    )
    proposal = result["proposal"]
    return {
        "action": "suggest_revision",
        "issue": proposal.get("issue"),
        "proposal_dir": relative_to_root(Path(result["proposal_dir"]), root),
        "metadata_path": relative_to_root(Path(result["metadata_path"]), root),
        "failure_context_path": relative_to_root(Path(result["failure_context_path"]), root),
        "apply_supported": proposal.get("apply_supported"),
        "risk_level": proposal.get("risk_level"),
        "manual_review_required": proposal.get("manual_review_required"),
        "quality_gate": nested_get(proposal, ["quality_gate", "status"]),
        "memory_context_status": nested_get(proposal, ["memory_context", "status"]),
        "memory_context_results": nested_get(proposal, ["memory_context", "result_count"]),
        "revision_of": proposal.get("revision_of"),
    }


def synthesize_revision(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    revision_path = resolve_path_arg(root, args, "revision_proposal")
    output_dir = optional_path_arg(root, args, "output")
    result = synthesize_revised_patch(
        root=root,
        revision_proposal_path=revision_path,
        output_dir=output_dir,
    )
    metadata_path = result.get("metadata_path")
    patch_path = result.get("patch_path")
    proposal_dir = result.get("proposal_dir")
    return {
        "action": "synthesize_revision",
        "synthesis_status": result.get("synthesis_status"),
        "synthesized_issue": result.get("synthesized_issue"),
        "synthesis_reason": result.get("synthesis_reason"),
        "missing_artifacts": result.get("missing_artifacts", []),
        "revision_proposal_path": relative_to_root(Path(result["revision_proposal_path"]), root)
        if result.get("revision_proposal_path")
        else None,
        "proposal_dir": relative_to_root(Path(proposal_dir), root) if proposal_dir else None,
        "metadata_path": relative_to_root(Path(metadata_path), root) if metadata_path else None,
        "patch_path": relative_to_root(Path(patch_path), root) if patch_path else None,
        "apply_supported": result.get("apply_supported"),
        "risk_level": result.get("risk_level"),
        "manual_review_required": result.get("manual_review_required"),
        "quality_gate": result.get("quality_gate"),
    }


def apply_fix(root: Path, args: dict[str, Any], yes: bool) -> dict[str, Any]:
    proposal_path = resolve_path_arg(root, args, "proposal")
    result = apply_fix_proposal(proposal_path, yes=yes)
    return {
        "action": "apply_fix_yes" if yes else "apply_fix_dry_run",
        "proposal_path": relative_to_root(Path(result["proposal_path"]), root),
        "target_path": result.get("target_path"),
        "dry_run": result.get("dry_run"),
        "would_change": result.get("would_change"),
        "applied": result.get("applied"),
        "result_path": relative_to_root(Path(result["result_path"]), root),
    }


def run_fresh(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    config_path = resolve_path_arg(root, args, "config")
    # Verification failures are evaluator evidence, not orchestration crashes.
    try:
        config = with_run_before_ingest(load_config(config_path), True)
        result = run_backtest(config)
    except Exception as exc:
        return {
            "action": "run_fresh",
            "verification_status": "failed",
            "config": relative_to_root(config_path, root),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-8:],
        }
    flags = basic_risk_flags(result)
    run_dir, manifest = save_run(config, result, flags=flags, runs_root=root / "runs")
    write_short_report(run_dir, manifest.metrics, flags, manifest.artifacts)
    return {
        "action": "run_fresh",
        "verification_status": "completed",
        "run_id": manifest.run_id,
        "run_dir": relative_to_root(run_dir, root),
        "metrics": compact_metrics(manifest.metrics),
        "flags": flags,
    }


def compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "variant_id",
        "cagr",
        "max_drawdown",
        "calmar",
        "sharpe",
        "turnover_per_year",
        "exposure",
    ]
    return {key: metrics[key] for key in keys if key in metrics}


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        status = str(check.get("status", "")).lower()
        if status in summary:
            summary[status] += 1
    return summary


def latest_promotion_payload(run_dir: Path) -> dict[str, Any]:
    candidates = sorted((run_dir / "audits").glob("PROMOTION_AUDIT_*.json"))
    if candidates:
        return read_json_if_exists(candidates[-1])
    return build_promotion_audit(run_dir)


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def require_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Action argument {key!r} is required.")
    return value


def resolve_path_arg(root: Path, args: dict[str, Any], key: str) -> Path:
    return resolve_maybe_relative_path(root, Path(require_arg(args, key)), root)


def optional_path_arg(root: Path, args: dict[str, Any], key: str) -> Path | None:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        return None
    return resolve_maybe_relative_path(root, Path(value), root)


def resolve_maybe_relative_path(root: Path, path: Path, base: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    candidate = (base / path).resolve()
    if candidate.exists():
        return candidate
    return (root / path).resolve()


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def nested_get(payload: dict[str, Any], keys: list[str]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def default_agent_output_dir(root: Path, folder: str, issue: str, file_path: str) -> Path:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return root / folder / f"{timestamp}_{safe_component(issue)}_{safe_component(file_path)}"


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "item"


def resolve_run_ref(root: Path, run: str) -> Path:
    if run == "latest":
        return latest_run_dir(root / "runs")
    return root / "runs" / run


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "read_project_state": lambda root, args: read_project_state(root),
    "read_capabilities": lambda root, args: read_capabilities(root),
    "inspect_latest_run": lambda root, args: inspect_latest_run(root),
    "metrics_latest": metrics_latest,
    "inspect_artifacts_latest": lambda root, args: inspect_artifacts_latest(root),
    "promote_latest": lambda root, args: promote_latest(root),
    "inspect_proposal": inspect_proposal,
    "query_code_memory": query_code_memory,
    "suggest_revision": suggest_revision,
    "synthesize_revision": synthesize_revision,
    "trace_source": trace_source,
    "suggest_fix": suggest_fix,
    "apply_fix_dry_run": lambda root, args: apply_fix(root, args, yes=False),
    "apply_fix_yes": lambda root, args: apply_fix(root, args, yes=True),
    "run_fresh": run_fresh,
}
