from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from quant_agent.llm import create_llm_client
from quant_agent.llm.prompts import (
    DIAGNOSIS_INSTRUCTIONS,
    STRUCTURED_DIAGNOSIS_INSTRUCTIONS,
    build_diagnosis_prompt,
)
from quant_agent.diagnostics.structured import (
    enrich_structured_diagnosis,
    parse_structured_diagnosis,
    render_structured_diagnosis_markdown,
    write_structured_json,
)


@dataclass(frozen=True)
class DiagnosisResult:
    text: str
    paths: dict[str, Path] = field(default_factory=dict)
    structured: dict | None = None
    raw_text: str | None = None


def diagnose_anomaly(
    run_dir: Path,
    provider: str = "auto",
    model: str | None = None,
    save: bool = True,
) -> tuple[str, Path | None]:
    result = diagnose_run(
        run_dir=run_dir,
        provider=provider,
        model=model,
        save=save,
        output_format="markdown",
    )
    return result.text, result.paths.get("markdown")


def diagnose_run(
    run_dir: Path,
    provider: str = "auto",
    model: str | None = None,
    save: bool = True,
    output_format: str = "both",
) -> DiagnosisResult:
    if output_format not in {"markdown", "json", "both"}:
        raise ValueError(f"Unsupported diagnosis format: {output_format}")

    prompt = build_diagnosis_prompt(run_dir)
    client = create_llm_client(provider=provider, model=model)

    if output_format == "markdown":
        diagnosis = client.complete(DIAGNOSIS_INSTRUCTIONS, prompt)
        paths = {}
        if save:
            path = run_dir / "diagnosis.md"
            path.write_text(diagnosis, encoding="utf-8")
            paths["markdown"] = path
        return DiagnosisResult(text=diagnosis, paths=paths, raw_text=diagnosis)

    raw = client.complete(STRUCTURED_DIAGNOSIS_INSTRUCTIONS, prompt)
    try:
        structured = parse_structured_diagnosis(raw)
    except (ValueError, TypeError) as exc:
        if save:
            raw_path = run_dir / "diagnosis.raw.txt"
            raw_path.write_text(raw, encoding="utf-8")
        raise RuntimeError(
            "LLM did not return valid structured diagnosis JSON. "
            "Raw output was saved to diagnosis.raw.txt when saving is enabled."
        ) from exc

    structured = enrich_structured_diagnosis(
        structured,
        run_id=run_dir.name,
        provider=getattr(client, "provider", provider),
        model=getattr(client, "model", model or "unknown"),
    )
    markdown = render_structured_diagnosis_markdown(structured)
    paths = {}
    if save:
        json_path = run_dir / "diagnosis.json"
        write_structured_json(json_path, structured)
        paths["json"] = json_path
        if output_format == "both":
            markdown_path = run_dir / "diagnosis.md"
            markdown_path.write_text(markdown, encoding="utf-8")
            paths["markdown"] = markdown_path

    text = markdown if output_format == "both" else json.dumps(structured, indent=2, sort_keys=True)
    return DiagnosisResult(text=text, paths=paths, structured=structured, raw_text=raw)
