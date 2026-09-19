"""Fail safely when publication candidates contain likely secrets or user paths.

The report intentionally prints only a finding category, relative path, and line
number. It never echoes the matched value.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


SCANNED_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SCANNED_NAMES = {".env.example", ".gitignore"}

EXCLUDED_TOP_LEVEL_DIRS = {
    ".code_memory",
    ".demo_runs",
    ".discovery",
    ".eval_tmp",
    ".fix_proposals",
    ".git",
    ".pytest_cache",
    ".source_traces",
    ".venv",
    ".venv311",
    ".wheels",
    "__pycache__",
    "build",
    "dist",
    "runs",
    "venv",
}
EXCLUDED_PATH_PREFIXES = ("evals/results/", "state/sessions/")
EXCLUDED_FILES = {
    ".codex/config.toml",
    ".env",
    "configs/xgboost_live_strategy.yaml",
    "state/project_state.json",
}

SECRET_PATTERNS = (
    ("openai_like_token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "github_token",
        re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)
USER_PATH_PATTERN = re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+")


@dataclass(frozen=True)
class Finding:
    category: str
    path: str
    line: int


def _is_candidate(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    relative_posix = relative.as_posix()
    if relative_posix in EXCLUDED_FILES:
        return False
    if relative_posix.startswith(EXCLUDED_PATH_PREFIXES):
        return False
    if relative.parts and relative.parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return False
    if any(part == "__pycache__" or part.endswith(".egg-info") for part in relative.parts[:-1]):
        return False
    return path.name in SCANNED_NAMES or path.suffix.lower() in SCANNED_SUFFIXES


def audit_publication(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if not _is_candidate(root, path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            for category, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(Finding(category, relative, line_number))
            if USER_PATH_PATTERN.search(line):
                findings.append(Finding("machine_user_path", relative, line_number))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to this script's parent repository).",
    )
    args = parser.parse_args()
    findings = audit_publication(args.root)
    if findings:
        print(f"Publication audit failed: {len(findings)} finding(s).")
        for finding in findings:
            print(f"{finding.category}: {finding.path}:{finding.line}")
        return 1
    print("Publication audit passed: no likely secrets or machine user paths found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
