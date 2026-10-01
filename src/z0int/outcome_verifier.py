"""Verified outcomes v0 (z0int#54): attach independently verified signals to past Claude Code turns.

The live plugin writes ``z0int.claude_code.turn_outcome.v0`` rows at Stop time. Those rows are
*observed behaviour* (``label_kind: observed_behaviour_not_optimal``): what the agent did, not
whether it worked. This module runs after the fact and appends separate
``z0int.claude_code.turn_outcome_verified.v0`` rows (observed rows are never rewritten) carrying:

  * verification **signals**, each with polarity, confidence, a #54 label class and provenance:
      - in-turn test / CI commands and their exit codes (Bash tool results);
      - commits the turn produced and whether they were later reverted, or rewritten by a later
        fix commit within N days (SZZ blame, ported from feat/promotion-sim-v0 promotion_sim.py),
        plus CI check-runs on the pushed commit (``gh api`` GET);
      - PRs the turn opened and whether they were merged / closed (``gh pr view``, read-only),
        downgraded when the agent merged its own PR (self-label);
      - deterministic correction cues in the *next* user prompt ("undo", "that's wrong", a
        re-ask of the same question, an interrupt) -- cue ids and counts only, never text;
      - whether an ASK (question / AskUserQuestion) was answered or ignored;
  * a ``verification_state``: verified_success / verified_failure / contested / unverified;
  * measurement completeness and an explicit "no counterfactual claimed" credit level.

``credit_join`` produces the credit-ready chain opportunity -> gate decision -> observed -> verified.
Nothing here writes to GitHub or to any repository; git is read-only (log/show/blame/branch).
Semantics: docs/verified-outcomes.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA = 'z0int.claude_code.turn_outcome_verified.v0'
JOIN_SCHEMA = 'z0int.claude_code.credit_join.v0'
OBSERVED_SCHEMA = 'z0int.claude_code.turn_outcome.v0'
VERIFIER = {'id': 'z0int.outcome_verifier', 'version': '0.2.0'}  # 0.2.0: + verification_density signals
HARNESS = 'claude-code'
STATES = ('verified_success', 'verified_failure', 'contested', 'unverified')
# z0int#54 provenance classes. Confidence is orthogonal and decides the state.
LABEL_CLASSES = ('deterministic_gold', 'negative_gold', 'execution_only', 'soft', 'unknown')
CONF_RANK = {'low': 0, 'medium': 1, 'high': 2}

HARNESS_PREFIXES = ('<agent-message', '<task-notification', '<system-reminder', '<command-', '<local-command',
                    '<bash-', 'Caveat: ')
INTERRUPT = '[Request interrupted by user'

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
COMMIT_OUT = re.compile(r'^\[(?P<branch>[^\]\s]+)(?: \(root-commit\))? (?P<sha>[0-9a-f]{7,40})\]', re.M)
PR_URL = re.compile(r'https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)')
PR_MERGE = re.compile(r'\bgh pr merge\b(?:\s+(\d+))?(?:.*?(?:-R|--repo)[ =]([\w.-]+/[\w.-]+))?')
TEST_PATH = re.compile(r'(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$')
CD_ARG = re.compile(r'(?:^|[;&|(]\s*|\s)cd\s+([^\s;&|)]+)')
GIT_C = re.compile(r'\bgit\s+-C\s+([^\s;&|)]+)')
EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
ASK_TOOL = 'AskUserQuestion'

# Ported from feat/promotion-sim-v0 src/z0int/promotion_sim.py (FIX_RE, DOC_RE, NON_CODE_WORKFLOW).
FIX_RE = re.compile(r'(^|\W)(fix(es|ed)?|hotfix|bug|regress\w*|broken|repair|unbreak)(\W|$)', re.I)
DOC_RE = re.compile(r'(\.md|\.txt|\.rst|LICENSE|\.lock|lock\.json|CHANGELOG[^/]*)$', re.I)
NON_CODE_WORKFLOW = re.compile(r'receipt|label|auto-?merge|promote|stale|greet|dependabot|sync|board|triage|pages', re.I)

# Deterministic correction cues on the NEXT user prompt (first 300 chars, lower-cased). Only cue ids leave.
STRONG_CUES = {
    'undo': re.compile(r'\bundo\b'),
    'revert_that': re.compile(r'\brevert (that|this|it|those|these|the change|your)\b'),
    'thats_wrong': re.compile(r"\b(that'?s|that is|this is|it'?s) (wrong|incorrect|not right|broken)\b"),
    'not_what_i': re.compile(r'\bnot what i (asked|wanted|meant|said)\b'),
    'you_broke': re.compile(r'\byou (broke|deleted|removed|messed)\b'),
    'didnt_work': re.compile(r"\b(doesn'?t|didn'?t|does not|did not) work\b"),
    'still_broken': re.compile(r'\bstill (broken|failing|fails|wrong|not working|erroring)\b'),
}
WEAK_CUES = {
    'leading_no': re.compile(r'^\s*(no|nope|nah)\b'),
    'wrong': re.compile(r'\bwrong\b'),
    'stop': re.compile(r'^\s*stop\b'),
}
WORD = re.compile(r'[a-z0-9_]+')
RE_ASK_JACCARD = 0.6


# ----------------------------------------------------------------------------- small helpers
def _sha(obj: Any, n: int = 16) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:n]


def _ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and isinstance(b.get('text'), str))
    return ''


def parse_since(spec: str | None, now: float | None = None) -> float:
    """'7d' / '36h' / '90m' / ISO date / epoch seconds -> epoch seconds. None -> 0 (everything)."""
    now = time.time() if now is None else now
    if not spec:
        return 0.0
    m = re.fullmatch(r'(\d+(?:\.\d+)?)([dhm])', spec.strip())
    if m:
        return now - float(m.group(1)) * {'d': 86400, 'h': 3600, 'm': 60}[m.group(2)]
    if re.fullmatch(r'\d+(\.\d+)?', spec.strip()):
        return float(spec)
    t = _ts(spec if 'T' in spec else spec + 'T00:00:00+00:00')
    if t is None:
        raise ValueError(f'cannot parse --since {spec!r}')
    return t


def state_dir(root: Path | None = None) -> Path:
    from . import paths
    return paths.ensure_layout(root)['state'] / HARNESS


def projects_dir() -> Path:
    return Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude')).expanduser() / 'projects'


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        with path.open(encoding='utf-8') as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


# ----------------------------------------------------------------------------- transcript -> turns
def is_real_prompt(row: Mapping[str, Any], current_prompt_id: str | None = None) -> bool:
    """A user row that starts a turn: not a tool result or compaction summary. A meta row (peer/agent
    message) starts a turn only when it carries a new promptId -- the Stop hook writes observed rows for those."""
    if row.get('type') != 'user' or row.get('isCompactSummary'):
        return False
    if row.get('isMeta') and (not row.get('promptId') or row.get('promptId') == current_prompt_id):
        return False
    content = (row.get('message') or {}).get('content')
    if isinstance(content, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
        return False
    return bool(_text(content).strip())


def _exit_code(block: Mapping[str, Any], tur: Any) -> int | None:
    """0 / N from a Bash tool_result; None when the outcome is unknown (backgrounded, interrupted)."""
    text = _text(block.get('content'))
    if isinstance(tur, dict):
        if tur.get('interrupted') or tur.get('backgroundTaskId') or tur.get('isAsync'):
            return None
    if block.get('is_error'):
        m = re.match(r'\s*Exit code (\d+)', text)
        return int(m.group(1)) if m else None
    if 'running in background' in text[:200].lower():
        return None
    return 0


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


def turns_from_transcript(path: Path) -> list[dict[str, Any]]:
    """One dict per turn. ``_prompt`` holds the prompt text transiently for cue matching; callers must not emit it."""
    turns: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    calls: dict[str, dict[str, Any]] = {}
    pr_links: list[dict[str, Any]] = []
    try:
        fh = path.open(encoding='utf-8')
    except OSError:
        return turns
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            kind = row.get('type')
            ts = _ts(row.get('timestamp'))
            if kind == 'pr-link':
                pr_links.append({'repo': row.get('prRepository'), 'number': row.get('prNumber'), 'ts': ts})
                continue
            if kind == 'user' and is_real_prompt(row, cur['prompt_id'] if cur else None):
                text = _text((row.get('message') or {}).get('content'))
                stripped = text.lstrip()
                if stripped.startswith(INTERRUPT):
                    if cur is not None:
                        cur['interrupted'] = True
                    continue
                cur = {'prompt_id': row.get('promptId'), 'session_id': row.get('sessionId'), 'started_at': ts,
                       'ended_at': ts, 'cwd': row.get('cwd'),
                       'harness_message': bool(row.get('isMeta')) or stripped.startswith(HARNESS_PREFIXES),
                       '_prompt': text, 'bash': [], 'edits': [], 'assistant_ids': set(), 'tool_calls': 0,
                       'asked_text': None, 'ask_tool': [], 'agents_spawned': 0, 'pr_created': [],
                       'pr_merged_by_agent': [], 'interrupted': False, 'subagent_ids': [],
                       # density verifiers (verification_density.py); '_'-prefixed fields hold text transiently
                       'tool_names': Counter(), 'edit_ops': [], 'reads': set(), 'tool_seq': [], '_final_text': ''}
                turns.append(cur)
                continue
            if cur is None:
                continue
            if ts is not None:
                cur['ended_at'] = max(cur['ended_at'] or ts, ts)
            if kind == 'assistant':
                msg = row.get('message') or {}
                if not isinstance(msg, dict) or msg.get('model') == '<synthetic>':
                    continue
                if msg.get('id'):
                    cur['assistant_ids'].add(msg['id'])
                blocks = [b for b in msg.get('content') or [] if isinstance(b, dict)]
                texts = [b.get('text', '') for b in blocks if b.get('type') == 'text' and b.get('text', '').strip()]
                if texts:
                    cur['asked_text'] = texts[-1].rstrip().endswith('?')
                    cur['_final_text'] = texts[-1]
                for b in blocks:
                    if b.get('type') != 'tool_use':
                        continue
                    cur['tool_calls'] += 1
                    name, inp = str(b.get('name') or ''), b.get('input') if isinstance(b.get('input'), dict) else {}
                    cur['tool_names'][name] += 1
                    call = {'name': name, 'turn': cur, 'cwd': row.get('cwd') or cur['cwd'], 't0': ts}
                    cur['tool_seq'].append(call)
                    if name == 'Bash' and isinstance(inp.get('command'), str):
                        call['command'] = inp['command']
                        cur['bash'].append(call)
                        for m in PR_MERGE.finditer(inp['command']):
                            cur['pr_merged_by_agent'].append({'number': int(m.group(1)) if m.group(1) else None,
                                                              'repo': m.group(2)})
                    elif name in EDIT_TOOLS:
                        p = inp.get('file_path') or inp.get('notebook_path')
                        if isinstance(p, str):
                            cur['edits'].append(p)
                            call['path'] = p
                            call['_new'] = inp.get('new_string') if name != 'Write' else inp.get('content')
                            call['_old'] = inp.get('old_string')
                            for e in inp.get('edits') or [] if name == 'MultiEdit' else []:
                                if isinstance(e, dict):
                                    call['_new'] = (call['_new'] or '') + '\n' + str(e.get('new_string') or '')
                                    call['_old'] = (call['_old'] or '') + '\n' + str(e.get('old_string') or '')
                            cur['edit_ops'].append(call)
                    elif name == 'Read' and isinstance(inp.get('file_path'), str):
                        call['path'] = inp['file_path']
                    elif name == ASK_TOOL:
                        cur['ask_tool'].append(call)
                    elif name in ('Agent', 'Task'):
                        cur['agents_spawned'] += 1
                    if b.get('id'):
                        calls[b['id']] = call
                continue
            if kind == 'user':
                content = (row.get('message') or {}).get('content')
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get('type') != 'tool_result':
                        continue
                    call = calls.pop(block.get('tool_use_id'), None)
                    if not call:
                        continue
                    text = _text(block.get('content'))
                    call['t1'] = ts
                    call['error'] = bool(block.get('is_error'))
                    tur = row.get('toolUseResult')
                    if call['name'] in EDIT_TOOLS and isinstance(tur, dict):
                        call['created'] = tur.get('type') == 'create' if call['name'] == 'Write' else False
                    elif call['name'] == 'Read' and not call['error'] and call.get('path'):
                        call['turn']['reads'].add(call['path'])
                    if call['name'] == 'Bash':
                        call['_out_tail'] = text[-600:]
                        call['exit'] = _exit_code(block, row.get('toolUseResult'))
                        cmd = call.get('command', '')
                        if CHECK_ANY.search(cmd):
                            call['shell_exit'] = call['exit']
                            call['exit'], call['piped'] = effective_exit(cmd, call['exit'], text[-2000:], CHECK_ANY)
                        if COMMIT_CMD.search(cmd):
                            call['commits'] = [m.group('sha') for m in COMMIT_OUT.finditer(text)]
                        if 'gh pr create' in cmd:
                            call['turn']['pr_created'] += [{'repo': m.group(1), 'number': int(m.group(2))}
                                                           for m in PR_URL.finditer(text)]
                    elif call['name'] == ASK_TOOL:
                        call['answered'] = not block.get('is_error')
                    elif call['name'] in ('Agent', 'Task'):
                        tur = row.get('toolUseResult')
                        if isinstance(tur, dict) and isinstance(tur.get('agentId'), str):
                            call['turn']['subagent_ids'].append(tur['agentId'])
    for link in pr_links:  # pr-link rows: attach to the turn whose span contains them
        if link['ts'] is None or not link['number']:
            continue
        owner = None
        for t in turns:
            if (t['started_at'] or 0) <= link['ts']:
                owner = t
        if owner is not None:
            owner['pr_created'].append({'repo': link['repo'], 'number': int(link['number'])})
    for t in turns:
        t['assistant_messages'] = len(t.pop('assistant_ids'))
        seen, prs = set(), []
        for p in t['pr_created']:
            key = (p['repo'], p['number'])
            if key not in seen:
                seen.add(key)
                prs.append(p)
        t['pr_created'] = prs
        for c in t['tool_seq']:
            c.pop('turn', None)
    return turns


def session_turns(path: Path) -> list[dict[str, Any]]:
    """Root turns with the work of subagents they spawned folded in (bash, edits, PRs, merges).

    A subagent's activity is attributed to the root turn that spawned it, even when the agent ran
    in the background past that turn's end. Subagents whose transcript is missing stay counted
    under ``subagents_not_inspected``.
    """
    turns = turns_from_transcript(path)
    subdir = path.with_suffix('') / 'subagents'
    for t in turns:
        t['subagents_inspected'] = 0
        for aid in t['subagent_ids']:
            sub = subdir / f'agent-{aid}.jsonl'
            if not sub.is_file():
                continue
            t['subagents_inspected'] += 1
            for st in turns_from_transcript(sub):
                for c in st['bash']:
                    c['via'] = 'subagent'
                t['bash'] += st['bash']
                t['edits'] += st['edits']
                t['edit_ops'] += [dict(e, via='subagent') for e in st['edit_ops']]
                t['reads'] |= st['reads']
                t['pr_created'] += [p for p in st['pr_created'] if p not in t['pr_created']]
                t['pr_merged_by_agent'] += st['pr_merged_by_agent']
                t['tool_calls_subagents'] = t.get('tool_calls_subagents', 0) + st['tool_calls']
    return turns


def find_transcript(session_id: str, base: Path | None = None) -> Path | None:
    base = base or projects_dir()
    hits = sorted(base.glob(f'*/{session_id}.jsonl'))
    return hits[0] if hits else None


# ----------------------------------------------------------------------------- git (read-only)
def git(repo: Path | str, *args: str) -> str:
    try:
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=120,
                              env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


@dataclass(frozen=True)
class Landed:
    """A commit that landed on some branch (ported from promotion_sim.Landed)."""
    sha: str
    t: float
    subject: str
    body: str
    files: frozenset[str]


def landed_commits(repo: Path, since: float) -> list[Landed]:
    """Every non-merge commit reachable from local or remote branches committed after ``since``."""
    out = git(repo, 'log', '--no-merges', '--branches', '--remotes', f'--since={int(since) - 1}',
              '--format=%x00%H%x1f%ct%x1f%s%x1f%b%x1e', '--name-only')
    res: dict[str, Landed] = {}
    for chunk in out.split('\x00')[1:]:
        head, _, names = chunk.partition('\x1e')
        sha, at, subj, body = (head.split('\x1f') + ['', '', '', ''])[:4]
        if sha and at:
            res[sha] = Landed(sha, float(at), subj, body, frozenset(n for n in names.split('\n') if n.strip()))
    return list(res.values())


def szz_origins(repo: Path, fix: Landed) -> set[str]:
    """SZZ (ported from promotion_sim.szz_origins): commits that last touched the lines a fix deletes/modifies."""
    parent = git(repo, 'rev-parse', f'{fix.sha}^1').strip()
    files = [f for f in fix.files if not DOC_RE.search(f)]
    if not parent or not files:
        return set()
    diff = git(repo, 'diff', '-U0', '--no-color', '--no-renames', parent, fix.sha, '--', *files)
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    cur = None
    for line in diff.splitlines():
        if line.startswith('--- '):
            cur = line[6:] if line.startswith('--- a/') else None
        elif line.startswith('@@') and cur:
            m = re.search(r'-(\d+)(?:,(\d+))?', line)
            if m and int(m.group(2) or 1) > 0:
                spans[cur].append((int(m.group(1)), int(m.group(1)) + int(m.group(2) or 1) - 1))
    out: set[str] = set()
    for f, rs in spans.items():
        args = [x for a, b in rs[:40] for x in ('-L', f'{a},{b}')]
        blame = git(repo, 'blame', '-w', '--porcelain', *args, parent, '--', f)
        out |= {ln.split()[0] for ln in blame.splitlines() if re.match(r'^[0-9a-f]{40} \d+ \d+', ln)}
    return out


def reverted_by(own: set[str], subject: str, landed: Iterable[Landed], t: float) -> str | None:
    """Ported from promotion_sim.reverted_by: a later ``Revert`` naming one of our SHAs or our subject."""
    keys = [s[:7] for s in own]
    for c in landed:
        if c.t <= t or c.sha in own or not c.subject.lower().startswith('revert'):
            continue
        blob = c.subject + '\n' + c.body
        if (subject and len(subject) > 8 and subject in blob) or any(k in blob for k in keys):
            return c.sha
    return None


class RepoIndex:
    """Resolves commit SHAs to repositories and caches per-repo history. Read-only."""

    def __init__(self, extra_roots: Iterable[Path] = ()):  # extra roots: fallback search (e.g. ~/workspace/*)
        self.extra_roots = list(extra_roots)
        self._top: dict[str, Path | None] = {}
        self._landed: dict[tuple[str, int], list[Landed]] = {}
        self._szz: dict[tuple[str, str], set[str]] = {}
        self.ls_cache: dict[str, list[str]] = {}
        self.outcomes: dict[tuple, dict[str, Any]] = {}  # commit_outcome memo (planning pass + real pass)
        self.sessions: dict[str, list[dict[str, Any]]] = {}  # session_turns memo

    def toplevel(self, path: str | Path | None) -> Path | None:
        if not path:
            return None
        key = str(path)
        if key not in self._top:
            p = Path(key).expanduser()
            top = git(p, 'rev-parse', '--show-toplevel').strip() if p.is_dir() else ''
            self._top[key] = Path(top) if top else None
        return self._top[key]

    def resolve(self, sha: str, candidates: Iterable[str | Path | None]) -> tuple[Path, str] | None:
        seen = []
        for c in list(candidates) + self.extra_roots:
            top = self.toplevel(c)
            if top is None or top in seen:
                continue
            seen.append(top)
            full = git(top, 'rev-parse', '--verify', '--quiet', f'{sha}^{{commit}}').strip()
            if full:
                return top, full
        return None

    def committed_between(self, candidates: Iterable[str | Path | None], t0: float, t1: float) -> tuple[Path, list[str]] | None:
        """Commits (any branch) whose committer time falls inside one tool call's window: for ``git commit -q``."""
        for c in candidates:
            top = self.toplevel(c)
            if top is None:
                continue
            out = git(top, 'log', '--branches', '--remotes', f'--since={int(t0) - 2}', f'--until={int(t1) + 2}',
                      '--format=%H %ct')
            shas = [ln.split()[0] for ln in out.splitlines()
                    if ln.strip() and t0 - 2 <= float(ln.split()[1]) <= t1 + 2]
            return (top, shas) if shas else None
        return None

    def landed(self, repo: Path, since: float) -> list[Landed]:
        key = (str(repo), int(since // 86400))
        if key not in self._landed:
            self._landed[key] = landed_commits(repo, key[1] * 86400)
        return self._landed[key]

    def origins(self, repo: Path, fix: Landed) -> set[str]:
        key = (str(repo), fix.sha)
        if key not in self._szz:
            self._szz[key] = szz_origins(repo, fix)
        return self._szz[key]


def command_paths(command: str, cwd: str | None) -> list[str]:
    out = []
    for m in list(GIT_C.finditer(command)) + list(CD_ARG.finditer(command)):
        p = m.group(1).strip('\'"')
        p = os.path.expanduser(p)
        if not os.path.isabs(p) and cwd:
            p = os.path.join(cwd, p)
        out.append(p)
    return out


def commit_outcome(index: RepoIndex, sha: str, candidates: list[str | None], *, fix_days: int, now: float,
                   own_shas: set[str]) -> dict[str, Any]:
    """Revert / SZZ-fix / survival facts for one commit the turn produced. Counts and SHAs only."""
    key = (sha, tuple(str(c) for c in candidates), fix_days, now, tuple(sorted(own_shas)))
    if key not in index.outcomes:
        index.outcomes[key] = _commit_outcome(index, sha, candidates, fix_days=fix_days, now=now, own_shas=own_shas)
    return index.outcomes[key]


def _commit_outcome(index: RepoIndex, sha: str, candidates: list[str | None], *, fix_days: int, now: float,
                    own_shas: set[str]) -> dict[str, Any]:
    hit = index.resolve(sha, candidates)
    if hit is None:
        return {'resolved': False}
    repo, full = hit
    meta = git(repo, 'show', '-s', '--format=%ct%x1f%s', full).strip().split('\x1f')
    t = float(meta[0]) if meta and meta[0] else now
    subject = meta[1] if len(meta) > 1 else ''
    files = {f for f in git(repo, 'show', '--format=', '--name-only', full).splitlines() if f.strip()}
    code = {f for f in files if not DOC_RE.search(f)}
    landed = index.landed(repo, t)
    own_full = {s for s in own_shas if len(s) == 40} | {full}
    rv = reverted_by(own_full, subject, landed, t)
    fixers = []
    horizon = t + fix_days * 86400
    for c in landed:
        if c.sha in own_full or not (t < c.t <= horizon) or not FIX_RE.search(c.subject) or not (c.files & code):
            continue
        if full in index.origins(repo, c):
            fixers.append(c.sha)
    on_remote = bool(git(repo, 'branch', '-r', '--contains', full).strip())
    return {'resolved': True, 'repo': repo, 'sha': full, 't': t, 'files': len(files), 'code_files': len(code),
            'reverted_by': rv, 'szz_fixed_by': sorted(fixers), 'window_elapsed': now >= horizon,
            'on_remote': on_remote}


# ----------------------------------------------------------------------------- GitHub (read-only)
def _gh_json(args: list[str]) -> Any:
    try:
        r = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _gh_final(args: tuple, out: Any) -> bool:
    """A lookup whose answer can no longer change: a merged/closed PR, or check-runs that all completed."""
    if args[:2] == ('pr', 'view'):
        return isinstance(out, dict) and out.get('state') in ('MERGED', 'CLOSED')
    if args[:1] == ('api',) and isinstance(out, dict):
        runs = out.get('check_runs')
        return bool(runs) and all(r.get('status') == 'completed' for r in runs)
    return False


def default_gh_cache() -> Path:
    base = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    return Path(base) / 'z0int' / 'gh-lookups.json'


class GitHub:
    """Only GET-shaped ``gh`` calls: ``gh pr view`` and ``gh api`` without a method/body flag.

    ``cache_path``: an on-disk cache of lookups (default off; the CLI uses ``~/.cache/z0int/gh-lookups.json``).
    Final answers (merged/closed PRs, all-completed check-runs) are reused forever; anything else for
    ``ttl`` seconds. Failures are never cached on disk. ``prefetch`` resolves many keys concurrently.
    """

    def __init__(self, enabled: bool = True, runner: Callable[[list[str]], Any] = _gh_json,
                 cache_path: Path | None = None, ttl: float = 3600.0, workers: int = 8):
        self.enabled = enabled
        self.run = runner
        self.calls = 0
        self.failures = 0
        self.disk_hits = 0
        self._cache: dict[tuple, Any] = {}
        self.cache_path = cache_path
        self.ttl = ttl
        self.workers = workers
        self.recording: list[list[str]] | None = None
        self._disk: dict[str, Any] = {}
        self._dirty = False
        if cache_path is not None:
            try:
                self._disk = json.loads(Path(cache_path).read_text())
            except (OSError, ValueError):
                self._disk = {}

    def _from_disk(self, key: tuple) -> tuple[bool, Any]:
        ent = self._disk.get(json.dumps(key))
        if isinstance(ent, dict) and (ent.get('final') or time.time() - float(ent.get('t', 0)) < self.ttl):
            return True, ent.get('out')
        return False, None

    def _store(self, key: tuple, out: Any) -> None:
        self._cache[key] = out
        if out is None:
            self.failures += 1
        elif self.cache_path is not None:
            self._disk[json.dumps(key)] = {'t': time.time(), 'final': _gh_final(key, out), 'out': out}
            self._dirty = True

    def _get(self, args: list[str]) -> Any:
        key = tuple(args)
        if key in self._cache:
            return self._cache[key]
        hit, out = self._from_disk(key)
        if hit:
            self.disk_hits += 1
            self._cache[key] = out
            return out
        if self.recording is not None:  # planning pass: note the key, answer nothing
            self.recording.append(list(args))
            return None
        self.calls += 1
        self._store(key, self.run(args))
        return self._cache[key]

    def prefetch(self, keys: Iterable[list[str]]) -> int:
        from concurrent.futures import ThreadPoolExecutor
        todo = []
        for k in keys:
            t = tuple(k)
            if t not in self._cache and t not in {tuple(x) for x in todo} and not self._from_disk(t)[0]:
                todo.append(k)
        if not todo:
            return 0
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            outs = list(ex.map(self.run, todo))
        for k, out in zip(todo, outs):
            self.calls += 1
            self._store(tuple(k), out)
        return len(todo)

    def flush(self) -> None:
        if self.cache_path is None or not self._dirty:
            return
        path = Path(self.cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._disk, sort_keys=True))
        os.replace(tmp, path)
        self._dirty = False

    def pr(self, repo: str, number: int) -> dict[str, Any] | None:
        if not self.enabled or not repo:
            return None
        return self._get(['pr', 'view', str(number), '-R', repo, '--json', 'state,mergedAt,closedAt'])

    def check_runs(self, repo: str, sha: str) -> list[dict[str, Any]] | None:
        if not self.enabled or not repo:
            return None
        out = self._get(['api', f'repos/{repo}/commits/{sha}/check-runs?per_page=100'])
        return out.get('check_runs') if isinstance(out, dict) else None


def github_slug(repo: Path) -> str | None:
    url = git(repo, 'remote', 'get-url', 'origin').strip()
    m = re.search(r'github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$', url)
    return m.group(1) if m else None


def ci_verdict(runs: list[dict[str, Any]] | None) -> str | None:
    if not runs:
        return None
    code = [r for r in runs if not NON_CODE_WORKFLOW.search(r.get('name') or '')]
    if not code:
        return None
    if any(r.get('conclusion') in ('failure', 'timed_out', 'startup_failure') for r in code):
        return 'failure'
    if any(r.get('status') != 'completed' for r in code):
        return 'pending'
    if all(r.get('conclusion') in ('success', 'skipped', 'neutral') for r in code):
        return 'success'
    return None


# ----------------------------------------------------------------------------- signals -> state
def signal(kind: str, polarity: int, confidence: str, label_class: str, oracle: str, **details: Any) -> dict[str, Any]:
    assert label_class in LABEL_CLASSES and confidence in CONF_RANK and polarity in (-1, 0, 1)
    return {'kind': kind, 'polarity': polarity, 'confidence': confidence, 'label_class': label_class,
            'oracle': oracle, **details}


def correction_cues(next_prompt: str | None, this_prompt: str | None) -> tuple[list[str], list[str], bool]:
    """(strong cue ids, weak cue ids, re_ask) for the next user prompt. Text never leaves this function."""
    if not next_prompt:
        return [], [], False
    head = next_prompt[:300].lower()
    strong = [k for k, rx in STRONG_CUES.items() if rx.search(head)]
    weak = [k for k, rx in WEAK_CUES.items() if rx.search(head)]
    re_ask = False
    if this_prompt:
        a, b = set(WORD.findall(this_prompt.lower())), set(WORD.findall(next_prompt.lower()))
        if len(a) >= 4 and len(b) >= 4:
            re_ask = len(a & b) / len(a | b) >= RE_ASK_JACCARD
    return strong, weak, re_ask


def decide(signals: list[dict[str, Any]]) -> tuple[str, str, str | None]:
    """(verification_state, label_class, confidence). Only medium/high signals decide; low ones annotate."""
    decisive = [s for s in signals if s['polarity'] and CONF_RANK[s['confidence']] >= CONF_RANK['medium']]
    pos = [s for s in decisive if s['polarity'] > 0]
    neg = [s for s in decisive if s['polarity'] < 0]
    if pos and neg:
        return 'contested', 'unknown', None
    best = max(pos or neg, key=lambda s: CONF_RANK[s['confidence']], default=None)
    if best is not None:
        return ('verified_success' if pos else 'verified_failure'), best['label_class'], best['confidence']
    if any(s['label_class'] == 'execution_only' for s in signals):
        return 'unverified', 'execution_only', None
    if any(s['polarity'] for s in signals):
        return 'unverified', 'soft', 'low'
    return 'unverified', 'unknown', None


def verify_turn(turn: Mapping[str, Any], next_prompt: str | None, *, index: RepoIndex, gh: GitHub, fix_days: int,
                now: float, session_merges: list[dict[str, Any]], session_end: float | None) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    measurement: dict[str, Any] = {'transcript': 'found', 'subagents_spawned': turn['agents_spawned'],
                                   'subagents_inspected': turn.get('subagents_inspected', 0),
                                   'subagents_not_inspected': max(0, turn['agents_spawned'] - turn.get('subagents_inspected', 0)),
                                   'gh': 'enabled' if gh.enabled else 'disabled'}
    # 1) tests / CI executed inside the turn (last run decides; the agent's own test edits downgrade it)
    tests = [c for c in turn['bash'] if TEST_CMD.search(c.get('command', ''))]
    known = [c for c in tests if c.get('exit') is not None]
    tests_edited = any(TEST_PATH.search(p) for p in turn['edits'])
    if known:
        last = known[-1]['exit']
        details = dict(runs=len(tests), runs_with_exit=len(known), failed_runs=sum(1 for c in known if c['exit']),
                       last_exit=last, tests_edited_in_turn=tests_edited)
        if last == 0:
            signals.append(signal('tests_in_turn', 1, 'low' if tests_edited else 'medium',
                                  'soft' if tests_edited else 'deterministic_gold', 'test_runner', **details))
        else:
            signals.append(signal('tests_in_turn', -1, 'medium', 'negative_gold', 'test_runner', **details))
    elif tests:
        signals.append(signal('tests_in_turn', 0, 'low', 'execution_only', 'test_runner', runs=len(tests),
                              runs_with_exit=0))
    ci = [c for c in turn['bash'] if CI_CMD.search(c.get('command', '')) and c.get('exit') is not None]
    if ci:
        last = ci[-1]['exit']
        if last == 0:
            signals.append(signal('ci_watch_in_turn', 1, 'medium', 'deterministic_gold', 'ci', runs=len(ci), last_exit=0))
        elif last != 8:  # gh pr checks exits 8 while checks are pending
            signals.append(signal('ci_watch_in_turn', -1, 'medium', 'negative_gold', 'ci', runs=len(ci), last_exit=last))
    # 2) commits produced by the turn: revert / SZZ fix / survival / CI on the pushed commit
    commit_calls = [c for c in turn['bash'] if c.get('exit') == 0 and COMMIT_CMD.search(c.get('command', ''))]
    for c in commit_calls:
        if not c.get('commits') and c.get('t0') and c.get('t1'):
            hit = index.committed_between(command_paths(c['command'], c.get('cwd')) + [c.get('cwd')], c['t0'], c['t1'])
            if hit:
                c['commits'] = hit[1]
                c['commits_by_window'] = True  # idempotent across the gh planning pass and the real pass
    by_window = sum(len(c['commits']) for c in commit_calls if c.get('commits_by_window'))
    commit_calls = [c for c in commit_calls if c.get('commits')]
    shas = [s for c in commit_calls for s in c['commits']]
    resolved = 0
    for c in commit_calls:
        cands = command_paths(c['command'], c.get('cwd')) + [c.get('cwd'), turn.get('cwd')]
        for sha in c['commits']:
            co = commit_outcome(index, sha, cands, fix_days=fix_days, now=now, own_shas=set(shas))
            if not co['resolved']:
                signals.append(signal('commit', 0, 'low', 'unknown', 'vcs_history', resolved=False))
                continue
            resolved += 1
            ref = {'sha': co['sha'], 'repo_id': _sha(str(co['repo']), 10), 'files': co['files'],
                   'on_remote': co['on_remote']}
            if co['reverted_by']:
                signals.append(signal('commit_reverted', -1, 'high', 'negative_gold', 'vcs_history', **ref,
                                      by=co['reverted_by']))
            if co['szz_fixed_by']:
                signals.append(signal('commit_szz_fixed', -1, 'high', 'negative_gold', 'vcs_history', **ref,
                                      by=co['szz_fixed_by'], fix_days=fix_days))
            if not co['reverted_by'] and not co['szz_fixed_by']:
                if co['window_elapsed']:
                    signals.append(signal('commit_survived', 1, 'low', 'soft', 'vcs_history', **ref, fix_days=fix_days))
                else:
                    signals.append(signal('commit_created', 0, 'low', 'execution_only', 'vcs_history', **ref,
                                          survival_window='pending', fix_days=fix_days))
            slug = github_slug(co['repo']) if co['on_remote'] else None
            verdict = ci_verdict(gh.check_runs(slug, co['sha'])) if slug else None
            if verdict == 'success':
                signals.append(signal('ci_on_commit', 1, 'medium', 'deterministic_gold', 'ci', sha=co['sha']))
            elif verdict == 'failure':
                signals.append(signal('ci_on_commit', -1, 'medium', 'negative_gold', 'ci', sha=co['sha']))
    measurement['commits'] = {'produced': len(shas), 'resolved': resolved, 'matched_by_time_window': by_window}
    # 3) PRs opened by the turn: merged / closed; merged by the agent itself is a self-label
    for p in turn['pr_created']:
        info = gh.pr(p['repo'], p['number'])
        ref = {'pr': f"{p['repo']}#{p['number']}"}
        if not info:
            signals.append(signal('pr', 0, 'low', 'unknown', 'pr_review', **ref, state='unavailable'))
            continue
        self_merged = any(m['number'] in (p['number'], None) and m['repo'] in (p['repo'], None) for m in session_merges)
        if info.get('state') == 'MERGED':
            signals.append(signal('pr_merged', 1, 'low' if self_merged else 'medium',
                                  'execution_only' if self_merged else 'deterministic_gold', 'pr_review', **ref,
                                  merged_by_agent=self_merged))
        elif info.get('state') == 'CLOSED':
            signals.append(signal('pr_closed_unmerged', -1, 'medium', 'negative_gold', 'pr_review', **ref))
        else:
            signals.append(signal('pr_open', 0, 'low', 'execution_only', 'pr_review', **ref))
    # 4) the next user turn: deterministic correction cues (ids only), re-ask, interrupt
    strong, weak, re_ask = correction_cues(next_prompt, turn.get('_prompt'))
    if strong:
        signals.append(signal('user_correction', -1, 'medium', 'soft', 'user_cue', cues=strong, cue_count=len(strong)))
    if weak or re_ask:
        signals.append(signal('user_weak_correction', -1, 'low', 'soft', 'user_cue', cues=weak, re_ask=re_ask))
    if turn.get('interrupted'):
        signals.append(signal('user_interrupt', -1, 'low', 'soft', 'user_cue'))
    # 5) ASK answered vs ignored (outcome of an ASK action, not of task success)
    asked_tool = bool(turn['ask_tool'])
    asked = asked_tool or bool(turn.get('asked_text'))
    if asked:
        if asked_tool:
            answered = any(c.get('answered') for c in turn['ask_tool'])
        else:
            answered = next_prompt is not None
        ignored = not answered and (session_end is None or now - session_end > 3600)
        signals.append(signal('ask_answered' if answered else ('ask_ignored' if ignored else 'ask_pending'), 0, 'low',
                              'soft', 'user_cue', via_tool=asked_tool))
    state, label_class, confidence = decide(signals)
    measurement['next_user_turn'] = next_prompt is not None
    return {'signals': signals, 'verification_state': state, 'label_class': label_class,
            'label_confidence': confidence, 'measurement': measurement, 'asked': asked}


# ----------------------------------------------------------------------------- driver
def _rows_for_session(turns: list[dict[str, Any]], session_id: str, *, index: RepoIndex, gh: GitHub, fix_days: int,
                      now: float, density: bool = True) -> dict[str, dict[str, Any]]:
    """prompt_id -> verification core for every user-initiated turn in one session transcript."""
    merges = [m for t in turns for m in t['pr_merged_by_agent']]
    session_end = max((t['ended_at'] or 0 for t in turns), default=None)
    real = [i for i, t in enumerate(turns) if not t['harness_message']]
    out: dict[str, dict[str, Any]] = {}
    for i, t in enumerate(turns):
        if not t.get('prompt_id'):
            continue
        nxt = next((turns[j]['_prompt'] for j in real if j > i), None)
        core = verify_turn(t, nxt, index=index, gh=gh, fix_days=fix_days, now=now, session_merges=merges,
                           session_end=session_end)
        if density:
            from . import verification_density as vd
            extra = vd.density_signals(turns, i, next_prompt=nxt, asked=core['asked'], existing=core['signals'],
                                       test_re=TEST_CMD, toplevel=index.toplevel, ls_cache=index.ls_cache)
            if extra:
                core['signals'] = core['signals'] + extra
                core['verification_state'], core['label_class'], core['label_confidence'] = decide(core['signals'])
        from .verification_density import turn_type
        core['turn'] = {'type': turn_type(t, TEST_CMD), 'started_at': _iso(t['started_at']), 'ended_at': _iso(t['ended_at']),
                        'assistant_messages': t['assistant_messages'], 'tool_calls': t['tool_calls'],
                        'bash_calls': len(t['bash']), 'bash_calls_via_subagents': sum(1 for c in t['bash'] if c.get('via')),
                        'edits': len(t['edits']),
                        'harness_message': t['harness_message']}
        core['_started'] = t['started_at']
        out[t['prompt_id']] = core
    return out


def verify(*, root: Path | None = None, since: float = 0.0, all_turns: bool = False, gh: GitHub | None = None,
           fix_days: int = 7, projects: Path | None = None, now: float | None = None,
           extra_repo_roots: Iterable[Path] = (), density: bool = True,
           _index: RepoIndex | None = None) -> list[dict[str, Any]]:
    """Build verified rows for observed turns (and, with ``all_turns``, every transcript turn since ``since``).

    With GitHub enabled and a lookup cache, a planning pass records the lookups the sweep needs, resolves
    the uncached ones concurrently, then the real pass reads them from memory."""
    now = time.time() if now is None else now
    gh = gh or GitHub()
    index = _index or RepoIndex(extra_repo_roots)
    if gh.enabled and gh.cache_path is not None and gh.recording is None:
        gh.recording = []
        try:
            verify(root=root, since=since, all_turns=all_turns, gh=gh, fix_days=fix_days, projects=projects, now=now,
                   extra_repo_roots=extra_repo_roots, density=False, _index=index)
            keys, gh.recording = gh.recording, None
        finally:
            gh.recording = None
        gh.prefetch(keys)
    sdir = state_dir(root)
    observed = [r for r in read_jsonl(sdir / 'outcomes.jsonl') if r.get('schema') == OBSERVED_SCHEMA]
    obs_by_key = {(r.get('session_id'), r.get('trace_id')): r for r in observed}
    sessions: dict[str, Path | None] = {}
    for sid, _ in obs_by_key:
        if sid:
            sessions[sid] = find_transcript(sid, projects)
    if all_turns:
        base = projects or projects_dir()
        for path in sorted(base.glob('*/*.jsonl')):
            try:
                if path.stat().st_mtime >= since:
                    sessions.setdefault(path.stem, path)
            except OSError:
                continue
    rows = []
    for sid, path in sorted(sessions.items()):
        if path is not None and str(path) not in index.sessions:
            index.sessions[str(path)] = session_turns(path)
        cores = _rows_for_session(index.sessions[str(path)], sid, index=index, gh=gh, fix_days=fix_days,
                                  now=now, density=density) if path else {}
        keys = [k for k in obs_by_key if k[0] == sid]
        if all_turns:
            keys += [(sid, pid) for pid, c in cores.items() if (sid, pid) not in obs_by_key
                     and not c['turn']['harness_message']]
        for key in keys:
            core = cores.get(key[1])
            if core is not None and (core['_started'] or 0) < since:
                continue
            if core is None:
                core = {'signals': [], 'verification_state': 'unverified', 'label_class': 'unknown',
                        'label_confidence': None, 'turn': None, 'asked': None,
                        'measurement': {'transcript': 'missing' if path is None else 'turn_not_found'}}
            core.pop('_started', None)
            obs = obs_by_key.get(key)
            rows.append(make_row(sid, key[1], core, obs, fix_days=fix_days, gh=gh, now=now, density=density))
    gh.flush()
    return rows


def make_row(session_id: str, trace_id: str | None, core: Mapping[str, Any], observed: Mapping[str, Any] | None, *,
             fix_days: int, gh: GitHub, now: float, density: bool = True) -> dict[str, Any]:
    content = {'signals': core['signals'], 'verification_state': core['verification_state'],
               'label_class': core['label_class'], 'label_confidence': core['label_confidence']}
    row = {
        'schema': SCHEMA, 'verifier': {**VERIFIER, 'params': {'fix_days': fix_days, 're_ask_jaccard': RE_ASK_JACCARD,
                                                              'density': density}},
        'session_id': session_id, 'trace_id': trace_id,
        'observed_ref': {'schema': OBSERVED_SCHEMA, 'present': observed is not None,
                         'row_sha': _sha(observed) if observed is not None else None,
                         'label_kind': (observed or {}).get('label_kind')},
        'turn': core.get('turn'), **content,
        'measurement': {**core['measurement'], 'gh_calls_failed': gh.failures},
        'credit': {'level': 'observational', 'counterfactual_available': False, 'counterfactual_claimed': False},
        'privacy': 'counts_ids_and_shas_only',
        'content_sha': _sha(content),
        'verified_at': _iso(now),
    }
    row['verification_id'] = _sha({'session': session_id, 'trace': trace_id, 'content': row['content_sha'],
                                   'verifier': VERIFIER}, 20)
    return row


def append_new(rows: list[dict[str, Any]], root: Path | None = None) -> tuple[int, Path]:
    """Append rows whose content changed since the latest verified row for the same turn. Never rewrites."""
    path = state_dir(root) / 'outcomes_verified.jsonl'
    latest = {}
    for r in read_jsonl(path):
        latest[(r.get('session_id'), r.get('trace_id'))] = r.get('content_sha')
    fresh = [r for r in rows if latest.get((r['session_id'], r['trace_id'])) != r['content_sha']]
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as fh:
            for r in fresh:
                fh.write(json.dumps(r, sort_keys=True) + '\n')
    return len(fresh), path


# ----------------------------------------------------------------------------- credit-ready join
def credit_join(root: Path | None = None) -> list[dict[str, Any]]:
    """opportunity -> gate decision -> observed -> verified, one row per observed turn. No request text."""
    sdir = state_dir(root)
    opps = {}
    for r in read_jsonl(sdir / 'opportunities.jsonl'):
        opp = r.get('opportunity') or {}
        key = (r.get('session_id'), (opp.get('trace') or {}).get('trace_id'))
        opps[key] = {'opportunity_id': (opp.get('trace') or {}).get('opportunity_id'),
                     'semantic_id': opp.get('semantic_id'), 'intent_revision': (opp.get('intent') or {}).get('revision'),
                     'gate': r.get('gate'),
                     'legal_actions': sorted(a['kind'] for a in opp.get('action_space') or [] if a.get('legal')),
                     'authority_source': (opp.get('authority') or {}).get('source')}
    verified = {}
    for r in read_jsonl(sdir / 'outcomes_verified.jsonl'):
        verified[(r.get('session_id'), r.get('trace_id'))] = r
    out = []
    for obs in read_jsonl(sdir / 'outcomes.jsonl'):
        if obs.get('schema') != OBSERVED_SCHEMA:
            continue
        key = (obs.get('session_id'), obs.get('trace_id'))
        opp, ver = opps.get(key), verified.get(key)
        observed_action = 'ASK' if obs.get('asked_user') else 'ACT'  # the Stop row records no OBSERVE/ABSTAIN/ESCALATE
        out.append({
            'schema': JOIN_SCHEMA, 'session_id': key[0], 'trace_id': key[1],
            'opportunity': opp, 'gate_decision': (opp or {}).get('gate'),
            'observed': {'action': observed_action, 'label_kind': obs.get('label_kind'),
                         **{k: obs.get(k) for k in ('asked_user', 'asked_via_tool', 'tool_calls', 'assistant_messages')}},
            'verified': None if ver is None else {
                'verification_id': ver['verification_id'], 'state': ver['verification_state'],
                'label_class': ver['label_class'], 'label_confidence': ver['label_confidence'],
                'oracles': sorted({s['oracle'] for s in ver['signals'] if s['polarity']}),
                'measurement': ver['measurement']},
            'gate_agrees_with_observed': None if opp is None else opp['gate'] == observed_action,
            'join': {'opportunity': opp is not None, 'verified': ver is not None},
            'credit': {'level': 'observational', 'counterfactual_available': False,
                       'note': 'observed action is not the optimal label; no counterfactual action was executed'},
        })
    return out


# ----------------------------------------------------------------------------- report (counts only)
def summarize(rows: list[dict[str, Any]], joined: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sig = Counter((s['kind'], s['polarity']) for r in rows for s in r['signals'])
    rep = {
        'turns': len(rows), 'with_observed_row': sum(1 for r in rows if r['observed_ref']['present']),
        'states': dict(Counter(r['verification_state'] for r in rows)),
        'label_classes': dict(Counter(r['label_class'] for r in rows)),
        'signals': {f'{k}[{p:+d}]': n for (k, p), n in sorted(sig.items())},
        'turns_with_any_signal': sum(1 for r in rows if r['signals']),
        'measurement': {
            'transcript_missing': sum(1 for r in rows if r['measurement'].get('transcript') != 'found'),
            'commits_produced': sum((r['measurement'].get('commits') or {}).get('produced', 0) for r in rows),
            'commits_resolved': sum((r['measurement'].get('commits') or {}).get('resolved', 0) for r in rows),
            'turns_with_unexamined_subagents': sum(1 for r in rows if r['measurement'].get('subagents_not_inspected')),
        },
    }
    if joined is not None:
        rep['join'] = {
            'observed_rows': len(joined),
            'with_opportunity': sum(1 for j in joined if j['join']['opportunity']),
            'with_verified': sum(1 for j in joined if j['join']['verified']),
            'full_chain': sum(1 for j in joined if j['join']['opportunity'] and j['join']['verified']),
            'gate_x_state': dict(Counter(f"{j['gate_decision']}->{(j['verified'] or {}).get('state')}"
                                         for j in joined if j['join']['opportunity'])),
            'observed_action_x_state': dict(Counter(f"{j['observed']['action']}->{(j['verified'] or {}).get('state')}"
                                                    for j in joined)),
        }
    return rep


def format_markdown(rep: Mapping[str, Any], *, title: str = 'Verified outcomes v0 -- live run', meta: Mapping | None = None) -> str:
    lines = [f'# {title}', '', 'Counts only. No prompt, response or command text. Semantics: '
             'z0intelligence docs/verified-outcomes.md (branch feat/verified-outcomes-v0).', '']
    for k, v in (meta or {}).items():
        lines.append(f'- {k}: {v}')
    lines += ['', '## Verification state', '', '| state | turns |', '|---|---:|']
    lines += [f'| {s} | {rep["states"].get(s, 0)} |' for s in STATES]
    lines += ['', '## Label class (z0int#54)', '', '| class | turns |', '|---|---:|']
    lines += [f'| {c} | {rep["label_classes"].get(c, 0)} |' for c in LABEL_CLASSES]
    lines += ['', '## Signals (kind[polarity])', '', '| signal | count |', '|---|---:|']
    lines += [f'| {k} | {n} |' for k, n in rep['signals'].items()]
    lines += ['', '## Measurement completeness', '']
    lines += [f'- turns: {rep["turns"]} (with observed turn_outcome.v0 row: {rep["with_observed_row"]}; '
              f'with any signal: {rep["turns_with_any_signal"]})']
    lines += [f'- {k}: {v}' for k, v in rep['measurement'].items()]
    if 'join' in rep:
        j = rep['join']
        lines += ['', '## Credit join (opportunity -> gate -> observed -> verified)', '']
        lines += [f'- {k}: {j[k]}' for k in ('observed_rows', 'with_opportunity', 'with_verified', 'full_chain')]
        lines += ['', '| gate -> verified state | turns |', '|---|---:|']
        lines += [f'| {k} | {n} |' for k, n in sorted(j['gate_x_state'].items())]
        lines += ['', '| observed action -> verified state | turns |', '|---|---:|']
        lines += [f'| {k} | {n} |' for k, n in sorted(j['observed_action_x_state'].items())]
    return '\n'.join(lines) + '\n'


# ----------------------------------------------------------------------------- CLI
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int outcomes', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('verify', help='append turn_outcome_verified.v0 rows (observed rows are never touched)')
    v.add_argument('--since', default=None, help="only turns started after this ('7d', '36h', ISO date); default all")
    v.add_argument('--all-turns', action='store_true', help='also verify transcript turns that have no observed row')
    v.add_argument('--fix-days', type=int, default=7, help='SZZ / revert observation window (days)')
    v.add_argument('--no-gh', action='store_true', help='skip read-only GitHub lookups (PR state, check-runs)')
    v.add_argument('--no-gh-cache', action='store_true', help='do not read/write the on-disk gh lookup cache')
    v.add_argument('--no-density', action='store_true', help='v0 signal set only (no verification_density signals)')
    v.add_argument('--projects-dir', type=Path, default=None)
    v.add_argument('--repo-root', action='append', type=Path, default=[],
                   help='extra repository to try when a commit SHA is not found in the turn cwd (repeatable)')
    v.add_argument('--dry-run', action='store_true', help='compute and summarise; do not append')
    v.add_argument('--report', type=Path, default=None, help='also write a counts-only markdown report here')
    v.add_argument('--json', action='store_true')
    sub.add_parser('density', add_help=False,
                   help='counts-only diagnosis: why turns are unverified, by cohort x turn type (-> verification_density)')
    sub.add_parser('export', add_help=False,
                   help='privacy-safe training table for the verified loop (-> z0int.loop_export; see --help)')
    j = sub.add_parser('join', help='credit-ready join: opportunity -> gate -> observed -> verified')
    j.add_argument('--out', type=Path, default=None, help='write join rows as JSONL (default: print summary only)')
    j.add_argument('--json', action='store_true')
    if argv is None:
        import sys
        argv = sys.argv[1:]
    if argv and argv[0] == 'density':
        from .verification_density import _main as density_main
        return density_main(list(argv[1:]))
    if argv and argv[0] == 'export':
        from .loop_export import _main as export_main
        return export_main(list(argv[1:]))
    args = ap.parse_args(argv)
    if args.cmd == 'verify':
        now = time.time()
        gh = GitHub(not args.no_gh, cache_path=None if args.no_gh_cache else default_gh_cache())
        rows = verify(since=parse_since(args.since, now), all_turns=args.all_turns, gh=gh,
                      fix_days=args.fix_days, projects=args.projects_dir, now=now, extra_repo_roots=args.repo_root,
                      density=not args.no_density)
        appended, path = (0, None) if args.dry_run else append_new(rows)
        rep = summarize(rows, credit_join())
        rep['appended'] = appended
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(format_markdown(rep, meta={
                'generated_at': _iso(now), 'since': args.since or 'all', 'all_turns': args.all_turns,
                'fix_days': args.fix_days, 'gh': 'disabled' if args.no_gh else 'read-only',
                'verifier': f"{VERIFIER['id']} {VERIFIER['version']}", 'rows_appended': appended}))
        if args.json:
            print(json.dumps(rep, indent=1))
        else:
            print(f"verified {rep['turns']} turns; appended {appended} -> {path or '(dry run)'}")
            print('states: ' + ', '.join(f'{s}={rep["states"].get(s, 0)}' for s in STATES))
        return 0
    joined = credit_join()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in joined))
    rep = summarize([], joined)['join']
    print(json.dumps(rep, indent=1) if args.json else ', '.join(f'{k}={v}' for k, v in rep.items()
                                                                 if not isinstance(v, dict)))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
