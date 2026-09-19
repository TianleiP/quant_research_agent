from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HUNK_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


def apply_fix_proposal(proposal_path: Path, yes: bool = False) -> dict[str, Any]:
    proposal_path = proposal_path.resolve()
    proposal = read_json(proposal_path)
    if proposal.get("apply_supported") is False:
        raise ValueError(
            "This proposal does not include an auto-applicable patch. "
            "Review the proposal package and generate a targeted patch first."
        )
    repo = Path(require_string(proposal, "repo")).resolve()
    relative_file = require_string(proposal, "file")
    target_path = (repo / relative_file).resolve()
    ensure_within_repo(repo, target_path)

    patch_path = resolve_patch_path(proposal, proposal_path)
    patch_text = patch_path.read_text(encoding="utf-8")
    patch = parse_unified_patch(patch_text)
    if patch["target_file"] != relative_file:
        raise ValueError(
            f"Patch target {patch['target_file']!r} does not match proposal file {relative_file!r}."
        )

    original = target_path.read_text(encoding="utf-8", errors="replace")
    modified = apply_unified_diff(original, patch_text)
    would_change = original != modified

    result = {
        "proposal_path": str(proposal_path),
        "patch_path": str(patch_path),
        "repo": str(repo),
        "file": relative_file,
        "target_path": str(target_path),
        "issue": proposal.get("issue"),
        "dry_run": not yes,
        "would_change": would_change,
        "applied": False,
        "applied_at": None,
    }
    if yes:
        if would_change:
            target_path.write_text(modified, encoding="utf-8")
        result["applied"] = True
        result["applied_at"] = datetime.now(tz=timezone.utc).isoformat()

    result_path = proposal_path.parent / "apply_result.json"
    result["result_path"] = str(result_path)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def apply_unified_diff(original: str, patch_text: str) -> str:
    patch = parse_unified_patch(patch_text)
    original_lines = original.splitlines(keepends=True)
    output: list[str] = []
    source_index = 0

    for hunk in patch["hunks"]:
        old_start = hunk["old_start"]
        hunk_source_index = old_start - 1
        if hunk_source_index < source_index:
            raise ValueError("Overlapping or out-of-order patch hunks.")

        output.extend(original_lines[source_index:hunk_source_index])
        source_index = hunk_source_index

        for op, line in hunk["lines"]:
            if op == " ":
                require_source_line(original_lines, source_index, line)
                output.append(original_lines[source_index])
                source_index += 1
            elif op == "-":
                require_source_line(original_lines, source_index, line)
                source_index += 1
            elif op == "+":
                output.append(line)
            else:
                raise ValueError(f"Unsupported patch operation: {op!r}")

    output.extend(original_lines[source_index:])
    return "".join(output)


def parse_unified_patch(patch_text: str) -> dict[str, Any]:
    lines = patch_text.splitlines(keepends=True)
    if len(lines) < 3 or not lines[0].startswith("--- ") or not lines[1].startswith("+++ "):
        raise ValueError("Patch must start with unified diff file headers.")
    source_file = strip_diff_path(lines[0][4:].strip())
    target_file = strip_diff_path(lines[1][4:].strip())
    hunks = []
    index = 2
    while index < len(lines):
        line = lines[index]
        if not line.startswith("@@ "):
            if not line.strip():
                index += 1
                continue
            raise ValueError(f"Unexpected patch line outside hunk: {line!r}")
        match = HUNK_RE.match(line)
        if not match:
            raise ValueError(f"Invalid hunk header: {line!r}")
        hunk = {
            "old_start": int(match.group("old_start")),
            "old_count": int(match.group("old_count") or "1"),
            "new_start": int(match.group("new_start")),
            "new_count": int(match.group("new_count") or "1"),
            "lines": [],
        }
        index += 1
        while index < len(lines) and not lines[index].startswith("@@ "):
            current = lines[index]
            if current.startswith("\\"):
                index += 1
                continue
            if not current:
                raise ValueError("Unexpected empty patch line.")
            op = current[0]
            if op not in {" ", "+", "-"}:
                raise ValueError(f"Invalid patch operation line: {current!r}")
            hunk["lines"].append((op, current[1:]))
            index += 1
        hunks.append(hunk)
    return {"source_file": source_file, "target_file": target_file, "hunks": hunks}


def strip_diff_path(value: str) -> str:
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def require_source_line(original_lines: list[str], index: int, expected: str) -> None:
    if index >= len(original_lines):
        raise ValueError("Patch expects source line beyond end of file.")
    actual = original_lines[index]
    if actual != expected:
        raise ValueError(
            "Patch context mismatch. "
            f"Expected {expected!r}, found {actual!r} at source line {index + 1}."
        )


def resolve_patch_path(proposal: dict[str, Any], proposal_path: Path) -> Path:
    value = require_string(proposal, "patch_path")
    path = Path(value)
    if path.is_absolute() and path.exists():
        return path
    if path.exists():
        return path.resolve()
    sibling = proposal_path.parent / path.name
    if sibling.exists():
        return sibling.resolve()
    raise FileNotFoundError(f"Patch file not found: {value}")


def require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Proposal is missing required string field: {key}")
    return value


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def ensure_within_repo(repo: Path, target_path: Path) -> None:
    try:
        target_path.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"Target path escapes proposal repo: {target_path}") from exc
