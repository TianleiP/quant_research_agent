from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from quant_agent.agent import resume_agent_session, run_agent_session
from quant_agent.agent.tools import invoke_tool
from quant_agent.audits.artifact_contract import run_artifact_contract_inspection
from quant_agent.audits.cost_stress import run_cost_stress_audit
from quant_agent.audits.docs_consistency import run_docs_consistency_audit
from quant_agent.audits.lag_safety import run_lag_safety_audit
from quant_agent.audits.promotion import generate_promotion_report
from quant_agent.audits.promotion_closure import generate_promotion_closure_report
from quant_agent.audits.robustness import run_robustness_sweep
from quant_agent.audits.validation_attribution import (
    DEFAULT_VALIDATION_START,
    run_validation_attribution_audit,
)
from quant_agent.backtest.adapter import run_backtest
from quant_agent.command_router import plan_command_route, render_route_plan
from quant_agent.config import load_config, with_run_before_ingest
from quant_agent.diagnostics.anomaly import diagnose_run
from quant_agent.discovery import discover_repo, explain_entrypoint
from quant_agent.flags import basic_risk_flags
from quant_agent.fixes import (
    apply_fix_proposal,
    suggest_generalized_fix,
    suggest_missing_export_fix,
    suggest_stale_metadata_fix,
)
from quant_agent.fixes.source_trace import trace_source_file
from quant_agent.memory import (
    build_code_memory,
    find_latest_code_memory_index,
    query_code_memory,
    render_memory_build_summary,
    render_memory_query,
)
from quant_agent.metrics import format_metrics_summary
from quant_agent.lineage import load_lineage, render_lineage, update_strategy_lineage
from quant_agent.registry import latest_run_dir, save_run
from quant_agent.replay.replay import replay_date
from quant_agent.reports import write_short_report
from quant_agent.state_updater import update_project_state


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a strategy backtest from a config file.")
    run_parser.add_argument("--config", required=True, type=Path)
    run_parser.add_argument(
        "--fresh",
        action="store_true",
        help="Run the external subprocess before ingesting output artifacts.",
    )
    run_parser.set_defaults(func=run_command)

    metrics_parser = subparsers.add_parser("metrics", help="View metrics for a run.")
    metrics_parser.add_argument("--run", default="latest")
    metrics_parser.set_defaults(func=metrics_command)

    inspect_parser = subparsers.add_parser("inspect-artifacts", help="Inspect whether a run satisfies quant-agent artifact contracts.")
    inspect_parser.add_argument("--run", default="latest")
    inspect_parser.set_defaults(func=inspect_artifacts_command)

    replay_parser = subparsers.add_parser("replay", help="Replay a strategy decision for a date.")
    replay_parser.add_argument("--run", default="latest")
    replay_parser.add_argument("--date", required=True)
    replay_parser.set_defaults(func=replay_command)

    diagnose_parser = subparsers.add_parser("diagnose", help="Generate an LLM diagnosis for a run.")
    diagnose_parser.add_argument("--run", default="latest")
    diagnose_parser.add_argument(
        "--provider",
        choices=["auto", "mock", "openai", "deepseek"],
        default="auto",
        help="LLM provider. auto uses OpenAI, then DeepSeek, then mock.",
    )
    diagnose_parser.add_argument("--model", help="Override the provider model.")
    diagnose_parser.add_argument(
        "--format",
        choices=["markdown", "json", "both"],
        default="both",
        help="Diagnosis output format. both saves diagnosis.json and diagnosis.md.",
    )
    diagnose_parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print diagnosis without writing diagnosis artifacts.",
    )
    diagnose_parser.set_defaults(func=diagnose_command)

    discover_parser = subparsers.add_parser("discover", help="Build a structural index for a code repo.")
    discover_parser.add_argument("--repo", required=True, type=Path)
    discover_parser.add_argument("--output", type=Path, help="Output directory for discovery artifacts.")
    discover_parser.add_argument(
        "--include-heavy-dirs",
        action="store_true",
        help="Include directories such as data/ and artifacts/.",
    )
    discover_parser.add_argument("--max-file-bytes", type=int, default=500_000)
    discover_parser.add_argument("--max-files", type=int, default=20_000)
    discover_parser.set_defaults(func=discover_command)

    explain_parser = subparsers.add_parser(
        "explain-entrypoint",
        help="Explain a discovered repo entrypoint with an LLM.",
    )
    explain_parser.add_argument("--repo", required=True, type=Path)
    explain_parser.add_argument("--file", required=True, help="File path relative to --repo.")
    explain_parser.add_argument("--discovery-dir", type=Path)
    explain_parser.add_argument(
        "--provider",
        choices=["auto", "mock", "openai", "deepseek"],
        default="auto",
    )
    explain_parser.add_argument("--model", help="Override the provider model.")
    explain_parser.add_argument("--max-source-chars", type=int, default=24_000)
    explain_parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print explanation without writing a report.",
    )
    explain_parser.set_defaults(func=explain_entrypoint_command)

    trace_parser = subparsers.add_parser("trace-source", help="Trace source code around a fix issue without modifying the repo.")
    trace_parser.add_argument("--repo", required=True, type=Path)
    trace_parser.add_argument("--file", required=True, help="File path relative to --repo.")
    trace_parser.add_argument(
        "--issue",
        choices=["missing-position-exports"],
        default="missing-position-exports",
    )
    trace_parser.add_argument("--output", type=Path, help="Output directory for trace artifacts.")
    trace_parser.set_defaults(func=trace_source_command)

    suggest_parser = subparsers.add_parser("suggest-fix", help="Generate a reviewed patch proposal.")
    suggest_parser.add_argument("--repo", required=True, type=Path)
    suggest_parser.add_argument("--file", required=True, help="File path relative to --repo.")
    suggest_parser.add_argument(
        "--issue",
        required=True,
        choices=["stale-metadata", "missing-position-exports", "generalized-fix"],
        help="Fix proposal type.",
    )
    suggest_parser.add_argument(
        "--problem",
        help="Problem statement for generalized-fix proposals.",
    )
    suggest_parser.add_argument(
        "--promoted-variant-id",
        help="Promoted/live variant id to write into metadata.",
    )
    suggest_parser.add_argument("--artifact-report", type=Path, help="Artifact contract report for missing-position-exports.")
    suggest_parser.add_argument(
        "--memory-index",
        type=Path,
        help="Optional code_memory.json to ground the proposal. If omitted, quant-agent uses the latest matching repo index under .code_memory.",
    )
    suggest_parser.add_argument(
        "--memory-query",
        help="Optional override query for proposal source-memory retrieval.",
    )
    suggest_parser.add_argument("--output", type=Path, help="Output directory for the proposal.")
    suggest_parser.add_argument(
        "--provider",
        choices=["auto", "mock", "openai", "deepseek"],
        default="auto",
        help="LLM provider used only with generalized-fix.",
    )
    suggest_parser.add_argument("--model", help="Provider model used only with generalized-fix.")
    suggest_parser.set_defaults(func=suggest_fix_command)

    apply_parser = subparsers.add_parser("apply-fix", help="Apply a reviewed patch proposal.")
    apply_parser.add_argument("--proposal", required=True, type=Path)
    apply_parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually modify the target repo. Without this flag, performs a dry run.",
    )
    apply_parser.set_defaults(func=apply_fix_command)

    audit_parser = subparsers.add_parser("audit", help="Run a manual audit.")
    audit_subparsers = audit_parser.add_subparsers(dest="audit_command", required=True)

    lag_parser = audit_subparsers.add_parser("lag", help="Run lag-safety audit.")
    lag_parser.add_argument("--run", default="latest")
    lag_parser.set_defaults(func=lag_audit_command)

    robustness_parser = audit_subparsers.add_parser("robustness", help="Run robustness sweep.")
    robustness_parser.add_argument("--run", default="latest")
    robustness_parser.set_defaults(func=robustness_command)

    validation_parser = audit_subparsers.add_parser("validation", help="Attribute validation-window performance.")
    validation_parser.add_argument("--run", default="latest")
    validation_parser.add_argument("--start-date", default=DEFAULT_VALIDATION_START)
    validation_parser.add_argument("--end-date")
    validation_parser.add_argument("--top-days", type=int, default=10)
    validation_parser.set_defaults(func=validation_command)

    costs_parser = audit_subparsers.add_parser("costs", help="Run transaction-cost and slippage stress audit.")
    costs_parser.add_argument("--run", default="latest")
    costs_parser.set_defaults(func=costs_command)

    docs_parser = audit_subparsers.add_parser("docs", help="Check documentation consistency.")
    docs_parser.add_argument("--run", default="latest")
    docs_parser.set_defaults(func=docs_command)

    promote_parser = subparsers.add_parser("promote", help="Generate a promotion report.")
    promote_parser.add_argument("--run", default="latest")
    promote_parser.add_argument("--name")
    promote_parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Generate structured diagnosis first if diagnosis.json is missing.",
    )
    promote_parser.add_argument(
        "--provider",
        choices=["auto", "mock", "openai", "deepseek"],
        default="auto",
        help="LLM provider used only with --diagnose.",
    )
    promote_parser.add_argument("--model", help="Provider model used only with --diagnose.")
    promote_parser.set_defaults(func=promote_command)

    close_promotion_parser = subparsers.add_parser("close-promotion", help="Classify and close promotion warnings.")
    close_promotion_parser.add_argument("--run", default="latest")
    close_promotion_parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Generate structured diagnosis first if diagnosis.json is missing.",
    )
    close_promotion_parser.add_argument(
        "--provider",
        choices=["auto", "mock", "openai", "deepseek"],
        default="auto",
        help="LLM provider used only with --diagnose.",
    )
    close_promotion_parser.add_argument("--model", help="Provider model used only with --diagnose.")
    close_promotion_parser.set_defaults(func=close_promotion_command)

    agent_parser = subparsers.add_parser("agent-session", help="Run a small LangGraph agent session.")
    agent_parser.add_argument("--task", required=True, help="Natural-language task for the agent loop.")
    agent_parser.add_argument("--max-steps", type=int, default=6)
    agent_parser.add_argument("--max-replans", type=int, default=2)
    agent_parser.add_argument(
        "--planner",
        choices=["rule", "llm", "react", "plan-react"],
        default="rule",
        help="Planner implementation. plan-react adds a persisted structured plan and bounded evaluation/replanning.",
    )
    agent_parser.add_argument(
        "--provider",
        choices=["auto", "mock", "openai", "deepseek"],
        default="auto",
        help="LLM provider used with --planner llm, react, or plan-react.",
    )
    agent_parser.add_argument("--model", help="Provider model used with --planner llm, react, or plan-react.")
    agent_parser.add_argument(
        "--checkpoint-backend",
        choices=["sqlite", "postgres"],
        help="Checkpoint backend. Defaults to QUANT_AGENT_CHECKPOINT_BACKEND or sqlite.",
    )
    agent_parser.set_defaults(func=agent_session_command)

    resume_parser = subparsers.add_parser("agent-resume", help="Approve or reject a paused agent session.")
    resume_parser.add_argument(
        "--session",
        required=True,
        help="Persistent thread/session id or path to a readable <session>.state.json snapshot.",
    )
    resume_group = resume_parser.add_mutually_exclusive_group(required=True)
    resume_group.add_argument("--approve", action="store_true", help="Approve and execute the pending action.")
    resume_group.add_argument("--reject", action="store_true", help="Reject the pending action and end the session.")
    resume_parser.add_argument(
        "--checkpoint-backend",
        choices=["sqlite", "postgres"],
        help="Checkpoint backend override. PostgreSQL connections come from the environment or .env.",
    )
    resume_parser.set_defaults(func=agent_resume_command)

    state_parser = subparsers.add_parser("update-state", help="Refresh state/project_state.json from current artifacts.")
    state_parser.add_argument("--run", default="latest", help="Run id to summarize, or latest.")
    state_parser.add_argument("--state", type=Path, default=Path("state/project_state.json"))
    state_parser.add_argument(
        "--test-status",
        help='Optional test status string to record, for example "54 passed".',
    )
    state_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the updated state summary without writing state/project_state.json.",
    )
    state_parser.set_defaults(func=update_state_command)

    ask_parser = subparsers.add_parser("ask", help="Route a natural-language request to a quant-agent command.")
    ask_parser.add_argument("request", nargs="*", help="Natural-language request.")
    ask_parser.add_argument("--task", help="Natural-language request, alternative to positional text.")
    ask_parser.add_argument("--run", default="latest", help="Run id to use for run-scoped commands.")
    ask_parser.add_argument(
        "--provider",
        choices=["auto", "mock", "openai", "deepseek"],
        default="auto",
        help="Provider for routed LLM/agent-session commands.",
    )
    ask_parser.add_argument(
        "--planner",
        choices=["rule", "llm", "react", "plan-react"],
        default="llm",
        help="Planner for routed agent-session commands.",
    )
    ask_parser.add_argument("--max-steps", type=int, default=6, help="Max steps for routed agent-session commands.")
    ask_parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the routed command. Without this flag, only prints the route plan.",
    )
    ask_parser.set_defaults(func=ask_command)

    lineage_parser = subparsers.add_parser("lineage", help="View or update strategy lineage.")
    lineage_subparsers = lineage_parser.add_subparsers(dest="lineage_command", required=True)

    lineage_show = lineage_subparsers.add_parser("show", help="Show recorded strategy lineage.")
    lineage_show.set_defaults(func=lineage_show_command)

    lineage_update = lineage_subparsers.add_parser("update", help="Create or update a strategy lineage entry.")
    lineage_update.add_argument("--strategy", required=True)
    lineage_update.add_argument("--previous")
    lineage_update.add_argument("--reason")
    lineage_update.add_argument("--changed-rule", action="append", default=[])
    lineage_update.add_argument("--inherited-rule", action="append", default=[])
    lineage_update.add_argument("--removed-rule", action="append", default=[])
    lineage_update.add_argument("--audit", action="append", default=[])
    lineage_update.add_argument("--status", default="candidate")
    lineage_update.set_defaults(func=lineage_update_command)

    memory_parser = subparsers.add_parser("memory", help="Build or query local codebase memory.")
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command", required=True)

    memory_build = memory_subparsers.add_parser("build", help="Build a local searchable code memory index.")
    memory_build.add_argument("--repo", required=True, type=Path)
    memory_build.add_argument("--output", type=Path, help="Output directory for code memory artifacts.")
    memory_build.add_argument("--include-heavy-dirs", action="store_true")
    memory_build.add_argument("--max-file-bytes", type=int, default=500_000)
    memory_build.add_argument("--max-files", type=int, default=20_000)
    memory_build.add_argument("--chunk-lines", type=int, default=80)
    memory_build.add_argument("--overlap-lines", type=int, default=20)
    memory_build.add_argument("--vector-size", type=int, default=512)
    memory_build.add_argument(
        "--embedding-provider",
        choices=["none", "auto", "openai"],
        default="none",
        help="Optional semantic embedding provider. Use openai for a true hybrid index.",
    )
    memory_build.add_argument("--embedding-model", help="Embedding model override.")
    memory_build.add_argument("--embedding-dimensions", type=int, default=256)
    memory_build.set_defaults(func=memory_build_command)

    memory_query = memory_subparsers.add_parser("query", help="Query a local code memory index.")
    memory_query.add_argument("--index", required=True, type=Path, help="Path to code_memory.json.")
    memory_query.add_argument("--query", required=True, help="Natural-language or code search query.")
    memory_query.add_argument("--top-k", type=int, default=8)
    memory_query.add_argument("--max-excerpt-chars", type=int, default=900)
    memory_query.add_argument(
        "--embedding-provider",
        choices=["auto", "none", "openai"],
        default="auto",
        help="How to embed the query. Auto uses the index provider when credentials are available.",
    )
    memory_query.add_argument("--embedding-model", help="Embedding model override.")
    memory_query.add_argument("--embedding-dimensions", type=int)
    memory_query.set_defaults(func=memory_query_command)

    return parser


def run_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.fresh:
        config = with_run_before_ingest(config, True)
    result = run_backtest(config)
    flags = basic_risk_flags(result)
    run_dir, manifest = save_run(config, result, flags=flags)
    write_short_report(run_dir, manifest.metrics, flags, manifest.artifacts)

    print(f"Run saved: {manifest.run_id}")
    print()
    print(format_metrics_summary(manifest.metrics))
    print()
    print("Flags:")
    for flag in flags:
        print(f"- {flag['level']}: {flag['message']}")
    print()
    print("Next actions:")
    print("[1] quant-agent metrics --run latest")
    print("[2] quant-agent audit lag --run latest")
    print("[3] quant-agent audit robustness --run latest")
    print("[4] quant-agent replay --run latest --date YYYY-MM-DD")
    print("[5] quant-agent audit validation --run latest")
    print("[6] quant-agent inspect-artifacts --run latest")
    print("[7] quant-agent audit docs --run latest")
    print("[8] quant-agent audit costs --run latest")
    print("[9] quant-agent promote --run latest")
    return 0


def metrics_command(args: argparse.Namespace) -> int:
    result = invoke_tool(
        "metrics_latest",
        {"run": args.run},
        root=Path.cwd().resolve(),
        rationale="CLI metrics command.",
    )
    print(f"Run: {result['run_id']}")
    print(format_metrics_summary(result["metrics"]))
    return 0


def inspect_artifacts_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    report_path = run_artifact_contract_inspection(run_dir)
    print(f"Artifact contract inspection written: {report_path}")
    return 0


def replay_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    print(replay_date(run_dir, args.date))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    result = diagnose_run(
        run_dir,
        provider=args.provider,
        model=args.model,
        output_format=args.format,
        save=not args.no_save,
    )
    print(result.text)
    if result.paths:
        print()
        for name, path in sorted(result.paths.items()):
            print(f"Diagnosis {name} written: {path}")
    return 0


def discover_command(args: argparse.Namespace) -> int:
    result = discover_repo(
        repo=args.repo,
        output_dir=args.output,
        include_heavy_dirs=args.include_heavy_dirs,
        max_file_bytes=args.max_file_bytes,
        max_files=args.max_files,
    )
    summary = result["repo_index"]["summary"]
    print(f"Discovery written: {result['output_dir']}")
    print(f"Indexed files: {summary['indexed_files']}")
    print(f"Python files: {summary['python_files']}")
    print(f"Candidate entrypoints: {summary['entrypoints']}")
    print(f"Artifact references: {summary['artifact_references']}")
    return 0


def explain_entrypoint_command(args: argparse.Namespace) -> int:
    explanation, report_path = explain_entrypoint(
        repo=args.repo,
        file_path=args.file,
        discovery_dir=args.discovery_dir,
        provider=args.provider,
        model=args.model,
        save=not args.no_save,
        max_source_chars=args.max_source_chars,
    )
    print(explanation)
    if report_path:
        print()
        print(f"Entrypoint explanation written: {report_path}")
    return 0


def trace_source_command(args: argparse.Namespace) -> int:
    result = trace_source_file(
        repo=args.repo,
        file_path=args.file,
        issue=args.issue,
        output_dir=args.output,
    )
    readiness_payload = result["trace"]["patch_readiness"]
    print(f"Source trace written: {result['markdown_path']}")
    print(f"Source trace JSON written: {result['json_path']}")
    print(f"Patch readiness: {readiness_payload['status']}")
    print(f"Reason: {readiness_payload['reason']}")
    return 0


def suggest_fix_command(args: argparse.Namespace) -> int:
    memory_index = args.memory_index or find_latest_code_memory_index(Path.cwd(), repo=args.repo)
    if args.issue == "stale-metadata":
        if not args.promoted_variant_id:
            raise ValueError("--promoted-variant-id is required for stale-metadata proposals.")
        result = suggest_stale_metadata_fix(
            repo=args.repo,
            file_path=args.file,
            promoted_variant_id=args.promoted_variant_id,
            output_dir=args.output,
            memory_index=memory_index,
            memory_query=args.memory_query,
        )
    elif args.issue == "missing-position-exports":
        result = suggest_missing_export_fix(
            repo=args.repo,
            file_path=args.file,
            artifact_report=args.artifact_report,
            output_dir=args.output,
            memory_index=memory_index,
            memory_query=args.memory_query,
        )
    elif args.issue == "generalized-fix":
        result = suggest_generalized_fix(
            repo=args.repo,
            file_path=args.file,
            problem=args.problem or args.memory_query or "Draft a generalized fix proposal for this source file.",
            output_dir=args.output,
            memory_index=memory_index,
            memory_query=args.memory_query,
            provider=args.provider,
            model=args.model,
        )
    else:
        raise ValueError(f"Unsupported issue: {args.issue}")
    proposal = result["proposal"]
    print(f"Fix proposal written: {result['proposal_dir']}")
    if result.get("patch_path"):
        print(f"Patch: {result['patch_path']}")
    elif result.get("candidate_patch_path"):
        print(f"Candidate patch: {result['candidate_patch_path']}")
    elif proposal.get("apply_supported") is False:
        print("Patch: not generated; targeted source review required")
    if result.get("draft_json_path"):
        print(f"LLM draft: {result['draft_json_path']}")
    print(f"Metadata: {result['metadata_path']}")
    print(f"Issue: {proposal['issue']}")
    print(f"Risk level: {proposal['risk_level']}")
    memory_context = proposal.get("memory_context", {})
    if isinstance(memory_context, dict):
        print(f"Memory context: {memory_context.get('status')} ({memory_context.get('result_count', 0)} results)")
    print(f"Quality gate: {proposal.get('quality_gate', {}).get('status')}")
    print("Status: proposed")
    print("Manual review required: true")
    return 0


def apply_fix_command(args: argparse.Namespace) -> int:
    result = apply_fix_proposal(args.proposal, yes=args.yes)
    mode = "applied" if result["applied"] else "dry-run"
    print(f"Apply fix result: {mode}")
    print(f"Target: {result['target_path']}")
    print(f"Would change: {str(result['would_change']).lower()}")
    print(f"Applied: {str(result['applied']).lower()}")
    print(f"Result metadata: {result['result_path']}")
    if not args.yes:
        print("No files were modified. Re-run with --yes to apply.")
    return 0


def lag_audit_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    report_path = run_lag_safety_audit(run_dir)
    print(f"Lag-safety audit written: {report_path}")
    return 0


def robustness_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    report_path = run_robustness_sweep(run_dir)
    print(f"Robustness sweep written: {report_path}")
    return 0


def validation_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    report_path = run_validation_attribution_audit(
        run_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        top_days=args.top_days,
    )
    print(f"Validation attribution audit written: {report_path}")
    return 0


def costs_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    report_path = run_cost_stress_audit(run_dir)
    print(f"Cost/slippage stress audit written: {report_path}")
    return 0


def docs_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    report_path = run_docs_consistency_audit(run_dir)
    print(f"Documentation consistency audit written: {report_path}")
    return 0


def promote_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    result = generate_promotion_report(
        run_dir,
        strategy_name=args.name,
        diagnose=args.diagnose,
        provider=args.provider,
        model=args.model,
    )
    promotion = result["promotion"]
    print(f"Promotion recommendation: {promotion['recommendation']}")
    print(f"Promotion JSON written: {result['json_path']}")
    print(f"Promotion report written: {result['markdown_path']}")
    return 0


def close_promotion_command(args: argparse.Namespace) -> int:
    run_dir = resolve_run_arg(args.run)
    result = generate_promotion_closure_report(
        run_dir,
        diagnose=args.diagnose,
        provider=args.provider,
        model=args.model,
    )
    closure = result["closure"]
    print(f"Promotion recommendation: {closure['promotion_recommendation']}")
    print(f"Closure recommendation: {closure['closure_recommendation']}")
    print(f"Closure JSON written: {result['json_path']}")
    print(f"Closure report written: {result['markdown_path']}")
    return 0


def agent_session_command(args: argparse.Namespace) -> int:
    result = run_agent_session(
        task=args.task,
        max_steps=args.max_steps,
        planner=args.planner,
        provider=args.provider,
        model=args.model,
        max_replans=args.max_replans,
        checkpoint_backend=args.checkpoint_backend,
    )
    print(f"Agent session: {result['session_id']}")
    print(f"Thread: {result['thread_id']}")
    print(f"Status: {result['status']}")
    print(f"Steps: {result['step']}")
    print(f"Session log: {result['session_path']}")
    print_checkpoint_summary(result)
    print_agent_plan(result)
    print()
    print("Actions:")
    for record in result["actions"]:
        action = record.get("action", {})
        print(f"- {record.get('status')}: {action.get('name')}")
    if result.get("errors"):
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result.get("final_message"):
        print()
        print(result["final_message"])
    return 0 if result["status"] in {"complete", "waiting_approval"} else 1


def agent_resume_command(args: argparse.Namespace) -> int:
    result = resume_agent_session(
        session=args.session,
        approve=args.approve,
        reject=args.reject,
        checkpoint_backend=args.checkpoint_backend,
    )
    print(f"Agent session: {result['session_id']}")
    print(f"Thread: {result['thread_id']}")
    print(f"Status: {result['status']}")
    print(f"Steps: {result['step']}")
    print(f"Session log: {result['session_path']}")
    print(f"State: {result['state_path']}")
    print_checkpoint_summary(result)
    if result.get("approval_path"):
        print(f"Approval: {result['approval_path']}")
    print_agent_plan(result)
    print()
    print("Actions:")
    for record in result["actions"]:
        action = record.get("action", {})
        approved = " approved" if record.get("approved") else ""
        print(f"- {record.get('status')}{approved}: {action.get('name')}")
    if result.get("errors"):
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result.get("final_message"):
        print()
        print(result["final_message"])
    return 0 if result["status"] == "complete" else 1


def print_checkpoint_summary(result: dict) -> None:
    backend = result.get("checkpoint_backend") or "sqlite"
    print(f"Checkpoint backend: {backend}")
    if result.get("checkpoint_path"):
        print(f"Checkpoint DB: {result['checkpoint_path']}")
    elif result.get("checkpoint_reference"):
        print(f"Checkpoint store: {result['checkpoint_reference']}")


def print_agent_plan(result: dict) -> None:
    plan = result.get("plan")
    if not isinstance(plan, dict) or not plan:
        return
    print(f"Plan: v{plan.get('version')} {plan.get('status')}")
    for step in plan.get("steps", []):
        if not isinstance(step, dict):
            continue
        tools = ", ".join(str(name) for name in step.get("tool_names", []))
        print(f"- [{step.get('status')}] {step.get('title')} ({tools})")
    evaluations = result.get("evaluation_history", [])
    print(f"Evaluations: {len(evaluations)}; replans: {result.get('replan_count', 0)}")


def update_state_command(args: argparse.Namespace) -> int:
    state = update_project_state(
        state_path=args.state,
        run=args.run,
        test_status=args.test_status,
        write=not args.dry_run,
    )
    latest = state.get("latest_verified_run", {})
    promotion = latest.get("promotion", {}) if isinstance(latest, dict) else {}
    position_artifacts = latest.get("position_level_artifacts", {}) if isinstance(latest, dict) else {}
    mode = "computed" if args.dry_run else "updated"
    print(f"Project state {mode}: {args.state}")
    if latest:
        print(f"Latest run: {latest.get('run_id')}")
        print(f"Artifact contract: {position_artifacts.get('artifact_contract_recommendation')}")
        print(f"Promotion: {promotion.get('recommendation')}")
    environment = state.get("environment", {})
    if environment.get("latest_test_status"):
        print(f"Test status: {environment['latest_test_status']}")
    return 0


def ask_command(args: argparse.Namespace) -> int:
    task = args.task or " ".join(args.request)
    plan = plan_command_route(
        task,
        run=args.run,
        provider=args.provider,
        planner=args.planner,
        max_steps=args.max_steps,
    )
    print(render_route_plan(plan))
    if not args.execute:
        return 0 if plan.status == "planned" else 1
    if plan.status != "planned" or not plan.command:
        print()
        print("Execution skipped because the route plan is incomplete.")
        return 1
    print()
    print("Executing routed command:")
    print()
    return main(plan.command[1:])


def lineage_show_command(args: argparse.Namespace) -> int:
    print(render_lineage(load_lineage()))
    return 0


def lineage_update_command(args: argparse.Namespace) -> int:
    entry = update_strategy_lineage(
        strategy=args.strategy,
        previous=args.previous,
        reason=args.reason,
        changed_rules=args.changed_rule,
        inherited_rules=args.inherited_rule,
        removed_rules=args.removed_rule,
        audit_links=args.audit,
        status=args.status,
    )
    print(f"Lineage updated: {entry['strategy']}")
    if entry.get("previous_baseline"):
        print(f"Previous: {entry['previous_baseline']}")
    print(f"Status: {entry.get('status')}")
    return 0


def memory_build_command(args: argparse.Namespace) -> int:
    result = build_code_memory(
        repo=args.repo,
        output_dir=args.output,
        include_heavy_dirs=args.include_heavy_dirs,
        max_file_bytes=args.max_file_bytes,
        max_files=args.max_files,
        chunk_lines=args.chunk_lines,
        overlap_lines=args.overlap_lines,
        vector_size=args.vector_size,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        embedding_dimensions=args.embedding_dimensions,
    )
    print(render_memory_build_summary(result["index"], Path(result["index_path"])))
    print(f"Code memory written: {result['index_path']}")
    return 0


def memory_query_command(args: argparse.Namespace) -> int:
    result = query_code_memory(
        index=args.index,
        query=args.query,
        top_k=args.top_k,
        max_excerpt_chars=args.max_excerpt_chars,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        embedding_dimensions=args.embedding_dimensions,
    )
    print(render_memory_query(result))
    return 0


def resolve_run_arg(run: str) -> Path:
    if run == "latest":
        return latest_run_dir()
    return Path("runs") / run


if __name__ == "__main__":
    raise SystemExit(main())
