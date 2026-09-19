from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_agent.discovery.scanner import collect_files, default_output_dir as default_discovery_dir
from quant_agent.discovery.scanner import discover_repo, read_text, safe_component
from quant_agent.memory.embeddings import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingClient,
    create_embedding_client,
)
from quant_agent.memory.hybrid import hybrid_rank


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
STOP_TOKENS = {
    "about",
    "code",
    "context",
    "does",
    "find",
    "for",
    "from",
    "give",
    "memory",
    "please",
    "query",
    "search",
    "show",
    "source",
    "the",
    "where",
    "with",
}
DEFAULT_VECTOR_SIZE = 512
DEFAULT_CHUNK_LINES = 80
DEFAULT_OVERLAP_LINES = 20


def build_code_memory(
    repo: Path,
    output_dir: Path | None = None,
    include_heavy_dirs: bool = False,
    max_file_bytes: int = 500_000,
    max_files: int = 20_000,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
    vector_size: int = DEFAULT_VECTOR_SIZE,
    embedding_provider: str = "none",
    embedding_model: str | None = None,
    embedding_dimensions: int | None = DEFAULT_EMBEDDING_DIMENSIONS,
    embedding_client: EmbeddingClient | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    if not repo.exists() or not repo.is_dir():
        raise FileNotFoundError(f"Repo path does not exist or is not a directory: {repo}")
    if chunk_lines < 10:
        raise ValueError("chunk_lines must be at least 10.")
    if overlap_lines < 0 or overlap_lines >= chunk_lines:
        raise ValueError("overlap_lines must be non-negative and smaller than chunk_lines.")

    output_dir = (output_dir or default_memory_dir(repo)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    discovery_dir = default_discovery_dir(repo).resolve()
    if not discovery_ready(discovery_dir):
        discover_repo(
            repo=repo,
            output_dir=discovery_dir,
            include_heavy_dirs=include_heavy_dirs,
            max_file_bytes=max_file_bytes,
            max_files=max_files,
        )

    files = collect_files(
        repo=repo,
        include_heavy_dirs=include_heavy_dirs,
        max_file_bytes=max_file_bytes,
        max_files=max_files,
    )
    chunks = []
    for file_item in files:
        if file_item.get("too_large"):
            continue
        path = repo / str(file_item["path"])
        text = read_text(path)
        chunks.extend(
            chunk_source_file(
                relative_path=str(file_item["path"]),
                extension=str(file_item["extension"]),
                text=text,
                chunk_lines=chunk_lines,
                overlap_lines=overlap_lines,
                vector_size=vector_size,
            )
        )

    semantic_client = embedding_client or create_embedding_client(
        embedding_provider,
        model=embedding_model,
        dimensions=embedding_dimensions,
    )
    semantic_provider = "none"
    semantic_model = None
    semantic_dimensions = None
    semantic_error = None
    if semantic_client is not None:
        semantic_provider = str(getattr(semantic_client, "provider", embedding_provider))
        semantic_model = str(getattr(semantic_client, "model", embedding_model or "unknown"))
        # Auto mode is best-effort; an explicitly supplied embedding client still fails closed.
        try:
            vectors = semantic_client.embed_texts([embedding_document(chunk) for chunk in chunks])
        except (OSError, RuntimeError, ValueError) as exc:
            if embedding_provider != "auto" or embedding_client is not None:
                raise
            semantic_error = f"{type(exc).__name__}: {exc}"
            semantic_client = None
            semantic_provider = "none"
            semantic_model = None
            vectors = []
        if len(vectors) != len(chunks):
            if semantic_client is not None:
                raise RuntimeError("Embedding provider did not return one vector per source chunk.")
        for chunk, vector in zip(chunks, vectors):
            chunk["embedding"] = [round(float(value), 8) for value in vector]
        if vectors:
            semantic_dimensions = len(vectors[0])
            if any(len(vector) != semantic_dimensions for vector in vectors):
                raise RuntimeError("Embedding provider returned inconsistent vector dimensions.")

    index = {
        "schema_version": 2,
        "repo": str(repo),
        "generated_at": utc_now(),
        "settings": {
            "include_heavy_dirs": include_heavy_dirs,
            "max_file_bytes": max_file_bytes,
            "max_files": max_files,
            "chunk_lines": chunk_lines,
            "overlap_lines": overlap_lines,
            "vector_size": vector_size,
            "embedding_provider": semantic_provider,
            "embedding_model": semantic_model,
            "embedding_dimensions": semantic_dimensions,
            "fusion": "weighted_reciprocal_rank_fusion",
        },
        "summary": {
            "indexed_files": len(files),
            "indexed_chunks": len(chunks),
            "embedded_chunks": len(chunks) if semantic_client is not None else 0,
            "embedding_status": "available" if semantic_client is not None else "not_enabled",
            "embedding_error": semantic_error,
            "skipped_large_files": sum(1 for item in files if item.get("too_large")),
        },
        "discovery_dir": str(discovery_dir),
        "chunks": chunks,
    }
    index_path = output_dir / "code_memory.json"
    report_path = output_dir / "CODE_MEMORY.md"
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_memory_build_summary(index, index_path), encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "index_path": str(index_path),
        "report_path": str(report_path),
        "index": index,
    }


def query_code_memory(
    index: Path,
    query: str,
    top_k: int = 8,
    max_excerpt_chars: int = 900,
    embedding_provider: str = "auto",
    embedding_model: str | None = None,
    embedding_dimensions: int | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be at least 1.")
    payload = read_index(index)
    vector_size = int(payload.get("settings", {}).get("vector_size") or DEFAULT_VECTOR_SIZE)
    query_vector = vectorize_text(query, vector_size)
    query_tokens = set(expand_tokens(tokenize(query)))
    chunks = [chunk for chunk in payload.get("chunks", []) if isinstance(chunk, dict)]
    stored_embeddings = [chunk for chunk in chunks if isinstance(chunk.get("embedding"), list)]
    query_embedding = None
    semantic_status = "not_indexed"
    semantic_error = None
    settings = payload.get("settings", {})
    if stored_embeddings and embedding_provider != "none":
        semantic_client = embedding_client
        if semantic_client is None:
            selected_model = embedding_model or settings.get("embedding_model") or DEFAULT_EMBEDDING_MODEL
            selected_dimensions = embedding_dimensions or settings.get("embedding_dimensions")
            requested_provider = embedding_provider
            if requested_provider == "auto" and settings.get("embedding_provider") != "openai":
                requested_provider = "none"
            semantic_client = create_embedding_client(
                requested_provider,
                model=str(selected_model),
                dimensions=int(selected_dimensions) if selected_dimensions else None,
            )
        if semantic_client is None:
            semantic_status = "unavailable_no_credentials"
        else:
            # Keep lexical retrieval usable when only the automatic semantic call fails.
            try:
                query_vectors = semantic_client.embed_texts([query])
            except (OSError, RuntimeError, ValueError) as exc:
                if embedding_provider != "auto" or embedding_client is not None:
                    raise
                semantic_error = f"{type(exc).__name__}: {exc}"
                query_vectors = []
                semantic_client = None
            if semantic_client is None:
                semantic_status = "unavailable_provider_error"
            elif len(query_vectors) != 1:
                raise RuntimeError("Embedding provider did not return one query vector.")
            else:
                query_embedding = query_vectors[0]
                expected_dimensions = len(stored_embeddings[0]["embedding"])
                if len(query_embedding) != expected_dimensions:
                    raise RuntimeError(
                        "Query embedding dimensions do not match the stored code-memory embeddings."
                    )
                semantic_status = "available"
    elif stored_embeddings:
        semantic_status = "disabled"

    ranked = hybrid_rank(
        chunks,
        query_tokens=query_tokens,
        query_vector=query_vector,
        query_embedding=query_embedding,
        tokenize_chunk=lambda value: expand_tokens(tokenize(value)),
        cosine_sparse=cosine,
        implementation_boost=implementation_boost,
    )
    results = []
    for item in ranked[:top_k]:
        chunk = item["chunk"]
        results.append(
            {
                "score": round(float(item["score"]), 6),
                "semantic_score": round(float(item["semantic_score"]), 6),
                "lexical_score": round(float(item["lexical_score"]), 6),
                "lexical_overlap": round(float(item["lexical_overlap"]), 6),
                "symbol_score": round(float(item["symbol_score"]), 6),
                "path_score": round(float(item["path_score"]), 6),
                "vector_score": round(float(item["vector_score"]), 6),
                "implementation_score": round(float(item["implementation_score"]), 6),
                "path": chunk.get("path"),
                "line_start": chunk.get("line_start"),
                "line_end": chunk.get("line_end"),
                "citation": source_citation(chunk),
                "symbols": chunk.get("symbols", []),
                "excerpt": truncate_excerpt(str(chunk.get("text", "")), max_excerpt_chars),
            }
        )
    return {
        "query": query,
        "index_path": str(index),
        "repo": payload.get("repo"),
        "generated_at": payload.get("generated_at"),
        "retrieval": {
            "mode": "hybrid" if query_embedding is not None else "lexical_symbol_fallback",
            "semantic_status": semantic_status,
            "embedding_provider": settings.get("embedding_provider", "none"),
            "embedding_model": settings.get("embedding_model"),
            "fusion": "weighted_reciprocal_rank_fusion",
            "semantic_error": semantic_error,
        },
        "result_count": len(results),
        "results": results,
    }


def find_latest_code_memory_index(root: Path, repo: Path | None = None) -> Path | None:
    memory_root = root / ".code_memory"
    if not memory_root.exists():
        return None
    candidates = sorted(
        memory_root.glob("**/code_memory.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if repo is None:
        return candidates[0] if candidates else None

    expected_repo = repo.resolve()
    for candidate in candidates:
        try:
            payload = read_index(candidate)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        repo_value = payload.get("repo")
        if not isinstance(repo_value, str) or not repo_value:
            continue
        try:
            indexed_repo = Path(repo_value).resolve()
        except OSError:
            continue
        if indexed_repo == expected_repo:
            return candidate
    return None


def chunk_source_file(
    relative_path: str,
    extension: str,
    text: str,
    chunk_lines: int,
    overlap_lines: int,
    vector_size: int,
) -> list[dict[str, Any]]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks = []
    step = chunk_lines - overlap_lines
    for start in range(0, len(lines), step):
        end = min(len(lines), start + chunk_lines)
        chunk_text = "\n".join(lines[start:end])
        token_values = expand_tokens(tokenize(" ".join([relative_path, chunk_text])))
        token_counts = Counter(token_values)
        vector = vectorize_tokens(token_values, vector_size)
        chunks.append(
            {
                "id": chunk_id(relative_path, start + 1, end, chunk_text),
                "path": relative_path,
                "extension": extension,
                "line_start": start + 1,
                "line_end": end,
                "symbols": extract_symbols(lines[start:end], start + 1),
                "tokens": top_tokens(token_values),
                "token_counts": dict(sorted(token_counts.items())),
                "token_count": sum(token_counts.values()),
                "vector": {str(key): value for key, value in sorted(vector.items())},
                "text": chunk_text,
            }
        )
        if end == len(lines):
            break
    return chunks


def render_memory_build_summary(index: dict[str, Any], index_path: Path) -> str:
    summary = index.get("summary", {})
    settings = index.get("settings", {})
    return "\n".join(
        [
            "# Code Memory",
            "",
            f"Repo: `{index.get('repo')}`",
            f"Generated at: `{index.get('generated_at')}`",
            f"Index: `{index_path}`",
            "",
            "## Summary",
            "",
            f"- Indexed files: {summary.get('indexed_files', 0)}",
            f"- Indexed chunks: {summary.get('indexed_chunks', 0)}",
            f"- Embedded chunks: {summary.get('embedded_chunks', 0)}",
            f"- Embedding status: {summary.get('embedding_status', 'not enabled')}",
            f"- Skipped large files: {summary.get('skipped_large_files', 0)}",
            "",
            "## Settings",
            "",
            f"- Chunk lines: {settings.get('chunk_lines')}",
            f"- Overlap lines: {settings.get('overlap_lines')}",
            f"- Vector size: {settings.get('vector_size')}",
            f"- Embedding provider: {settings.get('embedding_provider', 'none')}",
            f"- Embedding model: {settings.get('embedding_model') or 'not enabled'}",
            f"- Embedding dimensions: {settings.get('embedding_dimensions') or 'not enabled'}",
            f"- Fusion: {settings.get('fusion', 'legacy weighted score')}",
            "",
        ]
    )


def render_memory_query(result: dict[str, Any]) -> str:
    lines = [
        "Code memory query:",
        f"- Query: {result.get('query')}",
        f"- Repo: {result.get('repo')}",
        f"- Results: {result.get('result_count', 0)}",
        f"- Retrieval mode: {result.get('retrieval', {}).get('mode', 'legacy')}",
        f"- Semantic status: {result.get('retrieval', {}).get('semantic_status', 'not available')}",
        "",
    ]
    for index, item in enumerate(result.get("results", []), start=1):
        lines.extend(
            [
                f"{index}. {item.get('citation') or source_citation(item)} score={item.get('score')}",
                "   Scores: "
                f"semantic={item.get('semantic_score', 0)} "
                f"lexical={item.get('lexical_score', 0)} "
                f"symbol={item.get('symbol_score', 0)} "
                f"path={item.get('path_score', 0)}",
            ]
        )
        symbols = item.get("symbols", [])
        if symbols:
            symbol_text = ", ".join(f"{symbol.get('kind')} {symbol.get('name')}@{symbol.get('line')}" for symbol in symbols[:5])
            lines.append(f"   Symbols: {symbol_text}")
        excerpt = str(item.get("excerpt", "")).strip()
        if excerpt:
            lines.append("   Excerpt:")
            lines.extend(f"   {line}" for line in excerpt.splitlines()[:18])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def default_memory_dir(repo: Path) -> Path:
    return Path(".code_memory") / safe_component(repo.name)


def embedding_document(chunk: dict[str, Any]) -> str:
    symbols = " ".join(
        str(symbol.get("name", ""))
        for symbol in chunk.get("symbols", [])
        if isinstance(symbol, dict)
    )
    return "\n".join(
        [
            f"File: {chunk.get('path', '')}",
            f"Symbols: {symbols}",
            str(chunk.get("text", "")),
        ]
    )


def source_citation(chunk: dict[str, Any]) -> str:
    return f"{chunk.get('path')}:{chunk.get('line_start')}-{chunk.get('line_end')}"


def discovery_ready(discovery_dir: Path) -> bool:
    required = ["repo_index.json", "symbols.json", "entrypoints.json", "artifact_paths.json"]
    return all((discovery_dir / name).exists() for name in required)


def read_index(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Code memory index must be a JSON object: {path}")
    return payload


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def expand_tokens(tokens: list[str]) -> list[str]:
    expanded = []
    for token in tokens:
        expanded.append(token)
        for part in re.split(r"[_\W]+", token):
            if len(part) > 1 and part != token:
                expanded.append(part)
        for part in split_camel(token):
            if len(part) > 1 and part != token:
                expanded.append(part)
    return [token for token in expanded if len(token) > 1 and token not in STOP_TOKENS]


def split_camel(token: str) -> list[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token).split()
    return [part.lower() for part in parts]


def vectorize_text(text: str, vector_size: int) -> dict[int, float]:
    return vectorize_tokens(expand_tokens(tokenize(text)), vector_size)


def vectorize_tokens(tokens: list[str], vector_size: int) -> dict[int, float]:
    counts: dict[int, float] = {}
    for token in tokens:
        index = stable_hash(token) % vector_size
        counts[index] = counts.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values()))
    if norm <= 0:
        return {}
    return {index: round(value / norm, 8) for index, value in counts.items()}


def stable_hash(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def lexical_overlap(query_tokens: set[str], chunk_tokens: set[str]) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / max(1, len(query_tokens))


def path_overlap(query_tokens: set[str], path: str) -> float:
    path_tokens = set(expand_tokens(tokenize(path)))
    return lexical_overlap(query_tokens, path_tokens)


def implementation_boost(path: str) -> float:
    normalized = path.replace("\\", "/")
    if normalized.startswith("src/"):
        return 0.08
    if normalized.startswith("tests/"):
        return -0.04
    return 0.0


def top_tokens(tokens: list[str], limit: int = 80) -> list[str]:
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return [
        token
        for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def extract_symbols(lines: list[str], offset: int) -> list[dict[str, Any]]:
    symbols = []
    for index, line in enumerate(lines, start=offset):
        stripped = line.strip()
        for kind, pattern in [
            ("function", r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
            ("async_function", r"^async\s+def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
            ("class", r"^class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:(]"),
        ]:
            match = re.search(pattern, stripped)
            if match:
                symbols.append({"kind": kind, "name": match.group(1), "line": index})
    return symbols[:20]


def chunk_id(path: str, start: int, end: int, text: str) -> str:
    digest = hashlib.sha1(f"{path}:{start}:{end}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"{safe_component(path)}_{start}_{end}_{digest}"


def truncate_excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n..."


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
