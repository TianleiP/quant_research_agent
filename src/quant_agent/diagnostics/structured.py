from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_FINDING_KEYS = {
    "severity",
    "category",
    "title",
    "evidence",
    "recommended_action",
}


def parse_structured_diagnosis(raw_text: str) -> dict[str, Any]:
    payload = json.loads(extract_json_object(raw_text))
    validate_structured_diagnosis(payload)
    return payload


def validate_structured_diagnosis(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Structured diagnosis must be a JSON object.")
    required = ["summary", "findings", "missing_artifacts", "next_actions"]
    for key in required:
        if key not in payload:
            raise ValueError(f"Structured diagnosis missing required field: {key}")
    if not isinstance(payload["summary"], str):
        raise ValueError("Structured diagnosis field summary must be a string.")
    if not isinstance(payload["findings"], list):
        raise ValueError("Structured diagnosis field findings must be a list.")
    if not isinstance(payload["missing_artifacts"], list):
        raise ValueError("Structured diagnosis field missing_artifacts must be a list.")
    if not isinstance(payload["next_actions"], list):
        raise ValueError("Structured diagnosis field next_actions must be a list.")

    for index, finding in enumerate(payload["findings"]):
        if not isinstance(finding, dict):
            raise ValueError(f"Finding {index} must be an object.")
        missing = sorted(REQUIRED_FINDING_KEYS.difference(finding))
        if missing:
            raise ValueError(f"Finding {index} missing required fields: {', '.join(missing)}")
        if not isinstance(finding["evidence"], list):
            raise ValueError(f"Finding {index} evidence must be a list.")


def enrich_structured_diagnosis(
    payload: dict[str, Any],
    run_id: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["run_id"] = run_id
    enriched["provider"] = provider
    enriched["model"] = model
    return enriched


def render_structured_diagnosis_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Diagnosis: {payload.get('run_id', 'unknown')}",
        "",
        f"Provider: {payload.get('provider', 'unknown')}",
        f"Model: {payload.get('model', 'unknown')}",
        "",
        "## Summary",
        "",
        str(payload.get("summary", "")),
        "",
        "## Findings",
        "",
    ]
    findings = payload.get("findings", [])
    if findings:
        for finding in findings:
            lines.extend(
                [
                    f"- [{finding.get('severity', 'unknown')}] {finding.get('title', 'Untitled')}",
                    f"  Category: {finding.get('category', 'unknown')}",
                    "  Evidence:",
                ]
            )
            evidence = finding.get("evidence", [])
            if evidence:
                for item in evidence:
                    lines.append(f"  - {item}")
            else:
                lines.append("  - None provided.")
            lines.append(f"  Recommended action: {finding.get('recommended_action', '')}")
    else:
        lines.append("- No findings returned.")

    lines.extend(["", "## Missing Artifacts", ""])
    missing = payload.get("missing_artifacts", [])
    if missing:
        lines.extend(f"- {item}" for item in missing)
    else:
        lines.append("- None reported.")

    lines.extend(["", "## Next Actions", ""])
    actions = payload.get("next_actions", [])
    if actions:
        lines.extend(f"- {item}" for item in actions)
    else:
        lines.append("- None reported.")
    lines.append("")
    return "\n".join(lines)


def write_structured_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_json_object(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object.")
    return text[start : end + 1]
