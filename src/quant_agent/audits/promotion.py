from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.audits.cost_stress import run_cost_stress_audit
from quant_agent.audits.lag_safety import run_lag_safety_audit
from quant_agent.audits.robustness import run_robustness_sweep
from quant_agent.audits.validation_attribution import (
    run_validation_attribution_audit,
    validation_audit_source_exists,
)
from quant_agent.diagnostics.anomaly import diagnose_run
from quant_agent.lineage import strategy_lineage_entry
from quant_agent.metrics import format_metrics_summary, load_metrics


def generate_promotion_report(
    run_dir: Path,
    strategy_name: str | None = None,
    diagnose: bool = False,
    provider: str = "auto",
    model: str | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    audits_dir = run_dir / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)

    prerequisite_paths = ensure_prerequisites(
        run_dir=run_dir,
        diagnose=diagnose,
        provider=provider,
        model=model,
    )
    promotion = build_promotion_audit(run_dir, strategy_name=strategy_name)
    promotion["artifacts"]["lag_safety"] = relative_path(prerequisite_paths["lag_safety"], run_dir)
    promotion["artifacts"]["robustness"] = relative_path(prerequisite_paths["robustness"], run_dir)
    promotion["artifacts"]["cost_stress"] = relative_path(prerequisite_paths["cost_stress"], run_dir)
    if prerequisite_paths.get("diagnosis_json"):
        promotion["artifacts"]["diagnosis_json"] = relative_path(prerequisite_paths["diagnosis_json"], run_dir)
    if prerequisite_paths.get("diagnosis_md"):
        promotion["artifacts"]["diagnosis_md"] = relative_path(prerequisite_paths["diagnosis_md"], run_dir)
    if prerequisite_paths.get("robustness_json"):
        promotion["artifacts"]["robustness_json"] = relative_path(prerequisite_paths["robustness_json"], run_dir)
    if prerequisite_paths.get("validation_attribution"):
        promotion["artifacts"]["validation_attribution"] = relative_path(prerequisite_paths["validation_attribution"], run_dir)
    if prerequisite_paths.get("validation_attribution_json"):
        promotion["artifacts"]["validation_attribution_json"] = relative_path(prerequisite_paths["validation_attribution_json"], run_dir)

    name = safe_component(promotion["strategy_name"])
    json_path = audits_dir / f"PROMOTION_AUDIT_{name}.json"
    md_path = audits_dir / f"PROMOTION_AUDIT_{name}.md"
    promotion["artifacts"]["promotion_json"] = relative_path(json_path, run_dir)
    promotion["artifacts"]["promotion_markdown"] = relative_path(md_path, run_dir)

    json_path.write_text(json.dumps(promotion, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_promotion_markdown(promotion), encoding="utf-8")

    return {
        "promotion": promotion,
        "json_path": json_path,
        "markdown_path": md_path,
    }


def ensure_prerequisites(
    run_dir: Path,
    diagnose: bool,
    provider: str,
    model: str | None,
) -> dict[str, Path]:
    lag_path = run_dir / "audits" / "lag_safety.md"
    robustness_path = run_dir / "audits" / "robustness.md"
    robustness_json = run_dir / "audits" / "robustness.json"
    cost_stress_path = run_dir / "audits" / "cost_stress.md"
    cost_stress_json = run_dir / "audits" / "cost_stress.json"
    validation_path = run_dir / "audits" / "validation_attribution.md"
    validation_json = run_dir / "audits" / "validation_attribution.json"
    if not lag_path.exists():
        lag_path = run_lag_safety_audit(run_dir)
    if not robustness_path.exists() or not robustness_json.exists():
        robustness_path = run_robustness_sweep(run_dir)
        robustness_json = run_dir / "audits" / "robustness.json"
    if not cost_stress_path.exists() or not cost_stress_json.exists():
        cost_stress_path = run_cost_stress_audit(run_dir)
        cost_stress_json = run_dir / "audits" / "cost_stress.json"
    if (not validation_path.exists() or not validation_json.exists()) and validation_audit_source_exists(run_dir):
        validation_path = run_validation_attribution_audit(run_dir)
        validation_json = run_dir / "audits" / "validation_attribution.json"

    paths: dict[str, Path] = {
        "lag_safety": lag_path,
        "robustness": robustness_path,
        "cost_stress": cost_stress_path,
    }
    if robustness_json.exists():
        paths["robustness_json"] = robustness_json
    if validation_path.exists():
        paths["validation_attribution"] = validation_path
    if validation_json.exists():
        paths["validation_attribution_json"] = validation_json
    diagnosis_json = run_dir / "diagnosis.json"
    diagnosis_md = run_dir / "diagnosis.md"
    if diagnose and not diagnosis_json.exists():
        result = diagnose_run(
            run_dir=run_dir,
            provider=provider,
            model=model,
            save=True,
            output_format="both",
        )
        if "json" in result.paths:
            diagnosis_json = result.paths["json"]
        if "markdown" in result.paths:
            diagnosis_md = result.paths["markdown"]
    if diagnosis_json.exists():
        paths["diagnosis_json"] = diagnosis_json
    if diagnosis_md.exists():
        paths["diagnosis_md"] = diagnosis_md
    return paths


def build_promotion_audit(run_dir: Path, strategy_name: str | None = None) -> dict[str, Any]:
    manifest = read_json(run_dir / "manifest.json")
    metrics = load_metrics(run_dir)
    flags = read_json(run_dir / "flags.json", default=[])
    engine_metadata = read_json(run_dir / "engine_metadata.json")
    diagnosis = read_json(run_dir / "diagnosis.json")
    source_metadata = read_json(run_dir / "source_artifacts" / "metadata.json")
    if not source_metadata and isinstance(engine_metadata.get("source_metadata"), dict):
        source_metadata = engine_metadata["source_metadata"]
    robustness = read_json(run_dir / "audits" / "robustness.json")
    validation = read_json(run_dir / "audits" / "validation_attribution.json")
    cost_stress = read_json(run_dir / "audits" / "cost_stress.json")

    resolved_strategy = strategy_name or source_metadata.get("promoted_live_variant_id") or metrics.get("variant_id") or manifest.get("strategy_name") or run_dir.name
    checks = promotion_checks(
        run_dir=run_dir,
        manifest=manifest,
        metrics=metrics,
        flags=flags,
        engine_metadata=engine_metadata,
        diagnosis=diagnosis,
        source_metadata=source_metadata,
        robustness=robustness,
        validation=validation,
        cost_stress=cost_stress,
        strategy_name=str(resolved_strategy),
    )
    recommendation = recommendation_from_checks(checks)
    blocking = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]

    return {
        "run_id": run_dir.name,
        "strategy_name": str(resolved_strategy),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "recommendation": recommendation,
        "core_metrics": core_metrics(metrics),
        "checks": checks,
        "blocking_issues": blocking,
        "warnings": warnings,
        "flags": flags if isinstance(flags, list) else [],
        "diagnosis_findings": diagnosis.get("findings", []) if isinstance(diagnosis, dict) else [],
        "cost_stress": summarize_cost_stress(cost_stress),
        "lineage": strategy_lineage_entry(str(resolved_strategy), path=lineage_path_for_run(run_dir)),
        "next_actions": next_actions(recommendation, blocking, warnings, diagnosis),
        "artifacts": {
            "manifest": "manifest.json",
            "metrics": "metrics.json",
            "flags": "flags.json",
            "engine_metadata": "engine_metadata.json",
            "source_metadata": "source_artifacts/metadata.json",
            "summary_by_window": "source_artifacts/summary_by_window.csv",
        },
    }


def promotion_checks(
    run_dir: Path,
    manifest: dict[str, Any],
    metrics: dict[str, Any],
    flags: Any,
    engine_metadata: dict[str, Any],
    diagnosis: dict[str, Any],
    source_metadata: dict[str, Any],
    robustness: dict[str, Any],
    validation: dict[str, Any],
    cost_stress: dict[str, Any],
    strategy_name: str,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    add_check(
        checks,
        "pass" if manifest else "fail",
        "manifest_present",
        "Manifest is present." if manifest else "Manifest is missing.",
    )
    add_check(
        checks,
        "pass" if all(key in metrics for key in ["cagr", "max_drawdown", "calmar"]) else "fail",
        "core_metrics_present",
        "Core metrics are present." if all(key in metrics for key in ["cagr", "max_drawdown", "calmar"]) else "Core metrics are incomplete.",
    )

    max_drawdown = numeric(metrics.get("max_drawdown"))
    if max_drawdown is None:
        add_check(checks, "fail", "max_drawdown_missing", "Max drawdown is missing.")
    elif max_drawdown <= -0.40:
        add_check(checks, "fail", "max_drawdown_threshold", f"Max drawdown {max_drawdown:.4f} breaches -40% threshold.")
    else:
        add_check(checks, "pass", "max_drawdown_threshold", f"Max drawdown {max_drawdown:.4f} is within threshold.")

    decision_rows = count_csv_rows(run_dir / "decision_log.csv")
    add_check(
        checks,
        "pass" if decision_rows > 0 else "fail",
        "decision_log_present",
        f"Decision log contains {decision_rows} rows." if decision_rows > 0 else "Decision log is missing or empty.",
    )

    subprocess_ran = engine_metadata.get("subprocess_ran")
    exit_code = engine_metadata.get("exit_code")
    if subprocess_ran is True and exit_code == 0:
        add_check(checks, "pass", "fresh_subprocess_succeeded", "Fresh subprocess run succeeded with exit_code=0.")
    elif subprocess_ran is True:
        add_check(checks, "fail", "fresh_subprocess_failed", f"Fresh subprocess exit_code={exit_code}.")
    else:
        add_check(checks, "warn", "fresh_subprocess_not_confirmed", "Run was ingested from existing outputs or subprocess metadata is unavailable.")

    promoted_variant = source_metadata.get("promoted_live_variant_id")
    metrics_variant = metrics.get("variant_id")
    add_check(
        checks,
        "pass" if promoted_variant else "fail",
        "promoted_variant_metadata_present",
        f"promoted_live_variant_id={promoted_variant}." if promoted_variant else "promoted_live_variant_id is missing from source metadata.",
    )
    if promoted_variant and metrics_variant == promoted_variant:
        add_check(checks, "pass", "variant_identity_matches", f"metrics.variant_id matches promoted_live_variant_id={promoted_variant}.")
    elif promoted_variant:
        add_check(checks, "fail", "variant_identity_mismatch", f"metrics.variant_id={metrics_variant} differs from promoted_live_variant_id={promoted_variant}.")
    else:
        add_check(checks, "fail", "variant_identity_unverified", "Cannot verify variant identity without promoted_live_variant_id.")

    add_check(
        checks,
        "pass" if source_metadata.get("generated_at_utc") else "warn",
        "source_generated_at_present",
        f"generated_at_utc={source_metadata.get('generated_at_utc')}." if source_metadata.get("generated_at_utc") else "Source metadata generated_at_utc is missing.",
    )

    current_variant = source_metadata.get("current_variant_id")
    control_variant = source_metadata.get("control_variant_id")
    if current_variant and promoted_variant and current_variant != promoted_variant:
        if control_variant == current_variant:
            add_check(
                checks,
                "warn",
                "source_current_variant_is_control",
                "source current_variant_id differs from promoted variant but matches control_variant_id.",
            )
        else:
            add_check(
                checks,
                "fail",
                "source_current_variant_unexplained",
                "source current_variant_id differs from promoted variant and does not match control_variant_id.",
            )

    if isinstance(diagnosis, dict) and diagnosis.get("findings") is not None:
        add_check(checks, "pass", "diagnosis_present", "Structured diagnosis is present.")
        high_findings = [item for item in diagnosis.get("findings", []) if item.get("severity") == "high"]
        if high_findings:
            add_check(checks, "warn", "diagnosis_high_severity_findings", f"Structured diagnosis contains {len(high_findings)} high severity findings.")
    else:
        add_check(checks, "warn", "diagnosis_missing", "Structured diagnosis is missing; run diagnose --format both for full promotion context.")

    upstream_warnings = [
        item
        for item in flags
        if isinstance(item, dict) and str(item.get("level", "")).upper() in {"WARN", "ERROR", "FAIL"}
    ] if isinstance(flags, list) else []
    if upstream_warnings:
        add_check(checks, "warn", "upstream_flags_present", f"Prior run flags include {len(upstream_warnings)} warning/error items.")

    trade_rows = count_csv_rows(run_dir / "trades.csv")
    position_rows = count_csv_rows(run_dir / "positions.csv")
    if trade_rows == 0 or position_rows == 0:
        add_check(checks, "warn", "trade_position_artifacts_empty", f"trades rows={trade_rows}, positions rows={position_rows}.")
    else:
        add_check(checks, "pass", "trade_position_artifacts_present", f"trades rows={trade_rows}, positions rows={position_rows}.")

    validation_cagr = numeric(metrics.get("validation_2022_2026_ann_return"))
    full_cagr = numeric(metrics.get("full_2000_2026_ann_return") or metrics.get("cagr"))
    if validation_cagr is not None and full_cagr and validation_cagr > 2 * full_cagr:
        add_check(checks, "warn", "validation_cagr_outperformance", f"Validation CAGR {validation_cagr:.4f} is more than 2x full CAGR {full_cagr:.4f}.")
    if isinstance(validation, dict) and validation.get("recommendation"):
        if validation["recommendation"] == "blocked":
            add_check(checks, "fail", "validation_attribution_blocked", "Validation attribution audit has blocking findings.")
        elif validation["recommendation"] == "review_required":
            warn_count = len([item for item in validation.get("checks", []) if item.get("status") == "warn"])
            add_check(checks, "warn", "validation_attribution_review_required", f"Validation attribution audit has {warn_count} warning checks.")
        else:
            add_check(checks, "pass", "validation_attribution_reviewed", "Validation attribution audit passed.")

    if isinstance(cost_stress, dict) and cost_stress.get("recommendation"):
        if cost_stress["recommendation"] == "blocked":
            add_check(checks, "fail", "cost_stress_blocked", "Cost/slippage stress audit has blocking findings.")
        elif cost_stress["recommendation"] == "review_required":
            warn_count = len([item for item in cost_stress.get("checks", []) if item.get("status") == "warn"])
            add_check(checks, "warn", "cost_stress_review_required", f"Cost/slippage stress audit has {warn_count} warning checks.")
        else:
            add_check(checks, "pass", "cost_stress_reviewed", "Cost/slippage stress audit passed.")
    else:
        add_check(checks, "warn", "cost_stress_missing", "Cost/slippage stress audit is missing.")

    lineage = strategy_lineage_entry(strategy_name, path=lineage_path_for_run(run_dir))
    add_check(
        checks,
        "pass" if lineage else "warn",
        "strategy_lineage_present",
        "Strategy lineage entry is present." if lineage else f"No strategy lineage entry found for {strategy_name}.",
    )

    lag_path = run_dir / "audits" / "lag_safety.md"
    robustness_path = run_dir / "audits" / "robustness.md"
    add_check(checks, "pass" if lag_path.exists() else "warn", "lag_audit_present", "Lag-safety audit exists." if lag_path.exists() else "Lag-safety audit is missing.")
    if lag_path.exists() and "WARN:" in read_text(lag_path):
        add_check(checks, "warn", "lag_audit_contains_warnings", "Lag-safety audit contains warnings.")
    add_check(checks, "pass" if robustness_path.exists() else "warn", "robustness_audit_present", "Robustness audit exists." if robustness_path.exists() else "Robustness audit is missing.")
    if isinstance(robustness, dict) and robustness.get("recommendation"):
        if robustness["recommendation"] == "blocked":
            add_check(checks, "fail", "robustness_blocked", "Structured robustness audit has blocking findings.")
        elif robustness["recommendation"] == "review_required":
            warn_count = len([item for item in robustness.get("checks", []) if item.get("status") == "warn"])
            add_check(checks, "warn", "robustness_review_required", f"Structured robustness audit has {warn_count} warning checks.")
        else:
            add_check(checks, "pass", "robustness_reviewed", "Structured robustness audit passed.")
    robustness_text = read_text(robustness_path).lower() if robustness_path.exists() else ""
    if "unknown" in robustness_text or "not tested yet" in robustness_text:
        add_check(checks, "warn", "robustness_placeholder", "Robustness audit is a placeholder; parameter-neighborhood stability is not verified.")

    return checks


def recommendation_from_checks(checks: list[dict[str, str]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "blocked"
    if any(check["status"] == "warn" for check in checks):
        return "review_required"
    return "promote_candidate"


def next_actions(
    recommendation: str,
    blocking: list[dict[str, str]],
    warnings: list[dict[str, str]],
    diagnosis: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if blocking:
        actions.append("Resolve blocking promotion issues before considering live promotion.")
    if any(item["code"] == "diagnosis_missing" for item in warnings):
        actions.append("Run quant-agent diagnose --run <run_id> --format both.")
    if any(item["code"] == "trade_position_artifacts_empty" for item in warnings):
        actions.append("Export per-symbol trades, positions, or rankings if position-level promotion audit is required.")
    if any(item["code"] == "validation_cagr_outperformance" for item in warnings):
        actions.append("Replay high-return validation dates and inspect lagged signal fields.")
    if any(item["code"] == "cost_stress_review_required" for item in warnings):
        actions.append("Review the cost/slippage stress audit before promotion sign-off.")
    if any(item["code"] == "strategy_lineage_present" for item in warnings):
        actions.append("Record strategy lineage before promotion sign-off.")
    for item in diagnosis.get("next_actions", []) if isinstance(diagnosis, dict) else []:
        if isinstance(item, str) and item not in actions:
            actions.append(item)
    if not actions and recommendation == "promote_candidate":
        actions.append("Proceed to human review for promotion sign-off.")
    return actions


def render_promotion_markdown(promotion: dict[str, Any]) -> str:
    lines = [
        f"# Promotion Audit: {promotion['strategy_name']}",
        "",
        f"Run ID: `{promotion['run_id']}`",
        f"Recommendation: `{promotion['recommendation']}`",
        f"Generated at: `{promotion['generated_at']}`",
        "",
        "## Core Metrics",
        "",
        "```text",
        format_metrics_summary(promotion["core_metrics"]),
        "```",
        "",
        "## Checks",
        "",
    ]
    for check in promotion["checks"]:
        lines.append(f"- {check['status'].upper()}: {check['code']} - {check['message']}")

    lines.extend(["", "## Blocking Issues", ""])
    lines.extend(render_issue_list(promotion["blocking_issues"]))
    lines.extend(["", "## Warnings", ""])
    lines.extend(render_issue_list(promotion["warnings"]))

    lines.extend(["", "## Diagnosis Findings", ""])
    findings = promotion.get("diagnosis_findings", [])
    if findings:
        for finding in findings:
            lines.append(f"- [{finding.get('severity', 'unknown')}] {finding.get('title', 'Untitled')} ({finding.get('category', 'unknown')})")
    else:
        lines.append("- No structured diagnosis findings available.")

    lines.extend(["", "## Cost Stress", ""])
    cost_stress = promotion.get("cost_stress", {})
    if cost_stress:
        for scenario in cost_stress.get("scenarios", []):
            lines.append(
                f"- `{scenario.get('name')}`: CAGR `{scenario.get('cagr')}`, "
                f"MaxDD `{scenario.get('max_drawdown')}`, Sharpe `{scenario.get('sharpe')}`"
            )
    else:
        lines.append("- No cost stress summary available.")

    lines.extend(["", "## Strategy Lineage", ""])
    lineage = promotion.get("lineage", {})
    if lineage:
        lines.append(f"- Previous baseline: `{lineage.get('previous_baseline')}`")
        lines.append(f"- Status: `{lineage.get('status')}`")
        if lineage.get("reason"):
            lines.append(f"- Reason: {lineage.get('reason')}")
    else:
        lines.append("- No lineage entry recorded.")

    lines.extend(["", "## Next Actions", ""])
    actions = promotion.get("next_actions", [])
    if actions:
        lines.extend(f"- {action}" for action in actions)
    else:
        lines.append("- None.")

    lines.extend(["", "## Artifacts", ""])
    for name, path in sorted(promotion.get("artifacts", {}).items()):
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def render_issue_list(issues: list[dict[str, str]]) -> list[str]:
    if not issues:
        return ["- None."]
    return [f"- {item['code']}: {item['message']}" for item in issues]


def core_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "cagr",
        "max_drawdown",
        "calmar",
        "sharpe",
        "ann_vol",
        "exposure",
        "turnover_per_year",
        "variant_id",
        "full_2000_2026_ann_return",
        "validation_2022_2026_ann_return",
        "post_2013_ann_return",
    ]
    return {key: metrics[key] for key in keys if key in metrics}


def summarize_cost_stress(cost_stress: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cost_stress, dict) or not cost_stress:
        return {}
    return {
        "recommendation": cost_stress.get("recommendation"),
        "scenarios": [
            {
                "name": item.get("name"),
                "cagr": item.get("cagr"),
                "max_drawdown": item.get("max_drawdown"),
                "sharpe": item.get("sharpe"),
                "total_cost_bps": item.get("total_cost_bps"),
            }
            for item in cost_stress.get("scenarios", [])[:5]
            if isinstance(item, dict)
        ],
    }


def lineage_path_for_run(run_dir: Path) -> Path:
    if run_dir.parent.name == "runs":
        return run_dir.parent.parent / "state" / "strategy_lineage.json"
    return run_dir.parent / "state" / "strategy_lineage.json"


def add_check(checks: list[dict[str, str]], status: str, code: str, message: str) -> None:
    checks.append({"status": status, "code": code, "message": message})


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def safe_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value).strip("_") or "strategy"
