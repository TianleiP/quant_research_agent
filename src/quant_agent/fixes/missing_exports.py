from __future__ import annotations

import json
import re
import difflib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.audits.artifact_contract import EXPORT_CONTRACT
from quant_agent.fixes.proposal_contract import attach_proposal_contract, collect_memory_context
from quant_agent.fixes.source_trace import build_source_trace, render_source_trace_markdown


ISSUE = "missing-position-exports"


def suggest_missing_export_fix(
    repo: Path,
    file_path: str,
    artifact_report: Path | None = None,
    output_dir: Path | None = None,
    memory_index: Path | None = None,
    memory_query: str | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    relative_file = normalize_repo_file(file_path)
    source_path = repo / relative_file
    if not source_path.exists():
        raise FileNotFoundError(f"Target file does not exist under repo: {relative_file}")

    source = source_path.read_text(encoding="utf-8", errors="replace")
    report = read_json_if_exists(artifact_report)
    missing = missing_from_report(report) or ["positions", "trades", "rankings"]
    proposal_dir = proposal_output_dir(output_dir, relative_file)
    proposal_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = proposal_dir / "proposal.json"
    readme_path = proposal_dir / "README.md"
    contract_path = proposal_dir / "export_contract.md"
    profile_path = proposal_dir / "source_profile.json"
    trace_path = proposal_dir / "source_trace.json"
    trace_markdown_path = proposal_dir / "source_trace.md"
    patch_path = proposal_dir / "proposal.patch"
    profile = build_source_profile(source)
    trace = build_source_trace(source=source, relative_file=relative_file, issue=ISSUE)
    trace["repo"] = str(repo)
    trace["artifacts"] = {
        "source_trace_json": str(trace_path),
        "source_trace_markdown": str(trace_markdown_path),
    }
    patch_text = generate_missing_export_patch(source, relative_file, trace)
    apply_supported = patch_text is not None
    memory_context = collect_memory_context(
        memory_index,
        memory_query or missing_exports_memory_query(relative_file, missing),
    )

    proposal = {
        "issue": ISSUE,
        "status": "proposed",
        "risk_level": "medium",
        "repo": str(repo),
        "file": relative_file,
        "artifact_report": str(artifact_report) if artifact_report else None,
        "metadata_path": str(metadata_path),
        "contract_path": str(contract_path),
        "source_profile_path": str(profile_path),
        "source_trace_path": str(trace_path),
        "source_trace_markdown_path": str(trace_markdown_path),
        "patch_readiness": trace["patch_readiness"],
        "patch_path": str(patch_path) if patch_text else None,
        "apply_supported": apply_supported,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "missing_artifacts": missing,
        "summary": (
            "The run satisfies strategy-level artifacts but lacks position-level exports. "
            "Add exports for positions, trades, and rankings at the point where the source "
            "backtest already has per-date symbol weights and ranks."
        ),
        "manual_review_required": True,
        "apply_instruction": (
            "Review proposal.patch and source_trace.md. If accepted, apply with "
            "`quant-agent apply-fix --proposal <proposal.json> --yes`, rerun the "
            "external backtest through quant-agent with `--fresh`, then inspect artifacts."
            if apply_supported
            else (
                "This proposal intentionally does not auto-apply a patch. Review the source "
                "profile and export contract, then implement the export at the verified point "
                "where symbol-level weights/ranks are available."
            )
        ),
    }
    attach_proposal_contract(
        proposal,
        problem={
            "issue": ISSUE,
            "statement": (
                "The backtest output is missing position-level artifacts for realized "
                "positions, trades, and rankings."
            ),
            "impact": (
                "Quant-agent can ingest aggregate strategy metrics, but it cannot replay "
                "or audit symbol-level decisions without these exports."
            ),
        },
        target_files=[
            {
                "path": relative_file,
                "role": "backtest source entrypoint",
                "change_type": "patch" if apply_supported else "manual_review",
            }
        ],
        intended_behavior=[
            "Export positions, trades, and rankings from the source backtest where symbol-level objects already exist.",
            "Do not reconstruct holdings from aggregate daily returns inside quant-agent.",
            "Preserve existing strategy simulation behavior and aggregate daily curve outputs.",
        ],
        patch_plan=missing_exports_patch_plan(relative_file, apply_supported),
        verification={
            "commands": [
                "quant-agent apply-fix --proposal <proposal.json>",
                "quant-agent apply-fix --proposal <proposal.json> --yes",
                "quant-agent run --config <strategy-config> --fresh",
                "quant-agent inspect-artifacts --run latest",
            ],
            "expected": [
                "Dry-run reports whether the patch changes the target source file.",
                "Fresh ingest copies positions.csv, trades.csv, and rankings.csv into the quant-agent run directory.",
                "Artifact inspection no longer reports missing position-level artifacts.",
            ],
        },
        evidence=[
            {
                "kind": "artifact_report",
                "path": str(artifact_report) if artifact_report else None,
                "missing_artifacts": missing,
            },
            {
                "kind": "source_profile",
                "path": str(profile_path),
                "detail": "Keyword scan for existing CSV exports and symbol-level terms.",
            },
            {
                "kind": "source_trace",
                "path": str(trace_path),
                "patch_readiness": trace["patch_readiness"],
            },
            {
                "kind": "export_contract",
                "path": str(contract_path),
                "detail": "Required position-level artifact columns.",
            },
        ],
        memory_context=memory_context,
    )
    metadata_path.write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    profile_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    trace_markdown_path.write_text(render_source_trace_markdown(trace), encoding="utf-8")
    if patch_text:
        patch_path.write_text(patch_text, encoding="utf-8")
    contract_path.write_text(export_contract_markdown(missing), encoding="utf-8")
    readme_path.write_text(proposal_readme(proposal, profile, trace), encoding="utf-8")
    return {
        "proposal_dir": str(proposal_dir),
        "metadata_path": str(metadata_path),
        "readme_path": str(readme_path),
        "contract_path": str(contract_path),
        "source_profile_path": str(profile_path),
        "source_trace_path": str(trace_path),
        "source_trace_markdown_path": str(trace_markdown_path),
        "patch_path": str(patch_path) if patch_text else None,
        "proposal": proposal,
        "source_profile": profile,
        "source_trace": trace,
    }


def missing_exports_memory_query(relative_file: str, missing: list[str]) -> str:
    return (
        f"{relative_file} positions trades rankings weights ranks scores symbols "
        f"csv export simulate {' '.join(missing)}"
    )


def missing_exports_patch_plan(relative_file: str, apply_supported: bool) -> list[dict[str, Any]]:
    if apply_supported:
        return [
            {
                "step": 1,
                "target": relative_file,
                "description": "Add output directories for positions, trades, and rankings.",
            },
            {
                "step": 2,
                "target": relative_file,
                "description": "Wrap the simulator return-from-weights path to capture realized daily weights.",
            },
            {
                "step": 3,
                "target": relative_file,
                "description": "Write promoted live variant positions, trades, and rankings CSVs next to daily curves.",
            },
        ]
    return [
        {
            "step": 1,
            "target": relative_file,
            "description": "Review source_trace.md to confirm where symbol-level weights/ranks are available.",
        },
        {
            "step": 2,
            "target": relative_file,
            "description": "Add source-native exports at the verified symbol-level object site.",
        },
        {
            "step": 3,
            "target": relative_file,
            "description": "Rerun the backtest and inspect quant-agent artifact contract output.",
        },
    ]


def build_source_profile(source: str) -> dict[str, Any]:
    lines = source.splitlines()
    keyword_patterns = {
        "to_csv": r"\.to_csv\(",
        "weight": r"\bweights?\b|\bweight\b",
        "rank": r"\branks?\b|\brank\b",
        "turnover": r"\bturnover\b",
        "summary_by_window": r"summary_by_window",
        "daily_curves": r"daily_curves",
        "metadata": r"metadata\.json|metadata\s*=",
    }
    hits = {}
    for name, pattern in keyword_patterns.items():
        compiled = re.compile(pattern, flags=re.IGNORECASE)
        hits[name] = [
            {"line": index + 1, "text": line.strip()[:220]}
            for index, line in enumerate(lines)
            if compiled.search(line)
        ][:25]
    return {
        "line_count": len(lines),
        "keyword_hits": hits,
        "likely_has_existing_csv_exports": bool(hits["to_csv"]),
        "likely_mentions_weights": bool(hits["weight"]),
        "likely_mentions_rankings": bool(hits["rank"]),
        "likely_mentions_turnover": bool(hits["turnover"]),
    }


def generate_missing_export_patch(source: str, relative_file: str, trace: dict[str, Any]) -> str | None:
    if trace.get("patch_readiness", {}).get("status") != "ready_for_patch":
        return None
    try:
        modified = transform_xgboost_clean_ablation_exports(source)
    except ValueError:
        return None
    if modified == source:
        return None
    return unified_patch(source, modified, relative_file)


def transform_xgboost_clean_ablation_exports(source: str) -> str:
    newline = "\r\n" if "\r\n" in source else "\n"
    required_markers = [
        'DAILY_DIR = OUT_DIR / "daily_curves"',
        "daily = rob.last._simulate(",
        "daily.to_csv(DAILY_DIR / f\"{spec.variant_id}.csv\", index=False)",
    ]
    if any(marker not in source for marker in required_markers):
        raise ValueError("Source does not match the clean-ablation simulator export pattern.")
    updated = source
    updated = add_output_directories(updated, newline)
    updated = add_export_helpers(updated, newline)
    updated = add_directory_creation(updated, newline)
    updated = replace_simulate_call(updated, newline)
    return updated


def add_output_directories(source: str, newline: str) -> str:
    if "POSITIONS_DIR = OUT_DIR / \"positions\"" in source:
        return source
    marker = f'DAILY_DIR = OUT_DIR / "daily_curves"{newline}'
    insertion = (
        marker
        + f'POSITIONS_DIR = OUT_DIR / "positions"{newline}'
        + f'TRADES_DIR = OUT_DIR / "trades"{newline}'
        + f'RANKINGS_DIR = OUT_DIR / "rankings"{newline}'
    )
    if marker not in source:
        raise ValueError("Could not find DAILY_DIR constant.")
    return source.replace(marker, insertion, 1)


def add_directory_creation(source: str, newline: str) -> str:
    if "POSITIONS_DIR.mkdir(parents=True, exist_ok=True)" in source:
        return source
    marker = f"    DAILY_DIR.mkdir(parents=True, exist_ok=True){newline}"
    insertion = (
        marker
        + f"    POSITIONS_DIR.mkdir(parents=True, exist_ok=True){newline}"
        + f"    TRADES_DIR.mkdir(parents=True, exist_ok=True){newline}"
        + f"    RANKINGS_DIR.mkdir(parents=True, exist_ok=True){newline}"
    )
    if marker not in source:
        raise ValueError("Could not find DAILY_DIR mkdir call.")
    return source.replace(marker, insertion, 1)


def add_export_helpers(source: str, newline: str) -> str:
    if "def _simulate_with_weight_capture(" in source:
        return source
    match = re.search(r"(\r?\n\r?\ndef _make_specs\([^)]*\)[^\r\n]*:\r?\n)", source)
    if not match:
        raise ValueError("Could not find helper insertion point before _make_specs.")
    helper = export_helper_source(newline)
    marker = match.group(1)
    return source[: match.start()] + f"{newline}{newline}{helper}{marker}" + source[match.end() :]


def replace_simulate_call(source: str, newline: str) -> str:
    if "realized_weights = _simulate_with_weight_capture(" in source:
        return source
    old = (
        f"        daily = rob.last._simulate({newline}"
        f"            vin,{newline}"
        f"            current_panel,{newline}"
        f"            rob._to_last_spec(rob.RobustSpec(spec.variant_id, \"current_strategy_clean_ablation\", spec.notes, top2_key=\"top2_h1\")),{newline}"
        f"            {{\"top10_lq19\": partial_base, \"top10_noliq\": partial_base}},{newline}"
        f"        ){newline}"
        f"        daily[\"variant_id\"] = spec.variant_id{newline}"
        f"        daily.to_csv(DAILY_DIR / f\"{{spec.variant_id}}.csv\", index=False){newline}"
        f"        curves[spec.variant_id] = daily{newline}"
    )
    new = (
        f"        last_spec = rob._to_last_spec(rob.RobustSpec(spec.variant_id, \"current_strategy_clean_ablation\", spec.notes, top2_key=\"top2_h1\")){newline}"
        f"        realized_weights = _simulate_with_weight_capture({newline}"
        f"            vin,{newline}"
        f"            current_panel,{newline}"
        f"            last_spec,{newline}"
        f"            {{\"top10_lq19\": partial_base, \"top10_noliq\": partial_base}},{newline}"
        f"        ){newline}"
        f"        daily = realized_weights[\"daily\"]{newline}"
        f"        daily[\"variant_id\"] = spec.variant_id{newline}"
        f"        daily.to_csv(DAILY_DIR / f\"{{spec.variant_id}}.csv\", index=False){newline}"
        f"        if spec.variant_id == PROMOTED_LIVE_VARIANT_ID:{newline}"
        f"            _positions_from_weight_capture(spec.variant_id, realized_weights[\"weights\"], daily).to_csv(POSITIONS_DIR / f\"{{spec.variant_id}}.csv\", index=False){newline}"
        f"            _trades_from_weight_capture(spec.variant_id, realized_weights[\"weights\"]).to_csv(TRADES_DIR / f\"{{spec.variant_id}}.csv\", index=False){newline}"
        f"            _rankings_from_weight_capture(spec.variant_id, realized_weights[\"weights\"], daily).to_csv(RANKINGS_DIR / f\"{{spec.variant_id}}.csv\", index=False){newline}"
        f"        curves[spec.variant_id] = daily{newline}"
    )
    if old not in source:
        raise ValueError("Could not find exact simulator export block.")
    return source.replace(old, new, 1)


def export_helper_source(newline: str) -> str:
    lines = [
        "def _simulate_with_weight_capture(",
        "    inputs: dict[str, Any],",
        "    panel: pd.DataFrame,",
        "    spec: Any,",
        "    qbase_weights_by_key: dict[str, dict[pd.Timestamp, dict[str, float]]],",
        ") -> dict[str, Any]:",
        "    captured: list[tuple[pd.Timestamp, dict[str, float]]] = []",
        "    dates = [pd.Timestamp(x).normalize() for x in panel[\"date\"]]",
        "    date_iter = iter(dates)",
        "    hold = rob.last.fix.prev.optq.hold",
        "    original_return_from_weights = hold._return_from_weights",
        "",
        "    def _recording_return_from_weights(weights: dict[str, float], returns: dict[str, float]) -> float:",
        "        try:",
        "            dt = next(date_iter)",
        "        except StopIteration:",
        "            dt = pd.NaT",
        "        clean = hold._clean_weights(weights)",
        "        captured.append((pd.Timestamp(dt).normalize(), dict(clean)))",
        "        return original_return_from_weights(weights, returns)",
        "",
        "    hold._return_from_weights = _recording_return_from_weights",
        "    try:",
        "        daily = rob.last._simulate(inputs, panel, spec, qbase_weights_by_key)",
        "    finally:",
        "        hold._return_from_weights = original_return_from_weights",
        "    return {\"daily\": daily, \"weights\": captured}",
        "",
        "",
        "def _positions_from_weight_capture(",
        "    variant_id: str,",
        "    captured: list[tuple[pd.Timestamp, dict[str, float]]],",
        "    daily: pd.DataFrame,",
        ") -> pd.DataFrame:",
        "    daily_lookup = daily.copy()",
        "    daily_lookup[\"date\"] = pd.to_datetime(daily_lookup[\"date\"], errors=\"coerce\").dt.normalize()",
        "    daily_lookup = daily_lookup.set_index(\"date\", drop=False)",
        "    rows: list[dict[str, Any]] = []",
        "    for dt, weights in captured:",
        "        info = daily_lookup.loc[dt] if dt in daily_lookup.index else {}",
        "        ordered = sorted(weights.items(), key=lambda item: (item[0] == \"__CASH__\", -abs(float(item[1])), item[0]))",
        "        rank = 0",
        "        for symbol, weight in ordered:",
        "            weight = float(weight)",
        "            if abs(weight) <= 1e-15:",
        "                continue",
        "            if symbol != \"__CASH__\":",
        "                rank += 1",
        "            rows.append(",
        "                {",
        "                    \"date\": dt,",
        "                    \"variant_id\": variant_id,",
        "                    \"symbol\": symbol,",
        "                    \"weight\": weight,",
        "                    \"sleeve\": \"cash\" if symbol == \"__CASH__\" else str(getattr(info, \"decision_label\", \"realized_position\")),",
        "                    \"rank\": np.nan if symbol == \"__CASH__\" else rank,",
        "                    \"score\": np.nan,",
        "                    \"price\": np.nan,",
        "                    \"decision_label\": str(getattr(info, \"decision_label\", \"\")),",
        "                    \"gross_exposure\": float(getattr(info, \"gross_exposure\", np.nan)),",
        "                    \"cash_weight\": float(getattr(info, \"cash_weight\", np.nan)),",
        "                }",
        "            )",
        "    return pd.DataFrame(rows)",
        "",
        "",
        "def _trades_from_weight_capture(variant_id: str, captured: list[tuple[pd.Timestamp, dict[str, float]]]) -> pd.DataFrame:",
        "    rows: list[dict[str, Any]] = []",
        "    prev: dict[str, float] = {\"__CASH__\": 1.0}",
        "    for dt, weights in captured:",
        "        symbols = sorted(set(prev) | set(weights))",
        "        for symbol in symbols:",
        "            prev_weight = float(prev.get(symbol, 0.0))",
        "            target_weight = float(weights.get(symbol, 0.0))",
        "            change = target_weight - prev_weight",
        "            if abs(change) <= 1e-12:",
        "                continue",
        "            rows.append(",
        "                {",
        "                    \"date\": dt,",
        "                    \"variant_id\": variant_id,",
        "                    \"symbol\": symbol,",
        "                    \"prev_weight\": prev_weight,",
        "                    \"target_weight\": target_weight,",
        "                    \"weight_change\": change,",
        "                    \"turnover\": abs(change),",
        "                }",
        "            )",
        "        prev = dict(weights)",
        "    return pd.DataFrame(rows)",
        "",
        "",
        "def _rankings_from_weight_capture(",
        "    variant_id: str,",
        "    captured: list[tuple[pd.Timestamp, dict[str, float]]],",
        "    daily: pd.DataFrame,",
        ") -> pd.DataFrame:",
        "    daily_lookup = daily.copy()",
        "    daily_lookup[\"date\"] = pd.to_datetime(daily_lookup[\"date\"], errors=\"coerce\").dt.normalize()",
        "    daily_lookup = daily_lookup.set_index(\"date\", drop=False)",
        "    rows: list[dict[str, Any]] = []",
        "    for dt, weights in captured:",
        "        info = daily_lookup.loc[dt] if dt in daily_lookup.index else {}",
        "        ranked = [(symbol, float(weight)) for symbol, weight in weights.items() if symbol != \"__CASH__\" and abs(float(weight)) > 1e-15]",
        "        ranked = sorted(ranked, key=lambda item: (-abs(item[1]), item[0]))",
        "        for rank, (symbol, weight) in enumerate(ranked, start=1):",
        "            rows.append(",
        "                {",
        "                    \"date\": dt,",
        "                    \"variant_id\": variant_id,",
        "                    \"symbol\": symbol,",
        "                    \"rank\": rank,",
        "                    \"score\": np.nan,",
        "                    \"eligible\": True,",
        "                    \"sleeve\": str(getattr(info, \"decision_label\", \"realized_position\")),",
        "                    \"weight\": weight,",
        "                }",
        "            )",
        "    return pd.DataFrame(rows)",
    ]
    return newline.join(lines)


def unified_patch(original: str, modified: str, relative_file: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{relative_file}",
            tofile=f"b/{relative_file}",
        )
    )


def export_contract_markdown(missing: list[str]) -> str:
    lines = [
        "# Position-Level Export Contract",
        "",
        "Missing artifacts:",
        "",
    ]
    lines.extend(f"- `{name}`" for name in missing)
    lines.extend(["", "## Required Files", ""])
    for file_name, columns in EXPORT_CONTRACT.items():
        lines.append(f"### {file_name}")
        lines.append("")
        lines.append("```text")
        lines.append(", ".join(columns))
        lines.append("```")
        lines.append("")
    lines.extend(
        [
            "## Implementation Rule",
            "",
            "Export these files from the source backtest at the point where symbol-level "
            "weights, ranks, and scores are already known. Do not reconstruct holdings "
            "from aggregate daily returns inside quant-agent.",
            "",
        ]
    )
    return "\n".join(lines)


def proposal_readme(proposal: dict[str, Any], profile: dict[str, Any], trace: dict[str, Any]) -> str:
    readiness_payload = trace.get("patch_readiness", {})
    patch_section = [
        "## Patch",
        "",
        f"- Patch generated: `{bool(proposal.get('patch_path'))}`",
    ]
    if proposal.get("patch_path"):
        patch_section.extend(
            [
                f"- Patch file: `{proposal['patch_path']}`",
                "- Review the patch before applying it. It captures realized simulator weights during `_simulate` and exports position-level CSV files.",
            ]
        )
    else:
        patch_section.extend(
            [
                "The missing artifacts require access to internal symbol-level weights/ranks. "
                "A generic patch would risk exporting the wrong object or changing strategy behavior.",
            ]
        )
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
            *patch_section,
            "",
            "## Source Profile",
            "",
            f"- Lines: `{profile['line_count']}`",
            f"- Existing CSV exports detected: `{profile['likely_has_existing_csv_exports']}`",
            f"- Weight references detected: `{profile['likely_mentions_weights']}`",
            f"- Rank references detected: `{profile['likely_mentions_rankings']}`",
            f"- Turnover references detected: `{profile['likely_mentions_turnover']}`",
            "",
            "## Source Trace",
            "",
            f"- Patch readiness: `{readiness_payload.get('status')}`",
            f"- Reason: {readiness_payload.get('reason')}",
            f"- Trace JSON: `{proposal['source_trace_path']}`",
            f"- Trace report: `{proposal['source_trace_markdown_path']}`",
            "",
            "## Review",
            "",
            "- Review `export_contract.md`.",
            "- Review `source_trace.md` for likely export insertion points and symbol-level objects.",
            "- After a targeted patch is generated and approved, rerun the external backtest through quant-agent with `--fresh`.",
            "",
        ]
    )


def missing_from_report(report: dict[str, Any]) -> list[str]:
    items = report.get("missing_position_level_artifacts", [])
    if not isinstance(items, list):
        return []
    return [item.get("name") for item in items if isinstance(item, dict) and item.get("name")]


def read_json_if_exists(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def proposal_output_dir(output_dir: Path | None, relative_file: str) -> Path:
    if output_dir:
        return output_dir
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(".fix_proposals") / f"{timestamp}_{ISSUE}_{safe_component(relative_file)}"


def normalize_repo_file(file_path: str) -> str:
    path = Path(file_path)
    if path.is_absolute():
        raise ValueError("--file must be relative to --repo")
    return path.as_posix()


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "file"
