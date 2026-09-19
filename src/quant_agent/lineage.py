from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LINEAGE_PATH = Path("state/strategy_lineage.json")


def load_lineage(path: Path = DEFAULT_LINEAGE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "updated_at": None,
            "strategies": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Lineage file must be a JSON object: {path}")
    payload.setdefault("schema_version", 1)
    payload.setdefault("strategies", {})
    return payload


def save_lineage(payload: dict[str, Any], path: Path = DEFAULT_LINEAGE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def update_strategy_lineage(
    *,
    strategy: str,
    previous: str | None = None,
    reason: str | None = None,
    changed_rules: list[str] | None = None,
    inherited_rules: list[str] | None = None,
    removed_rules: list[str] | None = None,
    audit_links: list[str] | None = None,
    status: str = "candidate",
    path: Path = DEFAULT_LINEAGE_PATH,
) -> dict[str, Any]:
    lineage = load_lineage(path)
    strategies = lineage.setdefault("strategies", {})
    existing = strategies.get(strategy, {}) if isinstance(strategies.get(strategy), dict) else {}
    entry = {
        **existing,
        "strategy": strategy,
        "previous_baseline": previous if previous is not None else existing.get("previous_baseline"),
        "reason": reason if reason is not None else existing.get("reason"),
        "changed_rules": changed_rules if changed_rules is not None else existing.get("changed_rules", []),
        "inherited_rules": inherited_rules if inherited_rules is not None else existing.get("inherited_rules", []),
        "removed_rules": removed_rules if removed_rules is not None else existing.get("removed_rules", []),
        "audit_links": audit_links if audit_links is not None else existing.get("audit_links", []),
        "status": status,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    strategies[strategy] = entry
    save_lineage(lineage, path)
    return entry


def strategy_lineage_entry(strategy: str, path: Path = DEFAULT_LINEAGE_PATH) -> dict[str, Any]:
    lineage = load_lineage(path)
    strategies = lineage.get("strategies", {})
    entry = strategies.get(strategy) if isinstance(strategies, dict) else None
    return entry if isinstance(entry, dict) else {}


def render_lineage(payload: dict[str, Any]) -> str:
    strategies = payload.get("strategies", {})
    if not isinstance(strategies, dict) or not strategies:
        return "No strategy lineage entries recorded."
    lines = ["Strategy Lineage:"]
    for name, entry in sorted(strategies.items()):
        previous = entry.get("previous_baseline") or "none"
        status = entry.get("status") or "unknown"
        lines.append(f"- {name} ({status}) <- {previous}")
        if entry.get("reason"):
            lines.append(f"  Reason: {entry['reason']}")
        changed = entry.get("changed_rules", [])
        if changed:
            lines.append("  Changed rules: " + "; ".join(str(item) for item in changed))
        audits = entry.get("audit_links", [])
        if audits:
            lines.append("  Audits: " + "; ".join(str(item) for item in audits))
    return "\n".join(lines)
