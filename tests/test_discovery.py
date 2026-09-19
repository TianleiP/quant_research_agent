import json

from quant_agent.discovery import discover_repo


def test_discover_repo_writes_structural_index(tmp_path):
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    script = repo / "run_backtest.py"
    script.write_text(
        "\n".join(
            [
                "import argparse",
                "import pandas as pd",
                "",
                "CURRENT_VARIANT_ID = 'old_variant'",
                "",
                "def run_backtest():",
                "    df = pd.read_csv('data/input.csv')",
                "    df.to_csv('outputs/summary_by_window.csv')",
                "",
                "if __name__ == '__main__':",
                "    run_backtest()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "config.yaml").write_text("strategy: demo\n", encoding="utf-8")
    output_dir = tmp_path / "discovery"

    result = discover_repo(repo=repo, output_dir=output_dir)

    assert (output_dir / "repo_index.json").exists()
    assert (output_dir / "symbols.json").exists()
    assert (output_dir / "entrypoints.json").exists()
    assert (output_dir / "artifact_paths.json").exists()
    assert (output_dir / "findings.md").exists()
    assert result["repo_index"]["summary"]["python_files"] == 1
    assert result["entrypoints"][0]["path"] == "run_backtest.py"

    symbols = json.loads((output_dir / "symbols.json").read_text(encoding="utf-8"))
    python_file = symbols["python_files"][0]
    assert "argparse" in python_file["imports"]
    assert python_file["constants"]["CURRENT_VARIANT_ID"] == "old_variant"

    artifact_paths = json.loads((output_dir / "artifact_paths.json").read_text(encoding="utf-8"))
    values = {item["value"] for item in artifact_paths["references"]}
    assert "data/input.csv" in values
    assert "outputs/summary_by_window.csv" in values
