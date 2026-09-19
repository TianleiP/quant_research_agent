import json

from quant_agent.cli import main
from quant_agent.fixes.generalized import suggest_generalized_fix
from quant_agent.fixes.stale_metadata import (
    suggest_stale_metadata_fix,
    transform_stale_metadata_source,
)
from quant_agent.memory import build_code_memory


SAMPLE_SOURCE = """from __future__ import annotations

import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def test_transform_stale_metadata_source_adds_explicit_metadata_fields():
    updated = transform_stale_metadata_source(SAMPLE_SOURCE, "promoted_variant")

    assert "from datetime import datetime, timezone" in updated
    assert 'CURRENT_VARIANT_ID = "old_control"\nPROMOTED_LIVE_VARIANT_ID = "promoted_variant"\n\n' in updated
    assert 'PROMOTED_LIVE_VARIANT_ID = "promoted_variant"' in updated
    assert '"generated_at_utc": datetime.now(timezone.utc).isoformat()' in updated
    assert '"source_script": str(Path(__file__).resolve().relative_to(PROJECT_ROOT))' in updated
    assert '"control_variant_id": CURRENT_VARIANT_ID' in updated
    assert '"promoted_live_variant_id": PROMOTED_LIVE_VARIANT_ID' in updated
    assert '"current_variant_id": CURRENT_VARIANT_ID' in updated


def test_suggest_stale_metadata_fix_writes_patch_and_metadata(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "script.py"
    target.write_text(SAMPLE_SOURCE, encoding="utf-8")
    output_dir = tmp_path / "proposal"

    result = suggest_stale_metadata_fix(
        repo=repo,
        file_path="script.py",
        promoted_variant_id="promoted_variant",
        output_dir=output_dir,
    )

    patch_text = output_dir.joinpath("proposal.patch").read_text(encoding="utf-8")
    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))

    assert result["proposal_dir"] == str(output_dir)
    assert "PROMOTED_LIVE_VARIANT_ID" in patch_text
    assert "promoted_live_variant_id" in patch_text
    assert proposal["issue"] == "stale-metadata"
    assert proposal["manual_review_required"] is True
    assert proposal["proposal_schema_version"] == 1
    assert proposal["problem"]["issue"] == "stale-metadata"
    assert proposal["target_files"][0]["path"] == "script.py"
    assert proposal["patch_plan"]
    assert proposal["verification"]["commands"]
    assert proposal["quality_gate"]["status"] == "pass"
    assert proposal["memory_context"]["status"] == "not_available"
    assert target.read_text(encoding="utf-8") == SAMPLE_SOURCE


def test_suggest_stale_metadata_fix_includes_code_memory_evidence(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "script.py"
    target.write_text(SAMPLE_SOURCE, encoding="utf-8")
    memory_dir = tmp_path / ".code_memory" / "repo"
    build_code_memory(repo=repo, output_dir=memory_dir, chunk_lines=20, overlap_lines=5)
    output_dir = tmp_path / "proposal"

    suggest_stale_metadata_fix(
        repo=repo,
        file_path="script.py",
        promoted_variant_id="promoted_variant",
        output_dir=output_dir,
        memory_index=memory_dir / "code_memory.json",
    )

    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))

    assert proposal["memory_context"]["status"] == "available"
    assert proposal["memory_context"]["result_count"] >= 1
    assert proposal["memory_context"]["results"][0]["path"] == "script.py"


def test_suggest_fix_cli_auto_uses_matching_code_memory(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "script.py"
    target.write_text(SAMPLE_SOURCE, encoding="utf-8")
    build_code_memory(
        repo=repo,
        output_dir=tmp_path / ".code_memory" / "repo",
        chunk_lines=20,
        overlap_lines=5,
    )
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "proposal"

    exit_code = main(
        [
            "suggest-fix",
            "--repo",
            str(repo),
            "--file",
            "script.py",
            "--issue",
            "stale-metadata",
            "--promoted-variant-id",
            "promoted_variant",
            "--output",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "Memory context: available" in captured.out
    assert "Quality gate: pass" in captured.out
    assert proposal["memory_context"]["status"] == "available"


def test_suggest_generalized_fix_writes_review_only_package(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "script.py"
    target.write_text(SAMPLE_SOURCE, encoding="utf-8")
    output_dir = tmp_path / "generalized"

    result = suggest_generalized_fix(
        repo=repo,
        file_path="script.py",
        problem="Draft a safer metadata timestamp change.",
        output_dir=output_dir,
        provider="mock",
    )

    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))
    draft = json.loads(output_dir.joinpath("llm_draft.json").read_text(encoding="utf-8"))
    trace = json.loads(output_dir.joinpath("source_trace.json").read_text(encoding="utf-8"))

    assert result["proposal_dir"] == str(output_dir)
    assert proposal["issue"] == "generalized-fix"
    assert proposal["apply_supported"] is False
    assert proposal["patch_path"] is None
    assert proposal["candidate_patch_path"] == str(output_dir / "candidate.patch")
    assert proposal["quality_gate"]["status"] == "pass"
    assert proposal["memory_context"]["status"] == "not_available"
    assert draft["confidence"] == "low"
    assert trace["issue"] == "generalized-fix"
    assert output_dir.joinpath("candidate.patch").exists()
    assert output_dir.joinpath("llm_draft.raw.txt").exists()
    assert target.read_text(encoding="utf-8") == SAMPLE_SOURCE


def test_suggest_fix_cli_supports_generalized_review_only_mode(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath("script.py").write_text(SAMPLE_SOURCE, encoding="utf-8")
    output_dir = tmp_path / "generalized"
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "suggest-fix",
            "--repo",
            str(repo),
            "--file",
            "script.py",
            "--issue",
            "generalized-fix",
            "--problem",
            "Draft a safer metadata timestamp change.",
            "--provider",
            "mock",
            "--output",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    proposal = json.loads(output_dir.joinpath("proposal.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "Candidate patch:" in captured.out
    assert "LLM draft:" in captured.out
    assert "Issue: generalized-fix" in captured.out
    assert proposal["apply_supported"] is False
    assert proposal["patch_path"] is None
