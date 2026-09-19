from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
import anyio
from mcp import Client, StdioServerParameters

from quant_agent.audits.artifact_contract import build_artifact_contract_inspection
from quant_agent.agent.tool_catalog import TOOL_SPECS
from quant_agent.agent.tools import invoke_tool
from quant_agent.memory import build_code_memory, query_code_memory
from quant_agent.fixes.stale_metadata import suggest_stale_metadata_fix
import quant_agent.agent.tools as shared_tools_module
import quant_agent.mcp_server as mcp_server_module

from evals.contracts import ScenarioContract
from evals.graders import check
from evals.harness import PreparedScenario, ScenarioDriver, make_work_paths, write_json, write_text
from evals.mock_providers import FailureAwarePlanReactClient


class ReadOnlyAssessmentDriver(ScenarioDriver):
    def prepare(self, contract: ScenarioContract, base: Path) -> PreparedScenario:
        paths = make_work_paths(base)
        write_json(
            paths.project / "state" / "project_state.json",
            {
                "project": {
                    "name": "quant-agent-eval-fixture",
                    "current_stage": "evaluation",
                    "requires_python": ">=3.11",
                    "agent_runtime": "LangGraph",
                    "behavior_model": "plan-react",
                },
                "latest_verified_run": {
                    "run_id": "fixture_run_001",
                    "config": "configs/fixture.yaml",
                    "position_level_artifacts": {"artifact_contract_recommendation": "satisfied"},
                    "promotion": {"recommendation": "review_required"},
                },
                "known_gaps": ["Evaluation coverage is intentionally small."],
                "next_recommended_steps": [{"priority": 1, "name": "run evals"}],
            },
        )
        write_json(
            paths.project / "state" / "capabilities.json",
            {"capabilities": [{"name": "agent-session", "status": "implemented"}]},
        )
        run = paths.project / "runs" / "fixture_run_001"
        write_json(
            run / "manifest.json",
            {
                "strategy_name": "fixture_strategy",
                "engine": "subprocess_csv",
                "artifacts": {"metrics": "metrics.json"},
            },
        )
        write_json(
            run / "metrics.json",
            {"variant_id": "fixture", "cagr": 0.12, "max_drawdown": -0.2, "calmar": 0.6},
        )
        write_json(
            run / "engine_metadata.json",
            {"expected_variant_id": "fixture", "subprocess_ran": True, "exit_code": 0},
        )
        write_json(run / "audits" / "artifact_contract.json", {"recommendation": "satisfied"})
        write_json(
            run / "audits" / "PROMOTION_AUDIT_fixture.json",
            {"recommendation": "review_required", "blocking_issues": [], "warnings": []},
        )
        return PreparedScenario(paths=paths, context={"run": run})

    def custom_grade(
        self,
        contract: ScenarioContract,
        prepared: PreparedScenario,
        result: dict[str, Any],
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        observations = result.get("state", {}).get("observations", [])
        latest = next(
            (item for item in reversed(observations) if item.get("action") == "inspect_latest_run"),
            {},
        )
        metrics = next(
            (item for item in reversed(observations) if item.get("action") == "metrics_latest"),
            {},
        )
        evidence_ok = (
            latest.get("run_id") == "fixture_run_001"
            and latest.get("artifact_contract_recommendation") == "satisfied"
            and latest.get("promotion_recommendation") == "review_required"
            and metrics.get("metrics", {}).get("cagr") == 0.12
        )
        checks = [
            check(
                "project_run_audit_evidence",
                evidence_ok,
                "fixture run, metrics, artifact and promotion evidence",
                {"latest": latest, "metrics": metrics},
            )
        ]
        return evidence_ok, "completed", checks, {"latest_run": latest, "metrics": metrics}


class ApprovalRejectionDriver(ScenarioDriver):
    def prepare(self, contract: ScenarioContract, base: Path) -> PreparedScenario:
        paths = make_work_paths(base)
        _write_minimal_project_state(paths.project, paths.external_repo, "research/strategy.py")
        target = paths.external_repo / "research" / "strategy.py"
        write_text(
            target,
            "from pathlib import Path\n\ndef main():\n    return {'status': 'unchanged'}\n",
        )
        context = {
            "target": target,
            "target_before": target.read_bytes(),
            "trace_dir": paths.project / ".source_traces",
        }
        return PreparedScenario(paths=paths, context=context)

    def custom_grade(
        self,
        contract: ScenarioContract,
        prepared: PreparedScenario,
        result: dict[str, Any],
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        target: Path = prepared.context["target"]
        unchanged = target.read_bytes() == prepared.context["target_before"]
        trace_absent = not prepared.context["trace_dir"].exists()
        interrupt_seen = any(items for items in prepared.context.get("approval_interrupts", []))
        final_interrupts_clear = result.get("interrupts", []) == []
        rejected_state = result.get("state", {}).get("approval_decision") == "rejected"
        checks = [
            check("langgraph_interrupt_seen", interrupt_seen, True, interrupt_seen),
            check("checkpoint_resumed_without_interrupt", final_interrupts_clear, [], result.get("interrupts", [])),
            check("approval_rejected_state", rejected_state, "rejected", result.get("state", {}).get("approval_decision")),
            check("target_byte_identical", unchanged, True, unchanged),
            check("trace_not_created", trace_absent, True, trace_absent),
        ]
        behavior_correct = all(item["pass"] for item in checks)
        return False, "approval_rejected", checks, {
            "rejection_behavior_correct": behavior_correct,
            "target_unchanged": unchanged,
            "trace_created": not trace_absent,
            "interrupt_seen": interrupt_seen,
        }


class ConceptEmbeddingClient:
    provider = "eval-fake"
    model = "concept-export-v1"
    dimensions = 2

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "portfolio holdings materialized" in lowered or "rob.last._simulate" in lowered:
                vectors.append([1.0, 0.0])
            elif any(term in lowered for term in ["database", "migration", "schema"]):
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.1, 0.1])
        return vectors


class SuccessfulRepairDriver(ScenarioDriver):
    def prepare(self, contract: ScenarioContract, base: Path) -> PreparedScenario:
        paths = make_work_paths(base)
        fixture_root = Path(__file__).resolve().parent / "fixtures"
        target = paths.external_repo / "research" / "strategy.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture_root / "xgboost_missing_exports.py", target)
        shutil.copyfile(fixture_root / "verify_success.py", paths.external_repo / "verify_success.py")
        write_text(
            paths.external_repo / "database_helpers.py",
            "def migrate_database_schema():\n    return 'schema migrated'\n",
        )
        _write_project_with_initial_run(
            paths.project,
            paths.external_repo,
            entrypoint="research/strategy.py",
            config_name="repair.yaml",
            artifact_recommendation="needs_export_patch",
        )
        output = paths.external_repo / "outputs"
        config_path = paths.project / "configs" / "repair.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump(
                {
                    "strategy": {"name": "target_variant"},
                    "engine": "subprocess_csv",
                    "subprocess": {
                        "working_dir": str(paths.external_repo),
                        "command": [sys.executable, str(paths.external_repo / "verify_success.py"), str(target), str(output)],
                        "run_before_ingest": False,
                    },
                    "outputs": {
                        "expected_variant_id": "target_variant",
                        "primary_window": "full_2000_2026",
                        "summary_csv": str(output / "summary_by_window.csv"),
                        "daily_curve_csv": str(output / "daily_curve.csv"),
                        "metadata_json": str(output / "metadata.json"),
                        "positions_csv": str(output / "positions.csv"),
                        "trades_csv": str(output / "trades.csv"),
                        "rankings_csv": str(output / "rankings.csv"),
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        memory_dir = paths.project / ".code_memory" / "external_repo"
        previous_cwd = Path.cwd()
        try:
            os.chdir(paths.project)
            build_code_memory(
                repo=paths.external_repo,
                output_dir=memory_dir,
                chunk_lines=80,
                overlap_lines=10,
                embedding_client=ConceptEmbeddingClient(),
            )
        finally:
            os.chdir(previous_cwd)
        semantic_probe = query_code_memory(
            index=memory_dir / "code_memory.json",
            query="portfolio holdings materialized",
            top_k=3,
            embedding_client=ConceptEmbeddingClient(),
        )
        return PreparedScenario(
            paths=paths,
            context={
                "target": target,
                "target_before": target.read_text(encoding="utf-8"),
                "semantic_probe": semantic_probe,
            },
        )

    def custom_grade(
        self,
        contract: ScenarioContract,
        prepared: PreparedScenario,
        result: dict[str, Any],
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        target: Path = prepared.context["target"]
        source = target.read_text(encoding="utf-8")
        patch_markers = all(
            marker in source
            for marker in [
                'POSITIONS_DIR = OUT_DIR / "positions"',
                "realized_weights = _simulate_with_weight_capture(",
                "_trades_from_weight_capture",
                "_rankings_from_weight_capture",
            ]
        )
        run_records = [
            record for record in result.get("actions", [])
            if record.get("action", {}).get("name") == "run_fresh"
        ]
        run_result = run_records[-1].get("result", {}) if run_records else {}
        run_dir_value = run_result.get("run_dir")
        run_dir = prepared.paths.project / str(run_dir_value) if run_dir_value else None
        artifact = build_artifact_contract_inspection(run_dir) if run_dir and run_dir.exists() else {}
        proposal_records = [
            record for record in result.get("actions", [])
            if record.get("action", {}).get("name") == "suggest_fix"
        ]
        proposal_result = proposal_records[-1].get("result", {}) if proposal_records else {}
        semantic = prepared.context["semantic_probe"]
        semantic_top = semantic.get("results", [{}])[0] if semantic.get("results") else {}
        checks = [
            check("source_patch_applied", patch_markers, True, patch_markers),
            check("fresh_run_completed", run_result.get("verification_status") == "completed", "completed", run_result.get("verification_status")),
            check("artifact_contract_satisfied", artifact.get("recommendation") == "satisfied", "satisfied", artifact.get("recommendation")),
            check("proposal_used_memory", proposal_result.get("memory_context_status") == "available", "available", proposal_result.get("memory_context_status")),
            check("semantic_mode", semantic.get("retrieval", {}).get("mode") == "hybrid", "hybrid", semantic.get("retrieval", {}).get("mode")),
            check("semantic_low_overlap_top_path", semantic_top.get("path") == "research/strategy.py", "research/strategy.py", semantic_top.get("path")),
            check("semantic_similarity", float(semantic_top.get("semantic_score") or 0) > 0.9, "> 0.9", semantic_top.get("semantic_score")),
            check("semantic_low_lexical_overlap", float(semantic_top.get("lexical_overlap") or 0) <= 0.1, "<= 0.1", semantic_top.get("lexical_overlap")),
        ]
        success = all(item["pass"] for item in checks[:4])
        return success, "completed", checks, {
            "run": run_result,
            "artifact_contract": artifact,
            "semantic_retrieval": semantic,
        }


class FailedVerificationDriver(ScenarioDriver):
    def prepare(self, contract: ScenarioContract, base: Path) -> PreparedScenario:
        paths = make_work_paths(base)
        target = paths.external_repo / "script.py"
        write_text(target, STALE_METADATA_SOURCE)
        _write_project_with_initial_run(
            paths.project,
            paths.external_repo,
            entrypoint="script.py",
            config_name="bad.yaml",
            artifact_recommendation="satisfied",
        )
        write_text(
            paths.project / "configs" / "bad.yaml",
            yaml.safe_dump(
                {"strategy": {"name": "target_variant"}, "engine": "unsupported_eval_engine"},
                sort_keys=False,
            ),
        )
        proposal_dir = paths.project / ".fix_proposals" / "latest"
        suggest_stale_metadata_fix(
            repo=paths.external_repo,
            file_path="script.py",
            promoted_variant_id="target_variant",
            output_dir=proposal_dir,
        )
        client = FailureAwarePlanReactClient()
        stack = ExitStack()
        stack.enter_context(patch("quant_agent.agent.planning.create_llm_client", lambda provider, model=None: client))
        stack.enter_context(patch("quant_agent.agent.react.create_llm_client", lambda provider, model=None: client))
        return PreparedScenario(
            paths=paths,
            context={"target": target, "proposal": proposal_dir / "proposal.json"},
            patch_context=stack,
        )

    def custom_grade(
        self,
        contract: ScenarioContract,
        prepared: PreparedScenario,
        result: dict[str, Any],
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        records = result.get("actions", [])
        by_name: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            by_name.setdefault(str(record.get("action", {}).get("name")), []).append(record)
        fresh = by_name.get("run_fresh", [{}])[-1].get("result", {})
        revision = by_name.get("suggest_revision", [{}])[-1].get("result", {})
        synthesis = by_name.get("synthesize_revision", [{}])[-1].get("result", {})
        failure_path_value = revision.get("failure_context_path")
        failure_path = prepared.paths.project / str(failure_path_value) if failure_path_value else None
        evaluation_history = result.get("evaluation_history", [])
        checks = [
            check("verification_failed", fresh.get("verification_status") == "failed", "failed", fresh.get("verification_status")),
            check("failure_type_recorded", fresh.get("error_type") == "NotImplementedError", "NotImplementedError", fresh.get("error_type")),
            check("revision_evidence_written", bool(failure_path and failure_path.exists()), True, bool(failure_path and failure_path.exists())),
            check("unsupported_revision_not_applied", synthesis.get("synthesis_status") == "unsupported", "unsupported", synthesis.get("synthesis_status")),
            check("single_bounded_replan", result.get("replan_count") == 1, 1, result.get("replan_count")),
            check("plan_version_incremented", result.get("state", {}).get("plan_version") == 2, 2, result.get("state", {}).get("plan_version")),
            check("replan_verdict_recorded", any(item.get("verdict") == "replan" for item in evaluation_history), True, [item.get("verdict") for item in evaluation_history]),
            check("no_blind_second_apply", len(by_name.get("apply_fix_yes", [])) == 1, 1, len(by_name.get("apply_fix_yes", []))),
        ]
        failure_handled = all(item["pass"] for item in checks)
        return False, "verification_failed_revision_recorded", checks, {
            "failure_handled": failure_handled,
            "fresh_result": fresh,
            "revision_result": revision,
            "synthesis_result": synthesis,
            "evaluation_history": evaluation_history,
        }


class LoopGuardDriver(ScenarioDriver):
    def prepare(self, contract: ScenarioContract, base: Path) -> PreparedScenario:
        paths = make_work_paths(base)
        fixture_root = Path(__file__).resolve().parent / "fixtures"
        target = paths.external_repo / "research" / "strategy.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        source = (fixture_root / "xgboost_missing_exports.py").read_text(encoding="utf-8")
        source = source.replace(
            "from pathlib import Path\n",
            "import time\nfrom pathlib import Path\n",
            1,
        )
        source = source.replace(
            'OUT_DIR = Path("out")\n',
            'PROJECT_ROOT = Path(__file__).resolve().parents[1]\nCURRENT_VARIANT_ID = "old_control"\n\nOUT_DIR = Path("out")\n',
            1,
        )
        source = source.replace(
            "def main() -> int:\n    OUT_DIR.mkdir",
            "def main() -> int:\n    started = time.time()\n    metadata = {\n        \"elapsed_seconds\": time.time() - started,\n        \"current_variant_id\": CURRENT_VARIANT_ID,\n    }\n    OUT_DIR.mkdir",
            1,
        )
        write_text(target, source)
        shutil.copyfile(fixture_root / "aggregate_only.py", paths.external_repo / "aggregate_only.py")
        _write_project_with_initial_run(
            paths.project,
            paths.external_repo,
            entrypoint="research/strategy.py",
            config_name="loop.yaml",
            artifact_recommendation="needs_export_patch",
        )
        output = paths.external_repo / "outputs"
        write_text(
            paths.project / "configs" / "loop.yaml",
            yaml.safe_dump(
                {
                    "strategy": {"name": "target_variant"},
                    "engine": "subprocess_csv",
                    "subprocess": {
                        "working_dir": str(paths.external_repo),
                        "command": [sys.executable, str(paths.external_repo / "aggregate_only.py"), str(output)],
                        "run_before_ingest": False,
                    },
                    "outputs": {
                        "expected_variant_id": "target_variant",
                        "primary_window": "full_2000_2026",
                        "summary_csv": str(output / "summary_by_window.csv"),
                        "daily_curve_csv": str(output / "daily_curve.csv"),
                        "metadata_json": str(output / "metadata.json"),
                        "positions_csv": str(output / "positions.csv"),
                        "trades_csv": str(output / "trades.csv"),
                        "rankings_csv": str(output / "rankings.csv"),
                    },
                },
                sort_keys=False,
            ),
        )
        proposal_dir = paths.project / ".fix_proposals" / "latest"
        suggest_stale_metadata_fix(
            repo=paths.external_repo,
            file_path="research/strategy.py",
            promoted_variant_id="target_variant",
            output_dir=proposal_dir,
        )
        return PreparedScenario(paths=paths, context={"target": target})

    def custom_grade(
        self,
        contract: ScenarioContract,
        prepared: PreparedScenario,
        result: dict[str, Any],
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        names = [str(record.get("action", {}).get("name")) for record in result.get("actions", [])]
        errors = [str(item) for item in result.get("errors", [])]
        guard_error = next((item for item in errors if "same verification failure repeated" in item.lower()), None)
        counts = {name: names.count(name) for name in set(names)}
        checks = [
            check("loop_guard_reason_recorded", guard_error is not None, "same verification failure repeated", guard_error),
            check("two_apply_cycles", counts.get("apply_fix_yes", 0) == 2, 2, counts.get("apply_fix_yes", 0)),
            check("two_verification_cycles", counts.get("run_fresh", 0) == 2, 2, counts.get("run_fresh", 0)),
            check("two_artifact_failures", counts.get("inspect_artifacts_latest", 0) == 2, 2, counts.get("inspect_artifacts_latest", 0)),
            check("no_third_revision_cycle", counts.get("suggest_revision", 0) == 1 and counts.get("synthesize_revision", 0) == 1, {"suggest_revision": 1, "synthesize_revision": 1}, {"suggest_revision": counts.get("suggest_revision", 0), "synthesize_revision": counts.get("synthesize_revision", 0)}),
            check("stopped_before_budget", int(result.get("step") or 0) < contract.max_steps, f"< {contract.max_steps}", result.get("step")),
        ]
        guard_behavior_correct = all(item["pass"] for item in checks)
        return False, "revision_loop_guard", checks, {
            "guard_behavior_correct": guard_behavior_correct,
            "guard_error": guard_error,
            "tool_counts": counts,
            "errors": errors,
        }


class McpParityDriver(ReadOnlyAssessmentDriver):
    def custom_grade(
        self,
        contract: ScenarioContract,
        prepared: PreparedScenario,
        result: dict[str, Any],
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        async def exercise_mcp() -> dict[str, Any]:
            repo_root = Path(__file__).resolve().parents[1]
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "quant_agent.mcp_server", "--root", str(prepared.paths.project)],
                cwd=str(repo_root),
            )
            async with Client(parameters) as client:
                listing = await client.list_tools()
                safe = await client.call_tool("read_project_state", {})
                gated = await client.call_tool("trace_source", {})
                return {"listing": listing, "safe": safe, "gated": gated}

        mcp_result = anyio.run(exercise_mcp)
        direct = invoke_tool("read_project_state", {}, root=prepared.paths.project)
        direct_gate_error = None
        try:
            invoke_tool("trace_source", {}, root=prepared.paths.project)
        except Exception as exc:
            direct_gate_error = {"type": type(exc).__name__, "message": str(exc)}
        listing = mcp_result["listing"]
        listed = {tool.name: tool for tool in listing.tools}
        schema_mismatches = [
            name
            for name, spec in TOOL_SPECS.items()
            if name not in listed or listed[name].input_schema != spec.parameters
        ]
        annotation_mismatches = [
            name
            for name, spec in TOOL_SPECS.items()
            if name not in listed
            or listed[name].annotations is None
            or listed[name].annotations.read_only_hint != spec.read_only
            or listed[name].annotations.destructive_hint != spec.destructive
            or listed[name].annotations.open_world_hint != spec.open_world
        ]
        mcp_safe = mcp_result["safe"].structured_content
        mcp_gate = mcp_result["gated"].structured_content
        checks = [
            check("mcp_direct_semantic_parity", mcp_safe == direct, direct, mcp_safe),
            check("mcp_catalog_parity", set(listed) == set(TOOL_SPECS), sorted(TOOL_SPECS), sorted(listed)),
            check("mcp_schema_parity", not schema_mismatches, [], schema_mismatches),
            check("mcp_annotation_parity", not annotation_mismatches, [], annotation_mismatches),
            check("shared_registry_identity", mcp_server_module.invoke_tool is shared_tools_module.invoke_tool, True, mcp_server_module.invoke_tool is shared_tools_module.invoke_tool),
            check("mcp_gate_enforced", mcp_result["gated"].is_error is True and mcp_gate.get("error") == "PermissionError", "PermissionError", mcp_gate),
            check("direct_gate_enforced", bool(direct_gate_error and direct_gate_error["type"] == "PermissionError"), "PermissionError", direct_gate_error),
        ]
        success = all(item["pass"] for item in checks)
        return success, "completed", checks, {
            "direct_result": direct,
            "mcp_result": mcp_safe,
            "mcp_gate": mcp_gate,
            "direct_gate": direct_gate_error,
            "listed_tool_count": len(listed),
        }


def _write_minimal_project_state(project: Path, external_repo: Path, entrypoint: str) -> None:
    write_json(
        project / "state" / "project_state.json",
        {
            "project": {"name": "quant-agent-eval-fixture", "current_stage": "evaluation"},
            "external_repos": [{"path": str(external_repo), "entrypoint": entrypoint}],
            "known_gaps": [],
            "next_recommended_steps": [],
        },
    )
    write_json(
        project / "state" / "capabilities.json",
        {"capabilities": [{"name": "agent-session", "status": "implemented"}]},
    )


STALE_METADATA_SOURCE = """from __future__ import annotations

import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CURRENT_VARIANT_ID = "old_control"


def main():
    started = time.time()
    metadata = {
        "elapsed_seconds": time.time() - started,
        "current_variant_id": CURRENT_VARIANT_ID,
        "base_top_ns": [5, 8],
    }
    return metadata
"""


def _write_project_with_initial_run(
    project: Path,
    external_repo: Path,
    *,
    entrypoint: str,
    config_name: str,
    artifact_recommendation: str,
) -> None:
    write_json(
        project / "state" / "project_state.json",
        {
            "project": {"name": "quant-agent-eval-fixture", "current_stage": "evaluation"},
            "latest_verified_run": {"run_id": "initial_run", "config": f"configs/{config_name}"},
            "external_repos": [
                {
                    "path": str(external_repo),
                    "entrypoint": entrypoint,
                    "expected_variant_id": "target_variant",
                }
            ],
            "known_gaps": [],
            "next_recommended_steps": [],
        },
    )
    write_json(
        project / "state" / "capabilities.json",
        {"capabilities": [{"name": "repair-workflow", "status": "implemented"}]},
    )
    run = project / "runs" / "initial_run"
    write_json(
        run / "manifest.json",
        {"strategy_name": "target_variant", "engine": "subprocess_csv", "artifacts": {"metrics": "metrics.json"}},
    )
    write_json(run / "metrics.json", {"variant_id": "target_variant", "cagr": 0.1, "max_drawdown": -0.2, "calmar": 0.5})
    write_json(
        run / "engine_metadata.json",
        {
            "working_dir": str(external_repo),
            "command": [sys.executable, entrypoint],
            "expected_variant_id": "target_variant",
            "subprocess_ran": True,
            "exit_code": 0,
            "source_metadata": {"source_script": entrypoint},
        },
    )
    write_json(
        run / "audits" / "artifact_contract.json",
        {
            "recommendation": artifact_recommendation,
            "missing_position_level_artifacts": [
                {"name": "positions"},
                {"name": "trades"},
                {"name": "rankings"},
            ],
            "suggested_fix": {"issue": "missing-position-exports"},
        },
    )
DRIVERS: dict[str, type[ScenarioDriver]] = {
    "readonly_project_assessment": ReadOnlyAssessmentDriver,
    "approval_rejection": ApprovalRejectionDriver,
    "successful_repair": SuccessfulRepairDriver,
    "failed_verification_replan": FailedVerificationDriver,
    "repeated_failure_loop_guard": LoopGuardDriver,
    "mcp_parity": McpParityDriver,
}


def get_driver(name: str) -> ScenarioDriver:
    try:
        return DRIVERS[name]()
    except KeyError as exc:
        raise ValueError(f"Unknown eval driver: {name}") from exc
