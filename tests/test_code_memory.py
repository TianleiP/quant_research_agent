import json

from quant_agent.agent.tools import execute_agent_action
from quant_agent.agent.runner import run_agent_session
from quant_agent.cli import main
from quant_agent.memory import build_code_memory, query_code_memory, render_memory_query


def test_build_and_query_code_memory(tmp_path):
    repo = make_memory_repo(tmp_path)
    output_dir = tmp_path / "memory"

    result = build_code_memory(
        repo=repo,
        output_dir=output_dir,
        chunk_lines=20,
        overlap_lines=5,
        vector_size=128,
    )
    query = query_code_memory(
        index=output_dir / "code_memory.json",
        query="where are weights positions rankings exported to csv",
        top_k=3,
    )

    assert (output_dir / "code_memory.json").exists()
    assert (output_dir / "CODE_MEMORY.md").exists()
    assert result["index"]["summary"]["indexed_chunks"] >= 1
    assert query["result_count"] >= 1
    assert query["results"][0]["path"] == "research/run_strategy.py"
    assert "positions.csv" in query["results"][0]["excerpt"]
    assert "Code memory query:" in render_memory_query(query)


def test_memory_cli_build_and_query(tmp_path, capsys):
    repo = make_memory_repo(tmp_path)
    output_dir = tmp_path / "memory"

    build_exit = main(
        [
            "memory",
            "build",
            "--repo",
            str(repo),
            "--output",
            str(output_dir),
            "--chunk-lines",
            "20",
            "--overlap-lines",
            "5",
            "--vector-size",
            "128",
        ]
    )
    query_exit = main(
        [
            "memory",
            "query",
            "--index",
            str(output_dir / "code_memory.json"),
            "--query",
            "positions rankings csv",
            "--top-k",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert build_exit == 0
    assert query_exit == 0
    assert "Code memory written:" in captured.out
    assert "research/run_strategy.py" in captured.out


def test_agent_tool_queries_code_memory_with_inferred_args(tmp_path):
    repo = make_memory_repo(tmp_path)
    memory_dir = tmp_path / ".code_memory" / "sample"
    build_code_memory(repo=repo, output_dir=memory_dir, chunk_lines=20, overlap_lines=5)

    result = execute_agent_action(
        {
            "name": "query_code_memory",
            "args": {"query": "position export weights"},
            "rationale": "Find source context.",
            "requires_approval": False,
        },
        tmp_path,
    )

    assert result["action"] == "query_code_memory"
    assert result["result_count"] >= 1
    assert result["retrieval"]["mode"] == "lexical_symbol_fallback"
    assert result["results"][0]["path"] == "research/run_strategy.py"
    assert result["results"][0]["citation"].startswith("research/run_strategy.py:")


def test_llm_agent_prefetches_code_memory_before_fix_planning(tmp_path, monkeypatch):
    repo = make_memory_repo(tmp_path)
    build_code_memory(repo=repo, output_dir=tmp_path / ".code_memory" / "sample", chunk_lines=20, overlap_lines=5)
    make_agent_state_files(tmp_path)

    class CompleteClient:
        def complete(self, instructions, prompt):
            return json.dumps({"status": "complete"})

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: CompleteClient())

    result = run_agent_session(
        task="suggest missing export fix from source context",
        planner="llm",
        provider="mock",
        max_steps=4,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in result["actions"]]
    assert result["status"] == "complete"
    assert action_names == ["read_project_state", "read_capabilities", "query_code_memory"]


def test_code_memory_json_is_plain_json(tmp_path):
    repo = make_memory_repo(tmp_path)
    output_dir = tmp_path / "memory"

    build_code_memory(repo=repo, output_dir=output_dir)
    payload = json.loads((output_dir / "code_memory.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["chunks"]
    assert "vector" in payload["chunks"][0]
    assert "token_counts" in payload["chunks"][0]


def make_memory_repo(tmp_path):
    repo = tmp_path / "repo"
    research = repo / "research"
    research.mkdir(parents=True)
    (research / "run_strategy.py").write_text(
        "\n".join(
            [
                "import pandas as pd",
                "",
                "def simulate_strategy(panel):",
                "    weights = {'AAPL': 0.5, 'MSFT': 0.5}",
                "    positions = pd.DataFrame([{'symbol': k, 'weight': v} for k, v in weights.items()])",
                "    rankings = positions.assign(rank=[1, 2])",
                "    trades = positions.assign(weight_change=positions['weight'])",
                "    positions.to_csv('outputs/positions.csv', index=False)",
                "    rankings.to_csv('outputs/rankings.csv', index=False)",
                "    trades.to_csv('outputs/trades.csv', index=False)",
                "    return positions",
                "",
                "if __name__ == '__main__':",
                "    simulate_strategy(pd.DataFrame())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("Sample strategy research repo.\n", encoding="utf-8")
    return repo


def make_agent_state_files(root):
    state_dir = root / "state"
    state_dir.mkdir(exist_ok=True)
    write_json(
        state_dir / "project_state.json",
        {
            "project": {"name": "quant-agent"},
            "known_gaps": [],
            "next_recommended_steps": [],
        },
    )
    write_json(
        state_dir / "capabilities.json",
        {
            "capabilities": [
                {"name": "agent-session", "status": "implemented"},
                {"name": "memory", "status": "implemented"},
            ]
        },
    )


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
