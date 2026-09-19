from __future__ import annotations

from pathlib import Path
from typing import Any

from quant_agent.memory import query_code_memory


PROPOSAL_SCHEMA_VERSION = 1
REQUIRED_STRUCTURED_FIELDS = [
    "proposal_schema_version",
    "problem",
    "target_files",
    "intended_behavior",
    "patch_plan",
    "verification",
    "evidence",
    "memory_context",
]


def collect_memory_context(
    memory_index: Path | None,
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    if memory_index is None:
        return {
            "status": "not_available",
            "query": query,
            "reason": "No code memory index matched the target repo.",
            "result_count": 0,
            "results": [],
        }
    # Retrieval is supporting evidence; a broken index should not erase the proposal draft.
    try:
        result = query_code_memory(
            index=memory_index,
            query=query,
            top_k=top_k,
            max_excerpt_chars=700,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "status": "error",
            "index": str(memory_index),
            "query": query,
            "reason": f"{type(exc).__name__}: {exc}",
            "result_count": 0,
            "results": [],
        }

    return {
        "status": "available",
        "index": str(memory_index),
        "repo": result.get("repo"),
        "generated_at": result.get("generated_at"),
        "query": result.get("query"),
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


def attach_proposal_contract(
    proposal: dict[str, Any],
    *,
    problem: dict[str, Any],
    target_files: list[dict[str, Any]],
    intended_behavior: list[str],
    patch_plan: list[dict[str, Any]],
    verification: dict[str, Any],
    evidence: list[dict[str, Any]],
    memory_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proposal["proposal_schema_version"] = PROPOSAL_SCHEMA_VERSION
    proposal["problem"] = problem
    proposal["target_files"] = target_files
    proposal["intended_behavior"] = intended_behavior
    proposal["patch_plan"] = patch_plan
    proposal["verification"] = verification
    proposal["evidence"] = evidence
    proposal["memory_context"] = memory_context or {
        "status": "not_available",
        "result_count": 0,
        "results": [],
    }
    proposal["quality_gate"] = build_quality_gate(proposal)
    validate_structured_proposal(proposal)
    return proposal


def build_quality_gate(proposal: dict[str, Any]) -> dict[str, Any]:
    checks = [
        check_required_fields(proposal),
        check_non_empty_list(proposal, "target_files"),
        check_non_empty_list(proposal, "intended_behavior"),
        check_non_empty_list(proposal, "patch_plan"),
        check_verification(proposal),
        check_memory_context(proposal),
    ]
    status = "fail" if any(item["status"] == "fail" for item in checks) else "pass"
    return {"status": status, "checks": checks}


def validate_structured_proposal(proposal: dict[str, Any]) -> None:
    gate = proposal.get("quality_gate")
    if not isinstance(gate, dict):
        raise ValueError("Proposal is missing quality_gate.")
    failed = [
        item
        for item in gate.get("checks", [])
        if isinstance(item, dict) and item.get("status") == "fail"
    ]
    if failed:
        names = ", ".join(str(item.get("name")) for item in failed)
        raise ValueError(f"Structured proposal quality gate failed: {names}")


def check_required_fields(proposal: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_STRUCTURED_FIELDS if field not in proposal]
    return {
        "name": "required_structured_fields",
        "status": "fail" if missing else "pass",
        "detail": "Missing fields: " + ", ".join(missing) if missing else "All structured fields are present.",
    }


def check_non_empty_list(proposal: dict[str, Any], key: str) -> dict[str, Any]:
    value = proposal.get(key)
    ok = isinstance(value, list) and bool(value)
    return {
        "name": f"{key}_present",
        "status": "pass" if ok else "fail",
        "detail": f"{key} contains {len(value) if isinstance(value, list) else 0} item(s).",
    }


def check_verification(proposal: dict[str, Any]) -> dict[str, Any]:
    verification = proposal.get("verification")
    commands = verification.get("commands") if isinstance(verification, dict) else None
    ok = isinstance(commands, list) and bool(commands)
    return {
        "name": "verification_commands_present",
        "status": "pass" if ok else "fail",
        "detail": f"Verification command count: {len(commands) if isinstance(commands, list) else 0}.",
    }


def check_memory_context(proposal: dict[str, Any]) -> dict[str, Any]:
    context = proposal.get("memory_context")
    if not isinstance(context, dict):
        return {
            "name": "memory_context",
            "status": "warn",
            "detail": "No memory context object was attached.",
        }
    status = str(context.get("status") or "not_available")
    result_count = int(context.get("result_count") or 0)
    if status == "available" and result_count > 0:
        detail = f"Code memory returned {result_count} source chunk(s)."
    else:
        detail = str(context.get("reason") or f"Memory context status: {status}.")
    return {
        "name": "memory_context",
        "status": "pass" if status == "available" and result_count > 0 else "warn",
        "detail": detail,
    }
