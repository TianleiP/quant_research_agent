from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.fixes.proposal_contract import attach_proposal_contract, collect_memory_context


ISSUE = "stale-metadata"


def suggest_stale_metadata_fix(
    repo: Path,
    file_path: str,
    promoted_variant_id: str,
    output_dir: Path | None = None,
    memory_index: Path | None = None,
    memory_query: str | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    relative_file = normalize_repo_file(file_path)
    source_path = repo / relative_file
    if not source_path.exists():
        raise FileNotFoundError(f"Target file does not exist under repo: {relative_file}")

    original = source_path.read_text(encoding="utf-8", errors="replace")
    modified = transform_stale_metadata_source(original, promoted_variant_id)
    if original == modified:
        raise ValueError("No stale metadata patch could be generated for the target file.")

    patch = unified_patch(
        original=original,
        modified=modified,
        relative_file=relative_file,
    )
    proposal_dir = proposal_output_dir(output_dir, relative_file)
    proposal_dir.mkdir(parents=True, exist_ok=True)
    patch_path = proposal_dir / "proposal.patch"
    metadata_path = proposal_dir / "proposal.json"
    readme_path = proposal_dir / "README.md"

    patch_path.write_text(patch, encoding="utf-8")
    memory_context = collect_memory_context(
        memory_index,
        memory_query or stale_metadata_memory_query(relative_file, promoted_variant_id),
    )
    proposal = {
        "issue": ISSUE,
        "status": "proposed",
        "risk_level": "low",
        "repo": str(repo),
        "file": relative_file,
        "promoted_variant_id": promoted_variant_id,
        "patch_path": str(patch_path),
        "metadata_path": str(metadata_path),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "summary": (
            "Add explicit promoted_live_variant_id, control_variant_id, "
            "generated_at_utc, and source_script metadata without changing "
            "the existing CURRENT_VARIANT_ID control logic."
        ),
        "manual_review_required": True,
        "apply_instruction": "Review proposal.patch, then apply it from the target repo root if accepted.",
    }
    attach_proposal_contract(
        proposal,
        problem={
            "issue": ISSUE,
            "statement": (
                "The source metadata does not explicitly distinguish the promoted live "
                "variant from the existing control/current variant field."
            ),
            "impact": (
                "Downstream ingestion can misidentify which variant should be treated "
                "as live when the source script keeps an old CURRENT_VARIANT_ID value."
            ),
        },
        target_files=[
            {
                "path": relative_file,
                "role": "backtest source metadata writer",
                "change_type": "patch",
            }
        ],
        intended_behavior=[
            "Keep the existing CURRENT_VARIANT_ID control logic unchanged.",
            "Add an explicit PROMOTED_LIVE_VARIANT_ID constant.",
            "Write generated_at_utc, source_script, control_variant_id, and promoted_live_variant_id into metadata.",
        ],
        patch_plan=[
            {
                "step": 1,
                "target": relative_file,
                "description": "Import datetime/timezone support for generated_at_utc.",
            },
            {
                "step": 2,
                "target": relative_file,
                "description": "Insert PROMOTED_LIVE_VARIANT_ID next to CURRENT_VARIANT_ID.",
            },
            {
                "step": 3,
                "target": relative_file,
                "description": "Extend the metadata payload with explicit promoted/control/source fields.",
            },
        ],
        verification={
            "commands": [
                "quant-agent apply-fix --proposal <proposal.json>",
                "quant-agent apply-fix --proposal <proposal.json> --yes",
                "quant-agent run --config <strategy-config> --fresh",
            ],
            "expected": [
                "Dry-run reports would_change=true before applying.",
                "metadata.json includes promoted_live_variant_id equal to the promoted variant.",
                "Quant-agent ingest uses the promoted variant id instead of trusting current_variant_id.",
            ],
        },
        evidence=[
            {
                "kind": "source_file",
                "path": relative_file,
                "detail": "Patch was generated from the current source file contents.",
            },
            {
                "kind": "promoted_variant_id",
                "value": promoted_variant_id,
                "detail": "User- or state-provided promoted live variant id.",
            },
        ],
        memory_context=memory_context,
    )
    metadata_path.write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    readme_path.write_text(proposal_readme(proposal), encoding="utf-8")

    return {
        "proposal_dir": str(proposal_dir),
        "patch_path": str(patch_path),
        "metadata_path": str(metadata_path),
        "readme_path": str(readme_path),
        "proposal": proposal,
    }


def stale_metadata_memory_query(relative_file: str, promoted_variant_id: str) -> str:
    return (
        f"{relative_file} metadata current variant promoted live variant "
        f"{promoted_variant_id} generated_at source_script"
    )


def transform_stale_metadata_source(source: str, promoted_variant_id: str) -> str:
    newline = "\r\n" if "\r\n" in source else "\n"
    updated = source
    updated = add_datetime_import(updated, newline)
    updated = add_promoted_variant_constant(updated, promoted_variant_id, newline)
    updated = add_metadata_fields(updated, newline)
    return updated


def add_datetime_import(source: str, newline: str) -> str:
    if "from datetime import datetime, timezone" in source:
        return source
    marker = f"import time{newline}"
    if marker not in source:
        raise ValueError("Could not find `import time` for datetime import insertion.")
    return source.replace(marker, marker + f"from datetime import datetime, timezone{newline}", 1)


def add_promoted_variant_constant(source: str, promoted_variant_id: str, newline: str) -> str:
    if "PROMOTED_LIVE_VARIANT_ID" in source:
        return source
    match = re.search(
        r'^(CURRENT_VARIANT_ID\s*=\s*["\'][^"\']+["\'][ \t]*)(?:\r?\n)+',
        source,
        flags=re.MULTILINE,
    )
    if not match:
        raise ValueError("Could not find CURRENT_VARIANT_ID constant.")
    insertion = (
        match.group(1)
        + newline
        + f'PROMOTED_LIVE_VARIANT_ID = "{promoted_variant_id}"'
        + newline
        + newline
    )
    return source[: match.start()] + insertion + source[match.end() :]


def add_metadata_fields(source: str, newline: str) -> str:
    if '"promoted_live_variant_id": PROMOTED_LIVE_VARIANT_ID' in source:
        return source
    pattern = (
        f'        "elapsed_seconds": time.time() - started,{newline}'
        f'        "current_variant_id": CURRENT_VARIANT_ID,{newline}'
    )
    replacement = (
        f'        "elapsed_seconds": time.time() - started,{newline}'
        f'        "generated_at_utc": datetime.now(timezone.utc).isoformat(),{newline}'
        f'        "source_script": str(Path(__file__).resolve().relative_to(PROJECT_ROOT)),{newline}'
        f'        "control_variant_id": CURRENT_VARIANT_ID,{newline}'
        f'        "promoted_live_variant_id": PROMOTED_LIVE_VARIANT_ID,{newline}'
        f'        "current_variant_id": CURRENT_VARIANT_ID,{newline}'
    )
    if pattern not in source:
        raise ValueError("Could not find metadata elapsed_seconds/current_variant_id block.")
    return source.replace(pattern, replacement, 1)


def unified_patch(original: str, modified: str, relative_file: str) -> str:
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{relative_file}",
            tofile=f"b/{relative_file}",
        )
    )


def proposal_output_dir(output_dir: Path | None, relative_file: str) -> Path:
    if output_dir:
        return output_dir
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_file = safe_component(relative_file)
    return Path(".fix_proposals") / f"{timestamp}_{ISSUE}_{safe_file}"


def proposal_readme(proposal: dict[str, Any]) -> str:
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
            "## Review",
            "",
            "- This proposal does not modify the target repo.",
            "- Review `proposal.patch` before applying it.",
            "- After applying, rerun the external backtest through quant-agent with `--fresh`.",
            "",
        ]
    )


def normalize_repo_file(file_path: str) -> str:
    path = Path(file_path)
    if path.is_absolute():
        raise ValueError("--file must be relative to --repo")
    return path.as_posix()


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "file"
