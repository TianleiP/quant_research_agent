from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


DIAGNOSIS_INSTRUCTIONS = """
You are a quant research audit assistant. Produce a concise diagnosis for a
systematic strategy run. Focus on concrete evidence from the supplied artifacts:
performance shape, risk flags, suspicious inconsistencies, lag-safety questions,
missing artifacts, and next checks. Do not invent trades, positions, or rankings
when the artifacts say they are unavailable.

Use plain ASCII punctuation only. Avoid arrows, en dashes, em dashes, bullets
other than hyphen, and other special symbols.

Important interpretation rule: a stale source metadata field such as
current_variant_id is a metadata/documentation consistency warning, not proof
that the copied summary rows or daily curve belong to the wrong variant. When
expected_variant_id, metrics.variant_id, summary rows, and the copied daily
curve filename agree, treat the configured expected variant as the run identity.
""".strip()


STRUCTURED_DIAGNOSIS_INSTRUCTIONS = """
You are a quant research audit assistant. Return a single strict JSON object
and nothing else. Do not wrap it in markdown. Use plain ASCII text only.

Required schema:
{
  "summary": "one concise paragraph",
  "findings": [
    {
      "severity": "low|medium|high",
      "category": "performance|lag_safety|data_quality|metadata|missing_artifact|robustness|operations",
      "title": "short finding title",
      "evidence": ["specific evidence from supplied artifacts"],
      "recommended_action": "specific next action"
    }
  ],
  "missing_artifacts": ["artifact name"],
  "next_actions": ["specific action"]
}

Rules:
- Use only supplied artifacts.
- Do not invent trades, positions, or rankings when unavailable.
- Treat stale source metadata current_variant_id as a metadata/documentation
  consistency warning, not proof that copied summary rows or daily curve belong
  to the wrong variant.
- If expected_variant_id, metrics.variant_id, summary rows, and copied daily
  curve filename agree, treat the configured expected variant as run identity.
""".strip()


def build_diagnosis_prompt(run_dir: Path) -> str:
    manifest = read_json_if_exists(run_dir / "manifest.json")
    metrics = read_json_if_exists(run_dir / "metrics.json")
    flags = read_json_if_exists(run_dir / "flags.json")
    engine_metadata = read_json_if_exists(run_dir / "engine_metadata.json")
    short_report = read_text_if_exists(run_dir / "short_report.md", max_chars=4000)
    summary_rows = source_summary_rows(run_dir, metrics.get("variant_id"))

    payload: dict[str, Any] = {
        "run_id": run_dir.name,
        "manifest_summary": select_keys(
            manifest,
            [
                "strategy_name",
                "engine",
                "backtest_start",
                "backtest_end",
                "data_version",
                "universe_version",
            ],
        ),
        "core_metrics": select_keys(
            metrics,
            [
                "variant_id",
                "primary_window",
                "cagr",
                "max_drawdown",
                "calmar",
                "sharpe",
                "ann_vol",
                "cum_return",
                "exposure",
                "mean_cash_weight",
                "top2_active_share",
                "top2_partial_share",
                "top2_full_share",
                "source_daily_start",
                "source_daily_end",
                "source_daily_rows",
            ],
        ),
        "window_metrics": window_metrics(metrics),
        "flags": flags,
        "adapter_warnings": engine_metadata.get("warnings", []),
        "subprocess": select_keys(
            engine_metadata,
            [
                "subprocess_ran",
                "exit_code",
                "started_at",
                "finished_at",
                "elapsed_seconds",
                "expected_variant_id",
                "source_metadata_trusted_for_live_variant",
            ],
        ),
        "summary_by_window_rows": summary_rows,
        "short_report_excerpt": short_report,
        "known_missing_artifacts": [
            "per-symbol positions are currently empty unless a future adapter exports them",
            "trades are currently empty unless a future adapter exports them",
            "per-symbol ranking tables are not saved by the current XGBoost research script",
        ],
        "interpretation_notes": [
            "source_metadata.current_variant_id is known stale for this research script",
            "do not treat the stale source metadata current_variant_id as proof that the daily curve is the wrong variant",
            "the adapter filters summary rows by metrics.variant_id and copies the expected daily curve file",
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {"value": payload}


def read_text_if_exists(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars]


def select_keys(payload: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def window_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    selected = {}
    for key, value in metrics.items():
        if key.endswith("_ann_return") or key.endswith("_max_drawdown"):
            selected[key] = value
    return selected


def source_summary_rows(run_dir: Path, variant_id: str | None) -> list[dict[str, str]]:
    path = run_dir / "source_artifacts" / "summary_by_window.csv"
    if not path.exists() or not variant_id:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if row.get("variant_id") == variant_id]
