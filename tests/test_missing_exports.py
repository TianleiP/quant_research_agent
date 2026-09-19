import json
from pathlib import Path

import pytest

from quant_agent.fixes import apply_fix_proposal, suggest_missing_export_fix
from quant_agent.fixes.missing_exports import transform_xgboost_clean_ablation_exports
from quant_agent.fixes.revision_synthesis import synthesize_revised_patch
from quant_agent.memory import build_code_memory


SAMPLE_SOURCE = """from __future__ import annotations

from pathlib import Path


def main():
    weights = build_weights()
    ranks = build_ranks()
    summary_by_window.to_csv("summary_by_window.csv", index=False)
    daily_curve.to_csv("daily_curve.csv", index=False)
    return weights, ranks
"""


def test_suggest_missing_export_fix_writes_review_package(tmp_path):
    repo = make_repo(tmp_path)
    report = tmp_path / "artifact_contract.json"
    report.write_text(
        json.dumps(
            {
                "missing_position_level_artifacts": [
                    {"name": "positions"},
                    {"name": "trades"},
                    {"name": "rankings"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "proposal"

    result = suggest_missing_export_fix(
        repo=repo,
        file_path="research/run_strategy.py",
        artifact_report=report,
        output_dir=output_dir,
    )

    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))
    profile = json.loads(output_dir.joinpath("source_profile.json").read_text(encoding="utf-8"))
    trace = json.loads(output_dir.joinpath("source_trace.json").read_text(encoding="utf-8"))

    assert result["proposal_dir"] == str(output_dir)
    assert proposal["issue"] == "missing-position-exports"
    assert proposal["apply_supported"] is False
    assert proposal["patch_path"] is None
    assert proposal["source_trace_path"] == str(output_dir / "source_trace.json")
    assert proposal["patch_readiness"]["status"] == "ready_for_patch"
    assert proposal["missing_artifacts"] == ["positions", "trades", "rankings"]
    assert proposal["proposal_schema_version"] == 1
    assert proposal["problem"]["issue"] == "missing-position-exports"
    assert proposal["target_files"][0]["path"] == "research/run_strategy.py"
    assert proposal["patch_plan"]
    assert proposal["verification"]["commands"]
    assert proposal["quality_gate"]["status"] == "pass"
    assert proposal["memory_context"]["status"] == "not_available"
    assert profile["likely_mentions_weights"] is True
    assert profile["likely_mentions_rankings"] is True
    assert trace["patch_readiness"]["status"] == "ready_for_patch"
    assert output_dir.joinpath("source_trace.md").exists()
    assert "positions.csv" in output_dir.joinpath("export_contract.md").read_text(encoding="utf-8")


def test_apply_fix_rejects_non_applicable_missing_export_proposal(tmp_path):
    repo = make_repo(tmp_path)
    output_dir = tmp_path / "proposal"
    suggest_missing_export_fix(
        repo=repo,
        file_path="research/run_strategy.py",
        output_dir=output_dir,
    )

    with pytest.raises(ValueError, match="does not include an auto-applicable patch"):
        apply_fix_proposal(output_dir / "proposal.json", yes=False)


def test_transform_xgboost_clean_ablation_exports_adds_capture_exports():
    updated = transform_xgboost_clean_ablation_exports(XGBOOST_PATTERN_SOURCE)

    assert 'POSITIONS_DIR = OUT_DIR / "positions"' in updated
    assert "def _simulate_with_weight_capture(" in updated
    assert "realized_weights = _simulate_with_weight_capture(" in updated
    assert "if spec.variant_id == PROMOTED_LIVE_VARIANT_ID:" in updated
    assert "_positions_from_weight_capture" in updated
    assert "_trades_from_weight_capture" in updated
    assert "_rankings_from_weight_capture" in updated


def test_suggest_missing_export_fix_generates_patch_for_xgboost_pattern(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "research" / "run_strategy.py"
    target.parent.mkdir(parents=True)
    target.write_text(XGBOOST_PATTERN_SOURCE, encoding="utf-8")
    output_dir = tmp_path / "proposal"

    result = suggest_missing_export_fix(
        repo=repo,
        file_path="research/run_strategy.py",
        output_dir=output_dir,
    )

    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))
    patch_text = output_dir.joinpath("proposal.patch").read_text(encoding="utf-8")

    assert result["patch_path"] == str(output_dir / "proposal.patch")
    assert proposal["apply_supported"] is True
    assert proposal["patch_path"] == str(output_dir / "proposal.patch")
    assert proposal["target_files"][0]["change_type"] == "patch"
    assert "POSITIONS_DIR" in patch_text
    assert "realized_weights" in patch_text


def test_suggest_missing_export_fix_includes_code_memory_evidence(tmp_path):
    repo = make_repo(tmp_path)
    memory_dir = tmp_path / ".code_memory" / "repo"
    build_code_memory(repo=repo, output_dir=memory_dir, chunk_lines=20, overlap_lines=5)
    output_dir = tmp_path / "proposal"

    suggest_missing_export_fix(
        repo=repo,
        file_path="research/run_strategy.py",
        output_dir=output_dir,
        memory_index=memory_dir / "code_memory.json",
    )

    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))

    assert proposal["memory_context"]["status"] == "available"
    assert proposal["memory_context"]["result_count"] >= 1
    assert proposal["memory_context"]["results"][0]["path"] == "research/run_strategy.py"
    assert proposal["quality_gate"]["status"] == "pass"


def test_synthesize_revised_patch_generates_missing_export_patch(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "research" / "run_strategy.py"
    target.parent.mkdir(parents=True)
    target.write_text(XGBOOST_PATTERN_SOURCE, encoding="utf-8")
    revision_dir = tmp_path / ".fix_proposals" / "revision"
    revision_dir.mkdir(parents=True)
    revision_path = revision_dir / "proposal.json"
    revision_path.write_text(
        json.dumps(
            {
                "issue": "verification-failure-revision",
                "repo": str(repo),
                "file": "research/run_strategy.py",
                "revision_of": {"proposal_path": "old/proposal.json", "issue": "stale-metadata"},
                "artifact_inspection": {
                    "recommendation": "needs_export_patch",
                    "missing_position_level_artifacts": ["positions", "trades", "rankings"],
                },
                "summary": "Artifact inspection found missing position-level artifacts.",
            }
        ),
        encoding="utf-8",
    )

    result = synthesize_revised_patch(root=tmp_path, revision_proposal_path=revision_path)

    proposal_path = Path(result["metadata_path"])
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    patch_text = Path(result["patch_path"]).read_text(encoding="utf-8")
    assert result["synthesis_status"] == "synthesized"
    assert result["synthesized_issue"] == "missing-position-exports"
    assert result["apply_supported"] is True
    assert proposal["revision_source"] == str(revision_path.resolve())
    assert proposal["synthesis_missing_artifacts"] == ["positions", "trades", "rankings"]
    assert proposal["quality_gate"]["status"] == "pass"
    assert "realized_weights = _simulate_with_weight_capture(" in patch_text


def test_synthesize_revised_patch_skips_unsupported_failure(tmp_path):
    revision_dir = tmp_path / ".fix_proposals" / "revision"
    revision_dir.mkdir(parents=True)
    revision_path = revision_dir / "proposal.json"
    revision_path.write_text(
        json.dumps(
            {
                "issue": "verification-failure-revision",
                "repo": str(tmp_path / "repo"),
                "file": "research/run_strategy.py",
                "verification_failure": {
                    "verification_status": "failed",
                    "error_type": "NotImplementedError",
                    "error": "Unsupported engine",
                },
                "summary": "Fresh verification failed.",
            }
        ),
        encoding="utf-8",
    )

    result = synthesize_revised_patch(root=tmp_path, revision_proposal_path=revision_path)

    assert result["synthesis_status"] == "unsupported"
    assert result["synthesized_issue"] is None
    assert result["apply_supported"] is False
    assert result["proposal_dir"] is None


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "research" / "run_strategy.py"
    target.parent.mkdir(parents=True)
    target.write_text(SAMPLE_SOURCE, encoding="utf-8")
    return repo


XGBOOST_PATTERN_SOURCE = '''from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

OUT_DIR = Path("out")
DAILY_DIR = OUT_DIR / "daily_curves"


class rob:
    class last:
        @staticmethod
        def _simulate(vin, panel, spec, weights):
            return pd.DataFrame()
    @staticmethod
    def _to_last_spec(spec):
        return spec
    class RobustSpec:
        def __init__(self, variant_id, family, notes, top2_key=None):
            self.variant_id = variant_id
            self.family = family
            self.notes = notes


class Spec:
    variant_id = "demo"
    notes = "demo"
    base_top_n = 5
    base_score = "combo"
    accel_top_n = 2
    accel_score = "mom12"


def _make_specs() -> list[Any]:
    return [Spec()]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    specs = _make_specs()
    curves = {}
    for i, spec in enumerate(specs, start=1):
        qbase_weights = {}
        top2_weights = {}
        partial_base = {}
        vin = {}
        current_panel = pd.DataFrame({"date": []})
        daily = rob.last._simulate(
            vin,
            current_panel,
            rob._to_last_spec(rob.RobustSpec(spec.variant_id, "current_strategy_clean_ablation", spec.notes, top2_key="top2_h1")),
            {"top10_lq19": partial_base, "top10_noliq": partial_base},
        )
        daily["variant_id"] = spec.variant_id
        daily.to_csv(DAILY_DIR / f"{spec.variant_id}.csv", index=False)
        curves[spec.variant_id] = daily
    return 0
'''
