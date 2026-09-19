import json

import pytest

from quant_agent.agent.planning import normalize_evaluation
from quant_agent.agent.runner import resume_agent_session, run_agent_session


def test_plan_react_persists_multistep_plan_and_evaluates_each_step(tmp_path):
    make_project(tmp_path)

    result = run_agent_session(
        task="summarize latest metrics",
        planner="plan-react",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in result["actions"]]
    assert result["status"] == "complete"
    assert action_names == [
        "read_project_state",
        "read_capabilities",
        "metrics_latest",
        "inspect_latest_run",
    ]
    assert result["plan"]["status"] == "complete"
    assert [step["status"] for step in result["plan"]["steps"]] == ["complete", "complete"]
    assert len(result["evaluation_history"]) == 2
    assert result["replan_count"] == 0

    events = [
        json.loads(line)
        for line in (tmp_path / result["session_path"]).read_text(encoding="utf-8").splitlines()
    ]
    event_types = [event["type"] for event in events]
    assert "plan_created" in event_types
    assert event_types.count("evaluate") == 2


def test_plan_react_replans_remaining_work_once(tmp_path, monkeypatch):
    make_project(tmp_path)
    client = ReplanningClient(always_replan=False)
    monkeypatch.setattr("quant_agent.agent.planning.create_llm_client", lambda provider, model: client)
    monkeypatch.setattr("quant_agent.agent.react.create_llm_client", lambda provider, model: client)

    result = run_agent_session(
        task="summarize latest metrics",
        planner="plan-react",
        provider="mock",
        max_steps=5,
        max_replans=1,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in result["actions"]]
    assert result["status"] == "complete"
    assert action_names == [
        "read_project_state",
        "read_capabilities",
        "metrics_latest",
        "inspect_latest_run",
    ]
    assert result["replan_count"] == 1
    assert result["plan"]["version"] == 2
    assert len(result["plan_history"]) == 2
    assert [item["verdict"] for item in result["evaluation_history"]] == ["replan", "complete"]


def test_plan_react_blocks_when_replan_limit_is_exhausted(tmp_path, monkeypatch):
    make_project(tmp_path)
    client = ReplanningClient(always_replan=True)
    monkeypatch.setattr("quant_agent.agent.planning.create_llm_client", lambda provider, model: client)
    monkeypatch.setattr("quant_agent.agent.react.create_llm_client", lambda provider, model: client)

    result = run_agent_session(
        task="summarize latest metrics",
        planner="plan-react",
        provider="mock",
        max_steps=4,
        max_replans=0,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert result["status"] == "blocked"
    assert result["replan_count"] == 0
    assert "Replan limit reached" in result["final_message"]


def test_evaluator_cannot_complete_a_future_or_unmatched_step():
    with pytest.raises(ValueError, match="only the current"):
        normalize_evaluation(
            {
                "verdict": "continue",
                "completed_step_ids": ["v1_step_2"],
                "feedback": "skip ahead",
                "summary": "bad",
            },
            current_step={"id": "v1_step_1", "tool_names": ["metrics_latest"]},
            last_action={
                "status": "ok",
                "action": {"name": "metrics_latest"},
            },
        )


def test_plan_react_plan_survives_persisted_approval_interrupt(tmp_path):
    make_project(tmp_path)
    session_dir = tmp_path / "state" / "sessions"
    paused = run_agent_session(
        task="trace source",
        planner="plan-react",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=session_dir,
    )

    assert paused["status"] == "waiting_approval"
    assert paused["plan"]["version"] == 1
    assert paused["state"]["current_plan_step_id"] == "v1_step_1"

    resumed = resume_agent_session(
        paused["session_id"],
        reject=True,
        root=tmp_path,
        session_dir=session_dir,
    )

    assert resumed["status"] == "complete"
    assert resumed["plan"]["version"] == 1
    assert resumed["state"]["current_plan_step_id"] == "v1_step_1"


class ReplanningClient:
    def __init__(self, always_replan):
        self.always_replan = always_replan
        self.evaluations = 0
        self.tool_calls = 0

    def complete_structured(self, instructions, prompt, schema, schema_name):
        del instructions, schema
        if schema_name == "quant_agent_plan":
            return plan_payload("metrics_latest")
        if schema_name == "quant_agent_replan":
            return plan_payload("inspect_latest_run")
        if schema_name == "quant_agent_evaluation":
            self.evaluations += 1
            context = json.loads(prompt[prompt.find("{") :])
            step_id = context["current_step"]["id"]
            if self.evaluations == 1 or self.always_replan:
                verdict = "replan"
            else:
                verdict = "complete"
            return {
                "verdict": verdict,
                "completed_step_ids": [step_id],
                "feedback": "Revise the remaining evidence path." if verdict == "replan" else "Evidence is sufficient.",
                "summary": "Planned evidence is complete.",
            }
        raise AssertionError(schema_name)

    def decide_with_tools(self, instructions, messages, tools):
        del instructions, messages
        self.tool_calls += 1
        name = tools[0]["name"]
        return {
            "type": "tool_call",
            "call_id": f"planned_call_{self.tool_calls}",
            "name": name,
            "args": {},
        }


def plan_payload(tool_name):
    return {
        "goal": "Gather enough run evidence.",
        "steps": [
            {
                "title": tool_name.replace("_", " ").title(),
                "description": f"Run {tool_name}.",
                "tool_names": [tool_name],
                "success_criteria": ["The tool returns successfully."],
            }
        ],
    }


def make_project(root):
    state_dir = root / "state"
    state_dir.mkdir()
    (state_dir / "project_state.json").write_text(
        json.dumps(
            {
                "project": {"name": "quant-agent", "current_stage": "test"},
                "latest_verified_run": {"run_id": "demo_run"},
                "known_gaps": [],
                "next_recommended_steps": [],
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "capabilities.json").write_text(
        json.dumps({"capabilities": [{"name": "agent-session", "status": "implemented"}]}),
        encoding="utf-8",
    )
    run_dir = root / "runs" / "demo_run"
    (run_dir / "audits").mkdir(parents=True)
    write_json(run_dir / "manifest.json", {"strategy_name": "demo", "engine": "test"})
    write_json(
        run_dir / "metrics.json",
        {"variant_id": "demo", "cagr": 0.12, "max_drawdown": -0.2, "calmar": 0.6},
    )
    write_json(
        run_dir / "engine_metadata.json",
        {"expected_variant_id": "demo", "subprocess_ran": False, "exit_code": 0},
    )
    write_json(run_dir / "audits" / "artifact_contract.json", {"recommendation": "satisfied"})


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
