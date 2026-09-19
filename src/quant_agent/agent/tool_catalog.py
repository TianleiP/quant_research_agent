from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ToolRisk = Literal["safe", "approval_gated"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: ToolRisk
    parameters: dict[str, Any] = field(default_factory=lambda: _object_schema())
    read_only: bool = False
    destructive: bool = False
    open_world: bool = False

    @property
    def requires_approval(self) -> bool:
        return self.risk == "approval_gated"


def _object_schema(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


_TOOL_SPECS = [
    ToolSpec(
        "read_project_state",
        "Read centralized project implementation state.",
        "safe",
        read_only=True,
    ),
    ToolSpec(
        "read_capabilities",
        "Read command and capability map.",
        "safe",
        read_only=True,
    ),
    ToolSpec(
        "inspect_latest_run",
        "Read latest run manifest, metadata, compact metrics, artifact recommendation, and promotion recommendation.",
        "safe",
        read_only=True,
    ),
    ToolSpec("metrics_latest", "Read compact core metrics for the latest run.", "safe", read_only=True),
    ToolSpec(
        "inspect_artifacts_latest",
        "Run/read artifact contract inspection for the latest run.",
        "safe",
    ),
    ToolSpec("promote_latest", "Generate/read promotion audit for the latest run.", "safe"),
    ToolSpec(
        "inspect_proposal",
        "Read a fix proposal and summarize patch/apply status. Args: proposal.",
        "safe",
        _object_schema({"proposal": _string("Path to a proposal directory or proposal.json file.")}),
        read_only=True,
    ),
    ToolSpec(
        "query_code_memory",
        "Search local code memory with semantic, BM25 lexical, symbol, and path evidence, returning cited source chunks. Args: index, query, optional top_k.",
        "safe",
        _object_schema(
            {
                "index": _string("Path to the code-memory index JSON file."),
                "query": _string("Natural-language, symbol, path, or source-code query."),
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            }
        ),
        read_only=True,
        open_world=True,
    ),
    ToolSpec(
        "suggest_revision",
        "Write a structured revision proposal after failed verification. Args: proposal, optional failure/artifact_inspection/output.",
        "safe",
        _object_schema(
            {
                "proposal": _string("Path to the failed fix proposal."),
                "failure": _string("Path to failure_context.json, if known."),
                "artifact_inspection": _string("Path to artifact inspection JSON, if known."),
                "output": _string("Directory where the revision proposal should be written."),
            }
        ),
    ),
    ToolSpec(
        "synthesize_revision",
        "Try to turn a verification revision into a new structured fix proposal when the failure shape is supported. Args: revision_proposal, optional output.",
        "safe",
        _object_schema(
            {
                "revision_proposal": _string("Path to a revision proposal directory or JSON file."),
                "output": _string("Directory where the synthesized proposal should be written."),
            }
        ),
    ),
    ToolSpec(
        "trace_source",
        "Trace a source file for a fix issue. Args: repo, file, optional issue, output.",
        "approval_gated",
        _object_schema(
            {
                "repo": _string("Path to the source repository."),
                "file": _string("Source file path relative to the repository."),
                "issue": _string("Issue or missing behavior to trace."),
                "output": _string("Directory where trace artifacts should be written."),
            }
        ),
    ),
    ToolSpec(
        "suggest_fix",
        "Generate a structured fix proposal. Args: repo, file, issue, optional problem/promoted_variant_id/artifact_report/memory_index/memory_query/provider/model/output.",
        "approval_gated",
        _object_schema(
            {
                "repo": _string("Path to the source repository."),
                "file": _string("Source file path relative to the repository."),
                "issue": _string("Issue identifier or requested repair."),
                "problem": _string("Human-readable description of the problem."),
                "promoted_variant_id": _string("Related promoted strategy variant id."),
                "artifact_report": _string("Path to artifact inspection JSON."),
                "memory_index": _string("Path to the code-memory index JSON file."),
                "memory_query": _string("Query used to retrieve supporting source context."),
                "provider": _string("LLM provider for generalized proposal drafting."),
                "model": _string("Optional provider model name."),
                "output": _string("Directory where the fix proposal should be written."),
            }
        ),
        open_world=True,
    ),
    ToolSpec(
        "apply_fix_dry_run",
        "Dry-run a fix proposal without modifying target repo. Args: proposal.",
        "approval_gated",
        _object_schema({"proposal": _string("Path to a reviewed proposal directory or proposal.json file.")}),
    ),
    ToolSpec(
        "apply_fix_yes",
        "Apply a reviewed fix proposal and modify target repo. Args: proposal.",
        "approval_gated",
        _object_schema({"proposal": _string("Path to a reviewed proposal directory or proposal.json file.")}),
        destructive=True,
    ),
    ToolSpec(
        "run_fresh",
        "Run a configured external backtest fresh before ingest. Args: config.",
        "approval_gated",
        _object_schema({"config": _string("Path to the strategy run YAML configuration.")}),
        open_world=True,
    ),
]


TOOL_SPECS = {spec.name: spec for spec in _TOOL_SPECS}


def get_tool_spec(name: str) -> ToolSpec:
    try:
        return TOOL_SPECS[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported agent action: {name}") from exc


def tool_names(risk: ToolRisk | None = None) -> set[str]:
    return {
        name
        for name, spec in TOOL_SPECS.items()
        if risk is None or spec.risk == risk
    }
