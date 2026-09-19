from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import yaml

from quant_agent.agent.runner import resume_agent_session, run_agent_session
from quant_agent.audits.artifact_contract import (
    build_artifact_contract_inspection,
    run_artifact_contract_inspection,
)
from quant_agent.backtest.adapter import run_backtest
from quant_agent.config import load_config, with_run_before_ingest
from quant_agent.flags import basic_risk_flags
from quant_agent.memory import build_code_memory
from quant_agent.registry import save_run
from quant_agent.reports import write_short_report


TASK = (
    "Search code memory, trace the source, suggest a missing-export fix, "
    "dry-check that fix, apply that fix, and execute a fresh backtest."
)
EXPECTED_APPROVALS = [
    "trace_source",
    "suggest_fix",
    "apply_fix_dry_run",
    "apply_fix_yes",
    "run_fresh",
]
REQUIRED_TOOLS = [
    "read_project_state",
    "read_capabilities",
    "query_code_memory",
    *EXPECTED_APPROVALS,
]
REPAIR_MARKERS = [
    'POSITIONS_DIR = OUT_DIR / "positions"',
    "realized_weights = _simulate_with_weight_capture(",
    "_trades_from_weight_capture",
    "_rankings_from_weight_capture",
]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    workdir = resolve_workdir(repo_root, args.workdir)
    project, external_repo = prepare_demo(repo_root, workdir)

    print(f"Demo workspace: {workdir}")
    print("Initial evidence: positions.csv, trades.csv, and rankings.csv are missing.")
    result, approvals = run_workflow(
        project,
        approve_demo_actions=args.approve_demo_actions,
    )
    report = build_report(workdir, project, external_repo, result, approvals)
    write_report(workdir, report)
    print_summary(report)
    return 0 if report["success"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the self-contained Quant-Agent missing-export repair demo."
    )
    parser.add_argument(
        "--approve-demo-actions",
        action="store_true",
        help=(
            "Approve only the five expected demo actions after each real LangGraph "
            "interrupt. Unexpected actions are rejected."
        ),
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        help="New directory for the isolated demo workspace.",
    )
    return parser


def resolve_workdir(repo_root: Path, requested: Path | None) -> Path:
    if requested:
        workdir = requested.resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        workdir = repo_root / ".demo_runs" / f"repair_{stamp}"
    if workdir.exists():
        raise FileExistsError(f"Demo workdir already exists: {workdir}")
    workdir.mkdir(parents=True)
    return workdir


def prepare_demo(repo_root: Path, workdir: Path) -> tuple[Path, Path]:
    project = workdir / "project"
    external_repo = workdir / "demo_strategy_repo"
    shutil.copytree(repo_root / "examples" / "demo_strategy_repo", external_repo)
    (project / "configs").mkdir(parents=True)
    (project / "state").mkdir(parents=True)

    config_path = project / "configs" / "repair_demo.yaml"
    config_path.write_text(
        yaml.safe_dump(demo_config(external_repo), sort_keys=False),
        encoding="utf-8",
    )

    config = with_run_before_ingest(load_config(config_path), True)
    backtest = run_backtest(config)
    flags = basic_risk_flags(backtest)
    run_dir, manifest = save_run(config, backtest, flags=flags, runs_root=project / "runs")
    write_short_report(run_dir, manifest.metrics, flags, manifest.artifacts)
    run_artifact_contract_inspection(run_dir)

    write_json(
        project / "state" / "project_state.json",
        {
            "project": {"name": "quant-agent-repair-demo", "current_stage": "needs_repair"},
            "latest_verified_run": {
                "run_id": manifest.run_id,
                "config": "configs/repair_demo.yaml",
                "expected_variant_id": "target_variant",
            },
            "external_repos": [
                {
                    "path": str(external_repo),
                    "entrypoint": "research/strategy.py",
                    "expected_variant_id": "target_variant",
                }
            ],
            "known_gaps": ["Position, trade, and ranking exports are missing."],
            "next_recommended_steps": [{"priority": 1, "name": "repair missing exports"}],
        },
    )
    write_json(
        project / "state" / "capabilities.json",
        {"capabilities": [{"name": "repair-workflow", "status": "implemented"}]},
    )
    build_code_memory(
        repo=external_repo,
        output_dir=project / ".code_memory" / "demo_strategy_repo",
        chunk_lines=80,
        overlap_lines=10,
    )
    return project, external_repo


def demo_config(external_repo: Path) -> dict[str, Any]:
    return {
        "strategy": {"name": "target_variant"},
        "engine": "subprocess_csv",
        "subprocess": {
            "working_dir": str(external_repo),
            "command": [
                sys.executable,
                "-m",
                "demo_backtest",
                "research/strategy.py",
                "outputs",
            ],
            "run_before_ingest": False,
        },
        "outputs": {
            "expected_variant_id": "target_variant",
            "primary_window": "full_2000_2026",
            "summary_csv": "outputs/summary_by_window.csv",
            "daily_curve_csv": "outputs/daily_curve.csv",
            "metadata_json": "outputs/metadata.json",
            "positions_csv": "outputs/positions.csv",
            "trades_csv": "outputs/trades.csv",
            "rankings_csv": "outputs/rankings.csv",
        },
    }


def run_workflow(
    project: Path,
    *,
    approve_demo_actions: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session_dir = project / "state" / "sessions"
    result = run_agent_session(
        task=TASK,
        max_steps=8,
        planner="plan-react",
        provider="mock",
        max_replans=0,
        root=project,
        session_dir=session_dir,
    )
    approvals: list[dict[str, Any]] = []

    while result.get("status") == "waiting_approval":
        action = result.get("state", {}).get("current_action", {})
        action_name = action.get("name") if isinstance(action, dict) else None
        expected = EXPECTED_APPROVALS[len(approvals)] if len(approvals) < len(EXPECTED_APPROVALS) else None
        approved = bool(approve_demo_actions and action_name == expected)
        decision = "approve" if approved else "reject"
        approvals.append(
            {
                "action": action_name,
                "decision": decision,
                "interrupt_count": len(result.get("interrupts", [])),
                "checkpoint_id": result.get("checkpoint_id"),
            }
        )
        print(f"Approval interrupt: {action_name} -> {decision.upper()}")
        result = resume_agent_session(
            session=str(result["session_id"]),
            approve=approved,
            reject=not approved,
            root=project,
            session_dir=session_dir,
        )
        if not approved:
            break
    return result, approvals


def build_report(
    workdir: Path,
    project: Path,
    external_repo: Path,
    result: dict[str, Any],
    approvals: list[dict[str, Any]],
) -> dict[str, Any]:
    actions = [
        str(record.get("action", {}).get("name"))
        for record in result.get("actions", [])
        if isinstance(record.get("action"), dict)
    ]
    project_state = json.loads(
        (project / "state" / "project_state.json").read_text(encoding="utf-8")
    )
    initial_run_id = project_state["latest_verified_run"]["run_id"]
    initial_artifact = build_artifact_contract_inspection(project / "runs" / initial_run_id)
    source_path = external_repo / "research" / "strategy.py"
    source = source_path.read_text(encoding="utf-8")
    run_record = next(
        (
            record
            for record in reversed(result.get("actions", []))
            if record.get("action", {}).get("name") == "run_fresh"
        ),
        {},
    )
    run_result = run_record.get("result", {}) if isinstance(run_record, dict) else {}
    run_dir_value = run_result.get("run_dir") if isinstance(run_result, dict) else None
    run_dir = project / str(run_dir_value) if run_dir_value else None
    artifact = build_artifact_contract_inspection(run_dir) if run_dir and run_dir.exists() else {}
    expected_decisions = [
        {"action": name, "decision": "approve"} for name in EXPECTED_APPROVALS
    ]
    actual_decisions = [
        {"action": item.get("action"), "decision": item.get("decision")} for item in approvals
    ]
    checks = {
        "initial_contract_needs_repair": initial_artifact.get("recommendation")
        == "needs_export_patch",
        "agent_completed": result.get("status") == "complete",
        "required_tool_order": is_ordered_subsequence(REQUIRED_TOOLS, actions),
        "approval_sequence": actual_decisions == expected_decisions,
        "every_approval_interrupted": all(
            item.get("interrupt_count", 0) > 0 for item in approvals
        ),
        "source_patch_applied": all(marker in source for marker in REPAIR_MARKERS),
        "fresh_run_completed": run_result.get("verification_status") == "completed",
        "artifact_contract_satisfied": artifact.get("recommendation") == "satisfied",
    }
    return {
        "success": all(checks.values()),
        "task": TASK,
        "session_id": result.get("session_id"),
        "thread_id": result.get("thread_id"),
        "checkpoint_backend": result.get("checkpoint_backend"),
        "graph_status": result.get("status"),
        "steps": result.get("step"),
        "tools": actions,
        "approvals": approvals,
        "checks": checks,
        "initial_artifact_recommendation": initial_artifact.get("recommendation"),
        "initial_missing_artifacts": [
            item.get("name")
            for item in initial_artifact.get("missing_position_level_artifacts", [])
        ],
        "artifact_recommendation": artifact.get("recommendation"),
        "artifact_checks": artifact.get("checks", []),
        "paths": {
            "workspace": str(workdir),
            "project": str(project),
            "source": str(source_path),
            "session_log": result.get("session_path"),
            "fresh_run": str(run_dir) if run_dir else None,
        },
    }


def is_ordered_subsequence(required: list[str], actual: list[str]) -> bool:
    cursor = iter(actual)
    return all(any(item == expected for item in cursor) for expected in required)


def write_report(workdir: Path, report: dict[str, Any]) -> None:
    write_json(workdir / "demo_report.json", report)
    lines = [
        "# Quant-Agent Repair Demo",
        "",
        f"Result: `{'PASS' if report['success'] else 'FAIL'}`",
        f"Graph status: `{report['graph_status']}`",
        f"Checkpoint backend: `{report['checkpoint_backend']}`",
        (
            "Artifact contract: "
            f"`{report['initial_artifact_recommendation']}` -> "
            f"`{report['artifact_recommendation']}`"
        ),
        "",
        "## Tool sequence",
        "",
        *[f"{index}. `{name}`" for index, name in enumerate(report["tools"], start=1)],
        "",
        "## Approval interrupts",
        "",
        *[
            f"- `{item['action']}`: `{item['decision']}` (interrupts: {item['interrupt_count']})"
            for item in report["approvals"]
        ],
        "",
        "## Deterministic checks",
        "",
        *[
            f"- {'PASS' if passed else 'FAIL'}: `{name}`"
            for name, passed in report["checks"].items()
        ],
        "",
    ]
    (workdir / "demo_report.md").write_text("\n".join(lines), encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_summary(report: dict[str, Any]) -> None:
    print()
    print(f"Demo result: {'PASS' if report['success'] else 'FAIL'}")
    print(f"Agent status: {report['graph_status']}")
    print(
        "Artifact contract: "
        f"{report['initial_artifact_recommendation']} -> "
        f"{report['artifact_recommendation']}"
    )
    print(f"Tool sequence: {' -> '.join(report['tools'])}")
    print(f"Markdown report: {Path(report['paths']['workspace']) / 'demo_report.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
