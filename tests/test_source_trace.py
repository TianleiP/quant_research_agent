import json

from quant_agent.fixes.source_trace import build_source_trace, trace_source_file


READY_SOURCE = """from __future__ import annotations


def main():
    weights = build_weights()
    ranks = build_ranks()
    selected_symbols = list(weights.columns)
    daily.to_csv("daily.csv", index=False)
    summary.to_csv("summary.csv", index=False)
    return weights, ranks, selected_symbols
"""


NEEDS_CONTEXT_SOURCE = """from __future__ import annotations


def main():
    print("Building selector weights")
    daily.to_csv("daily.csv", index=False)
    summary.to_csv("summary.csv", index=False)
"""


def test_build_source_trace_ready_for_patch_when_export_scope_has_objects():
    trace = build_source_trace(
        source=READY_SOURCE,
        relative_file="research/run_strategy.py",
        issue="missing-position-exports",
    )

    assert trace["syntax_ok"] is True
    assert trace["patch_readiness"]["status"] == "ready_for_patch"
    assert len(trace["to_csv_calls"]) == 2
    assert any(item["name"] == "weights" for item in trace["symbol_object_candidates"])
    assert any(function["qualname"] == "main" for function in trace["functions"])


def test_build_source_trace_needs_context_without_assigned_symbol_objects():
    trace = build_source_trace(
        source=NEEDS_CONTEXT_SOURCE,
        relative_file="research/run_strategy.py",
        issue="missing-position-exports",
    )

    assert trace["patch_readiness"]["status"] == "impossible_from_saved_state"


def test_trace_source_file_writes_json_and_markdown(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "research" / "run_strategy.py"
    target.parent.mkdir(parents=True)
    target.write_text(READY_SOURCE, encoding="utf-8")
    output_dir = tmp_path / "trace"

    result = trace_source_file(
        repo=repo,
        file_path="research/run_strategy.py",
        output_dir=output_dir,
    )

    payload = json.loads(output_dir.joinpath("source_trace.json").read_text(encoding="utf-8"))
    assert result["json_path"] == str(output_dir / "source_trace.json")
    assert result["markdown_path"] == str(output_dir / "source_trace.md")
    assert payload["patch_readiness"]["status"] == "ready_for_patch"
    assert "Patch readiness" in output_dir.joinpath("source_trace.md").read_text(encoding="utf-8")
