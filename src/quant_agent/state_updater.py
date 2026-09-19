from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import tomllib

from quant_agent.registry import latest_run_dir


def update_project_state(
    root: Path | None = None,
    state_path: Path | None = None,
    run: str = "latest",
    test_status: str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    resolved_root = (root or Path.cwd()).resolve()
    resolved_state_path = state_path or (resolved_root / "state" / "project_state.json")
    state = read_json_if_exists(resolved_state_path)
    if not state:
        state = {"schema_version": 1}

    state["last_updated_local"] = datetime.now().date().isoformat()
    state.setdefault("project", {})
    state.setdefault("environment", {})
    state["project"].update(project_payload(resolved_root))
    state["environment"].update(environment_payload(resolved_root, test_status))

    run_dir = resolve_run_dir(resolved_root, run)
    if run_dir:
        state["latest_verified_run"] = latest_run_payload(resolved_root, run_dir, state.get("latest_verified_run", {}))

    capability_path = resolved_root / "state" / "capabilities.json"
    capabilities = capability_summary(capability_path)
    if capabilities:
        state["capability_status"] = capabilities

    state["state_refresh"] = {
        "updated_at_local": datetime.now().isoformat(timespec="seconds"),
        "state_path": relative_to_root(resolved_state_path, resolved_root),
        "latest_run": state.get("latest_verified_run", {}).get("run_id"),
        "test_status_supplied": test_status,
    }

    if write:
        resolved_state_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def project_payload(root: Path) -> dict[str, Any]:
    pyproject = read_pyproject(root / "pyproject.toml")
    project = pyproject.get("project", {}) if isinstance(pyproject.get("project"), dict) else {}
    payload = {
        "root": root.as_posix(),
    }
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str):
        payload["requires_python"] = requires_python
    dependencies = project.get("dependencies")
    if isinstance(dependencies, list) and any(str(item).lower().startswith("langgraph") for item in dependencies):
        payload["agent_orchestration_dependency"] = "langgraph"
    return payload


def environment_payload(root: Path, test_status: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "official_agent_runtime_python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "venv311_exists": (root / ".venv311").exists(),
        "latest_test_command": ".venv311/Scripts/python.exe -m pytest",
        "latest_test_command_py311": ".venv311/Scripts/python.exe -m pytest",
    }
    try:
        payload["langgraph_version"] = metadata.version("langgraph")
    except metadata.PackageNotFoundError:
        payload["langgraph_version"] = None
    if test_status:
        payload["latest_test_status"] = test_status
        payload["latest_test_status_py311"] = test_status
    return payload


def latest_run_payload(root: Path, run_dir: Path, previous: Any) -> dict[str, Any]:
    previous = previous if isinstance(previous, dict) else {}
    manifest = read_json_if_exists(run_dir / "manifest.json")
    metrics = read_json_if_exists(run_dir / "metrics.json")
    engine_metadata = read_json_if_exists(run_dir / "engine_metadata.json")
    artifact_contract = read_json_if_exists(run_dir / "audits" / "artifact_contract.json")
    promotion, promotion_path = latest_promotion(run_dir)
    markdown_path = promotion_path.with_suffix(".md") if promotion_path else None
    promotion_closure = read_json_if_exists(run_dir / "audits" / "promotion_closure.json")
    closure_markdown_path = run_dir / "audits" / "promotion_closure.md"
    run_id = run_dir.name

    config = previous.get("config") if previous.get("run_id") == run_id else None
    if not config:
        config = relative_to_root(run_dir / "config.yaml", root)

    return {
        "run_id": run_id,
        "run_dir": relative_to_root(run_dir, root),
        "config": config,
        "fresh_subprocess_run": bool(engine_metadata.get("subprocess_ran")),
        "elapsed_seconds": engine_metadata.get("elapsed_seconds"),
        "strategy_name": manifest.get("strategy_name") or metrics.get("variant_id") or run_id,
        "expected_variant_id": engine_metadata.get("expected_variant_id") or metrics.get("variant_id"),
        "primary_window": engine_metadata.get("primary_window") or metrics.get("primary_window"),
        "metrics": display_metrics(metrics),
        "position_level_artifacts": position_level_artifacts(artifact_contract),
        "promotion": promotion_payload(root, promotion, promotion_path, markdown_path),
        "promotion_closure": promotion_closure_payload(root, promotion_closure, closure_markdown_path),
    }


def display_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if isinstance(metrics.get("cagr"), (int, float)):
        payload["cagr_percent_display"] = f"{metrics['cagr']:.1%}"
    if isinstance(metrics.get("max_drawdown"), (int, float)):
        payload["max_drawdown_percent_display"] = f"{metrics['max_drawdown']:.1%}"
    if isinstance(metrics.get("calmar"), (int, float)):
        payload["calmar"] = round(float(metrics["calmar"]), 2)
    if isinstance(metrics.get("turnover_per_year"), (int, float)):
        payload["turnover_per_year"] = round(float(metrics["turnover_per_year"]), 1)
    if isinstance(metrics.get("exposure"), (int, float)):
        payload["exposure_percent_display"] = f"{metrics['exposure']:.1%}"
    return payload


def position_level_artifacts(artifact_contract: dict[str, Any]) -> dict[str, Any]:
    checks = artifact_contract.get("checks", [])
    by_name = {item.get("name"): item for item in checks if isinstance(item, dict)}
    return {
        "positions_rows": by_name.get("positions", {}).get("rows"),
        "trades_rows": by_name.get("trades", {}).get("rows"),
        "rankings_rows": by_name.get("rankings", {}).get("rows"),
        "artifact_contract_recommendation": artifact_contract.get("recommendation"),
    }


def promotion_payload(
    root: Path,
    promotion: dict[str, Any],
    json_path: Path | None,
    markdown_path: Path | None,
) -> dict[str, Any]:
    blocking = promotion.get("blocking_issues", [])
    warnings = promotion.get("warnings", [])
    return {
        "recommendation": promotion.get("recommendation"),
        "failures": [item.get("code") for item in blocking if isinstance(item, dict)],
        "warnings": [item.get("code") for item in warnings if isinstance(item, dict)],
        "json": relative_to_root(json_path, root) if json_path else None,
        "markdown": relative_to_root(markdown_path, root) if markdown_path and markdown_path.exists() else None,
    }


def promotion_closure_payload(
    root: Path,
    closure: dict[str, Any],
    markdown_path: Path,
) -> dict[str, Any]:
    items = closure.get("items", []) if isinstance(closure.get("items"), list) else []
    return {
        "recommendation": closure.get("closure_recommendation"),
        "summary": closure.get("summary", {}),
        "items": [
            {"code": item.get("code"), "status": item.get("status")}
            for item in items
            if isinstance(item, dict)
        ],
        "json": relative_to_root(markdown_path.with_suffix(".json"), root)
        if markdown_path.with_suffix(".json").exists()
        else None,
        "markdown": relative_to_root(markdown_path, root) if markdown_path.exists() else None,
    }


def latest_promotion(run_dir: Path) -> tuple[dict[str, Any], Path | None]:
    candidates = sorted((run_dir / "audits").glob("PROMOTION_AUDIT_*.json"))
    if not candidates:
        return {}, None
    path = candidates[-1]
    return read_json_if_exists(path), path


def capability_summary(path: Path) -> dict[str, Any]:
    payload = read_json_if_exists(path)
    capabilities = payload.get("capabilities", []) if isinstance(payload.get("capabilities"), list) else []
    if not capabilities:
        return {}
    return {
        "capability_count": len(capabilities),
        "implemented": [item.get("name") for item in capabilities if item.get("status") == "implemented"],
        "partial": [item.get("name") for item in capabilities if item.get("status") == "partial"],
        "planned": [item.get("name") for item in capabilities if item.get("status") == "planned"],
    }


def resolve_run_dir(root: Path, run: str) -> Path | None:
    runs_root = root / "runs"
    if not runs_root.exists():
        return None
    if run == "latest":
        try:
            return latest_run_dir(runs_root)
        except FileNotFoundError:
            return None
    return runs_root / run


def read_pyproject(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def relative_to_root(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)
