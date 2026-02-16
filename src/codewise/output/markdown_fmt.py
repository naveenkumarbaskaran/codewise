"""Markdown output formatter — for PR comments and CI logs."""

from __future__ import annotations

from codewise.models import (
    DocGenResult,
    ReviewResult,
    SecurityResult,
    Severity,
    TestGenResult,
)

SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
    Severity.INFO: "⚪",
}


class MarkdownFormatter:
    """Format results as Markdown — for PR comments, CI artifacts, docs."""

    def format_review(self, result: ReviewResult, show_rules: str = "") -> None:
        lines: list[str] = []
        lines.append("## 🔍 Codewise Code Review\n")

        score = f" — Score: **{result.score}/100**" if result.score is not None else ""
        lines.append(f"**{result.files_reviewed} files reviewed**{score}\n")

        if show_rules:
            lines.append("<details><summary>Active Rules</summary>\n")
            lines.append(show_rules)
            lines.append("\n</details>\n")

        if not result.findings:
            lines.append("✅ **No issues found.** Code looks good!\n")
        else:
            lines.append(f"Found **{len(result.findings)} issue(s)**:\n")
            lines.append("| | Severity | File | Finding |")
            lines.append("|---|----------|------|---------|")

            for f in sorted(result.findings, key=lambda x: _sev_rank(x.severity), reverse=True):
                emoji = SEVERITY_EMOJI.get(f.severity, "⚪")
                loc = f.file
                if f.line:
                    loc += f":{f.line}"
                desc = f.title.replace("|", "\\|")
                lines.append(f"| {emoji} | {f.severity.value} | `{loc}` | {desc} |")

            # Details for each finding
            lines.append("\n### Details\n")
            for i, f in enumerate(result.findings, 1):
                emoji = SEVERITY_EMOJI.get(f.severity, "⚪")
                loc = f.file
                if f.line:
                    loc += f":{f.line}"
                lines.append(f"#### {emoji} {i}. {f.title}\n")
                lines.append(f"**{f.severity.value}** | `{f.category.value}` | `{loc}`\n")
                lines.append(f"{f.description}\n")
                if f.suggestion:
                    lines.append(f"**Fix:** {f.suggestion}\n")
                if f.code_before:
                    lines.append(f"```\n{f.code_before}\n```\n")
                if f.code_after:
                    lines.append(f"**Suggested:**\n```\n{f.code_after}\n```\n")

        if result.tokens_used:
            lines.append(f"\n---\n*Model: {result.model} | Tokens: {result.tokens_used:,}*\n")

        print("\n".join(lines))

    def format_security(self, result: SecurityResult) -> None:
        lines: list[str] = []
        lines.append("## 🛡️ Codewise Security Scan\n")
        lines.append(f"**{result.files_scanned} files scanned** — Risk: **{result.risk_level.value.upper()}**\n")

        if not result.findings:
            lines.append("✅ **No security issues found.**\n")
        else:
            for f in sorted(result.findings, key=lambda x: _sev_rank(x.severity), reverse=True):
                emoji = SEVERITY_EMOJI.get(f.severity, "⚪")
                loc = f.file
                if f.line:
                    loc += f":{f.line}"
                refs = []
                if f.cwe:
                    refs.append(f.cwe)
                if f.owasp:
                    refs.append(f.owasp)
                ref_str = f" ({', '.join(refs)})" if refs else ""
                lines.append(f"### {emoji} {f.title}{ref_str}\n")
                lines.append(f"**{f.severity.value}** | `{f.category.value}` | `{loc}`\n")
                lines.append(f"{f.description}\n")
                if f.recommendation:
                    lines.append(f"**Recommendation:** {f.recommendation}\n")
                if f.evidence:
                    lines.append(f"```\n{f.evidence}\n```\n")

        print("\n".join(lines))

    def format_testgen(self, result: TestGenResult) -> None:
        lines: list[str] = []
        lines.append("## 🧪 Generated Tests\n")

        for t in result.tests:
            lines.append(f"### `{t.test_name}`\n")
            if t.description:
                lines.append(f"{t.description}\n")
            lines.append(f"Target: `{t.target_function or t.file}` | Framework: {t.framework}\n")
            lines.append(f"```python\n{t.test_code}\n```\n")

        lines.append(f"\n{result.summary}")
        print("\n".join(lines))

    def format_docgen(self, result: DocGenResult) -> None:
        lines: list[str] = []
        lines.append("## 📝 Generated Documentation\n")

        for c in result.changes:
            symbol = c.target_symbol or c.doc_type
            loc = c.file
            if c.line:
                loc += f":{c.line}"
            lines.append(f"### `{symbol}` in `{loc}`\n")
            lines.append(f"```\n{c.generated}\n```\n")

        lines.append(f"\n{result.summary}")
        print("\n".join(lines))

    def format_hook_result(self, result, hook_type: str) -> int:
        findings = result.findings
        if not findings:
            return 0
        lines = [f"**{hook_type}:** {len(findings)} issue(s) found"]
        for f in findings[:5]:
            emoji = SEVERITY_EMOJI.get(f.severity, "⚪")
            lines.append(f"- {emoji} `{f.file}:{f.line or '?'}` — {f.title}")
        print("\n".join(lines))
        return 1 if any(_sev_rank(f.severity) >= 3 for f in findings) else 0


def _sev_rank(severity: Severity) -> int:
    return {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}.get(severity, 0)
