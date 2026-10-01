"""Verification density v0: cheap, honest verifiers for turns the v0 outcome verifier leaves ``unverified``.

``outcome_verifier`` (z0int#54) labels a turn only when an independent oracle judged it: tests run
in the turn, CI, commit revert / SZZ fix, PR review, strong user-correction cues. On local
transcripts most live turns get none of these. This module

  * classifies every turn by **type** (qa / research / ops / edit_untested / edit_tested /
    orchestration / handoff) so "why unverified" can be counted, and
  * adds session-level verifiers, each with an explicit polarity, confidence, #54 label class and
    oracle (see docs/verification-density.md for the table and the pre-registered precision bar):

      - ``checked_later``          edits followed by a test / lint / typecheck run later in the
                                   session that covers the touched files (first covering run decides)
      - ``tests_suite_new_tests``  in-turn whole-suite pass where the only test files the turn touched
                                   were *new* files (pre-existing tests stayed independent judges)
      - ``ended_on_error``         the turn's last non-probe tool call failed and nothing after it
                                   succeeded
      - ``edit_reverted``          a later ``git checkout -- f`` / ``git restore f`` of a file the
                                   turn edited
      - ``edit_rewritten``         a later edit deleted most of the lines this turn added (low)
      - ``answer_ungrounded`` /    ``path:line`` citations in the final answer of a turn without
        ``answer_grounded``        edits: missing path or line beyond EOF at answer time. Grounding,
                                   not truth.
      - ``user_correction_v2`` /   validated cue sets on the next real user prompt (ids only)
        ``user_approval``

Only cue ids, counts, kinds and hashed ids leave this module. Prompt / answer / command / edit text
is read transiently, exactly like ``outcome_verifier``.
"""

from __future__ import annotations

import os
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

VERSION = '0.1.0'
TURN_TYPES = ('qa', 'research', 'ops', 'edit_untested', 'edit_tested', 'orchestration', 'handoff')

ORCH_TOOLS = frozenset({'Agent', 'Task', 'SendMessage', 'ScheduleWakeup', 'TaskStop', 'TaskCreate', 'TaskUpdate',
                        'ListAgents', 'TeamCreate', 'TeamDelete', 'CronCreate', 'RemoteTrigger', 'Monitor'})
PROBE_TOOLS = frozenset({'Read', 'Grep', 'Glob', 'LS', 'ToolSearch', 'WebFetch', 'WebSearch', 'TodoWrite', 'Skill',
                         'TaskList', 'TaskGet', 'TaskOutput', 'NotebookRead', 'ListAgents', 'ListMcpResourcesTool',
                         'ReadMcpResourceTool', 'Monitor', 'ScheduleWakeup'})
# Not failures of the work: the user declining a question / plan, permission prompts.
USER_GATED_TOOLS = frozenset({'AskUserQuestion', 'ExitPlanMode', 'EnterPlanMode'})
USER_REJECTION = re.compile(r"doesn'?t want to (proceed|take this action)|user (rejected|denied|declined)|"
                            r'permission (to use \S+ )?(has been )?denied|request interrupted', re.I)
# A Bash segment that only looks around. Used for turn typing and to skip probes in ``ended_on_error``.
PROBE_BASH = re.compile(
    r'^\s*(?:(?:cd|pushd)\s+\S+\s*(?:&&|;)\s*)*(?:(?:time|env\s+\S+=\S+)\s+)?'
    r'(ls|cat|head|tail|less|grep|egrep|rg|ag|find|fd|wc|sed\s+-n|awk|jq|yq|which|command\s+-v|type|stat|file|du|df|'
    r'tree|echo|printf|pwd|env|printenv|date|test|\[|diff|cmp|sort|uniq|cut|tr|nl|readlink|realpath|basename|dirname|'
    r'sleep|true|ps|pgrep|top|free|nproc|uname|hostname|whoami|id|ss|lsof|nvidia-smi|z0obs|sqlite3|'
    r'git\s+(?:-C\s+\S+\s+)?(?:status|log|diff|show|branch|rev-parse|remote|ls-files|ls-tree|blame|grep|'
    r'worktree\s+list|fetch|config\s+--get|merge-base|describe|shortlog|reflog|cat-file|for-each-ref|tag\s+-l|'
    r'rev-list|name-rev|stash\s+list)|'
    r'gh\s+(?:pr|issue|run|repo|release|workflow)\s+(?:view|list|status|checks|diff)|gh\s+api\s+(?!.*(?:-X|--method)\s*(?:POST|PATCH|PUT|DELETE))|'
    r'gh\s+auth\s+status|uv\s+pip\s+(?:list|show|freeze)|pip\s+(?:list|show|freeze)|systemctl\s+(?:status|is-active)|'
    r'kubectl\s+(?:get|describe|logs|top)|docker\s+(?:ps|images|logs|inspect)|journalctl)\b')

# Lint / typecheck / compile checks (tests are outcome_verifier.TEST_CMD).
LINT_CMD = re.compile(r'(^|[\s;&|(/])(ruff( check| format --check)?|flake8|pylint|mypy|pyright|basedpyright|'
                      r'tsc|eslint|biome (check|lint)|prettier --check|black --check|isort --check(-only)?|'
                      r'cargo (check|clippy|build)|go (vet|build)|golangci-lint|shellcheck|deno (check|lint)|'
                      r'python3? -m (py_compile|compileall|mypy|ruff)|bash -n|node --check)(?=$|[\s;&|)])')
TEST_OPTS_WITH_VALUE = frozenset({'-k', '-m', '-p', '-c', '-o', '--rootdir', '--ignore', '--deselect', '--maxfail',
                                  '-n', '--tb', '-W', '--junitxml', '--cov', '--timeout', '--durations', '-x'})
SEGMENT_SPLIT = re.compile(r'\s*(?:&&|\|\||;|\||\n)\s*')

GIT_RESTORE = re.compile(r'\bgit\s+(?:-C\s+(\S+)\s+)?(?:checkout\s+(?:HEAD\s+)?--|restore(?!\s+--staged\b)(?:\s+--worktree)?'
                         r'(?:\s+--source[ =](?:HEAD|@)\S*)?)\s+(.+)$')
CITATION = re.compile(r'(?<![\w/.~-])((?:~/|/|\./|\.\./)?(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*\.[A-Za-z][A-Za-z0-9]{0,7})'
                      r':(\d{1,6})(?:[-–](\d{1,6}))?\b')
URL_CONTEXT = re.compile(r'(https?|ftp)://\S*$')

# --- user cue sets v2 (validated sample: ~/.z0int/research/verification-density/, not in git) -----------------
CORRECTION_V2 = {
    'undo': re.compile(r'\bundo\b'),
    'revert_that': re.compile(r'\brevert (that|this|it|those|these|the change|your)\b'),
    'thats_wrong': re.compile(r"\b(that'?s|that is|this is|it'?s) (wrong|incorrect|not right|broken|not what)\b"),
    'not_what_i': re.compile(r'\bnot what i (asked|wanted|meant|said)\b'),
    'you_broke': re.compile(r'\b(you|u) (broke|deleted|removed|messed)\b'),
    'didnt_work': re.compile(r"\b(doesn'?t|didn'?t|does not|did not|isn'?t|is not|not) work(ing)?\b"),
    'still_broken': re.compile(r"\bstill (broken|failing|fails|wrong|not|erroring|seeing|getting|happening|no|isn'?t|aren'?t|doesn'?t|don'?t|can'?t)\b"),
    'listen': re.compile(r'\b(actually |pls |please )?listen to (what )?(i|me)\b|\bthis time actually\b'),
    'i_said': re.compile(r"\b(like i said|i (already )?(said|told (you|u)|asked (you|u))|as i said)\b"),
    'you_didnt': re.compile(r"\b(you|u) (didn'?t|did not|forgot|missed|ignored|never)\b"),
    'why_cant_you': re.compile(r"\bwhy (can'?t|cant|won'?t|didn'?t|do|did|does|are|r) (u|you)\b.{0,40}\b(struggl|fail|keep|always|not|never|break|forget|miss|ignore|stop)"),
    'frustration': re.compile(r'\b(smh|wtf|ugh+|bruh|ffs)\b'),
}
APPROVAL_STRONG = re.compile(r"\b(perfect|awesome|excellent|amazing|lgtm|looks good|sounds good|makes sense|"
                             r"love (it|this|that)|nice work|good job|great job|well done|thank(s| you)|ty|that worked|"
                             r"works now|it works|that works|great|beautiful|nailed it)\b")
APPROVAL_WEAK = re.compile(r'^\W*(ok(ay)?|ya|yeah|yes|yep|yup|sure|gotcha|gotchu|alright|cool|nice|sweet|k)\b')


def head_of(prompt: str | None, n: int) -> str:
    return (prompt or '')[:n].lower().replace('’', "'")


def correction_cues_v2(next_prompt: str | None) -> list[str]:
    head = head_of(next_prompt, 300)
    return [k for k, rx in CORRECTION_V2.items() if rx.search(head)] if head else []


def approval_cues(next_prompt: str | None) -> tuple[bool, bool]:
    """(strong, weak) approval in the first 80 chars of the next real user prompt."""
    head = head_of(next_prompt, 80)
    return bool(head and APPROVAL_STRONG.search(head)), bool(head and APPROVAL_WEAK.search(head))


# --- turn typing ---------------------------------------------------------------------------------------------------
def is_probe_command(command: str) -> bool:
    segs = [s for s in SEGMENT_SPLIT.split(command.strip()) if s.strip()]
    return bool(segs) and all(PROBE_BASH.match(s) or re.match(r'^\s*(cd|pushd|popd|export|set|source|\.)\b', s)
                              for s in segs)


def is_check_command(command: str, test_re: re.Pattern[str]) -> str | None:
    if test_re.search(command):
        return 'test'
    if LINT_CMD.search(command):
        return 'lint'
    return None


def turn_type(turn: Mapping[str, Any], test_re: re.Pattern[str]) -> str:
    if turn.get('harness_message'):
        return 'handoff'
    names: Mapping[str, int] = turn.get('tool_names') or {}
    if turn.get('agents_spawned') or any(n in ORCH_TOOLS for n in names):
        return 'orchestration'
    if turn.get('edits'):
        return 'edit_tested' if any(test_re.search(c.get('command', '')) for c in turn.get('bash') or []) \
            else 'edit_untested'
    if any(not is_probe_command(c.get('command', '')) for c in turn.get('bash') or []) or \
            any(n.startswith('mcp__') for n in names):
        return 'ops'
    if turn.get('tool_calls'):
        return 'research'
    return 'qa'


# --- path helpers ----------------------------------------------------------------------------------------------------
def _abs(p: str, base: str | None) -> str:
    p = os.path.expanduser(p.strip('\'"'))
    if not os.path.isabs(p) and base:
        p = os.path.join(base, p)
    return os.path.normpath(p)


def _segment_after(command: str, rx: re.Pattern[str]) -> tuple[str, str] | None:
    """(segment cwd-prefix-free text after the runner match, segment) for the first segment matching ``rx``."""
    for seg in SEGMENT_SPLIT.split(command):
        m = rx.search(seg)
        if m:
            return seg[m.end():], seg
    return None


def check_scope(command: str, kind: str, test_re: re.Pattern[str]) -> tuple[str, list[str]]:
    """('whole', []) / ('paths', [args]) / ('filtered', [args]) for the check's target selection."""
    hit = _segment_after(command, test_re if kind == 'test' else LINT_CMD)
    if hit is None:
        return 'whole', []
    rest, _ = hit
    try:
        toks = shlex.split(rest, comments=False)
    except ValueError:
        toks = rest.split()
    paths, filtered, skip = [], False, False
    for tok in toks:
        if skip:
            skip = False
            continue
        if tok in ('-k', '-m') or tok.startswith(('-k=', '-m=')) or '::' in tok or tok.startswith('--lf'):
            filtered = True
            skip = tok in ('-k', '-m')
            continue
        if tok in TEST_OPTS_WITH_VALUE:
            skip = True
            continue
        if tok.startswith('-') or '=' in tok:
            continue
        if '/' in tok or re.search(r'\.\w{1,5}$', tok) or tok.startswith('test'):
            paths.append(tok.split('::')[0])
    if filtered:
        return 'filtered', paths
    return ('paths', paths) if paths else ('whole', [])


def command_dir(command: str, cwd: str | None) -> str | None:
    from .outcome_verifier import command_paths
    cands = command_paths(command, cwd)
    return cands[-1] if cands else cwd


# --- the verifiers -----------------------------------------------------------------------------------------------
def _sig(kind: str, polarity: int, confidence: str, label_class: str, oracle: str, **details: Any) -> dict[str, Any]:
    from .outcome_verifier import signal
    return signal(kind, polarity, confidence, label_class, oracle, verifier_set='density', **details)


def _code_edits(turn: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .outcome_verifier import DOC_RE
    return [e for e in turn.get('edit_ops') or [] if e.get('path') and not e.get('error') and e.get('t1')
            and not DOC_RE.search(e['path'])]


def _covers(check: Mapping[str, Any], scope: str, args: list[str], files: set[str],
            toplevel: Callable[[str | None], Any]) -> bool:
    base = command_dir(check.get('command', ''), check.get('cwd'))
    if not base:
        return False
    if scope == 'whole':
        top = toplevel(base)
        root = str(top) if top else base
        return any(f == root or f.startswith(root.rstrip('/') + '/') for f in files)
    for a in args:
        ap = _abs(a, base)
        stem = Path(a).stem.removeprefix('test_').removesuffix('_test')
        for f in files:
            if f == ap or f.startswith(ap.rstrip('/') + '/'):
                return True
            if stem and len(stem) >= 3 and Path(f).stem == stem:
                return True
    return False


def checked_later(turns: list[Mapping[str, Any]], i: int, test_re: re.Pattern[str],
                  toplevel: Callable[[str | None], Any]) -> dict[str, Any] | None:
    """First test/lint/typecheck run (this turn or later) after turn i's last code edit that covers its files."""
    from .outcome_verifier import TEST_PATH
    edits = _code_edits(turns[i])
    if not edits:
        return None
    t_last = max(e['t1'] for e in edits)
    files = {_abs(e['path'], e.get('cwd')) for e in edits}
    own_test_edits = [e for e in turns[i].get('edit_ops') or [] if e.get('path') and TEST_PATH.search(e['path'])]
    existing_tests_edited = any(not e.get('created') for e in own_test_edits)
    later_edits = sorted((e['t1'], _abs(e['path'], e.get('cwd'))) for j, t in enumerate(turns) if j != i
                         for e in t.get('edit_ops') or [] if e.get('t1') and e['t1'] > t_last and e.get('path'))
    checks = sorted(((c['t0'], j, c) for j in range(i, len(turns)) for c in turns[j].get('bash') or []
                     if c.get('t0') and c['t0'] > t_last and c.get('exit') is not None), key=lambda x: x[0])
    for t0, j, c in checks:
        kind = is_check_command(c.get('command', ''), test_re)
        if kind is None:
            continue
        scope, args = check_scope(c['command'], kind, test_re)
        if not _covers(c, scope, args, files, toplevel):
            continue
        if any(t_last < te < t0 and f in files for te, f in later_edits):
            return {'superseded': True}  # another turn changed the same files first: judges mixed state
        ok = c['exit'] == 0
        details = {'check': kind, 'scope': scope, 'same_turn': j == i, 'turns_later': j - i, 'exit_class': 0 if ok else 1}
        if kind == 'test' and not existing_tests_edited:
            return _sig('checked_later', 1 if ok else -1, 'medium', 'deterministic_gold' if ok else 'negative_gold',
                        'test_runner', **details)
        return _sig('checked_later', 1 if ok else -1, 'low', 'soft', 'test_runner' if kind == 'test' else 'linter',
                    **details, self_judged=kind == 'test')
    return None


def tests_suite_new_tests(turn: Mapping[str, Any], test_re: re.Pattern[str]) -> dict[str, Any] | None:
    """The v0 rule downgrades an in-turn pass when the turn edited tests. If every test file it touched was
    created new and the last run was the whole suite, pre-existing tests still judged it independently."""
    from .outcome_verifier import TEST_PATH
    runs = [c for c in turn.get('bash') or [] if test_re.search(c.get('command', '')) and c.get('exit') is not None]
    test_edits = [e for e in turn.get('edit_ops') or [] if e.get('path') and TEST_PATH.search(e['path'])]
    if not runs or not test_edits or runs[-1]['exit'] != 0:
        return None
    created = {e['path'] for e in test_edits if e.get('created')}
    if any(e['path'] not in created for e in test_edits):
        return None
    scope, _ = check_scope(runs[-1]['command'], 'test', test_re)
    if scope != 'whole':
        return None
    return _sig('tests_suite_new_tests', 1, 'medium', 'deterministic_gold', 'test_runner', new_test_files=len(created))


def _call_failed(c: Mapping[str, Any]) -> bool | None:
    if c.get('name') == 'Bash':
        ex = c.get('exit')
        if ex is None:
            return None
        if ex == 8 and re.search(r'\bgh pr checks\b', c.get('command', '')):
            return None
        return ex != 0
    if 't1' not in c:
        return None
    return bool(c.get('error'))


def ended_on_error(turn: Mapping[str, Any]) -> dict[str, Any] | None:
    """The turn's last non-probe tool call failed, and nothing after it succeeded (own calls, not subagents)."""
    if turn.get('interrupted'):
        return None
    seq = [c for c in turn.get('tool_seq') or [] if c.get('name') not in PROBE_TOOLS
           and not (c.get('name') == 'Bash' and is_probe_command(c.get('command', '')))]
    for c in reversed(seq):
        failed = _call_failed(c)
        if failed is None:
            continue
        if c.get('name') in USER_GATED_TOOLS or USER_REJECTION.search(c.get('_out_tail') or c.get('_err') or ''):
            return None
        if not failed:
            return None
        return _sig('ended_on_error', -1, 'medium', 'negative_gold', 'tool_runtime', tool=c.get('name'),
                    exit=c.get('exit') if c.get('name') == 'Bash' else None)
    return None


def _added_lines(e: Mapping[str, Any]) -> set[str]:
    old = {ln.strip() for ln in (e.get('_old') or '').splitlines()}
    return {ln.strip() for ln in (e.get('_new') or '').splitlines() if len(ln.strip()) >= 8 and ln.strip() not in old}


def edit_reversal(turns: list[Mapping[str, Any]], i: int) -> list[dict[str, Any]]:
    edits = _code_edits(turns[i])
    if not edits:
        return []
    t_last = max(e['t1'] for e in edits)
    files = {_abs(e['path'], e.get('cwd')) for e in edits}
    out: list[dict[str, Any]] = []
    for j in range(i + 1, len(turns)):
        for c in turns[j].get('bash') or []:
            if c.get('exit') != 0 or not c.get('t0') or c['t0'] <= t_last:
                continue
            for seg in SEGMENT_SPLIT.split(c.get('command', '')):
                m = GIT_RESTORE.search(seg)
                if not m:
                    continue
                base = _abs(m.group(1), c.get('cwd')) if m.group(1) else command_dir(c['command'], c.get('cwd'))
                try:
                    args = [a for a in shlex.split(m.group(2)) if not a.startswith('-')]
                except ValueError:
                    continue
                hit = [a for a in args if a not in ('.', '*') and any(
                    f == _abs(a, base) or f.startswith(_abs(a, base).rstrip('/') + '/') for f in files)]
                if hit:
                    out.append(_sig('edit_reverted', -1, 'medium', 'negative_gold', 'workspace', turns_later=j - i))
                    return out
    added = set().union(*(_added_lines(e) for e in edits))
    if len(added) >= 3:
        for j in range(i + 1, len(turns)):
            removed = set()
            for e in turns[j].get('edit_ops') or []:
                if e.get('error') or _abs(e.get('path') or '', e.get('cwd')) not in files:
                    continue
                new = {ln.strip() for ln in (e.get('_new') or '').splitlines()}
                removed |= {ln.strip() for ln in (e.get('_old') or '').splitlines() if ln.strip() in added} - new
            if len(removed) >= max(3, len(added) // 2):
                out.append(_sig('edit_rewritten', -1, 'low', 'soft', 'workspace', turns_later=j - i,
                                share_removed=round(len(removed) / len(added), 2)))
                break
    return out


def _git_ls(top: str, cache: dict[str, list[str]]) -> list[str]:
    if top not in cache:
        from .outcome_verifier import git
        cache[top] = [ln for ln in git(top, 'ls-files').splitlines() if ln]
    return cache[top]


def grounding(turn: Mapping[str, Any], toplevel: Callable[[str | None], Any],
              ls_cache: dict[str, list[str]]) -> dict[str, Any] | None:
    """Do ``path:line`` citations in the final answer of a turn without edits resolve at answer time?

    Deterministic grounding, not truth. A citation is unmeasurable (skipped) when its base directory no
    longer exists (e.g. an eval tmpdir). Missing: no candidate resolves and no tracked file in the turn's
    repositories ends with the cited relative path. Line beyond EOF only when the file is unchanged since."""
    if turn.get('edit_ops') or not turn.get('_final_text'):
        return None
    text = turn['_final_text']
    cites = []
    for m in CITATION.finditer(text):
        if URL_CONTEXT.search(text[max(0, m.start() - 200):m.start()]):
            continue
        cites.append((m.group(1), int(m.group(2))))
    if not cites:
        return None
    cwd = turn.get('cwd')
    bases = [b for b in [cwd] + [command_dir(c.get('command', ''), c.get('cwd')) for c in turn.get('bash') or []] if b]
    known = {_abs(p, cwd) for p in (turn.get('reads') or set())}
    end = turn.get('ended_at') or 0
    tops: list[str] = []
    for b in bases:
        top = toplevel(b) if os.path.isdir(b) else None
        if top and str(top) not in tops:
            tops.append(str(top))
    if cwd and os.path.isdir(cwd) and not toplevel(cwd):  # e.g. ~/workspace: repos one level down
        try:
            for d in sorted(os.listdir(cwd))[:200]:
                full = os.path.join(cwd, d)
                if os.path.isdir(os.path.join(full, '.git')) or os.path.isfile(os.path.join(full, '.git')):
                    tops.append(full)
        except OSError:
            pass
    if not any(os.path.isdir(b) for b in bases):
        return None
    ok = missing = beyond = 0
    for path, line in cites[:30]:
        cands = [_abs(path, b) for b in bases] + [os.path.join(t, path) for t in tops] if not os.path.isabs(
            os.path.expanduser(path)) else [_abs(path, None)]
        hit = next((c for c in cands if c in known or os.path.isfile(c)), None)
        if hit is None and not os.path.isabs(os.path.expanduser(path)):
            rel = path.lstrip('./')
            for t in tops:
                found = next((f for f in _git_ls(t, ls_cache) if f == rel or f.endswith('/' + rel)), None)
                if found:
                    hit = os.path.join(t, found)
                    break
            if hit is None:
                hit = next((k for k in known if k.endswith('/' + rel)), None)
        if hit is None:
            missing += 1
            continue
        try:
            st = os.stat(hit)
            if end and st.st_mtime < end:
                with open(hit, 'rb') as fh:
                    n = sum(1 for _ in fh)
                if line > n + 1:
                    beyond += 1
                    continue
        except OSError:
            pass
        ok += 1
    details = {'citations': ok + missing + beyond, 'missing': missing, 'beyond_eof': beyond}
    if missing or beyond:
        return _sig('answer_ungrounded', -1, 'medium', 'negative_gold', 'grounding', **details)
    return _sig('answer_grounded', 1, 'low', 'soft', 'grounding', **details)


def user_cue_signals(next_prompt: str | None, asked: bool) -> list[dict[str, Any]]:
    """v2 cue sets. Both ship at LOW confidence until a >=50 labelled sample clears 0.9 precision (prereg)."""
    out = []
    corr = correction_cues_v2(next_prompt)
    if corr:
        out.append(_sig('user_correction_v2', -1, 'low', 'soft', 'user_cue', cues=corr))
    strong, weak = approval_cues(next_prompt)
    if (strong or weak) and not corr:
        out.append(_sig('user_approval', 1, 'low', 'soft', 'user_cue', strength='strong' if strong else 'weak',
                        after_question=asked))
    return out


def density_signals(turns: list[Mapping[str, Any]], i: int, *, next_prompt: str | None, asked: bool,
                    existing: Iterable[Mapping[str, Any]], test_re: re.Pattern[str],
                    toplevel: Callable[[str | None], Any], ls_cache: dict[str, list[str]]) -> list[dict[str, Any]]:
    t = turns[i]
    existing = list(existing)
    decisive_tests = any(s['kind'] == 'tests_in_turn' and s['polarity'] and s['confidence'] != 'low' for s in existing)
    out: list[dict[str, Any]] = []
    if not decisive_tests:
        s = tests_suite_new_tests(t, test_re)
        if s:
            out.append(s)
        else:
            s = checked_later(turns, i, test_re, toplevel)
            if s and 'superseded' not in s:
                out.append(s)
    s = ended_on_error(t)
    if s:
        out.append(s)
    out += edit_reversal(turns, i)
    s = grounding(t, toplevel, ls_cache)
    if s:
        out.append(s)
    out += user_cue_signals(next_prompt, asked)
    return out


def unverified_reason(row: Mapping[str, Any]) -> str:
    """Coarse reason a row stayed unverified (for the diagnosis table)."""
    kinds = Counter(s['kind'] for s in row.get('signals') or [])
    if row.get('verification_state') != 'unverified':
        return 'verified'
    if kinds.get('tests_in_turn') and any(s['kind'] == 'tests_in_turn' and s['confidence'] == 'low' and s['polarity'] > 0
                                          for s in row['signals']):
        return 'tests_pass_self_judged'
    if kinds.get('commit_created'):
        return 'commit_window_open'
    if any(s['polarity'] for s in row.get('signals') or []):
        return 'low_confidence_only'
    if kinds:
        return 'informational_only'
    return 'no_signal'


# --- diagnosis / measurement (counts only) -------------------------------------------------------------------------
LIVE_COHORTS = ('interactive', 'agent')


def diagnose(rows: Iterable[Mapping[str, Any]], cohort_fn: Callable[[str], str]) -> dict[str, Any]:
    """Counts by cohort x turn type x state, unverified reasons, and the live verified share. No text, no ids."""
    by: dict[str, Any] = {}
    cohorts: dict[str, str] = {}
    for r in rows:
        turn = r.get('turn') or {}
        sid = r.get('session_id') or ''
        if sid not in cohorts:
            cohorts[sid] = cohort_fn(sid)
        pop = 'live' if cohorts[sid] in LIVE_COHORTS else cohorts[sid]
        d = by.setdefault(pop, {'turns': 0, 'states': Counter(), 'types': {}, 'reasons': Counter(),
                                'decisive_kinds': Counter(), 'label_classes': Counter(), 'handoff': Counter()})
        if turn.get('harness_message'):  # not in the denominator (no opportunity record); counted for context
            d['handoff'][r.get('verification_state') or 'unverified'] += 1
            continue
        ty = turn.get('type') or 'unknown'
        st = r.get('verification_state') or 'unverified'
        d['turns'] += 1
        d['states'][st] += 1
        d['label_classes'][r.get('label_class')] += 1
        d['types'].setdefault(ty, Counter())[st] += 1
        d['reasons'][f'{ty}:{unverified_reason(r)}'] += st == 'unverified'
        for s in r.get('signals') or []:
            if s.get('polarity') and s.get('confidence') in ('medium', 'high'):
                d['decisive_kinds'][f"{s['kind']}[{s['polarity']:+d}]"] += 1
    out = {}
    for pop, d in by.items():
        resolved = d['turns'] - d['states'].get('unverified', 0)
        out[pop] = {'turns': d['turns'], 'resolved': resolved,
                    'verified_share': round(resolved / d['turns'], 4) if d['turns'] else None,
                    'not_success': d['states'].get('verified_failure', 0) + d['states'].get('contested', 0),
                    'states': dict(d['states']), 'label_classes': {str(k): v for k, v in d['label_classes'].items()},
                    'types': {k: dict(v) for k, v in sorted(d['types'].items())},
                    'unverified_reasons': {k: v for k, v in sorted(d['reasons'].items()) if v},
                    'decisive_signal_kinds': dict(sorted(d['decisive_kinds'].items())),
                    'handoff_turns_excluded': dict(d['handoff'])}
    out['_sessions_by_cohort'] = dict(Counter(cohorts.values()))
    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import time

    from . import outcome_verifier as ov
    from .loop_export import transcript_cohort
    ap = argparse.ArgumentParser(prog='z0int outcomes density', description=diagnose.__doc__)
    ap.add_argument('--since', default='7d')
    ap.add_argument('--no-gh', action='store_true')
    ap.add_argument('--no-density', action='store_true', help='diagnose the v0 signal set only')
    ap.add_argument('--projects-dir', type=Path, default=None)
    ap.add_argument('--out', type=Path, default=None, help='write the counts-only JSON here')
    args = ap.parse_args(argv)
    now = time.time()
    t0 = time.monotonic()
    gh = ov.GitHub(not args.no_gh, cache_path=ov.default_gh_cache())
    rows = ov.verify(since=ov.parse_since(args.since, now), all_turns=True, gh=gh, projects=args.projects_dir,
                     now=now, density=not args.no_density)
    stamps = sorted((r.get('turn') or {}).get('started_at') or '' for r in rows if (r.get('turn') or {}).get('started_at'))
    rep = {'schema': 'z0int.verification_density.diagnosis.v0', 'since': args.since, 'density': not args.no_density,
           'verifier': ov.VERIFIER, 'density_version': VERSION, 'gh': {'enabled': gh.enabled, 'calls': gh.calls,
                                                                      'disk_hits': gh.disk_hits, 'failed': gh.failures},
           'runtime_s': round(time.monotonic() - t0, 1), 'first_turn_at': stamps[0] if stamps else None,
           'last_turn_at': stamps[-1] if stamps else None, 'privacy': 'counts only',
           'populations': diagnose(rows, lambda sid: transcript_cohort(sid, args.projects_dir))}
    text = json.dumps(rep, indent=1, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + '\n')
    print(text)
    return 0
