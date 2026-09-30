"""Grant extraction for the action-authority check (z0int#55): what a user-authored text authorises.

Split from ``action_authority`` so the PreToolUse hook compiles these patterns only when it has new
user turns to read. Exact scope: a grant never reaches further than the words that name it; a
negated instruction becomes a prohibition. Harness text never reaches this module.
"""

from __future__ import annotations

import re

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing is not imported at run time (hook latency)
    from typing import Any, Iterable, Mapping  # noqa: F401


from .action_authority import _BRANCH_KINDS, _PROT_WORDS
from .action_effects import Ctx, parse_tool_call

# ------------------------------------------------------------------------------------------------ lexicon
_SENT_NEG = re.compile(r"(?:\b(?:don'?t|dont|do\s+not|never|without|not|no|no\s+need\s+to|avoid|hold\s+off\s+on|stop|refrain\s+from)"
                       r"|n't)\W+(?:\w+\W+){0,3}$", re.I)
_NO_NOUN = re.compile(r"\bno\s+(prs?|pull\s+requests?|push(?:es|ing)?|force[- ]?push(?:es|ing)?|merg(?:e|es|ing)|deploys?|"
                      r"releases?|installs?|sudo|ssh|comments?|issues?|publish(?:ing)?)\b", re.I)
_BRANCH_TOK = re.compile(r"(?:\b(?:to|into|onto|on|of)\s+(?:origin/|origin\s+)?|\bbranch\s+|^)([\w][\w./-]*)", re.I)
_NOT_BRANCH = {"the", "it", "this", "that", "my", "our", "them", "all", "everything", "changes", "change", "branch",
               "branches", "feature", "up", "a", "an", "origin", "remote", "upstream", "there", "here", "pr", "github",
               "ci", "force", "push", "main's", "your", "its", "these", "those", "both", "only", "please", "now", "then",
               "and", "or", "with", "via", "again", "back", "first", "just", "also", "to", "from", "in", "for", "when",
               "if", "once", "after", "before", "remote's", "local", "current", "new", "same", "each", "any", "one",
               "delete", "remove", "merge", "commit", "force-push", "it's", "is", "are", "be", "do", "done", "so",
               "worktree", "worktrees", "repo", "repository", "server", "host", "box", "machine"}

# kind -> (verb regex that *names* the action explicitly)
LEXICON: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("force", re.compile(r"\bforce[- ]?push(?:ed|es|ing)?\b|\bpush\s+(?:\S+\s+){0,3}(?:--force(?:-with-lease)?|-f)\b|\bpush\s+(?:it\s+)?with\s+force\b", re.I)),
    ("rewrite", re.compile(r"\brewrite\s+(?:the\s+)?(?:git\s+)?history\b|\bfilter-(?:branch|repo)\b|\bsquash\s+(?:and\s+force|history)\b", re.I)),
    ("discard", re.compile(r"\b(?:hard\s+reset|reset\s+--hard|reset\s+hard|discard\s+(?:the\s+|my\s+|all\s+|local\s+)?(?:local\s+)?(?:changes|work|edits)|"
                           r"git\s+clean\b|throw\s+away\s+(?:the\s+|my\s+|local\s+)?changes|drop\s+the\s+stash|clear\s+the\s+stash)", re.I)),
    ("delete", re.compile(r"\b(?:delete|remove|rm|prune|clean\s+up|cleanup|get\s+rid\s+of|wipe|purge|nuke)\b", re.I)),
    ("install", re.compile(r"\b(?:install|reinstall|uninstall|upgrade|pip\s+install|npm\s+i|brew\s+install|add\s+(?:the\s+)?(?:dep|dependency|package))\w*\b", re.I)),
    ("sudo", re.compile(r"\bsudo\b|\broot\s+(?:access|privileges?)\b|\bas\s+root\b", re.I)),
    ("ssh", re.compile(r"\bssh\b|\bscp\b|\brsync\b|\blog\s*(?:in|on)\s+(?:to\s+)?(?=[\w.-]+\b)|\bon\s+the\s+(?:remote|server|box|host)\b", re.I)),
    ("service", re.compile(r"\b(?:restart|start|stop|reload|enable|disable|kick|bounce)\s+(?:the\s+)?([\w@.-]+)(?:\s+(?:service|unit|daemon|server|timer))?", re.I)),
    ("ci", re.compile(r"\b(?:re-?run|rerun|trigger|dispatch|kick\s+off|restart|cancel)\s+(?:the\s+)?(?:failed\s+|failing\s+)?(?:ci|workflows?|jobs?|actions?|checks?|runs?|pipelines?|builds?)\b", re.I)),
    ("secret", re.compile(r"\b(?:print|show|echo|cat|read|use|set|add|rotate|export|put)\s+(?:\w+\s+){0,3}(?:tokens?|secrets?|\.env|api\s*keys?|credentials?|passwords?)\b", re.I)),
    ("network", re.compile(r"\b(?:post|put|send|upload|call|hit|submit|trigger)\s+(?:\w+\s+){0,4}(?:to|on|against)\s+(?:https?://)?([\w-]+\.[\w.-]+)", re.I)),
    ("upload", re.compile(r"\b(?:publish|share|upload)\s+(?:\w+\s+){0,3}(?:artifact|page|gist|it|this)\b|\bartifact\b", re.I)),
    ("issue", re.compile(r"\b(?:open|file|create|close|reopen|label|edit|update)\s+(?:\w+\s+){0,3}(?:issues?|labels?)\b", re.I)),
    ("comment", re.compile(r"\b(?:comment|reply|respond|post\s+(?:a\s+)?(?:comment|review|reply))\b", re.I)),
    ("pr", re.compile(r"\b(?:open|create|make|raise|file|submit|update|edit|close|draft|mark)\s+(?:\w+\s+){0,3}(?:prs?|pull\s+requests?)\b|\bgh\s+pr\s+create\b", re.I)),
    ("merge", re.compile(r"\bmerge\s+(?:(?:the|this|that|my|pr|pull\s+request|it)\s+)*(?:#?\d+)?", re.I)),
)
_WRITE_VERB = re.compile(r"\b(?:write|save|store|put|create|edit|update|modify|copy|move|mv|cp|install|add|mkdir|generate|output|"
                         r"log|append|delete|remove|rm|build|fix|change|patch|replace|overwrite|symlink|link|set\s+up|setup|"
                         r"configure|clean|wipe|touch|dump|export|place|drop|keep|record|persist|commit|sync)\b", re.I)
_PATH = re.compile(r"(?<![\w/.])(~/[^\s`'\",;)]*|/(?:home|opt|etc|usr|var|srv|mnt|media|data|tmp)/[^\s`'\",;)]*)")
_HOST_TOK = re.compile(r"\b(?:[\w.-]+@)?([a-z][\w-]*(?:\.[\w-]+)*)\b", re.I)
_PR_NUM = re.compile(r"(?:#|\bpr\s*#?|\bpull\s+request\s*#?)(\d+)\b", re.I)
_GENERIC_PKGS = re.compile(r"\b(?:deps|dependencies|dependency|requirements|packages|package|reqs|tools?|it|them|everything|all|what'?s\s+needed|missing)\b", re.I)
_STOP = {"the", "a", "an", "and", "or", "to", "into", "for", "with", "via", "it", "them", "this", "that", "please", "then",
         "now", "also", "just", "globally", "locally", "system", "user", "venv", "python", "package", "packages", "latest",
         "version", "on", "in", "from", "using", "use", "pip", "npm", "brew", "uv", "cargo", "install", "reinstall",
         "uninstall", "upgrade", "hook", "hooks", "plugin", "plugins", "live", "user's", "your", "my", "our", "its"}
AFFIRM = re.compile(r"^\W*(?:yes|yeah|yep|yup|y|sure|ok(?:ay)?|k|go\s+ahead|do\s+it|please\s+do|proceed|approved?|confirm(?:ed)?|"
                    r"lgtm|go\s+for\s+it|sounds\s+good|yes\s+please|affirmative|ship\s+it|continue|correct|right|absolutely|"
                    r"definitely|of\s+course|👍)\b", re.I)
STRONG_AFFIRM = re.compile(r"^\W*(?:yes|yep|yeah|go\s+ahead|do\s+it|please\s+do|approved?|proceed|lgtm|go\s+for\s+it)\b", re.I)
_PROPOSAL = re.compile(r"(?:want\s+me\s+to|should\s+i|shall\s+i|do\s+you\s+want\s+me\s+to|would\s+you\s+like\s+me\s+to|"
                       r"(?:ok(?:ay)?|safe)\s+to|ready\s+to|i\s+can|i\s+could|i'?ll|go\s+ahead\s+and|proceed\s+(?:with|to)|"
                       r"permission\s+to|approve\s+(?:me\s+)?to|let\s+me\s+know\s+if\s+(?:you\s+want\s+me\s+to|i\s+should))\s+"
                       r"([^?\n]{3,300})", re.I)
_CODE = re.compile(r"```(?:\w+)?\n(.*?)```|`([^`\n]{2,400})`", re.S)
_RUN_CUE = re.compile(r"\b(?:run|execute|do|use|try|type|go\s+ahead|please|you\s+can|ok(?:ay)?\s+to|feel\s+free|then)\b|^\W*`", re.I)


def _ei():
    from . import effect_inference as ei  # lazy: ~15 ms of regex compilation, only needed for user turns
    return ei


def _sentences(text: str) -> list[str]:
    return _ei()._sentences(text)


def _negated(sentence: str, start: int) -> bool:
    ei = _ei()
    return bool(_SENT_NEG.search(ei._clause_before(sentence, start)))


def _attributed(sentence: str, start: int) -> bool:
    ei = _ei()
    return bool(ei._ATTRIBUTED.search(sentence)) or any(q.start() <= start < q.end() for q in ei._QUOTED.finditer(sentence))


def _branches_near(sentence: str, start: int, end: int, default: str | None) -> list[str]:
    window = sentence[start:min(len(sentence), end + 60)]
    names = []
    for m in _BRANCH_TOK.finditer(window):
        b = m.group(1).rstrip(".,:)'\"").lower().removeprefix("origin/")
        if b and b not in _NOT_BRANCH and not b.isdigit() and (("/" in b or "-" in b or b in _PROT_WORDS or
                                                                 b == (default or "").lower())):
            names.append(b)
    if re.search(r"\bthe\s+default\s+branch\b", window, re.I) and default:
        names.append(default.lower())
    return list(dict.fromkeys(names))


def _hosts_near(sentence: str, start: int) -> list[str]:
    window = sentence[start:start + 80]
    hosts = []
    for m in re.finditer(r"\b(?:ssh|scp|rsync|on|into|to|onto|at|log\s*in\s+to|login\s+to)\s+(?:the\s+)?(?:[\w.-]+@)?([a-z][\w.-]*\d*[\w.-]*)", window, re.I):
        h = m.group(1).rstrip(".,:)").lower()
        if h not in _NOT_BRANCH and h not in ("remote", "server", "box", "host", "machine", "it", "there", "sudo"):
            hosts.append(h)
    return list(dict.fromkeys(hosts))


def _paths_in(sentence: str, ctx_home: str) -> list[str]:
    out = []
    for m in _PATH.finditer(sentence):
        p = m.group(1).rstrip(".,:")
        out.append(p)
    return out


def _pkgs_near(sentence: str, start: int) -> tuple[list[str], bool]:
    window = sentence[start:start + 120]
    generic = bool(_GENERIC_PKGS.search(window))
    words = [w.lower().strip("`'\".,:()") for w in re.split(r"\s+", window)[1:9]]
    pk = [re.split(r"[<>=!~\[@]", w)[0] for w in words if w and w not in _STOP and re.match(r"^[\w.-]+$", w)]
    return pk, generic


def extract_grants(text: str, *, source: str, turn: int, ctx: Ctx | None = None, default: str | None = None,
                   branch: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(grants, prohibitions) that one user-authored text carries. Exact scope; negation -> prohibition."""
    grants: list[dict[str, Any]] = []
    prohibitions: list[dict[str, Any]] = []
    if not isinstance(text, str) or not text.strip():
        return grants, prohibitions
    ei = _ei()
    packet = {"current_claims": [{"key": "git.branch", "value": branch}, {"key": "git.default_branch", "value": default}]}
    base = {"source": source, "turn": turn}
    # 1) effect_inference v1 scoped grants (push / merge / commit / pr / comment_issue / publish / deploy / message ...)
    try:
        inferred = ei.infer_effects(text, packet)
        for g in inferred.get("prompt_grants") or []:
            scope = dict(g.get("scope") or {})
            if scope.get("kind") == "merge":
                nums = _PR_NUM.findall(g.get("sentence") or g.get("phrase") or "")
                if nums or re.search(r"\b(?:pr|pull\s+request)\b", g.get("sentence") or "", re.I):
                    scope["prs"] = nums or "any"
            grants.append({**base, "via": "effect_inference", "phrase": g.get("phrase"), "scope": scope})
    except Exception:
        pass
    # 2) lexicon over sentences
    for sentence in _sentences(text):
        question = ei._is_question(sentence)
        for m in _NO_NOUN.finditer(sentence):
            noun = m.group(1).lower()
            kind = ("pr" if noun.startswith(("pr", "pull")) else "force" if noun.startswith("force") else
                    "push" if noun.startswith("push") else "merge" if noun.startswith("merg") else
                    "deploy" if noun.startswith("deploy") else "publish" if noun.startswith(("release", "publish")) else
                    "install" if noun.startswith("install") else "comment" if noun.startswith("comment") else
                    "issue" if noun.startswith("issue") else noun)
            prohibitions.append({**base, "phrase": m.group(0), "scope": {"kind": kind}})
        for kind, rx in LEXICON + (("push", re.compile(r"\bpush(?:es|ed|ing)?\b", re.I)),
                                   ("commit", re.compile(r"\bcommit\b", re.I)),
                                   ("deploy", re.compile(r"\b(?:deploy|redeploy)\b", re.I)),
                                   ("publish", re.compile(r"\b(?:publish|release)\b", re.I))):
            for m in rx.finditer(sentence):
                if _attributed(sentence, m.start()):
                    continue
                neg = _negated(sentence, m.start())
                if question and not neg:
                    continue
                scope: dict[str, Any] = {"kind": kind}
                if kind in _BRANCH_KINDS or kind == "delete":
                    br = _branches_near(sentence, m.start(), m.end(), default)
                    scope["branches"] = br if br else "non_default"
                if kind == "merge":
                    nums = _PR_NUM.findall(sentence[m.start():m.start() + 60])
                    if nums or re.search(r"\b(?:pr|pull\s+request)\b", sentence[m.start():m.start() + 60], re.I):
                        scope["prs"] = nums or "any"
                if kind in ("ssh", "sudo", "service"):
                    hosts = _hosts_near(sentence, m.start())
                    if kind == "ssh":
                        scope["hosts"] = hosts or "unnamed"
                    elif hosts:
                        scope["hosts"] = hosts
                if kind == "service":
                    scope["units"] = [m.group(1).lower()]
                if kind == "install":
                    pk, generic = _pkgs_near(sentence, m.start())
                    scope["packages"] = "any" if generic else pk
                if kind == "delete":
                    paths = _paths_in(sentence, "")
                    if paths:
                        scope["paths"] = paths
                    if re.search(r"\b(?:merged|stale|old|feature)\s+(?:local\s+)?branches\b|\bworktrees?\b", sentence, re.I):
                        scope["branches"] = "non_default"
                if kind == "network":
                    scope["hosts"] = [m.group(1).lower()]
                if neg:
                    if kind == "delete" and not (isinstance(scope.get("branches"), list) or scope.get("paths")):
                        continue  # "don't remove the tests" names no branch or path: nothing enforceable
                    if kind == "install" and not scope.get("packages"):
                        continue
                    if kind in ("push", "merge", "force", "pr", "deploy", "publish", "install", "sudo", "ssh", "commit",
                                "delete", "comment", "issue", "discard", "rewrite", "service", "ci", "upload"):
                        prohibitions.append({**base, "phrase": m.group(0), "scope": scope})
                    continue
                if kind in ("push", "commit", "deploy", "publish", "merge", "pr", "comment", "issue"):
                    continue  # granted (with exact scope) only through effect_inference above
                if kind == "ssh" and scope["hosts"] == "unnamed":
                    continue
                grants.append({**base, "via": "lexicon", "phrase": m.group(0), "scope": scope})
        # 3) paths the user tells us to write to (outside the repo) are in scope for writes
        if not question and _WRITE_VERB.search(sentence):
            for p in _paths_in(sentence, ""):
                i = sentence.find(p)
                if _negated(sentence, i) or _attributed(sentence, i):
                    continue
                grants.append({**base, "via": "path", "phrase": p, "scope": {"kind": "fs_outside_repo", "paths": [p]}})
    # 4) literal commands the user wrote ("run `git push origin master`")
    if ctx is not None:
        for m in _CODE.finditer(text):
            code = m.group(1) or m.group(2) or ""
            before = text[max(0, m.start() - 80):m.start()]
            sentence_before = re.split(r"[.!?\n]", before)[-1]
            if not (_RUN_CUE.search(sentence_before) or m.start() == 0 or (m.group(1) and re.search(r"\brun\b", before, re.I))):
                continue
            if _SENT_NEG.search(sentence_before) or ei._ATTRIBUTED.search(sentence_before):
                continue
            try:
                parsed = parse_tool_call("Bash", {"command": code}, ctx)
            except Exception:
                continue
            for e in parsed["privileged"]:
                grants.append({**base, "via": "literal_command", "phrase": code[:120],
                               "scope": {"kind": e["kind"], "exact_target": e["target"]}})
    return grants, prohibitions


