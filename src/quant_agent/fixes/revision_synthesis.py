from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.fixes.missing_exports import ISSUE as MISSING_EXPORTS_ISSUE
from quant_agent.fixes.missing_exports import suggest_missing_export_fix
from quant_agent.memory import find_latest_code_memory_index


REVISION_ISSUE = "verification-failure-revision"
POSITION_ARTIFACTS = {"positions", "trades", "rankings"}


def synthesize_revised_patch(
    root: Path,
    revision_proposal_path: Path,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    revision_proposal_path = revision_proposal_path.resolve()
    revision = read_json(revision_proposal_path)
    if revision.get("issue") != REVISION_ISSUE:
        return unsupported_result(
            root=root,
            revision_proposal_path=revision_proposal_path,
            reason=f"Expected a {REVISION_ISSUE!r} proposal.",
        )

    supported, missing, reason = missing_export_revision_signal(revision)
    if not supported:
        return unsupported_result(
            root=root,
            revision_proposal_path=revision_proposal_path,
            reason=reason,
        )

    repo_text = revision.get("repo")
    file_text = revision.get("file")
    if not isinstance(repo_text, str) or not repo_text:
        return unsupported_result(
            root=root,
            revision_proposal_path=revision_proposal_path,
            reason="Revision proposal does not identify a source repo.",
        )
    if not isinstance(file_text, str) or not file_text:
        return unsupported_result(
            root=root,
            revision_proposal_path=revision_proposal_path,
            reason="Revision proposal does not identify a source file.",
        )

    repo = Path(repo_text).resolve()
    target_dir = output_dir or default_output_dir(root, MISSING_EXPORTS_ISSUE, file_text)
    target_dir.mkdir(parents=True, exist_ok=True)
    artifact_report = target_dir / "synthesis_artifact_report.json"
    artifact_report.write_text(
        json.dumps(synthetic_artifact_report(missing, revision), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    memory_index = find_latest_code_memory_index(root, repo=repo)
    result = suggest_missing_export_fix(
        repo=repo,
        file_path=file_text,
        artifact_report=artifact_report,
        output_dir=target_dir,
        memory_index=memory_index,
        memory_query=missing_export_memory_query(file_text, missing),
    )
    proposal_path = Path(result["metadata_path"])
    proposal = read_json(proposal_path)
    proposal.update(
        {
            "revision_source": str(revision_proposal_path),
            "synthesized_from_issue": REVISION_ISSUE,
            "synthesis_status": "synthesized",
            "synthesis_reason": reason,
            "synthesis_missing_artifacts": missing,
            "synthesized_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    )
    proposal_path.write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["proposal"] = proposal
    return {
        "synthesis_status": "synthesized",
        "synthesized_issue": proposal.get("issue"),
        "synthesis_reason": reason,
        "missing_artifacts": missing,
        "revision_proposal_path": str(revision_proposal_path),
        "proposal_dir": result["proposal_dir"],
        "metadata_path": result["metadata_path"],
        "patch_path": result.get("patch_path"),
        "apply_supported": proposal.get("apply_supported"),
        "risk_level": proposal.get("risk_level"),
        "manual_review_required": proposal.get("manual_review_required"),
        "quality_gate": nested_get(proposal, ["quality_gate", "status"]),
        "proposal": proposal,
    }


def missing_export_revision_signal(revision: dict[str, Any]) -> tuple[bool, list[str], str]:
    artifact = revision.get("artifact_inspection")
    if not isinstance(artifact, dict):
        artifact = {}
    missing = normalize_artifact_names(artifact.get("missing_position_level_artifacts"))
    recommendation = str(artifact.get("recommendation") or "").lower()
    if missing:
        reason = (
            "Artifact inspection identified missing position-level exports: "
            + ", ".join(missing)
            + "."
        )
        return True, missing, reason
    if recommendation in {"needs_export_patch", "blocked"}:
        return (
            True,
            sorted(POSITION_ARTIFACTS),
            f"Artifact inspection recommendation was {recommendation!r}.",
        )
    return False, [], "Revision failure shape is not a supported missing-export artifact failure."


def normalize_artifact_names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    names = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        if not isinstance(name, str) or not name:
            continue
        cleaned = name.strip().lower()
        if cleaned.endswith(".csv"):
            cleaned = cleaned[:-4]
        if cleaned in POSITION_ARTIFACTS and cleaned not in names:
            names.append(cleaned)
    return names


def synthetic_artifact_report(missing: list[str], revision: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_by": "quant-agent revision synthesis",
        "source_revision_issue": revision.get("issue"),
        "source_revision_summary": revision.get("summary"),
        "recommendation": "needs_export_patch",
        "missing_position_level_artifacts": [{"name": name} for name in missing],
    }


def unsupported_result(root: Path, revision_proposal_path: Path, reason: str) -> dict[str, Any]:
    return {
        "synthesis_status": "unsupported",
        "synthesized_issue": None,
        "synthesis_reason": reason,
        "missing_artifacts": [],
        "revision_proposal_path": str(revision_proposal_path),
        "proposal_dir": None,
        "metadata_path": None,
        "patch_path": None,
        "apply_supported": False,
        "manual_review_required": True,
        "quality_gate": None,
    }


def missing_export_memory_query(file_text: str, missing: list[str]) -> str:
    return (
        f"{file_text} {' '.join(missing)} positions trades rankings weights ranks "
        "simulator export revision verification artifact contract"
    )


def default_output_dir(root: Path, issue: str, relative_file: str) -> Path:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return root / ".fix_proposals" / f"{timestamp}_revised-{safe_component(issue)}_{safe_component(relative_file)}"


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def nested_get(payload: dict[str, Any], keys: list[str]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "item"
