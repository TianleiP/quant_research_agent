import json

from quant_agent.memory import OpenAIEmbeddingClient, build_code_memory, query_code_memory


class ConceptEmbeddingClient:
    provider = "test"
    model = "concept-test-v1"
    dimensions = 2

    def embed_texts(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if any(term in lowered for term in ["rebalance_portfolio", "allocation", "holdings", "exposure"]):
                vectors.append([1.0, 0.0])
            elif any(term in lowered for term in ["database", "migration", "schema"]):
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.1, 0.1])
        return vectors


class FailingEmbeddingClient:
    provider = "openai"
    model = "text-embedding-test"
    dimensions = 2

    def embed_texts(self, texts):
        raise RuntimeError("temporary provider failure")


def test_hybrid_retrieval_uses_semantics_when_words_do_not_match(tmp_path):
    repo = make_hybrid_repo(tmp_path)
    output = tmp_path / "memory"
    client = ConceptEmbeddingClient()

    build_code_memory(
        repo=repo,
        output_dir=output,
        chunk_lines=20,
        overlap_lines=5,
        embedding_client=client,
    )
    result = query_code_memory(
        index=output / "code_memory.json",
        query="where is holdings exposure decided",
        top_k=2,
        embedding_client=client,
    )

    assert result["retrieval"]["mode"] == "hybrid"
    assert result["retrieval"]["semantic_status"] == "available"
    assert result["results"][0]["path"] == "strategy.py"
    assert result["results"][0]["semantic_score"] > 0.9
    assert result["results"][0]["citation"].startswith("strategy.py:")


def test_symbol_channel_breaks_lexical_tie(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "implementation.py").write_text(
        "def export_trades(rows):\n    return rows\n",
        encoding="utf-8",
    )
    (repo / "notes.md").write_text(
        "The export trades operation is documented here.\n",
        encoding="utf-8",
    )
    output = tmp_path / "memory"
    build_code_memory(repo=repo, output_dir=output, chunk_lines=10, overlap_lines=2)

    result = query_code_memory(
        index=output / "code_memory.json",
        query="export_trades",
        embedding_provider="none",
    )

    assert result["results"][0]["path"] == "implementation.py"
    assert result["results"][0]["symbol_score"] == 1.0
    assert result["retrieval"]["mode"] == "lexical_symbol_fallback"


def test_semantic_index_can_fall_back_without_query_credentials(tmp_path):
    repo = make_hybrid_repo(tmp_path)
    output = tmp_path / "memory"
    build_code_memory(
        repo=repo,
        output_dir=output,
        chunk_lines=20,
        overlap_lines=5,
        embedding_client=ConceptEmbeddingClient(),
    )

    result = query_code_memory(
        index=output / "code_memory.json",
        query="allocation function",
        embedding_provider="none",
    )

    assert result["retrieval"]["mode"] == "lexical_symbol_fallback"
    assert result["retrieval"]["semantic_status"] == "disabled"
    assert result["results"][0]["path"] == "strategy.py"


def test_legacy_index_remains_queryable(tmp_path):
    repo = make_hybrid_repo(tmp_path)
    output = tmp_path / "memory"
    build_code_memory(repo=repo, output_dir=output, chunk_lines=20, overlap_lines=5)
    index_path = output / "code_memory.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload["settings"].pop("fusion")
    for chunk in payload["chunks"]:
        chunk.pop("token_counts")
        chunk.pop("token_count")
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    result = query_code_memory(index=index_path, query="database migration", top_k=1)

    assert result["result_count"] == 1
    assert result["results"][0]["path"] == "storage.py"


def test_openai_embedding_client_batches_with_dimensions():
    captured = []
    client = object.__new__(OpenAIEmbeddingClient)
    client.model = "text-embedding-test"
    client.dimensions = 3
    client.batch_size = 2

    def fake_post(payload):
        captured.append(payload)
        return {
            "data": [
                {"index": index, "embedding": [float(index + 1), 0.0, 0.0]}
                for index, _text in enumerate(payload["input"])
            ]
        }

    client._post = fake_post
    vectors = client.embed_texts(["one", "two", "three"])

    assert len(captured) == 2
    assert captured[0] == {
        "model": "text-embedding-test",
        "input": ["one", "two"],
        "encoding_format": "float",
        "dimensions": 3,
    }
    assert len(vectors) == 3


def test_auto_build_falls_back_when_embedding_provider_fails(tmp_path, monkeypatch):
    repo = make_hybrid_repo(tmp_path)
    output = tmp_path / "memory"
    monkeypatch.setattr(
        "quant_agent.memory.code_index.create_embedding_client",
        lambda *args, **kwargs: FailingEmbeddingClient(),
    )

    result = build_code_memory(
        repo=repo,
        output_dir=output,
        chunk_lines=20,
        overlap_lines=5,
        embedding_provider="auto",
    )

    summary = result["index"]["summary"]
    assert summary["embedded_chunks"] == 0
    assert summary["embedding_status"] == "not_enabled"
    assert "temporary provider failure" in summary["embedding_error"]


def test_auto_query_falls_back_when_embedding_provider_fails(tmp_path, monkeypatch):
    repo = make_hybrid_repo(tmp_path)
    output = tmp_path / "memory"
    build_code_memory(
        repo=repo,
        output_dir=output,
        chunk_lines=20,
        overlap_lines=5,
        embedding_client=ConceptEmbeddingClient(),
    )
    index_path = output / "code_memory.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["settings"]["embedding_provider"] = "openai"
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "quant_agent.memory.code_index.create_embedding_client",
        lambda *args, **kwargs: FailingEmbeddingClient(),
    )

    result = query_code_memory(
        index=index_path,
        query="allocation function",
        embedding_provider="auto",
    )

    assert result["retrieval"]["mode"] == "lexical_symbol_fallback"
    assert result["retrieval"]["semantic_status"] == "unavailable_provider_error"
    assert "temporary provider failure" in result["retrieval"]["semantic_error"]
    assert result["results"][0]["path"] == "strategy.py"


def make_hybrid_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "strategy.py").write_text(
        "def rebalance_portfolio(signals):\n"
        "    allocation = normalize(signals)\n"
        "    return allocation\n",
        encoding="utf-8",
    )
    (repo / "storage.py").write_text(
        "def migrate_database(connection):\n"
        "    schema = create_schema(connection)\n"
        "    return schema\n",
        encoding="utf-8",
    )
    return repo
