import json

from quant_agent.replay.replay import replay_date


def test_replay_date_reports_missing_entry(tmp_path):
    (tmp_path / "decision_log.csv").write_text("decision_date,reason\n2021-01-01,test\n", encoding="utf-8")

    message = replay_date(tmp_path, "2021-01-02")

    assert "No decision log entry" in message


def test_replay_date_includes_position_trade_and_ranking_context(tmp_path):
    make_position_aware_run(tmp_path)

    message = replay_date(tmp_path, "2026-01-03")

    assert "Strategy state:" in message
    assert "- Gross exposure: 75.0%" in message
    assert "Holdings:" in message
    assert "- Securities held: 2" in message
    assert "AAPL | 50.0% | rank 1 | sleeve core" in message
    assert "MSFT | 25.0% | rank 2 | sleeve core" in message
    assert "Trade changes:" in message
    assert "- Total turnover: 100.0%" in message
    assert "AAPL increase 50.0% from 0.0% to 50.0%" in message
    assert "__CASH__ decrease -75.0% from 100.0% to 25.0%" in message
    assert "Ranking context:" in message
    assert "rank 1 | AAPL | weight 50.0% | sleeve core | eligible True" in message


def test_replay_date_falls_back_when_position_artifacts_are_absent(tmp_path):
    (tmp_path / "decision_log.csv").write_text(
        "decision_date,available_through,execution_date,market_gate,reason\n"
        "2026-01-03,2026-01-02,2026-01-03,risk_on,test reason\n",
        encoding="utf-8",
    )

    message = replay_date(tmp_path, "2026-01-03")

    assert "Position-level holdings are not available" in message
    assert "No trade changes recorded" in message
    assert "Ranking rows are not available" in message


def make_position_aware_run(run_dir):
    source_dir = run_dir / "source_artifacts"
    source_dir.mkdir()
    write_json(
        run_dir / "manifest.json",
        {
            "artifacts": {
                "decision_log": "decision_log.csv",
                "positions": "positions.csv",
                "trades": "trades.csv",
                "source_rankings_csv": "source_artifacts/rankings_live.csv",
            }
        },
    )
    write_csv(
        run_dir / "decision_log.csv",
        "decision_date,available_through,signal_date,execution_date,market_gate,feedback_stage,gross_exposure,cash_weight,reason\n"
        "2026-01-03,2026-01-02,2026-01-02,2026-01-03,risk_on,1,0.75,0.25,per-symbol ranks are not saved\n",
    )
    write_csv(
        run_dir / "positions.csv",
        "date,symbol,weight,sleeve,rank,score,cash_weight,gross_exposure\n"
        "2026-01-03,AAPL,0.5,core,1,,0.25,0.75\n"
        "2026-01-03,MSFT,0.25,core,2,,0.25,0.75\n"
        "2026-01-03,__CASH__,0.25,cash,,,,\n",
    )
    write_csv(
        run_dir / "trades.csv",
        "date,symbol,prev_weight,target_weight,weight_change,turnover\n"
        "2026-01-03,AAPL,0,0.5,0.5,0.5\n"
        "2026-01-03,MSFT,0,0.25,0.25,0.25\n"
        "2026-01-03,__CASH__,1,0.25,-0.75,0.25\n",
    )
    write_csv(
        source_dir / "rankings_live.csv",
        "date,symbol,rank,score,eligible,sleeve,weight\n"
        "2026-01-03,AAPL,1,,True,core,0.5\n"
        "2026-01-03,MSFT,2,,True,core,0.25\n",
    )


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_csv(path, text):
    path.write_text(text, encoding="utf-8")
