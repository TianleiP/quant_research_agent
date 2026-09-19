import json

from quant_agent.agent.argument_resolver import argument_context_for_prompt, resolve_agent_arguments
from quant_agent.agent.runner import run_agent_session
from quant_agent.agent.tools import execute_agent_action
from quant_agent.memory import build_code_memory


def test_argument_resolver_builds_defaults_from_state_run_and_proposal(tmp_path):
    repo = make_argument_planning_project(tmp_path)

    resolved = resolve_agent_arguments(tmp_path, task="trace the current source and suggest missing export fix")
    context = argument_context_for_prompt(tmp_path, task="trace the current source and suggest missing export fix")

    assert resolved["repo"] == str(repo)
    assert resolved["file"] == "research/run.py"
    assert resolved["config"] == "configs/xgboost_live_strategy.yaml"
    assert resolved["proposal"] == ".fix_proposals/latest/proposal.json"
    assert resolved["artifact_report"] == "runs/demo_run/audits/artifact_contract.json"
    assert resolved["promoted_variant_id"] == "base5_combo_accel2_mom12"
    assert context["action_defaults"]["trace_source"] == {
        "repo": str(repo),
        "file": "research/run.py",
        "issue": "missing-position-exports",
    }
    assert context["action_defaults"]["suggest_fix"]["artifact_report"] == "runs/demo_run/audits/artifact_contract.json"
    assert context["action_defaults"]["inspect_proposal"]["proposal"] == ".fix_proposals/latest/proposal.json"
    assert context["action_defaults"]["run_fresh"]["config"] == "configs/xgboost_live_strategy.yaml"


def test_llm_trace_source_args_are_resolved_before_approval(tmp_path, monkeypatch):
    repo = make_argument_planning_project(tmp_path)

    class EmptyTraceClient:
        def complete(self, instructions, prompt):
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": "trace_source",
                        "args": {},
                        "rationale": "Trace current source.",
                        "requires_approval": True,
                    },
                }
            )

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: EmptyTraceClient())

    result = run_agent_session(
        task="trace current source",
        planner="llm",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    approval = json.loads((tmp_path / result["approval_path"]).read_text(encoding="utf-8"))
    args = approval["action"]["args"]
    assert result["status"] == "waiting_approval"
    assert args["repo"] == str(repo)
    assert args["file"] == "research/run.py"
    assert args["issue"] == "missing-position-exports"


def test_llm_suggest_fix_args_are_resolved_before_approval(tmp_path, monkeypatch):
    repo = make_argument_planning_project(tmp_path)
    memory_dir = tmp_path / ".code_memory" / "external_repo"
    build_code_memory(repo=repo, output_dir=memory_dir, chunk_lines=20, overlap_lines=5)

    class EmptySuggestClient:
        def complete(self, instructions, prompt):
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": "suggest_fix",
                        "args": {},
                        "rationale": "Suggest the missing export fix.",
                        "requires_approval": True,
                    },
                }
            )

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: EmptySuggestClient())

    result = run_agent_session(
        task="suggest missing export fix",
        planner="llm",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    approval = json.loads((tmp_path / result["approval_path"]).read_text(encoding="utf-8"))
    args = approval["action"]["args"]
    assert result["status"] == "waiting_approval"
    assert args["repo"] == str(repo)
    assert args["file"] == "research/run.py"
    assert args["issue"] == "missing-position-exports"
    assert args["artifact_report"] == "runs/demo_run/audits/artifact_contract.json"
    assert args["memory_index"] == ".code_memory/external_repo/code_memory.json"


def test_argument_resolver_infers_generalized_fix_from_task(tmp_path):
    repo = make_argument_planning_project(tmp_path)

    context = argument_context_for_prompt(
        tmp_path,
        task="draft generalized fix for the current source behavior",
    )

    args = context["action_defaults"]["suggest_fix"]
    assert args["repo"] == str(repo)
    assert args["file"] == "research/run.py"
    assert args["issue"] == "generalized-fix"
    assert args["problem"] == "draft generalized fix for the current source behavior"


def test_execute_inspect_proposal_uses_latest_proposal_default(tmp_path):
    make_argument_planning_project(tmp_path)

    result = execute_agent_action(
        {"name": "inspect_proposal", "args": {}, "rationale": "Inspect latest proposal.", "requires_approval": False},
        tmp_path,
    )

    assert result["action"] == "inspect_proposal"
    assert result["proposal_path"] == ".fix_proposals/latest/proposal.json"
    assert result["issue"] == "missing-position-exports"


def make_argument_planning_project(root):
    state_dir = root / "state"
    config_dir = root / "configs"
    run_dir = root / "runs" / "demo_run"
    audits_dir = run_dir / "audits"
    proposal_dir = root / ".fix_proposals" / "latest"
    repo = root / "external_repo"
    repo_file = repo / "research" / "run.py"

    state_dir.mkdir()
    config_dir.mkdir()
    audits_dir.mkdir(parents=True)
    proposal_dir.mkdir(parents=True)
    repo_file.parent.mkdir(parents=True)

    (config_dir / "xgboost_live_strategy.yaml").write_text("strategy_name: demo\n", encoding="utf-8")
    repo_file.write_text(
        "import pandas as pd\n\n"
        "def main():\n"
        "    weights = {'AAPL': 1.0}\n"
        "    daily = pd.DataFrame([{'date': '2026-01-01'}])\n"
        "    daily.to_csv('out.csv', index=False)\n"
        "    return weights\n",
        encoding="utf-8",
    )
    write_json(
        state_dir / "project_state.json",
        {
            "latest_verified_run": {
                "run_id": "demo_run",
                "config": "configs/xgboost_live_strategy.yaml",
            },
            "external_repos": [
                {
                    "name": "xgboost",
                    "path": str(repo),
                    "entrypoint": "research/run.py",
                    "expected_variant_id": "base5_combo_accel2_mom12",
                }
            ],
        },
    )
    write_json(
        state_dir / "capabilities.json",
        {
            "capabilities": [
                {"name": "agent-session", "status": "implemented"},
                {"name": "trace-source", "status": "implemented"},
                {"name": "suggest-fix", "status": "implemented"},
            ]
        },
    )
    write_json(
        run_dir / "engine_metadata.json",
        {
            "working_dir": str(repo),
            "command": ["python", "research/run.py"],
            "expected_variant_id": "base5_combo_accel2_mom12",
            "source_metadata": {
                "source_script": "research\\run.py",
                "promoted_live_variant_id": "base5_combo_accel2_mom12",
            },
        },
    )
    write_json(
        audits_dir / "artifact_contract.json",
        {
            "recommendation": "review_required",
            "missing_position_level_artifacts": [{"name": "positions"}],
            "suggested_fix": {"issue": "missing-position-exports"},
        },
    )
    write_json(
        proposal_dir / "proposal.json",
        {
            "issue": "missing-position-exports",
            "status": "proposed",
            "apply_supported": True,
            "manual_review_required": True,
            "risk_level": "medium",
            "repo": str(repo),
            "file": "research/run.py",
        },
    )
    return repo


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
