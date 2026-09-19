from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, cast

from langgraph.checkpoint.sqlite import SqliteSaver

from quant_agent.agent.session import session_checkpoint_path
from quant_agent.llm.secrets import read_dotenv_value


CheckpointBackend = Literal["sqlite", "postgres"]
CHECKPOINT_BACKEND_ENV = "QUANT_AGENT_CHECKPOINT_BACKEND"
POSTGRES_URI_ENV = "QUANT_AGENT_CHECKPOINT_POSTGRES_URI"


@dataclass(frozen=True)
class CheckpointConfig:
    backend: CheckpointBackend
    sqlite_path: Path | None = None
    postgres_uri: str | None = None

    def __post_init__(self) -> None:
        if self.backend == "sqlite":
            if self.sqlite_path is None:
                raise ValueError("SQLite checkpoint backend requires a database path.")
            if self.postgres_uri:
                raise ValueError("SQLite checkpoint configuration cannot include a PostgreSQL URI.")
            return
        if self.backend == "postgres":
            if not self.postgres_uri:
                raise ValueError("PostgreSQL checkpoint backend requires a connection URI.")
            if self.sqlite_path is not None:
                raise ValueError("PostgreSQL checkpoint configuration cannot include a SQLite path.")
            return
        raise ValueError("checkpoint backend must be 'sqlite' or 'postgres'.")

    @property
    def checkpoint_path(self) -> str | None:
        return str(self.sqlite_path) if self.sqlite_path is not None else None

    @property
    def safe_reference(self) -> str:
        if self.backend == "sqlite":
            return str(self.sqlite_path)
        return "PostgreSQL (configured connection)"


def resolve_checkpoint_config(
    *,
    root: Path,
    session_dir: Path,
    backend: str | None = None,
    postgres_uri: str | None = None,
    sqlite_path: str | Path | None = None,
) -> CheckpointConfig:
    backend_name = (
        backend
        or _config_value(CHECKPOINT_BACKEND_ENV, root)
        or "sqlite"
    ).strip().lower()
    if backend_name not in {"sqlite", "postgres"}:
        raise ValueError("checkpoint backend must be 'sqlite' or 'postgres'.")

    if backend_name == "sqlite":
        checkpoint_db = Path(sqlite_path) if sqlite_path else session_checkpoint_path(session_dir)
        return CheckpointConfig(backend="sqlite", sqlite_path=checkpoint_db.resolve())

    checkpoint_uri = postgres_uri or _config_value(POSTGRES_URI_ENV, root)
    if not checkpoint_uri:
        raise ValueError(
            "PostgreSQL checkpoint backend requires "
            f"{POSTGRES_URI_ENV} or checkpoint_postgres_uri."
        )
    return CheckpointConfig(backend="postgres", postgres_uri=checkpoint_uri)


@contextmanager
def create_checkpointer(config: CheckpointConfig) -> Iterator[Any]:
    if config.backend == "sqlite":
        checkpoint_db = cast(Path, config.sqlite_path)
        checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(checkpoint_db)) as checkpointer:
            checkpointer.setup()
            yield checkpointer
        return

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - exercised without optional extra
        raise RuntimeError(
            "PostgreSQL checkpoint backend requires the optional 'postgres' dependencies. "
            "Install them with: pip install -e '.[postgres]'"
        ) from exc

    checkpoint_uri = cast(str, config.postgres_uri)
    with PostgresSaver.from_conn_string(checkpoint_uri) as checkpointer:
        checkpointer.setup()
        yield checkpointer


def _config_value(name: str, root: Path) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    return read_dotenv_value(root / ".env", name)
