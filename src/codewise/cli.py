"""CLI — Click-based command-line interface for codewise.

Usage:
    codewise review [--staged | --push | --branch BASE]
    codewise security [--staged | --push | --branch BASE]
    codewise testgen <files...>
    codewise docgen <files...>
    codewise rules [--list-packs | --init]
    codewise hooks [install | uninstall | status]
    codewise init
    codewise mcp
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from codewise import __version__

console = Console()

# ── Shared Options ──────────────────────────────────────────────────


def common_options(f):
    """Shared CLI options for review/security commands."""
    f = click.option("--model", "-m", default=None, help="LLM model (e.g., gpt-4o-mini, claude-sonnet-4-20250514)")(f)
    f = click.option("--api-key", envvar="CODEWISE_API_KEY", default=None, help="API key")(f)
    f = click.option("--config", "-c", "config_path", default=None, help="Config file path")(f)
    f = click.option("--format", "-f", "output_format", type=click.Choice(["terminal", "json", "sarif", "markdown"]), default=None)(f)
    f = click.option("--fail-on", type=click.Choice(["critical", "high", "medium", "low", "info", "none"]), default=None)(f)
    f = click.option("--repo-root", default=None, help="Git repository root")(f)
    f = click.option("--verbose", "-v", is_flag=True, help="Verbose output")(f)
    return f


def diff_options(f):
    """Options for choosing which diff to review."""
    f = click.option("--staged", is_flag=True, help="Review staged changes (pre-commit)")(f)
    f = click.option("--push", is_flag=True, help="Review unpushed commits (pre-push)")(f)
    f = click.option("--branch", "base_branch", default=None, help="Compare against branch (PR mode)")(f)
    f = click.option("--commit", default=None, help="Review specific commit or range")(f)
    f = click.option("--base", default=None, help="Base ref for push diff")(f)
    f = click.option("--hook-mode", is_flag=True, hidden=True, help="Compact output for git hooks")(f)
    return f


# ── Main CLI Group ──────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="codewise")
@click.pass_context
def main(ctx: click.Context):
    """codewise — LLM-agnostic code intelligence toolkit.

    Review code, scan for security issues, generate tests and docs.
    Works as CLI, MCP server, GitHub Action, or git hook.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── Review Command ──────────────────────────────────────────────────


@main.command()
@common_options
@diff_options
@click.argument("files", nargs=-1, type=click.Path(exists=True))
def review(
    model, api_key, config_path, output_format, fail_on, repo_root, verbose,
    staged, push, base_branch, commit, base, hook_mode, files,
):
    """Review code changes for bugs, quality, and best practices."""
    _setup_logging(verbose)
    config, rules = _load_config(config_path, repo_root, model=model, api_key=api_key,
                                  output_format=output_format, fail_on=fail_on)

    changes = _get_changes(staged, push, base_branch, commit, base, files, repo_root, config)
    if not changes:
        console.print("[yellow]No changes to review.[/yellow]")
        return

    # Filter changes
    from codewise.core.diff import filter_changes
    changes = filter_changes(changes, config.include_patterns, config.exclude_patterns, config.max_file_size)

    if not changes:
        console.print("[yellow]All changed files were excluded by filters.[/yellow]")
        return

    # Run regex rules first (fast, no LLM)
    from codewise.rules import run_regex_rules, build_llm_rules_instruction, summarize_rules
    from codewise.integrations.git import get_current_branch

    branch = None
    try:
        branch = get_current_branch(repo_root)
    except Exception:
        pass

    regex_findings = run_regex_rules(changes, rules, branch)

    # Build LLM extra instructions from rules
    llm_instruction = build_llm_rules_instruction(rules, changes, branch)
    if llm_instruction and config.extra_instructions:
        config.extra_instructions += llm_instruction
    elif llm_instruction:
        config.extra_instructions = llm_instruction

    # Run LLM review
    from codewise.core.reviewer import review_changes
    result = asyncio.run(review_changes(changes, config))

    # Merge regex findings into LLM results
    result.findings = regex_findings + result.findings

    # Output
    formatter = _get_formatter(config.output_format)
    rules_summary = summarize_rules(rules) if rules else ""

    if hook_mode:
        exit_code = formatter.format_hook_result(result, "Review")
        sys.exit(exit_code)
    else:
        formatter.format_review(result, show_rules=rules_summary)

    # Exit code based on fail_on
    if config.fail_on and _has_blocking_findings(result.findings, config.fail_on):
        sys.exit(1)


# ── Security Command ────────────────────────────────────────────────


@main.command()
@common_options
@diff_options
@click.argument("files", nargs=-1, type=click.Path(exists=True))
def security(
    model, api_key, config_path, output_format, fail_on, repo_root, verbose,
    staged, push, base_branch, commit, base, hook_mode, files,
):
    """Scan code for security vulnerabilities."""
    _setup_logging(verbose)
    config, rules = _load_config(config_path, repo_root, model=model, api_key=api_key,
                                  output_format=output_format, fail_on=fail_on)

    changes = _get_changes(staged, push, base_branch, commit, base, files, repo_root, config)
    if not changes:
        console.print("[yellow]No changes to scan.[/yellow]")
        return

    from codewise.core.diff import filter_changes
    changes = filter_changes(changes, config.include_patterns, config.exclude_patterns, config.max_file_size)

    from codewise.core.security import scan_changes
    result = asyncio.run(scan_changes(changes, config))

    formatter = _get_formatter(config.output_format)
    if hook_mode:
        exit_code = formatter.format_hook_result(result, "Security")
        sys.exit(exit_code)
    else:
        formatter.format_security(result)

    if config.fail_on and _has_blocking_findings(result.findings, config.fail_on):
        sys.exit(1)


# ── Test Generation Command ────────────────────────────────────────


@main.command()
@common_options
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--framework", default=None, help="Test framework (pytest, jest, go, junit)")
@click.option("--write", is_flag=True, help="Write generated tests to files")
def testgen(model, api_key, config_path, output_format, fail_on, repo_root, verbose, files, framework, write):
    """Generate test cases for source files."""
    _setup_logging(verbose)
    overrides = {}
    if framework:
        overrides["test_framework"] = framework
    config, _ = _load_config(config_path, repo_root, model=model, api_key=api_key,
                              output_format=output_format, **overrides)

    from codewise.core.testgen import generate_tests_for_diff
    result = asyncio.run(generate_tests_for_diff(list(files), config, repo_root))

    formatter = _get_formatter(config.output_format)
    formatter.format_testgen(result)

    if write and result.tests:
        for test in result.tests:
            test_path = Path(test.file)
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(test.test_code)
            console.print(f"[green]Wrote: {test_path}[/green]")


# ── Documentation Generation Command ──────────────────────────────


@main.command()
@common_options
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--apply", is_flag=True, help="Apply doc changes to source files")
def docgen(model, api_key, config_path, output_format, fail_on, repo_root, verbose, files, apply):
    """Generate or improve documentation for source files."""
    _setup_logging(verbose)
    config, _ = _load_config(config_path, repo_root, model=model, api_key=api_key,
                              output_format=output_format)

    from codewise.core.docgen import generate_docs_batch
    result = asyncio.run(generate_docs_batch(list(files), config, repo_root))

    formatter = _get_formatter(config.output_format)
    formatter.format_docgen(result)


# ── Rules Command ──────────────────────────────────────────────────


@main.group(invoke_without_command=True)
@click.pass_context
def rules(ctx: click.Context):
    """Manage review rules and rule packs."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@rules.command("list-packs")
def rules_list_packs():
    """List available standard rule packs."""
    from codewise.rules import get_available_packs, STANDARD_PACKS

    console.print("\n[bold]Available Rule Packs:[/bold]\n")
    for name, count in get_available_packs().items():
        console.print(f"  [cyan]{name}[/cyan] — {count} rules")
        pack = STANDARD_PACKS[name]
        for r in pack[:3]:
            console.print(f"    • {r['id']}: {r.get('message', r.get('llm_check', ''))[:60]}")
        if len(pack) > 3:
            console.print(f"    ... and {len(pack) - 3} more")
        console.print()

    console.print("[dim]Enable packs in .codewise.yaml under rules.enable_packs[/dim]\n")


@rules.command("show")
@click.option("--config", "-c", "config_path", default=None)
@click.option("--repo-root", default=None)
def rules_show(config_path, repo_root):
    """Show active rules for the current project."""
    from codewise.config import load_config
    from codewise.rules import summarize_rules

    _, rules_list = load_config(config_path, repo_root)
    console.print(f"\n{summarize_rules(rules_list)}\n")

    for r in rules_list:
        if not r.enabled:
            continue
        type_label = f"[cyan]regex[/cyan]" if r.rule_type.value == "regex" else f"[magenta]LLM[/magenta]"
        console.print(f"  {type_label} [{r.severity.value}] [bold]{r.id}[/bold]: {r.message or r.llm_check or ''}")


@rules.command("test")
@click.argument("rule_id")
@click.argument("file", type=click.Path(exists=True))
@click.option("--config", "-c", "config_path", default=None)
def rules_test(rule_id, file, config_path):
    """Test a specific rule against a file."""
    from codewise.config import load_config
    from codewise.rules import run_regex_rules
    from codewise.models import FileChange
    from codewise.core.diff import detect_language

    _, rules_list = load_config(config_path)
    target_rules = [r for r in rules_list if r.id == rule_id]
    if not target_rules:
        console.print(f"[red]Rule not found: {rule_id}[/red]")
        sys.exit(1)

    content = Path(file).read_text()
    change = FileChange(
        path=file,
        language=detect_language(file),
        full_content=content,
        patch=content,
    )

    findings = run_regex_rules([change], target_rules)
    if findings:
        console.print(f"\n[yellow]Found {len(findings)} match(es):[/yellow]")
        for f in findings:
            console.print(f"  Line {f.line}: {f.code_before}")
    else:
        console.print(f"\n[green]No matches for rule {rule_id}[/green]")


# ── Hooks Command ──────────────────────────────────────────────────


@main.group(invoke_without_command=True)
@click.pass_context
def hooks(ctx: click.Context):
    """Manage git hooks (pre-commit, pre-push)."""
    if ctx.invoked_subcommand is None:
        # Show status by default
        ctx.invoke(hooks_status_cmd)


@hooks.command("install")
@click.option("--pre-commit/--no-pre-commit", default=True, help="Install pre-commit hook")
@click.option("--pre-push/--no-pre-push", default=True, help="Install pre-push hook")
@click.option("--force", is_flag=True, help="Overwrite existing hooks")
@click.option("--repo-root", default=None)
def hooks_install(pre_commit, pre_push, force, repo_root):
    """Install codewise git hooks."""
    from codewise.integrations.git import install_hooks

    try:
        installed = install_hooks(repo_root, pre_commit=pre_commit, pre_push=pre_push, force=force)
        for path in installed:
            console.print(f"[green]✓ Installed: {path}[/green]")
        if not installed:
            console.print("[yellow]No hooks installed.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@hooks.command("uninstall")
@click.option("--repo-root", default=None)
def hooks_uninstall(repo_root):
    """Remove codewise git hooks."""
    from codewise.integrations.git import uninstall_hooks

    removed = uninstall_hooks(repo_root)
    for path in removed:
        console.print(f"[green]✓ Removed: {path}[/green]")
    if not removed:
        console.print("[yellow]No codewise hooks found to remove.[/yellow]")


@hooks.command("status")
def hooks_status_cmd():
    """Show git hook status."""
    from codewise.integrations.git import hooks_status

    try:
        status = hooks_status()
        console.print("\n[bold]Git Hook Status:[/bold]\n")
        for hook, state in status.items():
            if state == "installed":
                icon = "✅"
                label = "[green]codewise hook installed[/green]"
            elif state == "other-hook":
                icon = "⚠️"
                label = "[yellow]other hook exists (not codewise)[/yellow]"
            else:
                icon = "❌"
                label = "[dim]not installed[/dim]"
            console.print(f"  {icon} {hook}: {label}")
        console.print()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ── Init Command ───────────────────────────────────────────────────


@main.command()
@click.option("--force", is_flag=True, help="Overwrite existing config")
def init(force):
    """Create a .codewise.yaml config file in the current directory."""
    from codewise.config import generate_default_config

    config_path = Path.cwd() / ".codewise.yaml"
    if config_path.exists() and not force:
        console.print(f"[yellow]Config already exists: {config_path}[/yellow]")
        console.print("Use --force to overwrite.")
        return

    config_path.write_text(generate_default_config())
    console.print(f"[green]✓ Created: {config_path}[/green]")
    console.print("Edit this file to configure models, rules, and hooks.")


# ── MCP Server Command ─────────────────────────────────────────────


@main.command()
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio")
@click.option("--port", default=3000, help="Port for SSE transport")
def mcp(transport, port):
    """Start codewise as an MCP server."""
    try:
        from codewise.mcp.server import run_server
        run_server(transport=transport, port=port)
    except ImportError:
        console.print("[red]MCP support not installed. Run: pip install codewise-ai[mcp][/red]")
        sys.exit(1)


# ── Helpers ─────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s: %(message)s")


def _load_config(config_path, repo_root=None, **overrides):
    """Load config with CLI overrides."""
    from codewise.config import load_config
    from codewise.models import Severity

    # Clean None values from overrides
    clean = {k: v for k, v in overrides.items() if v is not None}

    # Handle fail_on special value
    if "fail_on" in clean:
        val = clean["fail_on"]
        if val == "none":
            clean["fail_on"] = None
        else:
            clean["fail_on"] = val  # will be parsed by config loader

    return load_config(config_path=config_path, repo_root=repo_root, overrides=clean)


def _get_changes(staged, push, base_branch, commit, base, files, repo_root, config):
    """Get file changes based on CLI flags."""
    from codewise.integrations.git import (
        get_staged_diff,
        get_push_diff,
        get_branch_diff,
        get_commit_diff,
        get_all_changes,
    )
    from codewise.core.diff import detect_language, read_file_content
    from codewise.models import FileChange

    if files:
        # Review specific files
        changes = []
        for path in files:
            content = Path(path).read_text(errors="replace")
            changes.append(FileChange(
                path=str(path),
                language=detect_language(str(path)),
                full_content=content,
                patch=content,
                is_new=True,
            ))
        return changes

    if staged:
        return get_staged_diff(repo_root)

    if push:
        return get_push_diff(remote_ref=base, repo_root=repo_root)

    if base_branch:
        return get_branch_diff(base=base_branch, repo_root=repo_root)

    if commit:
        return get_commit_diff(commit, repo_root)

    # Default: all uncommitted changes
    return get_all_changes(repo_root)


def _get_formatter(output_format: str):
    """Get output formatter by name."""
    from codewise.output import get_formatter
    return get_formatter(output_format)


def _has_blocking_findings(findings, fail_on) -> bool:
    """Check if any findings meet or exceed the fail_on severity."""
    from codewise.models import Severity
    rank = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}
    threshold = rank.get(fail_on, 3)
    return any(rank.get(f.severity, 0) >= threshold for f in findings)


if __name__ == "__main__":
    main()
