#!/usr/bin/env python3
"""Simulated codewise demo for terminal recording."""
import time, sys, os

os.environ["TERM"] = "xterm-256color"

def c(code, text):
    return f"\033[{code}m{text}\033[0m"

def slow(text, delay=0.012):
    for ch in text:
        sys.stdout.write(ch); sys.stdout.flush(); time.sleep(delay)
    print()

def section(text):
    print(c("1;36", f"\n  {text}"))
    print(c("36", f"  {'─'*56}"))

print(c("1;34", """
   ██████╗ ██████╗ ██████╗ ███████╗██╗    ██╗██╗███████╗███████╗
  ██╔════╝██╔═══██╗██╔══██╗██╔════╝██║    ██║██║██╔════╝██╔════╝
  ██║     ██║   ██║██║  ██║█████╗  ██║ █╗ ██║██║███████╗█████╗
  ██║     ██║   ██║██║  ██║██╔══╝  ██║███╗██║██║╚════██║██╔══╝
  ╚██████╗╚██████╔╝██████╔╝███████╗╚███╔███╔╝██║███████║███████╗
   ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝ ╚══╝╚══╝ ╚═╝╚══════╝╚══════╝"""))

time.sleep(0.2)
print(c("2", "  v0.1.0 • LLM-agnostic code review, security scan, test gen"))
time.sleep(0.4)

# Simulate: codewise review
section("$ codewise review --model gpt-4o")
time.sleep(0.3)

print(c("2", "  Scanning git diff... 3 files changed, 47 insertions(+), 12 deletions(-)"))
time.sleep(0.4)

files = [
    ("src/auth/handler.py",  "+18 −4"),
    ("src/api/routes.py",    "+22 −6"),
    ("tests/test_auth.py",   "+7 −2"),
]
for f, diff in files:
    print(f"  {c('33','▸')} {c('1',f):42s} {c('2',diff)}")
    time.sleep(0.15)

time.sleep(0.3)
print(c("2", "\n  Sending to GPT-4o for review..."))
time.sleep(0.6)

section("Code Review Results")
time.sleep(0.2)

findings = [
    ("🔴 CRITICAL", "31", "src/auth/handler.py:24", "SQL injection via f-string in query builder",
     "Use parameterized queries: cursor.execute(sql, (user_id,))"),
    ("🟡 WARNING",  "33", "src/api/routes.py:15",   "Missing rate limit on /api/token endpoint",
     "Add @rate_limit(max=10, window=60) decorator"),
    ("🟢 STYLE",    "32", "src/api/routes.py:31",   "Unused import: 'json' (line 3)",
     "Remove unused import to keep module clean"),
]

for sev, color, loc, msg, fix in findings:
    print(f"\n  {c(f'1;{color}', sev)}  {c('2',loc)}")
    print(f"    {c('1', msg)}")
    print(f"    {c('36', f'Fix: {fix}')}")
    time.sleep(0.3)

# Security scan
section("$ codewise scan --security")
time.sleep(0.3)

print(c("2", "  Running security analysis..."))
time.sleep(0.5)

vulns = [
    ("HIGH",   "31", "Hardcoded API key in config.py:8",           "CWE-798"),
    ("HIGH",   "31", "eval() on user input in utils.py:42",        "CWE-95"),
    ("MEDIUM", "33", "Debug mode enabled in production settings",   "CWE-489"),
]
print(f"\n  {c('1','Vulnerability'):16s} {c('1','Description'):44s} {c('1','CWE')}")
print(f"  {'─'*16} {'─'*44} {'─'*8}")
for sev, color, desc, cwe in vulns:
    print(f"  {c(f'1;{color}', sev):24s} {desc:44s} {c('2',cwe)}")
    time.sleep(0.15)

time.sleep(0.3)
print(f"\n  {c('1;31','2 HIGH')} | {c('1;33','1 MEDIUM')} | {c('1;32','0 LOW')}")

# Test gen
section("$ codewise generate-tests src/auth/handler.py")
time.sleep(0.3)
print(c("2", "  Analyzing handler.py → generating pytest test cases..."))
time.sleep(0.5)

tests = [
    "test_login_valid_credentials",
    "test_login_invalid_password_returns_401",
    "test_login_sql_injection_blocked",
    "test_token_refresh_expired_token",
    "test_logout_clears_session",
]
print(f"\n  {c('1','Generated')} tests/test_auth_handler.py:")
for t in tests:
    print(f"    {c('32','✓')} {c('37',t)}")
    time.sleep(0.1)

print(f"\n  {c('32','✓')} 5 test cases written • {c('2','92% branch coverage estimated')}")

time.sleep(0.4)
print(c("1;36", f"\n  {'─'*56}"))
print(c("1;32", "  ✓ Review complete • 3 findings • 3 vulns • 5 tests generated"))
print(c("2",    "    Works with: OpenAI, Anthropic, Gemini, Ollama, Bedrock"))
print(c("1;36", f"  {'─'*56}\n"))
time.sleep(1.0)
