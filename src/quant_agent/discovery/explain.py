from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quant_agent.discovery.scanner import default_output_dir, discover_repo, safe_component
from quant_agent.llm import create_llm_client


ENTRYPOINT_EXPLANATION_INSTRUCTIONS = """
You are a codebase entrypoint analyst for quantitative research backtests.
Explain the selected file using only the supplied discovery index and source
excerpt. Focus on execution flow, inputs, outputs, generated artifacts, global
constants, likely adapter contract, and safe edit points. Use plain ASCII
punctuation only. Do not claim runtime behavior that is not evidenced by the
index or source excerpt.
""".strip()


def explain_entrypoint(
    repo: Path,
    file_path: str,
    discovery_dir: Path | None = None,
    provider: str = "auto",
    model: str | None = None,
    save: bool = True,
    max_source_chars: int = 24_000,
) -> tuple[str, Path | None]:
    repo = repo.resolve()
    discovery_dir = (discovery_dir or default_output_dir(repo)).resolve()
    ensure_discovery(repo, discovery_dir)

    prompt = build_entrypoint_prompt(
        repo=repo,
        file_path=file_path,
        discovery_dir=discovery_dir,
        max_source_chars=max_source_chars,
    )
    client = create_llm_client(provider=provider, model=model)
    explanation = client.complete(ENTRYPOINT_EXPLANATION_INSTRUCTIONS, prompt)

    report_path = None
    if save:
        explanations_dir = discovery_dir / "explanations"
        explanations_dir.mkdir(parents=True, exist_ok=True)
        report_path = explanations_dir / f"{safe_component(file_path)}.md"
        report_path.write_text(explanation, encoding="utf-8")
    return explanation, report_path


def build_entrypoint_prompt(
    repo: Path,
    file_path: str,
    discovery_dir: Path,
    max_source_chars: int,
) -> str:
    normalized_file = normalize_repo_file(file_path)
    source_path = repo / normalized_file
    if not source_path.exists():
        raise FileNotFoundError(f"File does not exist under repo: {normalized_file}")

    symbols = read_json(discovery_dir / "symbols.json")
    entrypoints = read_json(discovery_dir / "entrypoints.json")
    artifact_paths = read_json(discovery_dir / "artifact_paths.json")
    repo_index = read_json(discovery_dir / "repo_index.json")

    python_file = find_by_path(symbols.get("python_files", []), normalized_file)
    entrypoint = find_by_path(entrypoints.get("entrypoints", []), normalized_file)
    artifact_refs = [
        item
        for item in artifact_paths.get("references", [])
        if item.get("source_file") == normalized_file
    ]
    file_record = find_by_path(repo_index.get("files", []), normalized_file)
    source_excerpt = source_path.read_text(encoding="utf-8", errors="replace")[:max_source_chars]

    payload: dict[str, Any] = {
        "repo": str(repo),
        "file": normalized_file,
        "file_record": file_record,
        "entrypoint_record": entrypoint,
        "python_symbol_record": python_file,
        "artifact_references_from_file": artifact_refs,
        "repo_summary": repo_index.get("summary", {}),
        "source_excerpt_truncated": len(source_excerpt) == max_source_chars,
        "source_excerpt": source_excerpt,
        "response_requirements": [
            "identify likely command or invocation pattern",
            "summarize execution flow in file order",
            "list inputs and hardcoded paths discovered from the excerpt/index",
            "list outputs and artifact writes discovered from the excerpt/index",
            "identify constants that may need adapter or metadata handling",
            "identify low-risk edit points and high-risk areas",
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def ensure_discovery(repo: Path, discovery_dir: Path) -> None:
    required = [
        discovery_dir / "repo_index.json",
        discovery_dir / "symbols.json",
        discovery_dir / "entrypoints.json",
        discovery_dir / "artifact_paths.json",
    ]
    if all(path.exists() for path in required):
        return
    discover_repo(repo=repo, output_dir=discovery_dir)


def normalize_repo_file(file_path: str) -> str:
    path = Path(file_path)
    if path.is_absolute():
        raise ValueError("--file must be relative to --repo")
    return path.as_posix()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Discovery artifact is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"value": payload}


def find_by_path(items: list[dict[str, Any]], path: str) -> dict[str, Any]:
    for item in items:
        if item.get("path") == path:
            return item
    return {}
