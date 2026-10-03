"""Content-free command classes for verification signals (z0int#54), shared by every harness.

Moved verbatim from ``outcome_verifier`` (which imports them from here): the runner/check/CI/commit/PR
regexes and ``effective_exit``. ``classify`` maps one shell command to a closed class plus the exit status to
trust; the command string itself is never part of the result, so callers can persist it.
"""

from __future__ import annotations

import re
from typing import Any

_TEST_RE = re.compile(r'(^|[\s;&|(/])(pytest|py\.test|tox|nox|(npm|pnpm|yarn|bun)( run)? test|vitest|jest|cargo test|'
                      r'go test|make (test|check)|ctest|mvn test|gradle test|rspec|phpunit|just test|'
                      r'python3? -m (pytest|unittest))(?=$|[\s;&|)])')
# Segments that mention a runner without running it (installing it, asking its version or location).
NOT_A_RUN = re.compile(r'\b(install|uninstall|add|remove|show|freeze|download|which|whereis)\b|--version\b|\s-V\b')
SHELL_SEPARATORS = re.compile(r'&&|\|\||;|\n')


class CommandMatcher:
    """``re``-like ``search`` over shell segments, skipping segments that only install / locate the runner."""

    def __init__(self, rx: re.Pattern[str]):
        self.rx, self.pattern = rx, rx.pattern

    def search(self, command: str) -> re.Match[str] | None:
        for seg in SHELL_SEPARATORS.split(command):
            if NOT_A_RUN.search(seg):
                continue
            m = self.rx.search(seg)
            if m:
                return m
        return None


TEST_CMD = CommandMatcher(_TEST_RE)
CHECK_EXTRA = re.compile(r'(^|[\s;&|(/])(ruff|flake8|pylint|mypy|pyright|basedpyright|tsc|eslint|cargo (check|clippy|build)|'
                         r'go (vet|build)|golangci-lint|shellcheck)(?=$|[\s;&|)])')
CHECK_ANY = CommandMatcher(re.compile(TEST_CMD.pattern + '|' + CHECK_EXTRA.pattern))
CI_CMD = re.compile(r'\bgh (run (watch|view)|pr checks)\b')
COMMIT_CMD = re.compile(r'\bgit(?:\s+-[Cc]\s+\S+)*\s+(commit|revert|cherry-pick)\b(?!-)')
PR_MERGE = re.compile(r'\bgh pr merge\b(?:\s+(\d+))?(?:.*?(?:-R|--repo)[ =]([\w.-]+/[\w.-]+))?')


# A check whose output is piped into a filter reports the filter's exit code, not the runner's (no pipefail).
PIPE_FILTER = re.compile(r'\|\s*(tail|head|grep|egrep|rg|sed|awk|cut|sort|uniq|wc|less|more|cat|tee|tr|jq|column)\b')
PIPE_SAFE = re.compile(r'pipefail|PIPESTATUS')
SUMMARY_FAIL = re.compile(r'\b\d+ (failed|errors?)\b|^FAILED |^FAIL\b|test result: FAILED|^Tests?:.*\bfailed\b|'
                          r'\bFAILED \((failures|errors)=|^ERROR collecting|Interrupted: \d+ errors?|'
                          r'^Found \d+ errors?|error\[E\d+\]|^error: could not compile', re.M)
SUMMARY_PASS = re.compile(r'\b\d+ passed\b|test result: ok\.|^ok\s+\S+|^Tests?:\s+\d+ passed|^OK( \(|$)|'
                          r'All checks passed|^Success: no issues found', re.M)


def effective_exit(command: str, code: int | None, out_tail: str, check_re: Any) -> tuple[int | None, bool]:
    """(exit code to trust, piped). When the check's output is piped into a filter without pipefail, the shell
    exit code belongs to the filter: read the runner's summary line instead (fail beats pass), else unknown."""
    if code is None:
        return None, False
    segs = command.split('|')
    piped = any(check_re.search(seg) and i + 1 < len(segs) and PIPE_FILTER.match('|' + segs[i + 1])
                for i, seg in enumerate(segs))
    if not piped or PIPE_SAFE.search(command):
        return code, False
    if SUMMARY_FAIL.search(out_tail):
        return 1, True
    if SUMMARY_PASS.search(out_tail):
        return 0, True
    return None, True


# Builds that are not checks or tests; ``cargo build`` / ``go build`` stay in CHECK_EXTRA for exit semantics.
BUILD_CMD = CommandMatcher(re.compile(r'(^|[\s;&|(/])(make|cmake --build|ninja|cargo build|go build|'
                                      r'(npm|pnpm|yarn|bun) run build|python3? -m build|mvn (package|install|compile)|'
                                      r'gradle (build|assemble)|docker build)(?=$|[\s;&|)])'))
CLASSES = ('test', 'check', 'ci', 'git-commit', 'git-revert', 'pr-merge', 'build', 'other')


def classify(command: str, exit_code: int | None = None, out_tail: str = '') -> dict[str, Any]:
    """``{check_class, exit, piped}`` for one shell command; the first matching class wins in CLASSES priority
    test > ci > git-revert/git-commit > pr-merge > build > check. Exit is ``effective_exit`` for checks."""
    command = command if isinstance(command, str) else ''
    commit = COMMIT_CMD.search(command)
    if TEST_CMD.search(command):
        klass = 'test'
    elif CI_CMD.search(command):
        klass = 'ci'
    elif commit:
        klass = 'git-revert' if commit.group(1) == 'revert' else 'git-commit'
    elif PR_MERGE.search(command):
        klass = 'pr-merge'
    elif BUILD_CMD.search(command):
        klass = 'build'
    elif CHECK_ANY.search(command):
        klass = 'check'
    else:
        klass = 'other'
    if CHECK_ANY.search(command):
        code, piped = effective_exit(command, exit_code, out_tail or '', CHECK_ANY)
    else:
        code, piped = exit_code, False
    return {'check_class': klass, 'exit': code, 'piped': piped}
