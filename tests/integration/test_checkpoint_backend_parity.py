from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quant_agent.agent.graph import build_agent_graph
from quant_agent.agent.persistence import create_checkpointer, resolve_checkpoint_config
from quant_agent.agent.runner import checkpoint_config
from quant_agent.llm.secrets import read_dotenv_value


TEST_POSTGRES_URI_ENV = "QUANT_AGENT_TEST_POSTGRES_URI"


@pytest.mark.parametrize(
    "backend",
    [
        "sqlite",
        pytest.param("postgres", marks=pytest.mark.postgres),
    ],
)
@pytest.mark.parametrize("decision", ["reject", "approve"])
def test_checkpoint_backend_restart_interrupt_resume_parity(tmp_path, backend, decision):
    postgres_uri = _postgres_uri_or_skip() if backend == "postgres" else None
    root = tmp_path / f"{backend}_{decision}"
    _make_agent_fixture(root)
    env = _checkpoint_env(backend, postgres_uri)

    started = _run_cli(
        root,
        env,
        "agent-session",
        "--task",
        "trace source",
        "--planner",
        "llm",
        "--provider",
        "mock",
        "--max-steps",
        "5",
        "--checkpoint-backend",
        backend,
    )
    assert started.returncode == 0, started.stderr or started.stdout

    state_paths = list((root / "state" / "sessions").glob("*.state.json"))
    assert len(state_paths) == 1
    state_path = state_paths[0]
    paused_state = json.loads(state_path.read_text(encoding="utf-8"))
    thread_id = paused_state["thread_id"]
    assert paused_state["status"] == "waiting_approval"
    assert paused_state["checkpoint_backend"] == backend
    assert paused_state["session_id"] == thread_id

    serialized_evidence = state_path.read_text(encoding="utf-8") + Path(
        paused_state["session_path"]
    ).read_text(encoding="utf-8")
    if postgres_uri:
        assert postgres_uri not in serialized_evidence

    persistence = resolve_checkpoint_config(
        root=root,
        session_dir=root / "state" / "sessions",
        backend=backend,
        postgres_uri=postgres_uri,
    )
    with create_checkpointer(persistence) as checkpointer:
        graph = build_agent_graph(checkpointer=checkpointer)
        snapshot = graph.get_state(checkpoint_config(thread_id))
        assert snapshot.values["thread_id"] == thread_id
        assert snapshot.values["status"] == "waiting_approval"
        assert snapshot.tasks[0].interrupts

    # The second CLI process must recover from LangGraph persistence, not the
    # readable JSON snapshot written by the first process.
    state_path.unlink()
    resumed = _run_cli(
        root,
        env,
        "agent-resume",
        "--session",
        thread_id,
        f"--{decision}",
        "--checkpoint-backend",
        backend,
    )
    assert resumed.returncode == 0, resumed.stderr or resumed.stdout

    final_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert final_state["thread_id"] == thread_id
    assert final_state["status"] == "complete"
    trace_actions = [
        item
        for item in final_state["actions"]
        if item.get("action", {}).get("name") == "trace_source"
    ]

    if decision == "reject":
        assert trace_actions == []
        assert not (root / ".source_traces").exists()
        assert "approval rejected" in final_state["final_message"]
    else:
        assert len(trace_actions) == 1
        assert trace_actions[0]["status"] == "ok"
        assert trace_actions[0]["approved"] is True
        assert len(list((root / ".source_traces").glob("**/source_trace.json"))) == 1

    with create_checkpointer(persistence) as checkpointer:
        graph = build_agent_graph(checkpointer=checkpointer)
        final_snapshot = graph.get_state(checkpoint_config(thread_id))
        assert final_snapshot.values["status"] == "complete"
        assert not any(task.interrupts for task in final_snapshot.tasks)


def _postgres_uri_or_skip() -> str:
    pytest.importorskip("langgraph.checkpoint.postgres")
    uri = os.environ.get(TEST_POSTGRES_URI_ENV) or read_dotenv_value(
        Path.cwd() / ".env",
        TEST_POSTGRES_URI_ENV,
    )
    if not uri:
        pytest.skip(f"Set {TEST_POSTGRES_URI_ENV} to run PostgreSQL checkpoint tests.")
    return uri


def _checkpoint_env(backend: str, postgres_uri: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env["QUANT_AGENT_CHECKPOINT_BACKEND"] = backend
    if postgres_uri:
        env["QUANT_AGENT_CHECKPOINT_POSTGRES_URI"] = postgres_uri
    else:
        env.pop("QUANT_AGENT_CHECKPOINT_POSTGRES_URI", None)
    return env


def _run_cli(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from quant_agent.cli import main; raise SystemExit(main())",
            *args,
        ],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def _make_agent_fixture(root: Path) -> None:
    state_dir = root / "state"
    repo = root / "source_repo"
    run_dir = root / "runs" / "fixture_20260914_001"
    state_dir.mkdir(parents=True)
    repo.mkdir()
    run_dir.mkdir(parents=True)
    (repo / "script.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def export_summary(frame, output: Path):\n"
        "    weights = frame[['symbol', 'weight', 'rank']]\n"
        "    weights.to_csv(output / 'summary.csv', index=False)\n",
        encoding="utf-8",
    )
    (state_dir / "project_state.json").write_text(
        json.dumps(
            {
                "project": {"name": "checkpoint-fixture", "current_stage": "test"},
                "external_repos": [
                    {
                        "path": str(repo),
                        "entrypoint": "script.py",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "capabilities.json").write_text(
        json.dumps(
            {
                "capabilities": [
                    {"name": "read_project_state", "status": "implemented"},
                    {"name": "read_capabilities", "status": "implemented"},
                    {"name": "trace_source", "status": "implemented"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps({"cagr": 0.1, "max_drawdown": -0.05}),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"strategy_name": "fixture", "engine": "fixture"}),
        encoding="utf-8",
    )
