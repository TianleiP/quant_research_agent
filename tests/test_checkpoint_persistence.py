from __future__ import annotations

import pytest

from quant_agent.agent.persistence import (
    POSTGRES_URI_ENV,
    resolve_checkpoint_config,
)


def test_checkpoint_config_defaults_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANT_AGENT_CHECKPOINT_BACKEND", raising=False)
    monkeypatch.delenv(POSTGRES_URI_ENV, raising=False)

    config = resolve_checkpoint_config(root=tmp_path, session_dir=tmp_path / "sessions")

    assert config.backend == "sqlite"
    assert config.sqlite_path == (tmp_path / "sessions" / "checkpoints.sqlite").resolve()
    assert config.postgres_uri is None
    assert config.safe_reference == str(config.sqlite_path)


def test_checkpoint_config_reads_postgres_from_environment_without_exposing_uri(tmp_path, monkeypatch):
    uri = "postgresql://quant_agent:very-secret@localhost:5432/quant_agent"
    monkeypatch.setenv("QUANT_AGENT_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv(POSTGRES_URI_ENV, uri)

    config = resolve_checkpoint_config(root=tmp_path, session_dir=tmp_path / "sessions")

    assert config.backend == "postgres"
    assert config.postgres_uri == uri
    assert config.checkpoint_path is None
    assert "very-secret" not in config.safe_reference
    assert "localhost" not in config.safe_reference


def test_checkpoint_config_reads_postgres_from_project_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANT_AGENT_CHECKPOINT_BACKEND", raising=False)
    monkeypatch.delenv(POSTGRES_URI_ENV, raising=False)
    (tmp_path / ".env").write_text(
        "QUANT_AGENT_CHECKPOINT_BACKEND=postgres\n"
        "QUANT_AGENT_CHECKPOINT_POSTGRES_URI=postgresql://user:pass@localhost:5432/db\n",
        encoding="utf-8",
    )

    config = resolve_checkpoint_config(root=tmp_path, session_dir=tmp_path / "sessions")

    assert config.backend == "postgres"
    assert config.postgres_uri == "postgresql://user:pass@localhost:5432/db"


def test_checkpoint_config_requires_postgres_uri(tmp_path, monkeypatch):
    monkeypatch.delenv(POSTGRES_URI_ENV, raising=False)

    with pytest.raises(ValueError, match=POSTGRES_URI_ENV):
        resolve_checkpoint_config(
            root=tmp_path,
            session_dir=tmp_path / "sessions",
            backend="postgres",
        )


def test_checkpoint_config_rejects_unknown_backend(tmp_path):
    with pytest.raises(ValueError, match="sqlite.*postgres"):
        resolve_checkpoint_config(
            root=tmp_path,
            session_dir=tmp_path / "sessions",
            backend="redis",
        )
