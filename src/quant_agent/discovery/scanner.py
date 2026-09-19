from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".venv311",
    ".venv_ibkr_paper",
    ".wheels",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "runs",
    "state",
    ".discovery",
    ".code_memory",
    ".fix_proposals",
    ".source_traces",
}

DEFAULT_HEAVY_DIRS = {
    "data",
    "artifacts",
}

INDEX_EXTENSIONS = {
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".md",
    ".txt",
    ".ps1",
    ".bat",
    ".cmd",
    ".sh",
}

ARTIFACT_EXTENSIONS = {
    ".csv",
    ".json",
    ".parquet",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".pkl",
    ".xlsx",
}

PATH_LITERAL_RE = re.compile(
    r"""(?P<quote>["'])(?P<value>[^"']+\.(?:csv|json|parquet|yaml|yml|md|txt|pkl|xlsx))(?P=quote)""",
    re.IGNORECASE,
)


def discover_repo(
    repo: Path,
    output_dir: Path | None = None,
    include_heavy_dirs: bool = False,
    max_file_bytes: int = 500_000,
    max_files: int = 20_000,
) -> dict[str, Any]:
    repo = repo.resolve()
    if not repo.exists() or not repo.is_dir():
        raise FileNotFoundError(f"Repo path does not exist or is not a directory: {repo}")

    output_dir = (output_dir or default_output_dir(repo)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    files = collect_files(
        repo=repo,
        include_heavy_dirs=include_heavy_dirs,
        max_file_bytes=max_file_bytes,
        max_files=max_files,
    )
    python_indexes = [index_python_file(repo, item) for item in files if item["extension"] == ".py"]
    artifact_refs = artifact_references(repo, files)
    entrypoints = find_entrypoints(python_indexes)

    repo_index = {
        "repo": str(repo),
        "generated_at": utc_now(),
        "settings": {
            "include_heavy_dirs": include_heavy_dirs,
            "max_file_bytes": max_file_bytes,
            "max_files": max_files,
        },
        "summary": {
            "indexed_files": len(files),
            "python_files": len(python_indexes),
            "entrypoints": len(entrypoints),
            "artifact_references": len(artifact_refs),
        },
        "files": files,
    }

    symbols = {
        "repo": str(repo),
        "python_files": python_indexes,
    }
    artifact_paths = {
        "repo": str(repo),
        "references": artifact_refs,
    }
    findings = build_findings(repo_index, entrypoints, artifact_refs, python_indexes)

    write_json(output_dir / "repo_index.json", repo_index)
    write_json(output_dir / "symbols.json", symbols)
    write_json(output_dir / "entrypoints.json", {"repo": str(repo), "entrypoints": entrypoints})
    write_json(output_dir / "artifact_paths.json", artifact_paths)
    (output_dir / "findings.md").write_text(findings, encoding="utf-8")

    return {
        "output_dir": str(output_dir),
        "repo_index": repo_index,
        "symbols": symbols,
        "entrypoints": entrypoints,
        "artifact_paths": artifact_paths,
        "findings": findings,
    }


def collect_files(
    repo: Path,
    include_heavy_dirs: bool,
    max_file_bytes: int,
    max_files: int,
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    skip_dirs = set(DEFAULT_SKIP_DIRS)
    if not include_heavy_dirs:
        skip_dirs.update(DEFAULT_HEAVY_DIRS)

    for root, dirs, names in os.walk(str(repo)):
        dirs[:] = [name for name in dirs if name not in skip_dirs and not name.endswith(".egg-info")]
        root_path = Path(root)
        for name in names:
            path = root_path / name
            extension = path.suffix.lower()
            if extension not in INDEX_EXTENSIONS:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            too_large = stat.st_size > max_file_bytes
            rel = path.relative_to(repo).as_posix()
            files.append(
                {
                    "path": rel,
                    "extension": extension,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "too_large": too_large,
                }
            )
            if len(files) >= max_files:
                return files
    return sorted(files, key=lambda item: item["path"])


def index_python_file(repo: Path, file_item: dict[str, Any]) -> dict[str, Any]:
    path = repo / file_item["path"]
    index = {
        "path": file_item["path"],
        "imports": [],
        "functions": [],
        "classes": [],
        "constants": {},
        "has_main_guard": False,
        "framework_hints": [],
        "file_references": [],
        "parse_error": None,
    }
    if file_item.get("too_large"):
        index["parse_error"] = "file exceeds max_file_bytes"
        return index

    text = read_text(path)
    index["file_references"] = path_literals(text)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        index["parse_error"] = f"{exc.msg} at line {exc.lineno}"
        return index

    visitor = PythonIndexVisitor()
    visitor.visit(tree)
    index.update(
        {
            "imports": sorted(visitor.imports),
            "functions": visitor.functions,
            "classes": visitor.classes,
            "constants": visitor.constants,
            "has_main_guard": visitor.has_main_guard,
            "framework_hints": sorted(visitor.framework_hints),
            "file_references": merge_references(index["file_references"], visitor.file_references),
        }
    )
    return index


class PythonIndexVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.functions: list[dict[str, Any]] = []
        self.classes: list[dict[str, Any]] = []
        self.constants: dict[str, Any] = {}
        self.has_main_guard = False
        self.framework_hints: set[str] = set()
        self.file_references: list[dict[str, Any]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name.split(".")[0])
            self.add_framework_hint(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module.split(".")[0])
            self.add_framework_hint(node.module)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append({"name": node.name, "line": node.lineno})
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append({"name": node.name, "line": node.lineno, "async": True})
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append({"name": node.name, "line": node.lineno})
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                value = literal_value(node.value)
                if value is not None:
                    self.constants[target.id] = json_safe(value)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if is_main_guard(node.test):
            self.has_main_guard = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = dotted_name(node.func)
        if call_name:
            self.add_framework_hint(call_name)
            ref = call_file_reference(call_name, node)
            if ref:
                self.file_references.append(ref)
        self.generic_visit(node)

    def add_framework_hint(self, value: str) -> None:
        lower = value.lower()
        for hint in ["argparse", "click", "typer", "fire", "pandas", "numpy", "pyarrow", "xgboost"]:
            if hint in lower:
                self.framework_hints.add(hint)


def artifact_references(repo: Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in files:
        if item.get("too_large"):
            continue
        path = repo / item["path"]
        text = read_text(path)
        for ref in path_literals(text):
            refs.append({"source_file": item["path"], **ref})
    return refs


def find_entrypoints(python_indexes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entrypoints = []
    for item in python_indexes:
        reasons = []
        if item.get("has_main_guard"):
            reasons.append("main_guard")
        hints = set(item.get("framework_hints", []))
        if hints.intersection({"argparse", "click", "typer", "fire"}):
            reasons.append("cli_framework")
        function_names = {fn["name"] for fn in item.get("functions", [])}
        if function_names.intersection({"main", "run", "run_backtest"}):
            reasons.append("main_like_function")
        if reasons:
            entrypoints.append(
                {
                    "path": item["path"],
                    "reasons": reasons,
                    "functions": item.get("functions", [])[:20],
                    "constants": item.get("constants", {}),
                    "framework_hints": item.get("framework_hints", []),
                }
            )
    return entrypoints


def build_findings(
    repo_index: dict[str, Any],
    entrypoints: list[dict[str, Any]],
    artifact_refs: list[dict[str, Any]],
    python_indexes: list[dict[str, Any]],
) -> str:
    parse_errors = [item for item in python_indexes if item.get("parse_error")]
    lines = [
        "# Discovery Findings",
        "",
        f"Repo: `{repo_index['repo']}`",
        f"Generated at: `{repo_index['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Indexed files: {repo_index['summary']['indexed_files']}",
        f"- Python files: {repo_index['summary']['python_files']}",
        f"- Candidate entrypoints: {len(entrypoints)}",
        f"- Artifact path references: {len(artifact_refs)}",
        f"- Python parse issues: {len(parse_errors)}",
        "",
        "## Candidate Entrypoints",
        "",
    ]
    if entrypoints:
        for entry in entrypoints[:25]:
            reasons = ", ".join(entry["reasons"])
            lines.append(f"- `{entry['path']}` ({reasons})")
    else:
        lines.append("- None detected.")

    lines.extend(["", "## Frequent Artifact References", ""])
    frequent = frequent_artifacts(artifact_refs)
    if frequent:
        for value, count in frequent[:25]:
            lines.append(f"- `{value}`: {count}")
    else:
        lines.append("- None detected.")

    if parse_errors:
        lines.extend(["", "## Parse Issues", ""])
        for item in parse_errors[:25]:
            lines.append(f"- `{item['path']}`: {item['parse_error']}")
    lines.append("")
    return "\n".join(lines)


def path_literals(text: str) -> list[dict[str, Any]]:
    refs = []
    for match in PATH_LITERAL_RE.finditer(text):
        value = match.group("value")
        suffix = Path(value).suffix.lower()
        if suffix in ARTIFACT_EXTENSIONS:
            refs.append({"value": value, "extension": suffix})
    return refs


def call_file_reference(call_name: str, node: ast.Call) -> dict[str, Any] | None:
    names = {
        "open",
        "read_csv",
        "to_csv",
        "read_json",
        "to_json",
        "read_parquet",
        "to_parquet",
        "write_text",
        "read_text",
    }
    if call_name.split(".")[-1] not in names or not node.args:
        return None
    value = literal_value(node.args[0])
    if isinstance(value, str) and Path(value).suffix.lower() in ARTIFACT_EXTENSIONS:
        return {"value": value, "extension": Path(value).suffix.lower(), "call": call_name}
    return None


def merge_references(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    merged = []
    for group in groups:
        for ref in group:
            key = (ref.get("value"), ref.get("extension"), ref.get("call"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
    return merged


def literal_value(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if not isinstance(node.left, ast.Name) or node.left.id != "__name__":
        return False
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return False
    if len(node.comparators) != 1:
        return False
    return literal_value(node.comparators[0]) == "__main__"


def dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def frequent_artifacts(refs: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for ref in refs:
        value = ref["value"]
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(json_safe(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def default_output_dir(repo: Path) -> Path:
    return Path(".discovery") / safe_component(repo.name)


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "repo"


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
