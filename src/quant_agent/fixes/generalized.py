from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.fixes.proposal_contract import attach_proposal_contract, collect_memory_context
from quant_agent.fixes.source_trace import build_source_trace, render_source_trace_markdown
from quant_agent.llm.client import create_llm_client


ISSUE = "generalized-fix"

GENERALIZED_FIX_INSTRUCTIONS = """You are quant-agent's generalized fix proposal drafter.
Return one strict JSON object and no markdown.
This is review-only: do not claim the patch was applied or safe to auto-apply.
Use only the provided source, source trace, and memory context.
Required fields:
{
  "summary": "short summary",
  "problem_statement": "specific bug or missing behavior",
  "intended_behavior": ["observable desired behavior"],
  "patch_plan": ["ordered implementation step"],
  "candidate_unified_diff": "optional unified diff beginning with ---/+++ or empty string",
  "verification_commands": ["command to verify"],
  "risks": ["risk or assumption"],
  "confidence": "low|medium|high"
}
"""


def suggest_generalized_fix(
    repo: Path,
    file_path: str,
    problem: str,
    output_dir: Path | None = None,
    memory_index: Path | None = None,
    memory_query: str | None = None,
    provider: str = "auto",
    model: str | None = None,
    max_source_chars: int = 24_000,
) -> dict[str, Any]:
    repo = repo.resolve()
    relative_file = normalize_repo_file(file_path)
    source_path = repo / relative_file
    if not source_path.exists():
        raise FileNotFoundError(f"Target file does not exist under repo: {relative_file}")

    source = source_path.read_text(encoding="utf-8", errors="replace")
    target_dir = proposal_output_dir(output_dir, relative_file)
    target_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = target_dir / "proposal.json"
    readme_path = target_dir / "README.md"
    trace_path = target_dir / "source_trace.json"
    trace_markdown_path = target_dir / "source_trace.md"
    draft_raw_path = target_dir / "llm_draft.raw.txt"
    draft_json_path = target_dir / "llm_draft.json"
    candidate_path = target_dir / "candidate.patch"

    trace = build_source_trace(source=source, relative_file=relative_file, issue=ISSUE)
    trace["repo"] = str(repo)
    trace["artifacts"] = {
        "source_trace_json": str(trace_path),
        "source_trace_markdown": str(trace_markdown_path),
    }
    memory_context = collect_memory_context(
        memory_index,
        memory_query or generalized_memory_query(relative_file, problem),
    )
    prompt = build_generalized_fix_prompt(
        repo=repo,
        relative_file=relative_file,
        problem=problem,
        source=source,
        trace=trace,
        memory_context=memory_context,
        max_source_chars=max_source_chars,
    )
    client = create_llm_client(provider=provider, model=model)
    raw = client.complete(GENERALIZED_FIX_INSTRUCTIONS, prompt)
    draft = parse_generalized_draft(raw)
    candidate_diff = str(draft.get("candidate_unified_diff") or "").strip()
    if candidate_diff:
        candidate_path.write_text(candidate_diff + "\n", encoding="utf-8")

    proposal = {
        "issue": ISSUE,
        "status": "proposed",
        "risk_level": "high",
        "repo": str(repo),
        "file": relative_file,
        "problem_input": problem,
        "metadata_path": str(metadata_path),
        "patch_path": None,
        "draft_json_path": str(draft_json_path),
        "draft_raw_path": str(draft_raw_path),
        "candidate_patch_path": str(candidate_path) if candidate_diff else None,
        "source_trace_path": str(trace_path),
        "source_trace_markdown_path": str(trace_markdown_path),
        "apply_supported": False,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "provider": getattr(client, "provider", provider),
        "model": getattr(client, "model", model or "unknown"),
        "summary": text_or_default(draft.get("summary"), "LLM drafted a review-only generalized fix proposal."),
        "manual_review_required": True,
        "apply_instruction": (
            "This is a review-only generalized proposal. Review llm_draft.json, source_trace.md, "
            "and candidate.patch if present. Convert it into a deterministic reviewed proposal before applying."
        ),
    }
    attach_proposal_contract(
        proposal,
        problem={
            "issue": ISSUE,
            "statement": text_or_default(draft.get("problem_statement"), problem),
            "impact": "The requested change is outside quant-agent's deterministic fix generators.",
        },
        target_files=[
            {
                "path": relative_file,
                "role": "LLM-reviewed source target",
                "change_type": "review_only",
            }
        ],
        intended_behavior=string_list_or_default(
            draft.get("intended_behavior"),
            ["Implement the requested behavior while preserving existing backtest semantics."],
        ),
        patch_plan=patch_plan_items(draft.get("patch_plan"), relative_file),
        verification={
            "commands": string_list_or_default(
                draft.get("verification_commands"),
                [
                    "Review llm_draft.json and candidate.patch.",
                    "Create a deterministic proposal before running apply-fix.",
                    "Run the relevant strategy or test suite after a reviewed patch is generated.",
                ],
            ),
            "expected": [
                "The candidate patch is manually reviewed and converted into a deterministic proposal.",
                "No code is modified by this generalized proposal itself.",
            ],
        },
        evidence=[
            {
                "kind": "source_file",
                "path": relative_file,
                "detail": "Source excerpt was provided to the LLM draft step.",
            },
            {
                "kind": "source_trace",
                "path": str(trace_path),
                "detail": "Static source trace attached for review.",
            },
            {
                "kind": "llm_draft",
                "path": str(draft_json_path),
                "provider": getattr(client, "provider", provider),
                "model": getattr(client, "model", model or "unknown"),
            },
        ],
        memory_context=memory_context,
    )

    trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    trace_markdown_path.write_text(render_source_trace_markdown(trace), encoding="utf-8")
    draft_raw_path.write_text(raw, encoding="utf-8")
    draft_json_path.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata_path.write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    readme_path.write_text(render_readme(proposal, draft), encoding="utf-8")

    return {
        "proposal_dir": str(target_dir),
        "metadata_path": str(metadata_path),
        "readme_path": str(readme_path),
        "draft_json_path": str(draft_json_path),
        "draft_raw_path": str(draft_raw_path),
        "candidate_patch_path": str(candidate_path) if candidate_diff else None,
        "source_trace_path": str(trace_path),
        "source_trace_markdown_path": str(trace_markdown_path),
        "proposal": proposal,
        "draft": draft,
        "source_trace": trace,
    }


def build_generalized_fix_prompt(
    *,
    repo: Path,
    relative_file: str,
    problem: str,
    source: str,
    trace: dict[str, Any],
    memory_context: dict[str, Any],
    max_source_chars: int,
) -> str:
    excerpt = source[:max_source_chars]
    if len(source) > max_source_chars:
        excerpt += "\n\n# <source truncated>"
    context = {
        "repo": str(repo),
        "file": relative_file,
        "problem": problem,
        "source_excerpt": excerpt,
        "source_trace": {
            "syntax_ok": trace.get("syntax_ok"),
            "functions": trace.get("functions", [])[:20],
            "classes": trace.get("classes", [])[:20],
            "to_csv_calls": trace.get("to_csv_calls", [])[:20],
            "symbol_object_candidates": trace.get("symbol_object_candidates", [])[:30],
            "patch_readiness": trace.get("patch_readiness", {}),
        },
        "memory_context": memory_context,
    }
    return "Generalized fix drafting context JSON:\n" + json.dumps(context, indent=2, sort_keys=True)


def parse_generalized_draft(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError:
        payload = json.loads(extract_json_object(raw))
    if not isinstance(payload, dict):
        raise ValueError("Generalized fix draft must be a JSON object.")
    return payload


def extract_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in generalized fix draft.")
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("Unclosed JSON object in generalized fix draft.")


def patch_plan_items(value: Any, relative_file: str) -> list[dict[str, Any]]:
    items = string_list_or_default(value, ["Review the source, draft the change, and add verification."])
    return [
        {
            "step": index,
            "target": relative_file,
            "description": item,
        }
        for index, item in enumerate(items, start=1)
    ]


def string_list_or_default(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned or default


def text_or_default(value: Any, default: str) -> str:
    return str(value).strip() if isinstance(value, str) and value.strip() else default


def generalized_memory_query(relative_file: str, problem: str) -> str:
    return f"{relative_file} {problem} fix patch verify source context"


def render_readme(proposal: dict[str, Any], draft: dict[str, Any]) -> str:
    risks = string_list_or_default(draft.get("risks"), ["Manual review required before any implementation."])
    lines = [
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
        "- This proposal is review-only and is not auto-applicable.",
        "- Review `llm_draft.json` and `source_trace.md`.",
    ]
    if proposal.get("candidate_patch_path"):
        lines.append("- Review `candidate.patch`; it is not wired to `apply-fix`.")
    lines.extend(["", "## Risks", ""])
    lines.extend(f"- {risk}" for risk in risks)
    lines.append("")
    return "\n".join(lines)


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
