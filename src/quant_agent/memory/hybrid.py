from __future__ import annotations

import math
from collections import Counter
from typing import Any


RRF_K = 60
SEMANTIC_WEIGHT = 1.0
LEXICAL_WEIGHT = 1.0
SYMBOL_WEIGHT = 0.70
PATH_WEIGHT = 0.45
HASHED_FALLBACK_WEIGHT = 0.40


def hybrid_rank(
    chunks: list[dict[str, Any]],
    *,
    query_tokens: set[str],
    query_vector: dict[int, float],
    query_embedding: list[float] | None,
    tokenize_chunk,
    cosine_sparse,
    implementation_boost,
) -> list[dict[str, Any]]:
    lexical_scores = bm25_scores(chunks, query_tokens, tokenize_chunk)
    candidates: list[dict[str, Any]] = []
    for position, chunk in enumerate(chunks):
        token_counts = chunk_token_counts(chunk, tokenize_chunk)
        chunk_tokens = set(token_counts)
        symbol_tokens = symbol_token_set(chunk, tokenize_chunk)
        path_tokens = set(tokenize_chunk(str(chunk.get("path", ""))))
        sparse_vector = {
            int(key): float(value)
            for key, value in chunk.get("vector", {}).items()
            if str(key).isdigit()
        }
        embedding = chunk.get("embedding")
        semantic_score = (
            cosine_dense(query_embedding, embedding)
            if query_embedding is not None and isinstance(embedding, list)
            else 0.0
        )
        candidates.append(
            {
                "chunk": chunk,
                "position": position,
                "semantic_score": max(0.0, semantic_score),
                "lexical_score": lexical_scores[position],
                "lexical_overlap": overlap_score(query_tokens, chunk_tokens),
                "symbol_score": overlap_score(query_tokens, symbol_tokens),
                "path_score": overlap_score(query_tokens, path_tokens),
                "vector_score": max(0.0, cosine_sparse(query_vector, sparse_vector)),
                "implementation_score": implementation_boost(str(chunk.get("path", ""))),
            }
        )

    channels = [
        ("semantic_score", SEMANTIC_WEIGHT, query_embedding is not None),
        ("lexical_score", LEXICAL_WEIGHT, True),
        ("symbol_score", SYMBOL_WEIGHT, True),
        ("path_score", PATH_WEIGHT, True),
        ("vector_score", HASHED_FALLBACK_WEIGHT, query_embedding is None),
    ]
    max_rrf = sum(weight / (RRF_K + 1) for _name, weight, enabled in channels if enabled)
    for item in candidates:
        item["rrf_score"] = 0.0

    for name, weight, enabled in channels:
        if not enabled:
            continue
        ranked = sorted(
            (item for item in candidates if float(item[name]) > 0),
            key=lambda item: (-float(item[name]), item["position"]),
        )
        for rank, item in enumerate(ranked, start=1):
            item["rrf_score"] += weight / (RRF_K + rank)

    for item in candidates:
        normalized_rrf = item["rrf_score"] / max_rrf if max_rrf else 0.0
        item["score"] = normalized_rrf + float(item["implementation_score"])

    return sorted(
        (item for item in candidates if float(item["score"]) > 0),
        key=lambda item: (
            -float(item["score"]),
            str(item["chunk"].get("path", "")),
            int(item["chunk"].get("line_start") or 0),
        ),
    )


def bm25_scores(
    chunks: list[dict[str, Any]],
    query_tokens: set[str],
    tokenize_chunk,
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    if not chunks or not query_tokens:
        return [0.0 for _chunk in chunks]
    counts = [chunk_token_counts(chunk, tokenize_chunk) for chunk in chunks]
    lengths = [sum(item.values()) for item in counts]
    average_length = sum(lengths) / max(1, len(lengths))
    document_frequency = {
        token: sum(1 for item in counts if token in item)
        for token in query_tokens
    }
    scores = []
    document_count = len(chunks)
    for token_counts, length in zip(counts, lengths):
        score = 0.0
        for token in query_tokens:
            frequency = token_counts.get(token, 0)
            if frequency <= 0:
                continue
            frequency_in_documents = document_frequency[token]
            inverse_document_frequency = math.log(
                1.0 + (document_count - frequency_in_documents + 0.5) / (frequency_in_documents + 0.5)
            )
            denominator = frequency + k1 * (
                1.0 - b + b * length / max(1.0, average_length)
            )
            score += inverse_document_frequency * frequency * (k1 + 1.0) / denominator
        scores.append(score)
    return scores


def chunk_token_counts(chunk: dict[str, Any], tokenize_chunk) -> Counter[str]:
    raw_counts = chunk.get("token_counts")
    if isinstance(raw_counts, dict):
        return Counter(
            {
                str(token): int(count)
                for token, count in raw_counts.items()
                if int(count) > 0
            }
        )
    text = " ".join([str(chunk.get("path", "")), str(chunk.get("text", ""))])
    return Counter(tokenize_chunk(text))


def symbol_token_set(chunk: dict[str, Any], tokenize_chunk) -> set[str]:
    values = []
    for symbol in chunk.get("symbols", []):
        if isinstance(symbol, dict):
            values.append(str(symbol.get("name", "")))
    return set(tokenize_chunk(" ".join(values)))


def overlap_score(query_tokens: set[str], target_tokens: set[str]) -> float:
    if not query_tokens or not target_tokens:
        return 0.0
    return len(query_tokens & target_tokens) / len(query_tokens)


def cosine_dense(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)

