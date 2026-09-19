from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.audits.promotion import generate_promotion_report
from quant_agent.metrics import load_metrics


def generate_promotion_closure_report(
    run_dir: Path,
    *,
    diagnose: bool = False,
    provider: str = "auto",
    model: str | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    promotion_result = generate_promotion_report(
        run_dir,
        diagnose=diagnose,
        provider=provider,
        model=model,
    )
    closure = build_promotion_closure(run_dir, promotion_result["promotion"])
    audits_dir = run_dir / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    json_path = audits_dir / "promotion_closure.json"
    markdown_path = audits_dir / "promotion_closure.md"
    closure["artifacts"]["promotion_closure_json"] = relative_path(json_path, run_dir)
    closure["artifacts"]["promotion_closure_markdown"] = relative_path(markdown_path, run_dir)
    json_path.write_text(json.dumps(closure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_promotion_closure_markdown(closure), encoding="utf-8")
    return {
        "closure": closure,
        "json_path": json_path,
        "markdown_path": markdown_path,
        "promotion_json_path": promotion_result["json_path"],
        "promotion_markdown_path": promotion_result["markdown_path"],
    }


def build_promotion_closure(run_dir: Path, promotion: dict[str, Any]) -> dict[str, Any]:
    diagnosis = read_json(run_dir / "diagnosis.json")
    cost_stress = read_json(run_dir / "audits" / "cost_stress.json")
    robustness = read_json(run_dir / "audits" / "robustness.json")
    validation = read_json(run_dir / "audits" / "validation_attribution.json")
    flags = read_json(run_dir / "flags.json", default=[])
    source_metadata = read_json(run_dir / "source_artifacts" / "metadata.json")
    metrics = load_metrics(run_dir)

    items = [
        classify_check(
            check,
            run_dir=run_dir,
            promotion=promotion,
            diagnosis=diagnosis,
            cost_stress=cost_stress,
            robustness=robustness,
            validation=validation,
            flags=flags,
            source_metadata=source_metadata,
            metrics=metrics,
        )
        for check in promotion.get("blocking_issues", [])
        if isinstance(check, dict)
    ]
    items.extend(
        classify_check(
            check,
            run_dir=run_dir,
            promotion=promotion,
            diagnosis=diagnosis,
            cost_stress=cost_stress,
            robustness=robustness,
            validation=validation,
            flags=flags,
            source_metadata=source_metadata,
            metrics=metrics,
        )
        for check in promotion.get("warnings", [])
        if isinstance(check, dict)
    )

    already_closed = closed_evidence_items(promotion, diagnosis)
    recommendation = closure_recommendation(items)
    return {
        "run_id": run_dir.name,
        "strategy_name": promotion.get("strategy_name") or metrics.get("variant_id") or run_dir.name,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "promotion_recommendation": promotion.get("recommendation"),
        "closure_recommendation": recommendation,
        "summary": status_counts(items),
        "items": items,
        "already_closed": already_closed,
        "next_actions": closure_next_actions(items, recommendation),
        "artifacts": {
            "promotion_json": promotion.get("artifacts", {}).get("promotion_json"),
            "promotion_markdown": promotion.get("artifacts", {}).get("promotion_markdown"),
            "diagnosis_json": "diagnosis.json" if (run_dir / "diagnosis.json").exists() else None,
            "cost_stress_json": "audits/cost_stress.json" if (run_dir / "audits" / "cost_stress.json").exists() else None,
            "robustness_json": "audits/robustness.json" if (run_dir / "audits" / "robustness.json").exists() else None,
            "validation_attribution_json": "audits/validation_attribution.json" if (run_dir / "audits" / "validation_attribution.json").exists() else None,
        },
    }


def classify_check(
    check: dict[str, Any],
    *,
    run_dir: Path,
    promotion: dict[str, Any],
    diagnosis: dict[str, Any],
    cost_stress: dict[str, Any],
    robustness: dict[str, Any],
    validation: dict[str, Any],
    flags: Any,
    source_metadata: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    code = str(check.get("code") or "unknown")
    level = str(check.get("status") or "warn")
    if level == "fail":
        return closure_item(
            code,
            "blocked",
            check.get("message"),
            "Blocking promotion check must be fixed before promotion can close.",
            "Fix the failed promotion check and regenerate the promotion report.",
            severity="blocking",
        )

    if code == "diagnosis_missing":
        if isinstance(diagnosis, dict) and diagnosis.get("findings") is not None:
            return closure_item(
                code,
                "closed",
                check.get("message"),
                "diagnosis.json is present and structured.",
                "No action required for diagnosis.",
                evidence=["diagnosis.json"],
            )
        return closure_item(
            code,
            "needs_action",
            check.get("message"),
            "Structured diagnosis has not been attached to this run.",
            f"Run quant-agent close-promotion --run {run_dir.name} --diagnose --provider mock.",
        )

    if code == "source_current_variant_is_control":
        strategy_name = str(promotion.get("strategy_name") or "")
        lineage = promotion.get("lineage", {})
        promoted = source_metadata.get("promoted_live_variant_id")
        metrics_variant = metrics.get("variant_id")
        if lineage and promoted == strategy_name and metrics_variant == strategy_name:
            return closure_item(
                code,
                "closed",
                check.get("message"),
                "The source still labels the control as current, but promoted_live_variant_id, metrics.variant_id, and lineage agree on the promoted variant.",
                "Keep trusting promoted_live_variant_id instead of source current_variant_id; consider cleaning source metadata later.",
                evidence=[
                    f"promoted_live_variant_id={promoted}",
                    f"metrics.variant_id={metrics_variant}",
                    f"lineage.previous_baseline={lineage.get('previous_baseline')}",
                ],
            )
        return closure_item(
            code,
            "needs_action",
            check.get("message"),
            "Variant identity is not fully documented by source metadata and lineage.",
            "Record lineage and/or patch source metadata so promoted_live_variant_id is explicit.",
        )

    if code == "strategy_lineage_present":
        if promotion.get("lineage"):
            return closure_item(
                code,
                "closed",
                check.get("message"),
                "Strategy lineage is recorded.",
                "No action required for lineage.",
                evidence=["state/strategy_lineage.json"],
            )
        return closure_item(
            code,
            "needs_action",
            check.get("message"),
            "No strategy lineage entry exists for this candidate.",
            "Run quant-agent lineage update with previous baseline, changed rules, and audit link.",
        )

    if code == "upstream_flags_present":
        flag_codes = [
            str(item.get("code") or item.get("message"))
            for item in flags
            if isinstance(item, dict) and str(item.get("level", "")).upper() in {"WARN", "ERROR", "FAIL"}
        ]
        return closure_item(
            code,
            "needs_decision",
            check.get("message"),
            "Prior run flags remain warning-level evidence that needs human sign-off.",
            "Review the flags and either accept them in the promotion notes or change the strategy/config.",
            evidence=flag_codes[:8],
        )

    if code == "validation_cagr_outperformance":
        return closure_item(
            code,
            "needs_decision",
            check.get("message"),
            "Validation CAGR is much stronger than full-window CAGR, which can indicate regime dependence or overfit risk.",
            "Use validation attribution and date replay to decide whether this is acceptable or needs a strategy change.",
            evidence=validation_evidence(validation, metrics),
        )

    if code == "validation_attribution_review_required":
        return closure_item(
            code,
            "needs_decision",
            check.get("message"),
            "Validation attribution has warning checks.",
            "Review validation_attribution.md and document whether the concentration is acceptable.",
            evidence=audit_warning_codes(validation),
        )

    if code == "cost_stress_review_required":
        return closure_item(
            code,
            "needs_decision",
            check.get("message"),
            "Cost/slippage stress remains acceptable on return but breaches the stressed drawdown threshold.",
            "Decide whether to reduce exposure/turnover, change cost assumptions, or explicitly accept this stressed drawdown.",
            evidence=cost_stress_evidence(cost_stress),
        )

    if code == "robustness_review_required":
        return closure_item(
            code,
            "needs_decision",
            check.get("message"),
            "Robustness audit has warning checks.",
            "Review robustness.md and document whether neighborhood sensitivity is acceptable.",
            evidence=audit_warning_codes(robustness),
        )

    if code == "diagnosis_high_severity_findings":
        return closure_item(
            code,
            "needs_decision",
            check.get("message"),
            "Structured diagnosis contains high-severity findings.",
            "Resolve or explicitly accept each high-severity diagnosis finding before promotion.",
            evidence=diagnosis_finding_titles(diagnosis, severity="high"),
            severity="high",
        )

    if code == "trade_position_artifacts_empty":
        return closure_item(
            code,
            "needs_action",
            check.get("message"),
            "Position-level artifacts are needed for a full replayable promotion review.",
            "Export positions.csv, trades.csv, and rankings artifacts, then re-run quant-agent.",
        )

    return closure_item(
        code,
        "needs_decision",
        check.get("message"),
        "Promotion warning is not automatically closable.",
        "Review the referenced audit evidence and record an explicit human decision.",
    )


def closed_evidence_items(promotion: dict[str, Any], diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    checks = promotion.get("checks", [])
    pass_codes = {item.get("code") for item in checks if isinstance(item, dict) and item.get("status") == "pass"}
    closed = []
    if "diagnosis_present" in pass_codes:
        closed.append({"code": "diagnosis_present", "status": "closed", "reason": "Structured diagnosis is attached."})
    if "strategy_lineage_present" in pass_codes:
        closed.append({"code": "strategy_lineage_present", "status": "closed", "reason": "Strategy lineage is recorded."})
    if "cost_stress_reviewed" in pass_codes:
        closed.append({"code": "cost_stress_reviewed", "status": "closed", "reason": "Cost/slippage stress audit passed."})
    if isinstance(diagnosis, dict) and diagnosis.get("findings") == []:
        closed.append({"code": "diagnosis_no_findings", "status": "closed", "reason": "Diagnosis returned no findings."})
    return closed


def closure_item(
    code: str,
    status: str,
    source_message: Any,
    rationale: str,
    action: str,
    *,
    evidence: list[Any] | None = None,
    severity: str = "warning",
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "severity": severity,
        "source_message": str(source_message or ""),
        "rationale": rationale,
        "recommended_action": action,
        "evidence": evidence or [],
    }


def closure_recommendation(items: list[dict[str, Any]]) -> str:
    statuses = {item.get("status") for item in items}
    if "blocked" in statuses:
        return "blocked"
    if "needs_action" in statuses:
        return "needs_action"
    if "needs_decision" in statuses:
        return "needs_human_decision"
    return "closed"


def closure_next_actions(items: list[dict[str, Any]], recommendation: str) -> list[str]:
    if recommendation == "closed":
        return ["All promotion warnings are closed by current evidence; proceed to human sign-off."]
    actions = []
    for item in items:
        if item.get("status") in {"blocked", "needs_action", "needs_decision"}:
            action = item.get("recommended_action")
            if isinstance(action, str) and action not in actions:
                actions.append(action)
    return actions


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"blocked": 0, "needs_action": 0, "needs_decision": 0, "closed": 0}
    for item in items:
        status = str(item.get("status") or "needs_decision")
        counts[status] = counts.get(status, 0) + 1
    return counts


def validation_evidence(validation: dict[str, Any], metrics: dict[str, Any]) -> list[Any]:
    evidence: list[Any] = [
        {
            "full_2000_2026_ann_return": metrics.get("full_2000_2026_ann_return") or metrics.get("cagr"),
            "validation_2022_2026_ann_return": metrics.get("validation_2022_2026_ann_return"),
        }
    ]
    if validation:
        evidence.append({"validation_recommendation": validation.get("recommendation")})
        evidence.extend(audit_warning_codes(validation))
    return evidence


def cost_stress_evidence(cost_stress: dict[str, Any]) -> list[Any]:
    if not cost_stress:
        return []
    scenarios = cost_stress.get("scenarios", [])
    high = scenarios[-1] if isinstance(scenarios, list) and scenarios else {}
    evidence = [
        {"recommendation": cost_stress.get("recommendation")},
        {
            "high_stress_cagr": high.get("cagr") if isinstance(high, dict) else None,
            "high_stress_max_drawdown": high.get("max_drawdown") if isinstance(high, dict) else None,
            "high_stress_total_cost_bps": high.get("total_cost_bps") if isinstance(high, dict) else None,
        },
    ]
    evidence.extend(audit_warning_codes(cost_stress))
    return evidence


def audit_warning_codes(audit: dict[str, Any]) -> list[str]:
    checks = audit.get("checks", []) if isinstance(audit, dict) else []
    return [
        str(item.get("code"))
        for item in checks
        if isinstance(item, dict) and item.get("status") == "warn" and item.get("code")
    ]


def diagnosis_finding_titles(diagnosis: dict[str, Any], severity: str) -> list[str]:
    findings = diagnosis.get("findings", []) if isinstance(diagnosis, dict) else []
    return [
        str(item.get("title") or "Untitled finding")
        for item in findings
        if isinstance(item, dict) and item.get("severity") == severity
    ]


def render_promotion_closure_markdown(closure: dict[str, Any]) -> str:
    lines = [
        f"# Promotion Warning Closure: {closure['strategy_name']}",
        "",
        f"Run ID: `{closure['run_id']}`",
        f"Promotion recommendation: `{closure['promotion_recommendation']}`",
        f"Closure recommendation: `{closure['closure_recommendation']}`",
        f"Generated at: `{closure['generated_at']}`",
        "",
        "## Summary",
        "",
    ]
    for status, count in sorted(closure.get("summary", {}).items()):
        lines.append(f"- {status}: `{count}`")
    lines.extend(["", "## Closure Items", ""])
    items = closure.get("items", [])
    if items:
        for item in items:
            lines.extend(render_item(item))
    else:
        lines.append("- No open warning or blocking items.")
    lines.extend(["", "## Already Closed By Evidence", ""])
    closed = closure.get("already_closed", [])
    if closed:
        for item in closed:
            lines.append(f"- {item.get('code')}: {item.get('reason')}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Next Actions", ""])
    for action in closure.get("next_actions", []):
        lines.append(f"- {action}")
    lines.extend(["", "## Artifacts", ""])
    for name, path in sorted(closure.get("artifacts", {}).items()):
        if path:
            lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def render_item(item: dict[str, Any]) -> list[str]:
    lines = [
        f"- `{item.get('code')}` -> `{item.get('status')}`",
        f"  Rationale: {item.get('rationale')}",
        f"  Action: {item.get('recommended_action')}",
    ]
    evidence = item.get("evidence", [])
    if evidence:
        lines.append("  Evidence: " + "; ".join(format_evidence(value) for value in evidence[:6]))
    return lines


def format_evidence(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}={format_evidence(inner)}" for key, inner in value.items())
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
