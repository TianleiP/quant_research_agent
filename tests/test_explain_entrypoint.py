from quant_agent.discovery import discover_repo
from quant_agent.discovery.explain import build_entrypoint_prompt, explain_entrypoint


def create_sample_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "run_strategy.py").write_text(
        "\n".join(
            [
                "import pandas as pd",
                "",
                "CURRENT_VARIANT_ID = 'demo_variant'",
                "",
                "def main():",
                "    df = pd.read_csv('inputs/prices.csv')",
                "    df.to_csv('outputs/summary_by_window.csv')",
                "",
                "if __name__ == '__main__':",
                "    main()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo


def test_build_entrypoint_prompt_includes_index_and_source(tmp_path):
    repo = create_sample_repo(tmp_path)
    discovery_dir = tmp_path / "discovery"
    discover_repo(repo=repo, output_dir=discovery_dir)

    prompt = build_entrypoint_prompt(
        repo=repo,
        file_path="run_strategy.py",
        discovery_dir=discovery_dir,
        max_source_chars=2000,
    )

    assert "CURRENT_VARIANT_ID" in prompt
    assert "outputs/summary_by_window.csv" in prompt
    assert "source_excerpt" in prompt


def test_explain_entrypoint_mock_writes_report(tmp_path):
    repo = create_sample_repo(tmp_path)
    discovery_dir = tmp_path / "discovery"
    discover_repo(repo=repo, output_dir=discovery_dir)

    explanation, report_path = explain_entrypoint(
        repo=repo,
        file_path="run_strategy.py",
        discovery_dir=discovery_dir,
        provider="mock",
    )

    assert "Entrypoint Explanation" in explanation
    assert report_path is not None
    assert report_path.exists()
