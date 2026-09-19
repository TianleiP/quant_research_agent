from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ApprovalDecision:
    action: str
    decision: str


@dataclass(frozen=True)
class ScenarioContract:
    id: str
    title: str
    driver: str
    planner: str
    provider: str
    task: str
    max_steps: int
    max_replans: int
    expected_graph_status: str
    expected_terminal_reason: str
    expected_task_success: bool
    required_selected_tools: list[str] = field(default_factory=list)
    required_executed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    exact_selected_tools: list[str] | None = None
    exact_executed_tools: list[str] | None = None
    approvals: list[ApprovalDecision] = field(default_factory=list)
    allowed_mutations: list[str] = field(default_factory=list)
    supports_real_provider: bool = False


def load_contract(path: Path) -> ScenarioContract:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Scenario contract must be a mapping: {path}")
    expected = payload.get("expected", {})
    approval = payload.get("approval", {})
    mutation = payload.get("mutation", {})
    tool_rules = payload.get("tools", {})
    decisions = [
        ApprovalDecision(action=str(item["action"]), decision=str(item["decision"]))
        for item in approval.get("decisions", [])
    ]
    contract = ScenarioContract(
        id=_required_text(payload, "id"),
        title=_required_text(payload, "title"),
        driver=_required_text(payload, "driver"),
        planner=_required_text(payload, "planner"),
        provider=_required_text(payload, "provider"),
        task=_required_text(payload, "task"),
        max_steps=int(payload.get("max_steps", 8)),
        max_replans=int(payload.get("max_replans", 2)),
        expected_graph_status=_required_text(expected, "graph_status"),
        expected_terminal_reason=_required_text(expected, "terminal_reason"),
        expected_task_success=bool(expected.get("task_success")),
        required_selected_tools=_text_list(tool_rules.get("required_selected_subsequence", [])),
        required_executed_tools=_text_list(tool_rules.get("required_executed_subsequence", [])),
        forbidden_tools=_text_list(tool_rules.get("forbidden", [])),
        exact_selected_tools=_optional_text_list(tool_rules.get("exact_selected_sequence")),
        exact_executed_tools=_optional_text_list(tool_rules.get("exact_executed_sequence")),
        approvals=decisions,
        allowed_mutations=_text_list(mutation.get("allowed_globs", [])),
        supports_real_provider=bool(payload.get("supports_real_provider", False)),
    )
    if contract.max_steps < 1:
        raise ValueError(f"Scenario {contract.id}: max_steps must be positive")
    if any(item.decision not in {"approve", "reject"} for item in decisions):
        raise ValueError(f"Scenario {contract.id}: approval decisions must be approve or reject")
    return contract


def load_contracts(directory: Path) -> list[ScenarioContract]:
    return [load_contract(path) for path in sorted(directory.glob("*.yaml"))]


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Required scenario field {key!r} is missing")
    return value.strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Scenario sequence fields must be lists of strings")
    return list(value)


def _optional_text_list(value: Any) -> list[str] | None:
    return None if value is None else _text_list(value)

