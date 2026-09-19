from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.fixes.proposal_contract import attach_proposal_contract, collect_memory_context
from quant_agent.memory import find_latest_code_memory_index


ISSUE = "verification-failure-revision"


def suggest_verification_revision(
    root: Path,
    proposal_path: Path,
    failure: dict[str, Any] | None = None,
    artifact_inspection: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    proposal_path = proposal_path.resolve()
    original = read_json(proposal_path)
    repo = Path(str(original.get("repo") or root)).resolve()
    relative_file = str(original.get("file") or "")
    target_dir = output_dir or default_output_dir(root, original.get("issue"), relative_file)
    target_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = target_dir / "proposal.json"
    context_path = target_dir / "failure_context.md"
    readme_path = target_dir / "README.md"
    memory_index = find_latest_code_memory_index(root, repo=repo)
    memory_query = revision_memory_query(original, failure, artifact_inspection)
    memory_context = collect_memory_context(memory_index, memory_query)
    problem_statement = problem_from_failure(failure, artifact_inspection)

    proposal = {
        "issue": ISSUE,
        "status": "proposed",
        "risk_level": "high",
        "repo": str(repo),
        "file": relative_file,
        "patch_path": None,
        "metadata_path": str(metadata_path),
        "failure_context_path": str(context_path),
        "apply_supported": False,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "manual_review_required": True,
        "revision_of": {
            "proposal_path": str(proposal_path),
            "issue": original.get("issue"),
            "patch_path": original.get("patch_path"),
        },
        "verification_failure": compact_failure_payload(failure),
        "artifact_inspection": compact_artifact_payload(artifact_inspection),
        "summary": problem_statement,
        "apply_instruction": (
            "This is a revision plan, not an auto-applicable patch. Review failure_context.md, "
            "update or regenerate a targeted fix proposal, then rerun the patch-run-inspect lifecycle."
        ),
    }
    attach_proposal_contract(
        proposal,
        problem={
            "issue": ISSUE,
            "statement": problem_statement,
            "impact": (
                "The previous proposal cannot be considered complete until verification passes "
                "or the failing artifact contract is intentionally accepted."
            ),
        },
        target_files=[
            {
                "path": relative_file or "<unknown>",
                "role": "previous patch target",
                "change_type": "revision_required",
            }
        ],
        intended_behavior=[
            "Preserve the previous proposal's intended behavior.",
            "Address the verification failure without broad unrelated refactors.",
            "Produce a new targeted proposal before another apply attempt.",
        ],
        patch_plan=[
            {
                "step": 1,
                "target": "verification output",
                "description": "Inspect the captured failure and artifact contract result.",
            },
            {
                "step": 2,
                "target": relative_file or "<unknown>",
                "description": "Use source trace and code memory context to isolate the failing implementation point.",
            },
            {
                "step": 3,
                "target": ".fix_proposals",
                "description": "Generate a revised fix proposal with a concrete patch, then dry-run it.",
            },
        ],
        verification={
            "commands": [
                "quant-agent apply-fix --proposal <revised-proposal.json>",
                "quant-agent apply-fix --proposal <revised-proposal.json> --yes",
                "quant-agent run --config <strategy-config> --fresh",
                "quant-agent inspect-artifacts --run latest",
            ],
            "expected": [
                "The revised patch applies cleanly.",
                "The fresh verification run completes without execution errors.",
                "Artifact inspection returns satisfied or documents an intentional exception.",
            ],
        },
        evidence=[
            {
                "kind": "previous_proposal",
                "path": str(proposal_path),
                "issue": original.get("issue"),
            },
            {
                "kind": "verification_failure",
                "detail": compact_failure_payload(failure),
            },
            {
                "kind": "artifact_inspection",
                "detail": compact_artifact_payload(artifact_inspection),
            },
        ],
        memory_context=memory_context,
    )
    metadata_path.write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    context_path.write_text(render_failure_context(proposal, original), encoding="utf-8")
    readme_path.write_text(render_readme(proposal), encoding="utf-8")
    return {
        "proposal_dir": str(target_dir),
        "metadata_path": str(metadata_path),
        "failure_context_path": str(context_path),
        "readme_path": str(readme_path),
        "proposal": proposal,
    }


def problem_from_failure(
    failure: dict[str, Any] | None,
    artifact_inspection: dict[str, Any] | None,
) -> str:
    if failure:
        error = failure.get("error") or failure.get("message") or "unknown verification error"
        return f"Fresh verification failed after applying the proposal: {error}"
    if artifact_inspection:
        recommendation = artifact_inspection.get("recommendation") or "unknown"
        missing = artifact_inspection.get("missing_position_level_artifacts") or []
        if missing:
            return (
                "Fresh verification completed, but artifact inspection returned "
                f"{recommendation} with missing artifacts: {', '.join(map(str, missing))}."
            )
        return f"Fresh verification completed, but artifact inspection returned {recommendation}."
    return "Verification did not produce a satisfied result."


def revision_memory_query(
    original: dict[str, Any],
    failure: dict[str, Any] | None,
    artifact_inspection: dict[str, Any] | None,
) -> str:
    chunks = [
        str(original.get("file") or ""),
        str(original.get("issue") or ""),
        str(original.get("summary") or ""),
    ]
    if failure:
        chunks.extend([str(failure.get("error_type") or ""), str(failure.get("error") or "")])
    if artifact_inspection:
        chunks.extend(
            [
                str(artifact_inspection.get("recommendation") or ""),
                " ".join(str(item) for item in artifact_inspection.get("missing_position_level_artifacts") or []),
            ]
        )
    return " ".join(chunk for chunk in chunks if chunk).strip() or "verification failure revision"


def compact_failure_payload(failure: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(failure, dict):
        return {}
    return {
        key: failure.get(key)
        for key in ["action", "verification_status", "config", "error_type", "error", "traceback_tail"]
        if failure.get(key) not in (None, "", [], {})
    }


def compact_artifact_payload(artifact_inspection: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(artifact_inspection, dict):
        return {}
    return {
        key: artifact_inspection.get(key)
        for key in [
            "action",
            "run_id",
            "recommendation",
            "missing_position_level_artifacts",
            "checks",
            "json_path",
            "markdown_path",
        ]
        if artifact_inspection.get(key) not in (None, "", [], {})
    }


def render_failure_context(proposal: dict[str, Any], original: dict[str, Any]) -> str:
    lines = [
        "# Verification Failure Revision",
        "",
        f"Issue: `{proposal['issue']}`",
        f"Revision of: `{proposal['revision_of']['proposal_path']}`",
        f"Original issue: `{original.get('issue')}`",
        "",
        "## Problem",
        "",
        proposal["summary"],
        "",
        "## Failure",
        "",
        "```json",
        json.dumps(proposal.get("verification_failure", {}), indent=2, sort_keys=True),
        "```",
        "",
        "## Artifact Inspection",
        "",
        "```json",
        json.dumps(proposal.get("artifact_inspection", {}), indent=2, sort_keys=True),
        "```",
        "",
        "## Memory Context",
        "",
        f"Status: `{proposal.get('memory_context', {}).get('status')}`",
        f"Results: `{proposal.get('memory_context', {}).get('result_count', 0)}`",
        "",
    ]
    for item in proposal.get("memory_context", {}).get("results", [])[:5]:
        lines.append(f"- `{item.get('path')}:{item.get('line_start')}-{item.get('line_end')}` score={item.get('score')}")
    lines.append("")
    return "\n".join(lines)


def render_readme(proposal: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Fix Proposal: {proposal['issue']}",
            "",
            f"Repo: `{proposal['repo']}`",
            f"File: `{proposal['file']}`",
            f"Risk level: `{proposal['risk_level']}`",
            "",
            "## Summary",
            "",
            proposal["summary"],
            "",
            "## Review",
            "",
            "- This proposal is not auto-applicable.",
            "- Review `failure_context.md`.",
            "- Generate a revised targeted patch proposal before applying more code changes.",
            "",
        ]
    )


def default_output_dir(root: Path, original_issue: Any, relative_file: str) -> Path:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    parts = [timestamp, ISSUE, str(original_issue or "unknown"), relative_file or "unknown"]
    return root / ".fix_proposals" / safe_component("_".join(parts))


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "revision"
