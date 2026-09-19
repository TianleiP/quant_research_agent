from quant_agent.llm.secrets import read_dotenv_value


def test_read_dotenv_value(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY='secret-value'\n", encoding="utf-8")

    assert read_dotenv_value(path, "DEEPSEEK_API_KEY") == "secret-value"
