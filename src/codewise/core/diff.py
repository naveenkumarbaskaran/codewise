"""Diff parsing and file-change extraction utilities."""

from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path

from unidiff import PatchSet

from codewise.models import DiffHunk, FileChange

logger = logging.getLogger("codewise.diff")

# Language detection by extension
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "zsh",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".md": "markdown",
    ".r": "r",
    ".R": "r",
    ".dart": "dart",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".tf": "terraform",
    ".dockerfile": "dockerfile",
    ".vue": "vue",
    ".svelte": "svelte",
}


def detect_language(path: str) -> str:
    """Detect programming language from file extension."""
    ext = Path(path).suffix.lower()
    name = Path(path).name.lower()
    if name == "dockerfile":
        return "dockerfile"
    if name in ("makefile", "gnumakefile"):
        return "makefile"
    return _EXT_LANG.get(ext, "unknown")


def parse_diff(diff_text: str) -> list[FileChange]:
    """Parse a unified diff string into FileChange objects."""
    patch = PatchSet(diff_text)
    changes: list[FileChange] = []

    for patched_file in patch:
        path = patched_file.path
        language = detect_language(path)

        added: list[str] = []
        removed: list[str] = []
        hunks: list[DiffHunk] = []

        for hunk in patched_file:
            hunk_lines: list[str] = []
            for line in hunk:
                if line.is_added:
                    added.append(str(line.value).rstrip("\n"))
                elif line.is_removed:
                    removed.append(str(line.value).rstrip("\n"))
                hunk_lines.append(str(line))

            hunks.append(
                DiffHunk(
                    start_line=hunk.target_start,
                    end_line=hunk.target_start + hunk.target_length - 1,
                    header=str(hunk).split("\n")[0] if str(hunk) else "",
                    content="\n".join(hunk_lines),
                )
            )

        changes.append(
            FileChange(
                path=path,
                language=language,
                added_lines=added,
                removed_lines=removed,
                patch=str(patched_file),
                is_new=patched_file.is_added_file,
                is_deleted=patched_file.is_removed_file,
                hunks=hunks,
            )
        )

    return changes


def should_include(
    path: str,
    include_patterns: list[str],
    exclude_patterns: list[str],
    max_file_size: int | None = None,
    repo_root: str | None = None,
) -> bool:
    """Check if a file path matches include/exclude filters."""
    # Check excludes first
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(path, pattern):
            logger.debug("Excluded by pattern %s: %s", pattern, path)
            return False

    # Check includes
    included = any(fnmatch.fnmatch(path, p) for p in include_patterns)
    if not included:
        return False

    # Check file size
    if max_file_size and repo_root:
        full_path = os.path.join(repo_root, path)
        if os.path.exists(full_path):
            size = os.path.getsize(full_path)
            if size > max_file_size:
                logger.debug("File too large (%d bytes): %s", size, path)
                return False

    return True


def filter_changes(
    changes: list[FileChange],
    include_patterns: list[str],
    exclude_patterns: list[str],
    max_file_size: int | None = None,
    repo_root: str | None = None,
) -> list[FileChange]:
    """Filter file changes by include/exclude patterns and size."""
    return [
        c
        for c in changes
        if should_include(c.path, include_patterns, exclude_patterns, max_file_size, repo_root)
    ]


def read_file_content(path: str, repo_root: str | None = None) -> str | None:
    """Read file content, returning None if file doesn't exist or is binary."""
    full_path = os.path.join(repo_root, path) if repo_root else path
    try:
        with open(full_path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None
