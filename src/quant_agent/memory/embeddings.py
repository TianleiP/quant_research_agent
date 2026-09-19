from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

from quant_agent.llm.secrets import get_secret


OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSIONS = 256
DEFAULT_EMBEDDING_BATCH_SIZE = 32
MAX_EMBEDDING_INPUT_CHARS = 24_000


class EmbeddingClient(Protocol):
    provider: str
    model: str
    dimensions: int | None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAIEmbeddingClient:
    provider = "openai"

    def __init__(
        self,
        model: str | None = None,
        dimensions: int | None = DEFAULT_EMBEDDING_DIMENSIONS,
        timeout_seconds: int = 60,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
    ) -> None:
        api_key = get_secret("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set for semantic code-memory embeddings.")
        if dimensions is not None and dimensions < 1:
            raise ValueError("Embedding dimensions must be at least 1.")
        if batch_size < 1 or batch_size > 2048:
            raise ValueError("Embedding batch size must be between 1 and 2048.")
        self.api_key = api_key
        self.model = model or os.environ.get(
            "QUANT_AGENT_EMBEDDING_MODEL",
            DEFAULT_EMBEDDING_MODEL,
        )
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        cleaned = [prepare_embedding_input(text) for text in texts]
        for start in range(0, len(cleaned), self.batch_size):
            vectors.extend(self._post_batch(cleaned[start : start + self.batch_size]))
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"OpenAI embeddings returned {len(vectors)} vectors for {len(texts)} inputs."
            )
        return vectors

    def _post_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        response = self._post(payload)
        data = response.get("data")
        if not isinstance(data, list):
            raise RuntimeError("OpenAI embeddings response did not contain a data list.")
        ordered = sorted(
            (item for item in data if isinstance(item, dict)),
            key=lambda item: int(item.get("index", 0)),
        )
        vectors = [validate_dense_vector(item.get("embedding")) for item in ordered]
        if len(vectors) != len(texts):
            raise RuntimeError("OpenAI embeddings response did not contain one vector per input.")
        return vectors

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            OPENAI_EMBEDDINGS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI embeddings API error {exc.code}: {error_body}") from exc
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenAI embeddings response was not a JSON object.")
        return parsed


def create_embedding_client(
    provider: str,
    *,
    model: str | None = None,
    dimensions: int | None = DEFAULT_EMBEDDING_DIMENSIONS,
) -> EmbeddingClient | None:
    selected = resolve_embedding_provider(provider)
    if selected == "none":
        return None
    if selected == "openai":
        return OpenAIEmbeddingClient(model=model, dimensions=dimensions)
    raise ValueError("embedding provider must be 'auto', 'none', or 'openai'.")


def resolve_embedding_provider(provider: str) -> str:
    normalized = str(provider or "none").strip().lower()
    if normalized == "auto":
        return "openai" if get_secret("OPENAI_API_KEY") else "none"
    if normalized not in {"none", "openai"}:
        raise ValueError("embedding provider must be 'auto', 'none', or 'openai'.")
    return normalized


def prepare_embedding_input(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return "(empty source chunk)"
    return cleaned[:MAX_EMBEDDING_INPUT_CHARS]


def validate_dense_vector(value: Any) -> list[float]:
    if not isinstance(value, list) or not value:
        raise RuntimeError("Embedding vector must be a non-empty list.")
    vector = [float(item) for item in value]
    if not all(math.isfinite(item) for item in vector):
        raise RuntimeError("Embedding vector contains a non-finite value.")
    return vector
