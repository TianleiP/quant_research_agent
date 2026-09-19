import json

import pytest

from quant_agent.diagnostics.anomaly import diagnose_anomaly, diagnose_run
from quant_agent.diagnostics.structured import (
    parse_structured_diagnosis,
    render_structured_diagnosis_markdown,
)


def test_diagnose_anomaly_mock_writes_report(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"strategy_name": "demo", "engine": "dummy"}),
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(
        json.dumps({"cagr": 0.1, "max_drawdown": -0.2, "variant_id": "demo"}),
        encoding="utf-8",
    )
    (tmp_path / "flags.json").write_text("[]", encoding="utf-8")
    (tmp_path / "engine_metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "short_report.md").write_text("# Short Report", encoding="utf-8")

    diagnosis, report_path = diagnose_anomaly(tmp_path, provider="mock")

    assert "Provider: mock" in diagnosis
    assert report_path is not None
    assert report_path.exists()


def test_diagnose_run_mock_both_writes_json_and_markdown(tmp_path):
    write_run_artifacts(tmp_path)

    result = diagnose_run(tmp_path, provider="mock", output_format="both")

    assert result.structured is not None
    assert result.structured["run_id"] == tmp_path.name
    assert result.structured["provider"] == "mock"
    assert "json" in result.paths
    assert "markdown" in result.paths
    assert result.paths["json"].exists()
    assert result.paths["markdown"].exists()
    payload = json.loads(result.paths["json"].read_text(encoding="utf-8"))
    assert payload["findings"][0]["category"] == "missing_artifact"


def test_diagnose_run_mock_json_no_save_prints_json(tmp_path):
    write_run_artifacts(tmp_path)

    result = diagnose_run(tmp_path, provider="mock", output_format="json", save=False)

    payload = json.loads(result.text)
    assert payload["provider"] == "mock"
    assert result.paths == {}


def test_parse_structured_diagnosis_rejects_missing_fields():
    with pytest.raises(ValueError, match="missing required field"):
        parse_structured_diagnosis('{"summary": "x"}')


def test_render_structured_diagnosis_markdown():
    payload = parse_structured_diagnosis(
        json.dumps(
            {
                "summary": "summary text",
                "findings": [
                    {
                        "severity": "high",
                        "category": "lag_safety",
                        "title": "Timing check required",
                        "evidence": ["decision log present"],
                        "recommended_action": "Run replay.",
                    }
                ],
                "missing_artifacts": ["positions"],
                "next_actions": ["Run lag audit"],
            }
        )
    )
    payload["run_id"] = "run_1"
    payload["provider"] = "mock"
    payload["model"] = "mock"

    markdown = render_structured_diagnosis_markdown(payload)

    assert "# Diagnosis: run_1" in markdown
    assert "[high] Timing check required" in markdown


def test_diagnose_run_invalid_json_saves_raw_output(tmp_path, monkeypatch):
    write_run_artifacts(tmp_path)

    class BadClient:
        provider = "bad"
        model = "bad-model"

        def complete(self, instructions, prompt):
            return "not json"

    monkeypatch.setattr(
        "quant_agent.diagnostics.anomaly.create_llm_client",
        lambda provider="auto", model=None: BadClient(),
    )

    with pytest.raises(RuntimeError, match="valid structured diagnosis JSON"):
        diagnose_run(tmp_path, provider="mock", output_format="json", save=True)

    assert (tmp_path / "diagnosis.raw.txt").read_text(encoding="utf-8") == "not json"


def write_run_artifacts(path):
    (path / "manifest.json").write_text(
        json.dumps({"strategy_name": "demo", "engine": "dummy"}),
        encoding="utf-8",
    )
    (path / "metrics.json").write_text(
        json.dumps({"cagr": 0.1, "max_drawdown": -0.2, "variant_id": "demo"}),
        encoding="utf-8",
    )
    (path / "flags.json").write_text("[]", encoding="utf-8")
    (path / "engine_metadata.json").write_text("{}", encoding="utf-8")
    (path / "short_report.md").write_text("# Short Report", encoding="utf-8")
