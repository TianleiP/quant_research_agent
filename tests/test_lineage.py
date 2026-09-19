from quant_agent.lineage import (
    load_lineage,
    render_lineage,
    strategy_lineage_entry,
    update_strategy_lineage,
)


def test_update_strategy_lineage_records_and_renders_entry(tmp_path):
    path = tmp_path / "state" / "strategy_lineage.json"

    entry = update_strategy_lineage(
        strategy="candidate_v2",
        previous="baseline_v1",
        reason="Better validation drawdown with same signal family.",
        changed_rules=["Use Top5 combo pocket."],
        inherited_rules=["Keep lagged market guard."],
        removed_rules=["Remove stale Top8 pocket."],
        audit_links=["runs/candidate_v2/audits/PROMOTION_AUDIT_candidate_v2.md"],
        status="candidate",
        path=path,
    )

    payload = load_lineage(path)
    rendered = render_lineage(payload)

    assert entry["previous_baseline"] == "baseline_v1"
    assert strategy_lineage_entry("candidate_v2", path=path)["changed_rules"] == ["Use Top5 combo pocket."]
    assert "candidate_v2 (candidate) <- baseline_v1" in rendered
    assert "Better validation drawdown" in rendered
