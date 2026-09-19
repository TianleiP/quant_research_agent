import json

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from quant_agent.agent.graph import build_agent_graph
from quant_agent.agent.lifecycle import RevisionLoopBlocked, choose_patch_lifecycle_action
from quant_agent.agent.nodes import decide_node
from quant_agent.agent.planner import parse_planner_response
from quant_agent.agent.runner import resume_agent_session, run_agent_session
from quant_agent.fixes.stale_metadata import suggest_stale_metadata_fix


def test_agent_session_runs_langgraph_loop_for_metrics_task(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)

    result = run_agent_session(
        task="summarize latest metrics",
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
    assert "Latest run: demo_20260610_001" in result["final_message"]
    assert "CAGR=12.0%" in result["final_message"]
    assert result["session_path"].endswith(".jsonl")

    events = [
        json.loads(line)
        for line in (tmp_path / result["session_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert [event["type"] for event in events][:3] == ["start", "observe", "decide"]


def test_agent_session_can_run_artifact_inspection(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)

    result = run_agent_session(
        task="inspect latest artifact contract",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in result["actions"]]
    assert result["status"] == "complete"
    assert "inspect_artifacts_latest" in action_names
    assert (tmp_path / "runs" / "demo_20260610_001" / "audits" / "artifact_contract.json").exists()
    assert "Artifact inspection: satisfied" in result["final_message"]


def test_agent_session_completes_when_last_action_uses_exact_step_budget(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)

    result = run_agent_session(
        task="inspect latest artifact status",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert result["status"] == "complete"
    assert result["step"] == 5
    assert "Agent session completed." in result["final_message"]


def test_agent_session_llm_planner_uses_mock_provider(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)

    result = run_agent_session(
        task="inspect artifact and promotion status with metrics",
        planner="llm",
        provider="mock",
        max_steps=6,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in result["actions"]]
    assert result["status"] == "complete"
    assert action_names == [
        "read_project_state",
        "read_capabilities",
        "inspect_artifacts_latest",
        "promote_latest",
        "metrics_latest",
        "inspect_latest_run",
    ]
    assert "Promotion audit:" in result["final_message"]


def test_agent_session_react_loops_native_tool_result_back_to_model(tmp_path, monkeypatch):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)
    calls = []

    class ReactClient:
        def decide_with_tools(self, instructions, messages, tools):
            calls.append({"instructions": instructions, "messages": messages, "tools": tools})
            if len(calls) == 1:
                return {
                    "type": "tool_call",
                    "call_id": "call_metrics_1",
                    "name": "metrics_latest",
                    "args": {},
                }
            tool_message = messages[-1]
            assert tool_message["role"] == "tool"
            assert tool_message["tool_call_id"] == "call_metrics_1"
            assert '"cagr": 0.12' in tool_message["content"]
            return {"type": "final", "content": "Metrics evidence is sufficient."}

    monkeypatch.setattr(
        "quant_agent.agent.react.create_llm_client",
        lambda provider, model: ReactClient(),
    )
    result = run_agent_session(
        task="summarize latest metrics",
        planner="react",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in result["actions"]]
    assert result["status"] == "complete"
    assert action_names == ["read_project_state", "read_capabilities", "metrics_latest"]
    assert result["react_trace"][0]["type"] == "tool_call"
    assert result["react_trace"][-1] == {
        "type": "final",
        "content": "Metrics evidence is sufficient.",
    }
    assert "Model conclusion: Metrics evidence is sufficient." in result["final_message"]


def test_agent_session_react_derives_approval_from_policy_not_model(tmp_path, monkeypatch):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)

    class RiskyReactClient:
        def decide_with_tools(self, instructions, messages, tools):
            return {
                "type": "tool_call",
                "call_id": "call_trace_1",
                "name": "trace_source",
                "args": {"repo": str(tmp_path), "file": "strategy.py"},
            }

    monkeypatch.setattr(
        "quant_agent.agent.react.create_llm_client",
        lambda provider, model: RiskyReactClient(),
    )
    result = run_agent_session(
        task="trace source",
        planner="react",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert result["status"] == "waiting_approval"
    assert result["state"]["current_action"]["name"] == "trace_source"
    assert result["state"]["current_action"]["requires_approval"] is True
    assert result["interrupts"]


def test_agent_session_llm_planner_blocks_risky_action(tmp_path, monkeypatch):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)

    class RiskyClient:
        def complete(self, instructions, prompt):
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": "install_package",
                        "args": {},
                        "rationale": "bad idea",
                        "requires_approval": False,
                    },
                }
            )

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: RiskyClient())

    result = run_agent_session(
        task="apply fix",
        planner="llm",
        provider="mock",
        max_steps=4,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert result["status"] == "blocked"
    assert "blocked" in result["final_message"]
    assert result["errors"]


def test_agent_session_pauses_for_approval_and_rejects(tmp_path, monkeypatch):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)
    repo = make_source_repo(tmp_path)

    class TraceClient:
        def complete(self, instructions, prompt):
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": "trace_source",
                        "args": {
                            "repo": str(repo),
                            "file": "script.py",
                            "output": str(tmp_path / "trace_out"),
                        },
                        "rationale": "Trace source before proposing a fix.",
                        "requires_approval": True,
                    },
                }
            )

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: TraceClient())

    result = run_agent_session(
        task="trace source",
        planner="llm",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert result["status"] == "waiting_approval"
    approval_path = tmp_path / result["approval_path"]
    assert approval_path.exists()
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["action"]["name"] == "trace_source"

    rejected = resume_agent_session(
        session=result["session_id"],
        reject=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert rejected["status"] == "complete"
    assert "approval rejected" in rejected["final_message"]
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["status"] == "rejected"


def test_agent_session_approves_trace_source(tmp_path, monkeypatch):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)
    repo = make_source_repo(tmp_path)

    class TraceThenCompleteClient:
        def complete(self, instructions, prompt):
            if "trace_source" in prompt and '"completed_actions"' in prompt:
                context = json.loads(prompt[prompt.find("{") :])
                if "trace_source" in context.get("completed_actions", []):
                    return json.dumps({"status": "complete"})
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": "trace_source",
                        "args": {
                            "repo": str(repo),
                            "file": "script.py",
                            "output": str(tmp_path / "trace_out"),
                        },
                        "rationale": "Trace source before proposing a fix.",
                        "requires_approval": True,
                    },
                }
            )

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: TraceThenCompleteClient())

    result = run_agent_session(
        task="trace source",
        planner="llm",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )
    assert result["status"] == "waiting_approval"

    approved = resume_agent_session(
        session=result["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert approved["status"] == "complete"
    action_names = [record["action"]["name"] for record in approved["actions"]]
    assert action_names[-1] == "trace_source"
    assert (tmp_path / "trace_out" / "source_trace.json").exists()


def test_agent_session_persists_native_checkpoint_and_interrupt(tmp_path, monkeypatch):
    result = start_trace_interrupt(tmp_path, monkeypatch)

    assert result["status"] == "waiting_approval"
    assert result["thread_id"] == result["session_id"]
    assert result["checkpoint_id"]
    assert result["interrupts"]
    assert result["interrupts"][0]["value"]["action"]["name"] == "trace_source"
    assert result["interrupts"][0]["value"]["thread_id"] == result["thread_id"]

    checkpoint_path = tmp_path / "state" / "sessions" / "checkpoints.sqlite"
    assert checkpoint_path.exists()
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        graph = build_agent_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": result["thread_id"]}}
        snapshot = graph.get_state(config)
        history = list(graph.get_state_history(config))

    assert snapshot.values["status"] == "waiting_approval"
    assert snapshot.tasks[0].interrupts
    assert len(history) >= 3


def test_agent_session_resumes_from_checkpoint_without_state_snapshot(tmp_path, monkeypatch):
    result = start_trace_interrupt(tmp_path, monkeypatch)
    state_path = tmp_path / result["state_path"]
    state_path.unlink()

    resumed = resume_agent_session(
        session=result["thread_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert resumed["status"] == "complete"
    assert resumed["thread_id"] == result["thread_id"]
    assert resumed["interrupts"] == []
    assert state_path.exists()
    assert (tmp_path / "trace_out" / "source_trace.json").exists()


def test_agent_session_approves_apply_fix_dry_run(tmp_path, monkeypatch):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)
    repo = make_source_repo(tmp_path)
    proposal_dir = tmp_path / "proposal"
    suggest_stale_metadata_fix(
        repo=repo,
        file_path="script.py",
        promoted_variant_id="promoted_variant",
        output_dir=proposal_dir,
    )

    class DryRunThenCompleteClient:
        def complete(self, instructions, prompt):
            context = json.loads(prompt[prompt.find("{") :])
            if "apply_fix_dry_run" in context.get("completed_actions", []):
                return json.dumps({"status": "complete"})
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": "apply_fix_dry_run",
                        "args": {"proposal": str(proposal_dir / "proposal.json")},
                        "rationale": "Verify patch applicability without modifying source.",
                        "requires_approval": True,
                    },
                }
            )

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: DryRunThenCompleteClient())

    result = run_agent_session(
        task="dry run fix",
        planner="llm",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )
    assert result["status"] == "waiting_approval"

    approved = resume_agent_session(
        session=result["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert approved["status"] == "complete"
    apply_result = json.loads((proposal_dir / "apply_result.json").read_text(encoding="utf-8"))
    assert apply_result["dry_run"] is True
    assert apply_result["applied"] is False
    assert repo.joinpath("script.py").read_text(encoding="utf-8") == SAMPLE_STALE_METADATA_SOURCE


def test_rule_agent_runs_patch_run_inspect_lifecycle_with_approvals(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)
    make_dummy_config(tmp_path)
    repo = make_source_repo(tmp_path)
    proposal_dir = tmp_path / ".fix_proposals" / "latest"
    suggest_stale_metadata_fix(
        repo=repo,
        file_path="script.py",
        promoted_variant_id="promoted_variant",
        output_dir=proposal_dir,
    )

    first = run_agent_session(
        task="complete latest proposal patch-run-inspect lifecycle",
        max_steps=10,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert first["status"] == "waiting_approval"
    approval = read_approval(tmp_path, first)
    assert approval["action"]["name"] == "apply_fix_dry_run"

    second = resume_agent_session(
        session=first["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert second["status"] == "waiting_approval"
    approval = read_approval(tmp_path, second)
    assert approval["action"]["name"] == "apply_fix_yes"
    dry_run_result = json.loads((proposal_dir / "apply_result.json").read_text(encoding="utf-8"))
    assert dry_run_result["dry_run"] is True
    assert dry_run_result["would_change"] is True

    third = resume_agent_session(
        session=first["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert third["status"] == "waiting_approval"
    approval = read_approval(tmp_path, third)
    assert approval["action"]["name"] == "run_fresh"
    assert "PROMOTED_LIVE_VARIANT_ID" in repo.joinpath("script.py").read_text(encoding="utf-8")

    final = resume_agent_session(
        session=first["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in final["actions"]]
    assert final["status"] == "complete"
    assert action_names == [
        "read_project_state",
        "read_capabilities",
        "inspect_proposal",
        "apply_fix_dry_run",
        "apply_fix_yes",
        "run_fresh",
        "inspect_artifacts_latest",
        "suggest_revision",
        "synthesize_revision",
    ]
    assert "Patch dry-run: would_change=true" in final["final_message"]
    assert "Patch applied: true" in final["final_message"]
    assert "Verification run:" in final["final_message"]
    assert "Revision proposal:" in final["final_message"]
    revision = latest_action_result(final, "suggest_revision")
    synthesis = latest_action_result(final, "synthesize_revision")
    assert revision["issue"] == "verification-failure-revision"
    assert revision["quality_gate"] == "pass"
    assert (tmp_path / revision["failure_context_path"]).exists()
    assert synthesis["synthesis_status"] == "synthesized"
    assert synthesis["synthesized_issue"] == "missing-position-exports"
    assert synthesis["apply_supported"] is False


def test_rule_agent_writes_revision_when_verification_run_fails(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)
    make_bad_config(tmp_path)
    repo = make_source_repo(tmp_path)
    proposal_dir = tmp_path / ".fix_proposals" / "latest"
    suggest_stale_metadata_fix(
        repo=repo,
        file_path="script.py",
        promoted_variant_id="promoted_variant",
        output_dir=proposal_dir,
    )

    first = run_agent_session(
        task="complete latest proposal patch-run-inspect lifecycle",
        max_steps=10,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )
    second = resume_agent_session(
        session=first["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )
    third = resume_agent_session(
        session=first["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )
    final = resume_agent_session(
        session=first["session_id"],
        approve=True,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    assert second["status"] == "waiting_approval"
    assert third["status"] == "waiting_approval"
    assert final["status"] == "complete"
    action_names = [record["action"]["name"] for record in final["actions"]]
    assert action_names == [
        "read_project_state",
        "read_capabilities",
        "inspect_proposal",
        "apply_fix_dry_run",
        "apply_fix_yes",
        "run_fresh",
        "suggest_revision",
        "synthesize_revision",
    ]
    run_result = latest_action_result(final, "run_fresh")
    revision = latest_action_result(final, "suggest_revision")
    synthesis = latest_action_result(final, "synthesize_revision")
    assert run_result["verification_status"] == "failed"
    assert run_result["error_type"] == "NotImplementedError"
    assert revision["quality_gate"] == "pass"
    assert synthesis["synthesis_status"] == "unsupported"
    assert synthesis["synthesized_issue"] is None
    proposal = json.loads((tmp_path / revision["metadata_path"]).read_text(encoding="utf-8"))
    assert proposal["issue"] == "verification-failure-revision"
    assert proposal["verification_failure"]["error_type"] == "NotImplementedError"
    assert proposal["apply_supported"] is False
    assert "Verification failed:" in final["final_message"]


def test_patch_lifecycle_restarts_from_applicable_synthesized_proposal(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_dummy_config(tmp_path)
    state = lifecycle_state_after_applicable_synthesis(tmp_path)
    completed = action_names_from_state(state)

    action = choose_patch_lifecycle_action(state, completed)

    assert action is not None
    assert action["name"] == "inspect_proposal"
    assert action["args"]["proposal"] == ".fix_proposals/revised/proposal.json"


def test_patch_lifecycle_dry_runs_restarted_synthesized_proposal(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_dummy_config(tmp_path)
    state = lifecycle_state_after_applicable_synthesis(tmp_path)
    state["actions"].append(
        ok_action(
            "inspect_proposal",
            {"proposal": ".fix_proposals/revised/proposal.json"},
            {
                "action": "inspect_proposal",
                "proposal_path": ".fix_proposals/revised/proposal.json",
                "issue": "missing-position-exports",
                "apply_supported": True,
                "patch_exists": True,
            },
        )
    )
    completed = action_names_from_state(state)

    action = choose_patch_lifecycle_action(state, completed)

    assert action is not None
    assert action["name"] == "apply_fix_dry_run"
    assert action["args"]["proposal"] == ".fix_proposals/revised/proposal.json"
    assert action["requires_approval"] is True


def test_patch_lifecycle_blocks_repeated_revision_failure(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_dummy_config(tmp_path)
    state = lifecycle_state_after_repeated_revision_failure(tmp_path)
    completed = action_names_from_state(state)

    with pytest.raises(RevisionLoopBlocked, match="same verification failure repeated"):
        choose_patch_lifecycle_action(state, completed)


def test_decide_node_blocks_cleanly_on_revision_loop(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_dummy_config(tmp_path)
    state = lifecycle_state_after_repeated_revision_failure(tmp_path)
    state.update(
        {
            "status": "running",
            "current_action": None,
            "approval_decision": None,
            "session_path": str(tmp_path / "state" / "sessions" / "loop.jsonl"),
            "errors": [],
            "step": len(state["actions"]),
        }
    )

    result = decide_node(state)

    assert result["status"] == "blocked"
    assert "revision loop guard" in result["final_message"]
    assert "same verification failure repeated" in result["final_message"]


def test_patch_lifecycle_blocks_repeated_synthesized_proposal_signature(tmp_path):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_dummy_config(tmp_path)
    state = lifecycle_state_after_applicable_synthesis(tmp_path)
    state["actions"].append(
        ok_action(
            "synthesize_revision",
            {"revision_proposal": ".fix_proposals/revision_2/proposal.json"},
            {
                "action": "synthesize_revision",
                "synthesis_status": "synthesized",
                "synthesized_issue": "missing-position-exports",
                "metadata_path": ".fix_proposals/revised_2/proposal.json",
                "patch_hash": "same_patch",
                "apply_supported": True,
                "quality_gate": "pass",
            },
        )
    )
    completed = action_names_from_state(state)

    with pytest.raises(RevisionLoopBlocked, match="same synthesized proposal signature"):
        choose_patch_lifecycle_action(state, completed)


def test_llm_agent_uses_deterministic_patch_lifecycle_before_provider(tmp_path, monkeypatch):
    make_project_state(tmp_path)
    make_capabilities(tmp_path)
    make_run(tmp_path)
    repo = make_source_repo(tmp_path)
    proposal_dir = tmp_path / ".fix_proposals" / "latest"
    suggest_stale_metadata_fix(
        repo=repo,
        file_path="script.py",
        promoted_variant_id="promoted_variant",
        output_dir=proposal_dir,
    )

    class UnexpectedClient:
        def complete(self, instructions, prompt):
            raise AssertionError("Lifecycle action should be selected before calling the LLM provider.")

    monkeypatch.setattr("quant_agent.agent.planner.create_llm_client", lambda provider, model: UnexpectedClient())

    result = run_agent_session(
        task="complete latest proposal patch-run-inspect lifecycle",
        planner="llm",
        provider="mock",
        max_steps=5,
        root=tmp_path,
        session_dir=tmp_path / "state" / "sessions",
    )

    action_names = [record["action"]["name"] for record in result["actions"]]
    assert result["status"] == "waiting_approval"
    assert action_names == ["read_project_state", "read_capabilities", "inspect_proposal"]
    assert read_approval(tmp_path, result)["action"]["name"] == "apply_fix_dry_run"


def test_parse_planner_response_accepts_markdown_wrapped_json():
    payload = parse_planner_response(
        '```json\n{"status": "continue", "action": {"name": "metrics_latest", "args": {}, "rationale": "check", "requires_approval": false}}\n```'
    )

    assert payload["status"] == "continue"
    assert payload["action"]["name"] == "metrics_latest"


def test_run_agent_session_rejects_unknown_planner(tmp_path):
    with pytest.raises(ValueError, match="planner must be"):
        run_agent_session("test", planner="unknown", root=tmp_path)


def make_project_state(root):
    state_dir = root / "state"
    state_dir.mkdir()
    (state_dir / "project_state.json").write_text(
        json.dumps(
            {
                "project": {
                    "name": "quant-agent",
                    "current_stage": "test",
                    "requires_python": ">=3.11",
                    "agent_runtime": ".venv311",
                    "behavior_model": "test loop",
                },
                "latest_verified_run": {
                    "run_id": "demo_20260610_001",
                    "config": "configs/demo.yaml",
                    "position_level_artifacts": {
                        "artifact_contract_recommendation": "satisfied",
                    },
                    "promotion": {
                        "recommendation": "review_required",
                    },
                },
                "known_gaps": ["No LLM planner."],
                "next_recommended_steps": [
                    {
                        "priority": 1,
                        "name": "agent-session loop",
                        "description": "Build loop.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def start_trace_interrupt(root, monkeypatch):
    make_project_state(root)
    make_capabilities(root)
    make_run(root)
    repo = make_source_repo(root)

    class TraceThenCompleteClient:
        def complete(self, instructions, prompt):
            if "trace_source" in prompt and '"completed_actions"' in prompt:
                context = json.loads(prompt[prompt.find("{") :])
                if "trace_source" in context.get("completed_actions", []):
                    return json.dumps({"status": "complete"})
            return json.dumps(
                {
                    "status": "continue",
                    "action": {
                        "name": "trace_source",
                        "args": {
                            "repo": str(repo),
                            "file": "script.py",
                            "output": str(root / "trace_out"),
                        },
                        "rationale": "Trace source before proposing a fix.",
                        "requires_approval": True,
                    },
                }
            )

    monkeypatch.setattr(
        "quant_agent.agent.planner.create_llm_client",
        lambda provider, model: TraceThenCompleteClient(),
    )
    return run_agent_session(
        task="trace source",
        planner="llm",
        provider="mock",
        max_steps=5,
        root=root,
        session_dir=root / "state" / "sessions",
    )


def make_capabilities(root):
    (root / "state" / "capabilities.json").write_text(
        json.dumps(
            {
                "capabilities": [
                    {"name": "run", "status": "implemented"},
                    {"name": "agent-session", "status": "planned"},
                ]
            }
        ),
        encoding="utf-8",
    )


def make_run(root):
    run_dir = root / "runs" / "demo_20260610_001"
    source_dir = run_dir / "source_artifacts"
    audits_dir = run_dir / "audits"
    source_dir.mkdir(parents=True)
    audits_dir.mkdir()
    write_json(
        run_dir / "manifest.json",
        {
            "strategy_name": "demo",
            "engine": "subprocess_csv",
            "artifacts": {
                "metrics": "metrics.json",
                "decision_log": "decision_log.csv",
                "equity_curve": "equity_curve.csv",
                "positions": "positions.csv",
                "trades": "trades.csv",
                "source_daily_curve": "source_artifacts/demo.csv",
                "source_summary_by_window": "source_artifacts/summary_by_window.csv",
                "source_rankings_csv": "source_artifacts/rankings.csv",
            },
        },
    )
    write_json(
        run_dir / "metrics.json",
        {
            "variant_id": "demo",
            "cagr": 0.12,
            "max_drawdown": -0.2,
            "calmar": 0.6,
            "turnover_per_year": 4.0,
            "exposure": 0.8,
        },
    )
    write_json(
        run_dir / "engine_metadata.json",
        {
            "expected_variant_id": "demo",
            "subprocess_ran": True,
            "exit_code": 0,
            "source_metadata": {
                "promoted_live_variant_id": "demo",
                "generated_at_utc": "2026-06-10T00:00:00+00:00",
            },
        },
    )
    write_json(run_dir / "flags.json", [])
    write_csv(run_dir / "decision_log.csv", "date,decision\n2026-01-01,hold\n")
    write_csv(run_dir / "equity_curve.csv", "date,equity\n2026-01-01,1.0\n")
    write_csv(run_dir / "positions.csv", "date,symbol,weight\n2026-01-01,AAPL,1.0\n")
    write_csv(run_dir / "trades.csv", "date,symbol,weight_change\n2026-01-01,AAPL,1.0\n")
    write_csv(source_dir / "demo.csv", "date,daily_return,equity\n2026-01-01,0.0,1.0\n")
    write_csv(source_dir / "summary_by_window.csv", "window,variant_id\nfull,demo\n")
    write_csv(source_dir / "rankings.csv", "date,symbol,rank\n2026-01-01,AAPL,1\n")
    write_json(
        audits_dir / "artifact_contract.json",
        {"recommendation": "satisfied"},
    )


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_csv(path, text):
    path.write_text(text, encoding="utf-8")


def latest_action_result(result, action_name):
    for record in reversed(result["actions"]):
        if record["action"]["name"] == action_name:
            return record["result"]
    raise AssertionError(f"Action not found: {action_name}")


def read_approval(root, result):
    return json.loads((root / result["approval_path"]).read_text(encoding="utf-8"))


def lifecycle_state_after_applicable_synthesis(root):
    return {
        "task": "complete latest proposal patch-run-inspect lifecycle",
        "root": str(root),
        "actions": [
            ok_action("read_project_state", {}, {"action": "read_project_state"}),
            ok_action("read_capabilities", {}, {"action": "read_capabilities"}),
            ok_action(
                "inspect_proposal",
                {"proposal": ".fix_proposals/latest/proposal.json"},
                {
                    "action": "inspect_proposal",
                    "proposal_path": ".fix_proposals/latest/proposal.json",
                    "issue": "stale-metadata",
                    "apply_supported": True,
                    "patch_exists": True,
                },
            ),
            ok_action(
                "apply_fix_dry_run",
                {"proposal": ".fix_proposals/latest/proposal.json"},
                {"action": "apply_fix_dry_run", "would_change": True},
            ),
            ok_action(
                "apply_fix_yes",
                {"proposal": ".fix_proposals/latest/proposal.json"},
                {"action": "apply_fix_yes", "applied": True},
            ),
            ok_action("run_fresh", {}, {"action": "run_fresh", "verification_status": "completed"}),
            ok_action(
                "inspect_artifacts_latest",
                {},
                {
                    "action": "inspect_artifacts_latest",
                    "recommendation": "needs_export_patch",
                    "missing_position_level_artifacts": ["rankings"],
                },
            ),
            ok_action(
                "suggest_revision",
                {
                    "proposal": ".fix_proposals/latest/proposal.json",
                    "artifact_inspection": {"recommendation": "needs_export_patch"},
                },
                {
                    "action": "suggest_revision",
                    "issue": "verification-failure-revision",
                    "metadata_path": ".fix_proposals/revision/proposal.json",
                    "quality_gate": "pass",
                },
            ),
            ok_action(
                "synthesize_revision",
                {"revision_proposal": ".fix_proposals/revision/proposal.json"},
                {
                    "action": "synthesize_revision",
                    "synthesis_status": "synthesized",
                    "synthesized_issue": "missing-position-exports",
                    "metadata_path": ".fix_proposals/revised/proposal.json",
                    "patch_hash": "same_patch",
                    "apply_supported": True,
                    "quality_gate": "pass",
                },
            ),
        ],
        "observations": [],
    }


def lifecycle_state_after_repeated_revision_failure(root):
    state = lifecycle_state_after_applicable_synthesis(root)
    state["actions"].extend(
        [
            ok_action(
                "inspect_proposal",
                {"proposal": ".fix_proposals/revised/proposal.json"},
                {
                    "action": "inspect_proposal",
                    "proposal_path": ".fix_proposals/revised/proposal.json",
                    "issue": "missing-position-exports",
                    "apply_supported": True,
                    "patch_exists": True,
                },
            ),
            ok_action(
                "apply_fix_dry_run",
                {"proposal": ".fix_proposals/revised/proposal.json"},
                {"action": "apply_fix_dry_run", "would_change": True},
            ),
            ok_action(
                "apply_fix_yes",
                {"proposal": ".fix_proposals/revised/proposal.json"},
                {"action": "apply_fix_yes", "applied": True},
            ),
            ok_action("run_fresh", {}, {"action": "run_fresh", "verification_status": "completed"}),
            ok_action(
                "inspect_artifacts_latest",
                {},
                {
                    "action": "inspect_artifacts_latest",
                    "recommendation": "needs_export_patch",
                    "missing_position_level_artifacts": ["rankings"],
                },
            ),
        ]
    )
    return state


def ok_action(name, args, result):
    return {
        "action": {
            "name": name,
            "args": args,
            "rationale": "test",
            "requires_approval": False,
        },
        "status": "ok",
        "result": result,
    }


def action_names_from_state(state):
    return {record["action"]["name"] for record in state["actions"]}


def make_dummy_config(root):
    config_dir = root / "configs"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "demo.yaml").write_text(
        "strategy:\n  name: lifecycle_demo\nengine: dummy\nparameters:\n  top_n: 8\n",
        encoding="utf-8",
    )


def make_bad_config(root):
    config_dir = root / "configs"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "demo.yaml").write_text(
        "strategy:\n  name: failing_lifecycle_demo\nengine: unsupported_engine\n",
        encoding="utf-8",
    )


SAMPLE_STALE_METADATA_SOURCE = """from __future__ import annotations

import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CURRENT_VARIANT_ID = "old_control"


def main():
    started = time.time()
    metadata = {
        "elapsed_seconds": time.time() - started,
        "current_variant_id": CURRENT_VARIANT_ID,
        "base_top_ns": [5, 8],
    }
    return metadata
"""


def make_source_repo(root):
    repo = root / "source_repo"
    repo.mkdir()
    repo.joinpath("script.py").write_text(SAMPLE_STALE_METADATA_SOURCE, encoding="utf-8")
    return repo
