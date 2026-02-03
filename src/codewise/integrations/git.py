"""Git integration — diff extraction, hook management, and push-time checks.

Provides:
- get_staged_diff() — for pre-commit hooks
- get_push_diff() — for pre-push hooks (local commits vs remote)
- get_branch_diff() — for CI/PR reviews
- get_commit_diff() — for specific commit ranges
- install_hooks() / uninstall_hooks() — manage git hooks
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
from pathlib import Path

from codewise.core.diff import parse_diff
from codewise.models import FileChange

logger = logging.getLogger("codewise.git")


# ── Diff Extraction ─────────────────────────────────────────────────

def get_staged_diff(repo_root: str | None = None) -> list[FileChange]:
    """Get diff of staged (cached) changes — used by pre-commit hook."""
    root = _resolve_root(repo_root)
    diff_text = _git(["diff", "--cached", "--unified=3"], cwd=root)
    if not diff_text.strip():
        return []
    return parse_diff(diff_text)


def get_unstaged_diff(repo_root: str | None = None) -> list[FileChange]:
    """Get diff of unstaged working-tree changes."""
    root = _resolve_root(repo_root)
    diff_text = _git(["diff", "--unified=3"], cwd=root)
    if not diff_text.strip():
        return []
    return parse_diff(diff_text)


def get_all_changes(repo_root: str | None = None) -> list[FileChange]:
    """Get all uncommitted changes (staged + unstaged)."""
    root = _resolve_root(repo_root)
    diff_text = _git(["diff", "HEAD", "--unified=3"], cwd=root)
    if not diff_text.strip():
        return []
    return parse_diff(diff_text)


def get_push_diff(
    remote: str = "origin",
    remote_ref: str | None = None,
    repo_root: str | None = None,
) -> list[FileChange]:
    """Get diff of local commits not yet pushed — used by pre-push hook.

    Compares current HEAD against the remote tracking branch.
    If remote_ref is provided, uses that explicitly.
    """
    root = _resolve_root(repo_root)
    branch = get_current_branch(root)

    if remote_ref is None:
        # Auto-detect remote tracking branch
        remote_ref = f"{remote}/{branch}"
        # Check if remote ref exists
        try:
            _git(["rev-parse", "--verify", remote_ref], cwd=root)
        except GitError:
            # No remote tracking branch — diff against empty (all commits)
            logger.info("No remote tracking branch %s, reviewing all commits", remote_ref)
            diff_text = _git(["diff", "--unified=3", "HEAD"], cwd=root)
            return parse_diff(diff_text) if diff_text.strip() else []

    diff_text = _git(["diff", f"{remote_ref}..HEAD", "--unified=3"], cwd=root)
    if not diff_text.strip():
        return []
    return parse_diff(diff_text)


def get_branch_diff(
    base: str = "main",
    head: str = "HEAD",
    repo_root: str | None = None,
) -> list[FileChange]:
    """Get diff between two branches — for PR-style reviews."""
    root = _resolve_root(repo_root)
    # Find merge base for a cleaner diff
    try:
        merge_base = _git(["merge-base", base, head], cwd=root).strip()
        diff_text = _git(["diff", f"{merge_base}..{head}", "--unified=3"], cwd=root)
    except GitError:
        # Fallback to simple diff
        diff_text = _git(["diff", f"{base}..{head}", "--unified=3"], cwd=root)

    if not diff_text.strip():
        return []
    return parse_diff(diff_text)


def get_commit_diff(
    commit_range: str,
    repo_root: str | None = None,
) -> list[FileChange]:
    """Get diff for a commit range (e.g., 'HEAD~3..HEAD', 'abc123')."""
    root = _resolve_root(repo_root)
    # Single commit = show that commit's diff
    if ".." not in commit_range:
        diff_text = _git(["show", commit_range, "--format=", "--unified=3"], cwd=root)
    else:
        diff_text = _git(["diff", commit_range, "--unified=3"], cwd=root)

    if not diff_text.strip():
        return []
    return parse_diff(diff_text)


def get_current_branch(repo_root: str | None = None) -> str:
    """Get the current branch name."""
    root = _resolve_root(repo_root)
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).strip()


def get_repo_root(path: str | None = None) -> str | None:
    """Find the git repository root from the given path."""
    try:
        root = _git(
            ["rev-parse", "--show-toplevel"],
            cwd=path or os.getcwd(),
        ).strip()
        return root
    except GitError:
        return None


def get_changed_files_count(
    remote: str = "origin",
    remote_ref: str | None = None,
    repo_root: str | None = None,
) -> int:
    """Count files changed between local and remote — for pre-push max_files check."""
    root = _resolve_root(repo_root)
    branch = get_current_branch(root)
    remote_ref = remote_ref or f"{remote}/{branch}"

    try:
        output = _git(["diff", "--name-only", f"{remote_ref}..HEAD"], cwd=root)
        return len([l for l in output.strip().split("\n") if l.strip()])
    except GitError:
        return 0


# ── Hook Management ─────────────────────────────────────────────────

PRE_COMMIT_HOOK = """\
#!/usr/bin/env bash
# Codewise pre-commit hook — runs review + security on staged changes
# Installed by: codewise hooks install
# Remove with:  codewise hooks uninstall

set -e

# Check if codewise is available
if ! command -v codewise &> /dev/null; then
    echo "⚠️  codewise not found in PATH — skipping pre-commit checks"
    echo "   Install: pip install codewise-ai"
    exit 0
fi

echo "🔍 Codewise: reviewing staged changes..."
codewise review --staged --hook-mode
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ Codewise found issues that block this commit."
    echo "   Fix the issues above or use --no-verify to skip."
    exit 1
fi

echo "✅ Codewise: all checks passed."
"""

PRE_PUSH_HOOK = """\
#!/usr/bin/env bash
# Codewise pre-push hook — runs review + security on unpushed commits
# Installed by: codewise hooks install
# Remove with:  codewise hooks uninstall

set -e

# Read push info from stdin (provided by git)
while read local_ref local_sha remote_ref remote_sha; do
    if [ "$local_sha" = "0000000000000000000000000000000000000000" ]; then
        # Branch being deleted, skip
        continue
    fi

    # Check if codewise is available
    if ! command -v codewise &> /dev/null; then
        echo "⚠️  codewise not found in PATH — skipping pre-push checks"
        exit 0
    fi

    echo "🔍 Codewise: reviewing changes before push..."
    echo "   Local:  $local_ref ($local_sha)"
    echo "   Remote: $remote_ref ($remote_sha)"

    # Determine what to diff against
    if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
        # New branch — review all commits
        codewise review --push --hook-mode
    else
        # Existing branch — review new commits only
        codewise review --push --base "$remote_sha" --hook-mode
    fi

    EXIT_CODE=$?

    if [ $EXIT_CODE -ne 0 ]; then
        echo ""
        echo "❌ Codewise found issues that block this push."
        echo "   Fix the issues above or use --no-verify to skip."
        exit 1
    fi
done

echo "✅ Codewise: all checks passed."
"""


def install_hooks(
    repo_root: str | None = None,
    pre_commit: bool = True,
    pre_push: bool = True,
    force: bool = False,
) -> list[str]:
    """Install git hooks into the repo.

    Returns list of installed hook paths.
    """
    root = _resolve_root(repo_root)
    hooks_dir = Path(root) / ".git" / "hooks"

    if not hooks_dir.exists():
        raise GitError(f"Not a git repository: {root}")

    installed: list[str] = []

    if pre_commit:
        hook_path = hooks_dir / "pre-commit"
        if hook_path.exists() and not force:
            # Check if it's our hook
            content = hook_path.read_text()
            if "codewise" not in content.lower():
                raise GitError(
                    f"pre-commit hook already exists at {hook_path}. "
                    "Use --force to overwrite, or add codewise to your existing hook."
                )
        _write_hook(hook_path, PRE_COMMIT_HOOK)
        installed.append(str(hook_path))

    if pre_push:
        hook_path = hooks_dir / "pre-push"
        if hook_path.exists() and not force:
            content = hook_path.read_text()
            if "codewise" not in content.lower():
                raise GitError(
                    f"pre-push hook already exists at {hook_path}. "
                    "Use --force to overwrite."
                )
        _write_hook(hook_path, PRE_PUSH_HOOK)
        installed.append(str(hook_path))

    return installed


def uninstall_hooks(
    repo_root: str | None = None,
    pre_commit: bool = True,
    pre_push: bool = True,
) -> list[str]:
    """Remove codewise git hooks.

    Only removes hooks that contain 'codewise' marker. Returns list of removed paths.
    """
    root = _resolve_root(repo_root)
    hooks_dir = Path(root) / ".git" / "hooks"
    removed: list[str] = []

    for hook_name, should_remove in [("pre-commit", pre_commit), ("pre-push", pre_push)]:
        if not should_remove:
            continue
        hook_path = hooks_dir / hook_name
        if hook_path.exists():
            content = hook_path.read_text()
            if "codewise" in content.lower():
                hook_path.unlink()
                removed.append(str(hook_path))
            else:
                logger.warning(
                    "Skipping %s — not a codewise hook (won't delete other tools' hooks)",
                    hook_path,
                )

    return removed


def hooks_status(repo_root: str | None = None) -> dict[str, str]:
    """Check which codewise hooks are installed.

    Returns dict like {"pre-commit": "installed", "pre-push": "not-installed"}.
    """
    root = _resolve_root(repo_root)
    hooks_dir = Path(root) / ".git" / "hooks"
    status: dict[str, str] = {}

    for hook_name in ("pre-commit", "pre-push"):
        hook_path = hooks_dir / hook_name
        if not hook_path.exists():
            status[hook_name] = "not-installed"
        else:
            content = hook_path.read_text()
            if "codewise" in content.lower():
                status[hook_name] = "installed"
            else:
                status[hook_name] = "other-hook"

    return status


# ── Internals ───────────────────────────────────────────────────────

class GitError(Exception):
    """Raised when a git operation fails."""


def _git(args: list[str], cwd: str | None = None) -> str:
    """Run a git command and return stdout."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)} timed out after 30s")
    except FileNotFoundError:
        raise GitError("git is not installed or not in PATH")


def _resolve_root(repo_root: str | None) -> str:
    """Resolve repo root — use provided or auto-detect."""
    if repo_root:
        return repo_root
    root = get_repo_root()
    if root is None:
        raise GitError("Not inside a git repository. Run from a git repo or pass --repo-root.")
    return root


def _write_hook(path: Path, content: str) -> None:
    """Write a hook file and make it executable."""
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
