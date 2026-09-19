import json

import pytest

from quant_agent.fixes import apply_fix_proposal, suggest_stale_metadata_fix


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


def make_proposal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "script.py"
    target.write_text(SAMPLE_SOURCE, encoding="utf-8")
    proposal_dir = tmp_path / "proposal"
    suggest_stale_metadata_fix(
        repo=repo,
        file_path="script.py",
        promoted_variant_id="promoted_variant",
        output_dir=proposal_dir,
    )
    return repo, target, proposal_dir / "proposal.json"


def test_apply_fix_proposal_dry_run_does_not_modify_target(tmp_path):
    _repo, target, proposal_path = make_proposal(tmp_path)

    result = apply_fix_proposal(proposal_path, yes=False)

    assert result["dry_run"] is True
    assert result["would_change"] is True
    assert result["applied"] is False
    assert target.read_text(encoding="utf-8") == SAMPLE_SOURCE
    assert proposal_path.parent.joinpath("apply_result.json").exists()


def test_apply_fix_proposal_yes_modifies_target(tmp_path):
    _repo, target, proposal_path = make_proposal(tmp_path)

    result = apply_fix_proposal(proposal_path, yes=True)
    updated = target.read_text(encoding="utf-8")

    assert result["dry_run"] is False
    assert result["would_change"] is True
    assert result["applied"] is True
    assert "PROMOTED_LIVE_VARIANT_ID" in updated
    assert "promoted_live_variant_id" in updated


def test_apply_fix_proposal_rejects_patch_file_mismatch(tmp_path):
    _repo, _target, proposal_path = make_proposal(tmp_path)
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["file"] = "other.py"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match proposal file"):
        apply_fix_proposal(proposal_path, yes=False)
