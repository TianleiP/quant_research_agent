from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ISSUE_CHOICES = {"missing-position-exports", "generalized-fix"}
SYMBOL_TOKENS = [
    "weight",
    "weights",
    "rank",
    "ranks",
    "score",
    "scores",
    "position",
    "positions",
    "holding",
    "holdings",
    "trade",
    "trades",
    "symbol",
    "symbols",
    "ticker",
    "tickers",
    "selected",
    "sleeve",
]
KEYWORD_PATTERNS = {
    "to_csv": r"\.to_csv\(",
    "weight": r"\bweights?\b|\bweight\b",
    "rank": r"\branks?\b|\brank\b",
    "score": r"\bscores?\b|\bscore\b",
    "symbol": r"\bsymbols?\b|\btickers?\b",
    "turnover": r"\bturnover\b",
    "daily_curve": r"daily_curve|daily_curves",
    "summary_by_window": r"summary_by_window",
    "variant": r"\bvariant\b|variant_id|spec",
    "simulate": r"\bsimulat\w*\b",
}


def trace_source_file(
    repo: Path,
    file_path: str,
    issue: str = "missing-position-exports",
    output_dir: Path | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    relative_file = normalize_repo_file(file_path)
    source_path = repo / relative_file
    if not source_path.exists():
        raise FileNotFoundError(f"Target file does not exist under repo: {relative_file}")
    source = source_path.read_text(encoding="utf-8", errors="replace")
    trace = build_source_trace(source=source, relative_file=relative_file, issue=issue)
    trace["repo"] = str(repo)

    target_dir = output_dir or default_output_dir(relative_file)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "source_trace.json"
    markdown_path = target_dir / "source_trace.md"
    trace["artifacts"] = {
        "source_trace_json": str(json_path),
        "source_trace_markdown": str(markdown_path),
    }
    json_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_source_trace_markdown(trace), encoding="utf-8")
    return {
        "trace": trace,
        "trace_dir": str(target_dir),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def build_source_trace(source: str, relative_file: str, issue: str = "missing-position-exports") -> dict[str, Any]:
    if issue not in ISSUE_CHOICES:
        raise ValueError(f"Unsupported trace issue: {issue}")
    lines = source.splitlines()
    trace: dict[str, Any] = {
        "issue": issue,
        "file": relative_file,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "line_count": len(lines),
        "syntax_ok": True,
        "imports": [],
        "functions": [],
        "classes": [],
        "to_csv_calls": [],
        "export_site_candidates": [],
        "symbol_object_candidates": [],
        "keyword_hits": keyword_hits(lines),
        "patch_readiness": {},
    }
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        trace["syntax_ok"] = False
        trace["syntax_error"] = {
            "line": exc.lineno,
            "offset": exc.offset,
            "message": exc.msg,
        }
        trace["patch_readiness"] = readiness("needs_more_context", "Source file could not be parsed by Python AST.")
        return trace

    collector = SourceTraceCollector(lines)
    collector.visit(tree)
    trace["imports"] = collector.imports
    trace["functions"] = collector.functions
    trace["classes"] = collector.classes
    trace["to_csv_calls"] = collector.to_csv_calls
    trace["symbol_object_candidates"] = collector.symbol_object_candidates
    trace["export_site_candidates"] = build_export_site_candidates(collector.to_csv_calls, lines)
    trace["patch_readiness"] = assess_patch_readiness(trace)
    return trace


class SourceTraceCollector(ast.NodeVisitor):
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.scope_stack: list[str] = []
        self.imports: list[dict[str, Any]] = []
        self.functions: list[dict[str, Any]] = []
        self.classes: list[dict[str, Any]] = []
        self.to_csv_calls: list[dict[str, Any]] = []
        self.symbol_object_candidates: list[dict[str, Any]] = []

    def visit_Import(self, node: ast.Import) -> Any:
        self.imports.append(
            {
                "line": node.lineno,
                "module": None,
                "names": [alias.name for alias in node.names],
                "kind": "import",
            }
        )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        self.imports.append(
            {
                "line": node.lineno,
                "module": node.module,
                "names": [alias.name for alias in node.names],
                "kind": "from",
            }
        )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        class_info = {
            "name": node.name,
            "line_start": node.lineno,
            "line_end": node_end_line(node),
            "methods": [item.name for item in node.body if isinstance(item, ast.FunctionDef)],
        }
        self.classes.append(class_info)
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        qualname = ".".join(self.scope_stack + [node.name])
        calls = sorted({call_name(item) for item in ast.walk(node) if isinstance(item, ast.Call) and call_name(item)})
        assigned = sorted({name for item in ast.walk(node) for name in assigned_names(item)})
        referenced_names = sorted({item.id for item in ast.walk(node) if isinstance(item, ast.Name)})
        self.functions.append(
            {
                "name": node.name,
                "qualname": qualname,
                "line_start": node.lineno,
                "line_end": node_end_line(node),
                "calls": calls[:80],
                "assigned_names": assigned[:120],
                "referenced_names": referenced_names[:120],
                "contains_to_csv": any(is_to_csv_call(item) for item in ast.walk(node) if isinstance(item, ast.Call)),
                "contains_symbol_candidate": any(is_symbol_candidate(name) for name in assigned + referenced_names),
            }
        )
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_Call(self, node: ast.Call) -> Any:
        if is_to_csv_call(node):
            self.to_csv_calls.append(
                {
                    "line": node.lineno,
                    "function": current_scope(self.scope_stack),
                    "object": dotted_name(node.func.value) if isinstance(node.func, ast.Attribute) else "",
                    "call": source_line(self.lines, node.lineno),
                }
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> Any:
        for name in assigned_names(node):
            self.add_symbol_candidate(name=name, line=node.lineno, kind="assignment")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        for name in assigned_names(node):
            self.add_symbol_candidate(name=name, line=node.lineno, kind="assignment")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> Any:
        for name in assigned_names(node):
            self.add_symbol_candidate(name=name, line=node.lineno, kind="assignment")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> Any:
        if isinstance(node.ctx, ast.Load):
            self.add_symbol_candidate(name=node.id, line=node.lineno, kind="reference")
        self.generic_visit(node)

    def add_symbol_candidate(self, name: str, line: int, kind: str) -> None:
        if not is_symbol_candidate(name):
            return
        candidate = {
            "name": name,
            "line": line,
            "function": current_scope(self.scope_stack),
            "kind": kind,
            "text": source_line(self.lines, line),
        }
        key = (candidate["name"], candidate["line"], candidate["function"], candidate["kind"])
        existing = {
            (item["name"], item["line"], item["function"], item["kind"])
            for item in self.symbol_object_candidates
        }
        if key not in existing and len(self.symbol_object_candidates) < 250:
            self.symbol_object_candidates.append(candidate)


def assess_patch_readiness(trace: dict[str, Any]) -> dict[str, str]:
    if not trace.get("syntax_ok"):
        return readiness("needs_more_context", "Source syntax must be valid before patch generation.")
    to_csv_calls = trace.get("to_csv_calls", [])
    if not to_csv_calls:
        return readiness("needs_more_context", "No existing CSV export site was found.")

    export_functions = {item.get("function") for item in to_csv_calls}
    assignment_candidates = [
        item
        for item in trace.get("symbol_object_candidates", [])
        if item.get("kind") == "assignment" and item.get("function") in export_functions
    ]
    names = {str(item.get("name", "")).lower() for item in assignment_candidates}
    has_weight = any("weight" in name for name in names)
    has_rank_or_score = any("rank" in name or "score" in name for name in names)
    has_symbol_context = any("symbol" in name or "ticker" in name or "selected" in name for name in names)
    has_simulator_call = any(
        "simulate" in str(call).lower()
        for function in trace.get("functions", [])
        if function.get("qualname") in export_functions
        for call in function.get("calls", [])
    )
    if has_weight and (has_rank_or_score or has_symbol_context):
        return readiness(
            "ready_for_patch",
            "A CSV export site and symbol-level candidate objects appear in the same function.",
        )
    if has_weight and has_simulator_call:
        return readiness(
            "ready_for_patch",
            "The export function calls a simulator and has weight-map candidates; a wrapper can capture realized simulator weights without changing strategy logic.",
        )

    if trace.get("symbol_object_candidates"):
        return readiness(
            "needs_more_context",
            "Symbol-level candidates exist, but not enough assigned weight/rank/symbol objects were found at the export site.",
        )
    return readiness(
        "impossible_from_saved_state",
        "The file exposes aggregate CSV outputs but no symbol-level object candidates.",
    )


def build_export_site_candidates(to_csv_calls: list[dict[str, Any]], lines: list[str]) -> list[dict[str, Any]]:
    candidates = []
    for call in to_csv_calls:
        line = int(call["line"])
        start = max(1, line - 3)
        end = min(len(lines), line + 3)
        candidates.append(
            {
                "line": line,
                "function": call.get("function", "module"),
                "object": call.get("object", ""),
                "context": [
                    {"line": index, "text": source_line(lines, index)}
                    for index in range(start, end + 1)
                ],
            }
        )
    return candidates


def keyword_hits(lines: list[str]) -> dict[str, list[dict[str, Any]]]:
    hits = {}
    for name, pattern in KEYWORD_PATTERNS.items():
        compiled = re.compile(pattern, flags=re.IGNORECASE)
        hits[name] = [
            {"line": index + 1, "text": line.strip()[:220]}
            for index, line in enumerate(lines)
            if compiled.search(line)
        ][:40]
    return hits


def render_source_trace_markdown(trace: dict[str, Any]) -> str:
    readiness_payload = trace.get("patch_readiness", {})
    lines = [
        "# Source Trace",
        "",
        f"Issue: `{trace['issue']}`",
        f"File: `{trace['file']}`",
        f"Patch readiness: `{readiness_payload.get('status')}`",
        f"Reason: {readiness_payload.get('reason')}",
        "",
        "## Function Map",
        "",
    ]
    for function in trace.get("functions", []):
        lines.append(
            f"- `{function['qualname']}` lines {function['line_start']}-{function['line_end']}; "
            f"to_csv={function['contains_to_csv']}; symbol_candidates={function['contains_symbol_candidate']}"
        )
    lines.extend(["", "## Export Site Candidates", ""])
    for candidate in trace.get("export_site_candidates", []):
        lines.append(f"- line {candidate['line']} in `{candidate['function']}`: `{candidate['object']}.to_csv(...)`")
    if not trace.get("export_site_candidates"):
        lines.append("- None.")

    lines.extend(["", "## Symbol-Level Object Candidates", ""])
    candidates = trace.get("symbol_object_candidates", [])
    if candidates:
        for candidate in candidates[:60]:
            lines.append(
                f"- {candidate['kind']} `{candidate['name']}` at line {candidate['line']} "
                f"in `{candidate['function']}`"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Keyword Hits", ""])
    for name, hits in sorted(trace.get("keyword_hits", {}).items()):
        lines.append(f"- `{name}`: {len(hits)} hits")
    lines.append("")
    return "\n".join(lines)


def readiness(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def assigned_names(node: ast.AST) -> list[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]
    else:
        return []
    names: list[str] = []
    for target in targets:
        names.extend(target_names(target))
    return names


def target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in target.elts:
            names.extend(target_names(item))
        return names
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, ast.Subscript):
        return target_names(target.value)
    return []


def call_name(node: ast.Call) -> str | None:
    return dotted_name(node.func)


def dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def is_to_csv_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "to_csv"


def is_symbol_candidate(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in SYMBOL_TOKENS)


def node_end_line(node: ast.AST) -> int:
    lines = [getattr(item, "lineno", None) for item in ast.walk(node)]
    numeric_lines = [line for line in lines if isinstance(line, int)]
    return max(numeric_lines) if numeric_lines else getattr(node, "lineno", 0)


def current_scope(scope_stack: list[str]) -> str:
    return ".".join(scope_stack) if scope_stack else "module"


def source_line(lines: list[str], line: int) -> str:
    if line <= 0 or line > len(lines):
        return ""
    return lines[line - 1].strip()[:260]


def default_output_dir(relative_file: str) -> Path:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(".source_traces") / f"{timestamp}_{safe_component(relative_file)}"


def normalize_repo_file(file_path: str) -> str:
    path = Path(file_path)
    if path.is_absolute():
        raise ValueError("--file must be relative to --repo")
    return path.as_posix()


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "file"
