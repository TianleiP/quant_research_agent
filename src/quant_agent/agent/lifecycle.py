from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from quant_agent.agent.actions import make_action
from quant_agent.agent.argument_resolver import resolve_agent_arguments
from quant_agent.agent.state import AgentAction, AgentState


PATCH_LIFECYCLE_TERMS = [
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


class RevisionLoopBlocked(RuntimeError):
    """Raised when the patch lifecycle repeats the same revision path."""


def wants_patch_lifecycle(task: str) -> bool:
    text = task.lower()
    if any(term in text for term in PATCH_LIFECYCLE_TERMS):
        return True
    return (
        "proposal" in text
        and "apply" in text
        and any(token in text for token in ["verify", "verification", "inspect", "run"])
    )


def choose_patch_lifecycle_action(state: AgentState, completed: set[str]) -> AgentAction | None:
    task = str(state.get("task") or "")
    if not wants_patch_lifecycle(task):
        return None
    block_reason = revision_loop_block_reason(state)
    if block_reason:
        raise RevisionLoopBlocked(block_reason)

    cycle = current_lifecycle_cycle(state)
    completed_current = completed_action_names_since(state, cycle["start_index"])
    proposal_arg = {"proposal": cycle["proposal_path"]} if cycle.get("proposal_path") else {}

    if "inspect_proposal" not in completed_current:
        return make_action(
            "inspect_proposal",
            "Inspect the current proposal before running the patch lifecycle.",
            args=proposal_arg,
        )

    proposal = latest_action_result_since(state, "inspect_proposal", cycle["start_index"]) or {}
    if proposal.get("apply_supported") is False:
        return None
    if proposal and proposal.get("patch_exists") is False:
        return None

    dry_run = latest_action_result_since(state, "apply_fix_dry_run", cycle["start_index"])
    if "apply_fix_dry_run" not in completed_current:
        return make_action(
            "apply_fix_dry_run",
            "Dry-run the proposal patch before modifying the target repo.",
            args=proposal_arg,
            requires_approval=True,
        )

    if "apply_fix_yes" not in completed_current and dry_run and dry_run.get("would_change") is True:
        return make_action(
            "apply_fix_yes",
            "Apply the reviewed proposal patch after a successful dry-run.",
            args=proposal_arg,
            requires_approval=True,
        )

    applied = latest_action_result_since(state, "apply_fix_yes", cycle["start_index"])
    if should_run_verification(state, completed_current, applied, cycle["start_index"]):
        return make_action(
            "run_fresh",
            "Run the configured strategy fresh to verify the applied patch.",
            requires_approval=True,
        )

    fresh = latest_action_result_since(state, "run_fresh", cycle["start_index"])
    if run_fresh_failed(fresh):
        if "suggest_revision" not in completed_current:
            return make_action(
                "suggest_revision",
                "Create a revision proposal from the failed verification run.",
                args={"failure": fresh, **proposal_arg},
            )
        return choose_revision_synthesis_action(state, completed_current, cycle["start_index"])

    if ("run_fresh" in completed_current or not lifecycle_config_available(state)) and "inspect_artifacts_latest" not in completed_current:
        return make_action("inspect_artifacts_latest", "Inspect artifact contract after the patch lifecycle.")

    artifact = latest_action_result_since(state, "inspect_artifacts_latest", cycle["start_index"])
    if artifact_inspection_failed(artifact):
        if "suggest_revision" not in completed_current:
            return make_action(
                "suggest_revision",
                "Create a revision proposal from the failed artifact inspection.",
                args={"artifact_inspection": artifact, **proposal_arg},
            )
        return choose_revision_synthesis_action(state, completed_current, cycle["start_index"])

    if ("run_fresh" in completed_current or "inspect_artifacts_latest" in completed_current) and "inspect_latest_run" not in completed_current:
        return make_action("inspect_latest_run", "Inspect the latest run after verification.")

    return None


def choose_revision_synthesis_action(
    state: AgentState,
    completed: set[str],
    start_index: int,
) -> AgentAction | None:
    if "synthesize_revision" in completed:
        return None
    revision = latest_action_result_since(state, "suggest_revision", start_index) or {}
    metadata_path = revision.get("metadata_path")
    if not isinstance(metadata_path, str) or not metadata_path:
        return None
    return make_action(
        "synthesize_revision",
        "Try to synthesize a revised patch proposal from the verification revision.",
        args={"revision_proposal": metadata_path},
    )


def should_run_verification(
    state: AgentState,
    completed: set[str],
    applied: dict[str, Any] | None,
    start_index: int,
) -> bool:
    if "run_fresh" in completed:
        return False
    if not lifecycle_config_available(state):
        return False
    if "apply_fix_yes" in completed:
        return bool(not applied or applied.get("applied") is True)
    dry_run = latest_action_result_since(state, "apply_fix_dry_run", start_index)
    return bool(dry_run and dry_run.get("would_change") is False)


def run_fresh_failed(observation: dict[str, Any] | None) -> bool:
    return bool(observation and observation.get("verification_status") == "failed")


def artifact_inspection_failed(observation: dict[str, Any] | None) -> bool:
    if not observation:
        return False
    recommendation = observation.get("recommendation")
    return bool(recommendation and recommendation != "satisfied")


def lifecycle_config_available(state: AgentState) -> bool:
    root = Path(str(state.get("root") or Path.cwd()))
    resolved = resolve_agent_arguments(root, task=str(state.get("task") or ""))
    return bool(resolved.get("config"))


def latest_observation(state: AgentState, action_name: str) -> dict[str, Any] | None:
    for observation in reversed(state.get("observations", [])):
        if observation.get("action") == action_name:
            return observation
    return None


def current_lifecycle_cycle(state: AgentState) -> dict[str, Any]:
    for index, record in reversed(list(enumerate(state.get("actions", [])))):
        if record.get("status") != "ok":
            continue
        action = record.get("action", {})
        result = record.get("result", {})
        if not isinstance(action, dict) or not isinstance(result, dict):
            continue
        if action.get("name") != "synthesize_revision":
            continue
        if result.get("synthesis_status") != "synthesized" or result.get("apply_supported") is not True:
            continue
        metadata_path = result.get("metadata_path")
        if isinstance(metadata_path, str) and metadata_path:
            return {"proposal_path": metadata_path, "start_index": index}

    root = Path(str(state.get("root") or Path.cwd()))
    resolved = resolve_agent_arguments(root, task=str(state.get("task") or ""))
    return {"proposal_path": resolved.get("proposal"), "start_index": -1}


def revision_loop_block_reason(state: AgentState) -> str | None:
    repeated_proposal = repeated_synthesized_proposal_signature(state)
    if repeated_proposal:
        return (
            "Revision loop guard stopped the lifecycle because the same synthesized "
            f"proposal signature appeared more than once: {repeated_proposal}."
        )

    cycle = current_lifecycle_cycle(state)
    if int(cycle.get("start_index", -1)) < 0:
        return None
    current_failure = latest_failure_signature_since(state, int(cycle["start_index"]))
    if not current_failure:
        return None
    previous_failures = failure_signatures_before(state, int(cycle["start_index"]))
    if current_failure in previous_failures:
        return (
            "Revision loop guard stopped the lifecycle because the same verification "
            f"failure repeated after applying a revised proposal: {current_failure}."
        )
    return None


def repeated_synthesized_proposal_signature(state: AgentState) -> str | None:
    seen: set[str] = set()
    for record in state.get("actions", []):
        if record.get("status") != "ok":
            continue
        action = record.get("action", {})
        result = record.get("result", {})
        if not isinstance(action, dict) or not isinstance(result, dict):
            continue
        if action.get("name") != "synthesize_revision":
            continue
        if result.get("synthesis_status") != "synthesized" or result.get("apply_supported") is not True:
            continue
        signature = synthesized_proposal_signature(state, result)
        if not signature:
            continue
        if signature in seen:
            return signature
        seen.add(signature)
    return None


def synthesized_proposal_signature(state: AgentState, result: dict[str, Any]) -> str | None:
    metadata = read_json_if_exists(resolve_state_path(state, result.get("metadata_path")))
    patch_hash = first_text(result.get("patch_hash"), metadata.get("patch_hash"))
    if not patch_hash:
        patch_hash = file_sha256(resolve_state_path(state, first_text(result.get("patch_path"), metadata.get("patch_path"))))
    payload = {
        "issue": first_text(metadata.get("issue"), result.get("synthesized_issue")),
        "repo": normalize_path_text(metadata.get("repo")),
        "file": normalize_path_text(metadata.get("file")),
        "patch_hash": patch_hash,
    }
    if not any(payload.values()):
        return None
    return stable_signature("proposal", payload)


def latest_failure_signature_since(state: AgentState, start_index: int) -> str | None:
    for record in reversed(state.get("actions", [])[start_index + 1 :]):
        action = record.get("action", {})
        result = record.get("result", {})
        if not isinstance(action, dict) or not isinstance(result, dict):
            continue
        signature = failure_signature(action.get("name"), result)
        if signature:
            return signature
    return None


def failure_signatures_before(state: AgentState, end_index: int) -> set[str]:
    signatures: set[str] = set()
    for record in state.get("actions", [])[: end_index + 1]:
        action = record.get("action", {})
        result = record.get("result", {})
        if not isinstance(action, dict) or not isinstance(result, dict):
            continue
        signature = failure_signature(action.get("name"), result)
        if signature:
            signatures.add(signature)
    return signatures


def failure_signature(action_name: Any, result: dict[str, Any]) -> str | None:
    if action_name == "run_fresh" and run_fresh_failed(result):
        return stable_signature(
            "run_fresh",
            {
                "error_type": result.get("error_type"),
                "error": result.get("error"),
            },
        )
    if action_name == "inspect_artifacts_latest" and artifact_inspection_failed(result):
        return stable_signature(
            "artifact",
            {
                "recommendation": result.get("recommendation"),
                "missing": normalize_artifact_names(result.get("missing_position_level_artifacts")),
            },
        )
    return None


def completed_action_names_since(state: AgentState, start_index: int) -> set[str]:
    names = set()
    for record in state.get("actions", [])[start_index + 1 :]:
        action = record.get("action", {})
        if record.get("status") == "ok" and isinstance(action, dict):
            name = action.get("name")
            if isinstance(name, str):
                names.add(name)
    return names


def latest_action_result_since(
    state: AgentState,
    action_name: str,
    start_index: int,
) -> dict[str, Any] | None:
    for record in reversed(state.get("actions", [])[start_index + 1 :]):
        action = record.get("action", {})
        result = record.get("result", {})
        if (
            record.get("status") == "ok"
            and isinstance(action, dict)
            and action.get("name") == action_name
            and isinstance(result, dict)
        ):
            return result
    return None


def normalize_artifact_names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    names = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("name")
        else:
            value = item
        if isinstance(value, str) and value:
            cleaned = value.strip().lower()
            if cleaned.endswith(".csv"):
                cleaned = cleaned[:-4]
            names.append(cleaned)
    return sorted(set(names))


def resolve_state_path(state: AgentState, value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    root = Path(str(state.get("root") or Path.cwd()))
    return root / path


def read_json_if_exists(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def file_sha256(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def normalize_path_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.replace("\\", "/")


def stable_signature(kind: str, payload: dict[str, Any]) -> str:
    return f"{kind}:{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
