from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from evals.contracts import load_contracts
from evals.harness import run_scenario
from evals.scenario_drivers import get_driver


EVAL_ROOT = Path(__file__).resolve().parent


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic end-to-end quant-agent eval scenarios.")
    parser.add_argument("--scenario", action="append", default=[], help="Scenario id to run; repeatable.")
    parser.add_argument("--provider", help="Optional provider override for supported smoke scenarios.")
    parser.add_argument("--output", type=Path, help="Output directory; defaults under evals/results/.")
    parser.add_argument("--keep-workdirs", action="store_true", help="Keep isolated fixture directories for debugging.")
    args = parser.parse_args(argv)

    contracts = load_contracts(EVAL_ROOT / "scenarios")
    if args.scenario:
        requested = set(args.scenario)
        contracts = [item for item in contracts if item.id in requested]
        missing = requested - {item.id for item in contracts}
        if missing:
            parser.error("Unknown scenario(s): " + ", ".join(sorted(missing)))
    suite_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (args.output or EVAL_ROOT / "results" / suite_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [
        run_scenario(
            contract,
            get_driver(contract.driver),
            provider_override=args.provider,
            keep_workdir=args.keep_workdirs,
        )
        for contract in contracts
    ]
    payload = {
        "suite_id": suite_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": len(results),
        "passed": sum(1 for item in results if item.get("pass") is True),
        "failed": sum(1 for item in results if item.get("pass") is not True),
        "results": results,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **{key: payload[key] for key in ["passed", "failed"]}}, indent=2))
    return 0 if payload["failed"] == 0 else 1


def render_markdown(payload: dict) -> str:
    lines = [
        "# Quant-Agent Eval Summary",
        "",
        f"Suite: `{payload['suite_id']}`",
        "",
        f"Result: **{payload['passed']} passed, {payload['failed']} failed**",
        "",
        "| Scenario | Result | Graph | Terminal reason | Task success | Steps | Latency (ms) |",
        "|---|---:|---|---|---:|---:|---:|",
    ]
    for item in payload["results"]:
        lines.append(
            "| {scenario_id} | {status} | {graph} | {reason} | {task} | {steps} | {latency} |".format(
                scenario_id=item.get("scenario_id"),
                status="PASS" if item.get("pass") else "FAIL",
                graph=item.get("graph_status", "error"),
                reason=item.get("terminal_reason", item.get("error", "unknown")),
                task=item.get("task_success", "n/a"),
                steps=item.get("steps", "n/a"),
                latency=item.get("latency_ms", "n/a"),
            )
        )
    lines.extend(["", "## Failed checks", ""])
    failed_lines = []
    for item in payload["results"]:
        for check in item.get("checks", []):
            if check.get("pass") is not True:
                failed_lines.append(f"- `{item['scenario_id']}` / `{check['name']}`: expected `{check.get('expected')}`, got `{check.get('actual')}`")
        if item.get("error"):
            failed_lines.append(f"- `{item['scenario_id']}`: {item['error']}")
    lines.extend(failed_lines or ["- None."])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())

