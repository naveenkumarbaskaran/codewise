"""Tests for the rules engine."""

from codewise.models import FileChange, ReviewCategory, Severity
from codewise.rules import (
    Rule,
    RuleType,
    _dict_to_rule,
    _file_matches_with_braces,
    build_llm_rules_instruction,
    get_available_packs,
    load_rules_from_config,
    run_regex_rules,
    summarize_rules,
)


def test_rule_type_regex():
    r = Rule(id="test", pattern=r"\bprint\(")
    assert r.rule_type == RuleType.REGEX


def test_rule_type_llm():
    r = Rule(id="test", llm_check="Check for type hints")
    assert r.rule_type == RuleType.LLM


def test_rule_type_composite():
    r = Rule(id="test", pattern=r"\bprint\(", llm_check="Also verify logging")
    assert r.rule_type == RuleType.COMPOSITE


def test_rule_matches_file():
    r = Rule(id="test", file_pattern="*.py")
    assert r.matches_file("src/main.py")
    assert not r.matches_file("index.js")


def test_rule_matches_file_with_path_ignore():
    r = Rule(id="test", file_pattern="*.py", paths_ignore=["tests/*"])
    assert r.matches_file("src/main.py")
    assert not r.matches_file("tests/test_main.py")


def test_rule_matches_branch():
    r = Rule(id="test", branches=["main", "release/*"])
    assert r.matches_branch("main")
    assert r.matches_branch("release/v1.0")
    assert not r.matches_branch("feature/foo")
    assert r.matches_branch(None)  # Unknown branch → allow


def test_brace_pattern():
    assert _file_matches_with_braces("app.js", "*.{js,ts}")
    assert _file_matches_with_braces("app.ts", "*.{js,ts}")
    assert not _file_matches_with_braces("app.py", "*.{js,ts}")
    assert _file_matches_with_braces("main.py", "*.py")


def test_run_regex_rules_basic():
    rules = [
        Rule(
            id="no-print",
            pattern=r"\bprint\(",
            file_pattern="*.py",
            severity=Severity.MEDIUM,
            category=ReviewCategory.BEST_PRACTICE,
            message="Use logging instead of print().",
        ),
    ]
    changes = [
        FileChange(
            path="main.py",
            language="python",
            full_content='import os\nprint("hello")\nx = 1\n',
            patch='print("hello")',
        ),
    ]
    findings = run_regex_rules(changes, rules)
    assert len(findings) == 1
    assert findings[0].line == 2
    assert "no-print" in findings[0].title


def test_run_regex_rules_no_match():
    rules = [
        Rule(id="no-print", pattern=r"\bprint\(", file_pattern="*.py"),
    ]
    changes = [
        FileChange(path="main.go", language="go", full_content="fmt.Println()", patch="fmt.Println()"),
    ]
    findings = run_regex_rules(changes, rules)
    assert len(findings) == 0  # .go doesn't match *.py


def test_run_regex_rules_branch_filter():
    rules = [
        Rule(
            id="no-todo",
            pattern=r"TODO",
            file_pattern="*.py",
            branches=["main"],
            severity=Severity.LOW,
            category=ReviewCategory.MAINTAINABILITY,
            message="Remove TODOs",
        ),
    ]
    changes = [
        FileChange(path="main.py", language="python", full_content="# TODO: fix", patch="# TODO: fix"),
    ]
    # Match on main
    assert len(run_regex_rules(changes, rules, branch="main")) == 1
    # No match on feature branch
    assert len(run_regex_rules(changes, rules, branch="feature/x")) == 0


def test_load_rules_from_config_packs():
    config_data = {
        "rules": {
            "enable_packs": ["python-best-practices", "security-basics"],
        }
    }
    rules = load_rules_from_config(config_data)
    assert len(rules) > 5  # Both packs combined
    assert any(r.id == "py/no-print" for r in rules)
    assert any(r.id == "sec/no-hardcoded-secrets" for r in rules)


def test_load_rules_from_config_custom():
    config_data = {
        "rules": {
            "custom": [
                {
                    "id": "my-rule",
                    "pattern": r"DEBUGMODE",
                    "severity": "high",
                    "message": "No debug flags",
                }
            ]
        }
    }
    rules = load_rules_from_config(config_data)
    assert len(rules) == 1
    assert rules[0].id == "my-rule"
    assert rules[0].severity == Severity.HIGH


def test_load_rules_disable():
    config_data = {
        "rules": {
            "enable_packs": ["python-best-practices"],
            "disable": ["py/no-print"],
        }
    }
    rules = load_rules_from_config(config_data)
    no_print = [r for r in rules if r.id == "py/no-print"]
    assert len(no_print) == 1
    assert no_print[0].enabled is False


def test_load_rules_severity_override():
    config_data = {
        "rules": {
            "enable_packs": ["python-best-practices"],
            "severity_overrides": {"py/no-print": "info"},
        }
    }
    rules = load_rules_from_config(config_data)
    no_print = [r for r in rules if r.id == "py/no-print"][0]
    assert no_print.severity == Severity.INFO


def test_get_available_packs():
    packs = get_available_packs()
    assert "python-best-practices" in packs
    assert "security-basics" in packs
    assert all(isinstance(v, int) for v in packs.values())


def test_summarize_rules():
    rules = load_rules_from_config({"rules": {"enable_packs": ["python-best-practices"]}})
    summary = summarize_rules(rules)
    assert "rules active" in summary
    assert "python-best-practices" in summary


def test_build_llm_rules_instruction():
    rules = [
        Rule(id="type-check", llm_check="Flag missing type hints", file_pattern="*.py"),
    ]
    changes = [
        FileChange(path="main.py", language="python", patch="def foo(): pass"),
    ]
    instruction = build_llm_rules_instruction(rules, changes)
    assert "type-check" in instruction
    assert "Flag missing type hints" in instruction


def test_build_llm_rules_instruction_empty():
    rules = [
        Rule(id="type-check", llm_check="Flag missing type hints", file_pattern="*.go"),
    ]
    changes = [
        FileChange(path="main.py", language="python", patch="def foo(): pass"),
    ]
    instruction = build_llm_rules_instruction(rules, changes)
    assert instruction == ""  # No match (*.go vs main.py)
