"""Grant extraction for the action-authority check (z0int#55): what a user-authored text authorises.

Split from ``action_authority`` so the PreToolUse hook compiles these patterns only when it has new
user turns to read. Exact scope: a grant never reaches further than the words that name it; a
negated instruction becomes a prohibition. Harness text never reaches this module.

v1 (PREREG_v1.md):

* every grant / prohibition carries ``order`` = turn + its position inside the text, so "the latest
  statement that speaks to the action wins" also holds inside one prompt;
* umbrella prohibitions ("no GitHub writes", "don't touch anything outside the repo", "read-only",
  "nothing that leaves the machine", "don't install anything", "leave main alone", "stay off gpu-3",
  "don't touch my dotfiles") expand to the effect kinds and targets their words cover, with
  ``except ...`` / ``other than ...`` clauses carved back out as later grants;
* a path is granted for writing only when it is the object of a write verb ("append results to
  LOG.md", "keep notes in ~/notes/x.md"), never merely because a sentence mentions it ("read
  ~/x/GOAL.md"); bare file names resolve against the directory of a path named in the same text;
* host lists ("ssh to gpu-2 and gpu-3"), install targets ("any packages into ~/.venvs/tools" /
  "project .venv only"), service verbs ("restart X" is not "stop X").
"""

from __future__ import annotations

import re

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing is not imported at run time (hook latency)
    from typing import Any, Iterable, Mapping  # noqa: F401


from .action_authority import _BRANCH_KINDS, _PROT_WORDS
from .action_effects import Ctx, parse_tool_call

# ------------------------------------------------------------------------------------------------ lexicon
_NEG_WORDS = (r"don'?t|dont|do\s+not|never|without|not|no|no\s+need\s+to|avoid|hold\s+off\s+(?:on|with)|stop|refrain\s+from|"
              r"quit|cease|no\s+more|nothing|mustn'?t|must\s+not|shouldn'?t|should\s+not|can'?t|cannot|may\s+not|won'?t|"
              r"forbidden|prohibited|not\s+allowed\s+to|off[- ]limits")
_SENT_NEG = re.compile(r"(?:\b(?:" + _NEG_WORDS + r")|n't)\W+(?:\w+\W+){0,3}$", re.I)
_NO_NOUN = re.compile(r"\bno\s+(?:more\s+)?(prs?|pull\s+requests?|push(?:es|ing)?|force[- ]?push(?:es|ing)?|merg(?:e|es|ing)|deploys?|"
                      r"releases?|installs?|sudo|ssh|comments?|issues?|publish(?:ing)?|commits?|restarts?|deletes?|deletions?)\b", re.I)
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
    ("rewrite", re.compile(r"\brewrit(?:e|ing)\s+(?:the\s+|its\s+|git\s+|branch\s+)*history\b|\bfilter-(?:branch|repo)\b|\bsquash\s+(?:and\s+force|history)\b|"
                           r"\b(?:interactive\s+)?rebase\s+(?:and\s+)?(?:force|then\s+force)", re.I)),
    ("discard", re.compile(r"\b(?:hard\s+reset|reset\s+--hard|reset\s+hard|discard\s+(?:the\s+|my\s+|all\s+|local\s+|any\s+|uncommitted\s+)*(?:local\s+)?(?:changes|work|edits|modifications)|"
                           r"git\s+clean\b|throw\s+away\s+(?:the\s+|my\s+|local\s+)?changes|drop\s+the\s+stash|clear\s+the\s+stash|"
                           r"(?:revert|restore)\s+(?:the\s+)?(?:working\s+tree|uncommitted\s+changes|local\s+changes))", re.I)),
    ("delete", re.compile(r"\b(?:delete|remove|rm|prune|clean\s+up|cleanup|get\s+rid\s+of|wipe|purge|nuke|clear\s+out)\b", re.I)),
    ("install", re.compile(r"\b(?:install|reinstall|uninstall|upgrade|pip\s+install|npm\s+i|brew\s+install|add\s+(?:the\s+)?(?:dep|dependency|package))\w*\b", re.I)),
    ("sudo", re.compile(r"\bsudo\b|\broot\s+(?:access|privileges?)\b|\bas\s+root\b", re.I)),
    ("ssh", re.compile(r"\bssh\b|\bscp\b|\brsync\b|\blog\s*(?:in|on)\s+(?:to\s+)?(?=[\w.-]+\b)|\bon\s+the\s+(?:remote|server|box|host)\b|"
                       r"\b(?:on|to|into|onto|from)(?=\s+(?:the\s+)?(?:host|server|box|machine|node)?\s*(?:[\w-]+@)?(?:[a-z][\w-]*-?\d+[\w-]*|[\w-]+\.(?:lab|local|internal|lan|corp)\b))", re.I)),
    ("service", re.compile(r"\b(restart|start|stop|reload|enable|disable|kick|bounce|kill)(?:s|ed|ing)?\s+(?:the\s+|any\s+)?([\w@.-]+)(?:\s+(?:user\s+)?(?:service|unit|daemon|server|timer|container|stack))?", re.I)),
    ("ci", re.compile(r"\b(?:re-?run|rerun|trigger|dispatch|kick\s+off|restart|cancel)\s+(?:the\s+)?(?:failed\s+|failing\s+)?(?:ci|workflows?|jobs?|actions?|checks?|runs?|pipelines?|builds?)\b", re.I)),
    ("secret", re.compile(r"\b(?:print|show|echo|cat|read|use|set|add|rotate|export|put|dump|look\s+at|open|display|grab|fetch|get|copy|"
                          r"check|inspect|view|grep|source|load|review|verify|compare|pull)\s+(?:\w+\s+){0,3}(?:tokens?|secrets?|\.env[\w.]*|api\s*keys?|credentials?|passwords?|keys?)\b", re.I)),
    ("network", re.compile(r"\b(?:post|put|send|upload|call|hit|submit|trigger)\s+(?:\w+\s+){0,4}(?:to|on|against)\s+(?:https?://)?([\w-]+\.[\w.-]+)", re.I)),
    ("upload", re.compile(r"\b(?:publish|share|upload|post|put)\s+(?:\w+\s+){0,5}(?:artifact|page|gist|it|this)\b|\bartifact\b|"
                          r"\b(?:as|in|into|to)\s+an?\s+(?:secret\s+|public\s+|private\s+)?gist\b", re.I)),
    ("issue", re.compile(r"\b(?:open|file|create|close|reopen|label|edit|update|add|apply|remove|tag)\s+(?:\w+\s+){0,3}(?:issues?|labels?)\b", re.I)),
    ("service", re.compile(r"\b(?:add|install|set\s+up|setup|create|schedule|edit|update|register)\s+(?:a\s+|the\s+|an?\s+)?(?:\w+\s+)?(cron)(?:tab|\s+jobs?|\s+entry)?\b()", re.I)),
    ("comment", re.compile(r"\b(?:comment|reply|respond|post\s+(?:a\s+)?(?:comment|review|reply))\b", re.I)),
    ("pr", re.compile(r"\b(?:open|create|make|raise|file|submit|update|edit|close|draft|mark)\s+(?:\w+\s+){0,3}(?:prs?|pull\s+requests?)\b|\bgh\s+pr\s+create\b", re.I)),
    ("merge", re.compile(r"\bmerge\s+(?:(?:the|this|that|my|pr|pull\s+request|it)\s+)*(?:#?\d+)?", re.I)),
)
_EXTRA_LEX = (("push", re.compile(r"\bpush(?:es|ed|ing)?\b", re.I)),
              ("commit", re.compile(r"\bcommit(?:s|ting)?\b", re.I)),
              ("deploy", re.compile(r"\b(?:deploy|redeploy)\b", re.I)),
              ("publish", re.compile(r"\b(?:publish|release)\b", re.I)))
_WRITE_VERB = re.compile(r"\b(?:write|save|store|put|create|edit|update|modify|copy|move|mv|cp|install|add|mkdir|generate|output|"
                         r"log|append|delete|remove|rm|build|fix|change|patch|replace|overwrite|symlink|link|set\s+up|setup|"
                         r"configure|clean|wipe|touch|dump|export|place|drop|keep|record|persist|commit|sync|tweak|adjust|"
                         r"rewrite|refactor|implement|apply|go|goes|lands?|download|fetch|extract|unpack|clone|work(?:\s+(?:in|on))?|"
                         r"hack(?:\s+on)?|cd\s+into)\b", re.I)
_READ_VERB = re.compile(r"\b(?:read|look\s+(?:at|in|into|through|over)|check|see|inspect|view|open|review|scan|study|consult|tail|"
                        r"cat|grep|search|reference|follow|respect|per|according\s+to|summari[sz]e|compare|diff|audit|explore|"
                        r"browse|understand|analy[sz]e|investigate|find|list|ls|show)\b", re.I)
_PATH = re.compile(r"(?<![\w/.:])(~/[^\s`'\",;)]*|/(?:home|opt|etc|usr|var|srv|mnt|media|data|tmp|Users|root|scratch|workspace|"
                   r"Library|private|nix|snap)(?:/[^\s`'\",;)]*)?|/[\w.-]+/[\w.-][^\s`'\",;)]*)")
_BARE_FILE = re.compile(r"(?<![\w/.~-])([\w][\w.-]*\.(?:md|txt|json|jsonl|log|ya?ml|toml|csv|tsv|ini|cfg|conf|sh|py|env))\b")
_PR_NUM = re.compile(r"(?:#|\bpr\s*#?|\bpull\s+request\s*#?)(\d+)\b", re.I)
_GENERIC_PKGS = re.compile(r"\b(?:deps|dependencies|dependency|requirements|packages|package|reqs|tools?|it|them|everything|all|what'?s\s+needed|"
                           r"missing|anything|whatever|any)\b", re.I)
_STOP = {"the", "a", "an", "and", "or", "to", "into", "for", "with", "via", "it", "them", "this", "that", "please", "then",
         "now", "also", "just", "globally", "locally", "system", "user", "venv", "python", "package", "packages", "latest",
         "version", "on", "in", "from", "using", "use", "pip", "npm", "brew", "uv", "cargo", "install", "reinstall",
         "uninstall", "upgrade", "hook", "hooks", "plugin", "plugins", "live", "user's", "your", "my", "our", "its", "if",
         "you", "need", "needed", "we", "is", "are", "be", "only", "any", "whatever", "anything", "project", "repo", "local"}
AFFIRM = re.compile(r"^\W*(?:yes|yeah|yep|yup|y|sure|ok(?:ay)?|k|go\s+ahead|do\s+it|please\s+do|proceed|approved?|confirm(?:ed)?|"
                    r"lgtm|go\s+for\s+it|sounds\s+good|yes\s+please|affirmative|ship\s+it|continue|correct|right|absolutely|"
                    r"definitely|of\s+course|👍)\b", re.I)
STRONG_AFFIRM = re.compile(r"^\W*(?:yes|yep|yeah|go\s+ahead|do\s+it|please\s+do|approved?|proceed|lgtm|go\s+for\s+it)\b", re.I)
NEGATIVE = re.compile(r"^\W*(?:no|nope|nah|n|don'?t|do\s+not|not\s+(?:yet|now)|hold\s+off|stop|cancel|skip|never|negative|"
                      r"neither|none)\b", re.I)
_PROPOSAL = re.compile(r"(?:want\s+me\s+to|should\s+i|shall\s+i|do\s+you\s+want\s+me\s+to|would\s+you\s+like\s+me\s+to|"
                       r"(?:ok(?:ay)?|safe)\s+to|ready\s+to|i\s+can|i\s+could|i'?ll|go\s+ahead\s+and|proceed\s+(?:with|to)|"
                       r"permission\s+to|approve\s+(?:me\s+)?to|let\s+me\s+know\s+if\s+(?:you\s+want\s+me\s+to|i\s+should))\s+"
                       r"([^?\n]{3,300})", re.I)
_CODE = re.compile(r"```(?:\w+)?\n(.*?)```|`([^`\n]{2,400})`", re.S)
_RUN_CUE = re.compile(r"\b(?:run|execute|do|use|try|type|go\s+ahead|please|you\s+can|ok(?:ay)?\s+to|feel\s+free|then)\b|^\W*`", re.I)
_EXCEPT = re.compile(r"\b(?:except(?:\s+for)?|other\s+than|apart\s+from|besides|aside\s+from|save\s+for|excluding|unless\s+it'?s|"
                     r"but\s+(?:you\s+)?(?:can|may|are\s+allowed\s+to)|but\s+(?:it'?s\s+)?(?:fine|ok(?:ay)?)\s+to)\b", re.I)

_ALL_PRIV = ("push", "force", "merge", "commit", "delete", "discard", "rewrite", "pr", "comment", "issue", "publish", "deploy",
             "ci", "external_system", "message", "upload", "install", "ssh", "sudo", "secret", "network", "service",
             "fs_outside_repo")
_GITHUB = ("push", "force", "pr", "merge", "comment", "issue", "publish", "ci", "external_system", "upload", "delete_remote")
_LEAVES = ("push", "force", "pr", "merge", "comment", "issue", "publish", "deploy", "ci", "external_system", "message", "upload",
           "network", "ssh")
_HOSTISH_STOP = {"check", "run", "see", "look", "do", "make", "fix", "get", "the", "a", "an", "it", "that", "this", "access",
                 "anywhere", "any", "anything", "remote", "server", "box", "host", "machine", "then", "and", "or", "if", "when",
                 "into", "to", "on", "at", "jobs", "logs", "status", "restart", "inspect", "read", "tail", "grab", "pull",
                 "copy", "sync", "deploy", "with", "for", "from", "using", "via", "as", "but", "so", "other", "same", "one",
                 "back", "here", "there", "now", "again", "please", "only", "also", "just", "is", "are", "be", "you", "me",
                 "we", "they", "ing", "prod's", "all", "hosts", "servers", "boxes", "machines", "either", "both", "each"}
_HOST_FILL = {"to", "into", "onto", "on", "at", "the", "host", "hosts", "box", "boxes", "server", "servers", "machine",
              "machines", "node", "nodes", "ing", "in", "either", "both", "access", "rights", "login", "permission", "permissions"}


def _ei():
    from . import effect_inference as ei  # lazy: ~15 ms of regex compilation, only needed for user turns
    return ei


def _sentences(text: str) -> list[str]:
    return _ei()._sentences(text)


def _negated(sentence: str, start: int) -> bool:
    ei = _ei()
    return bool(_SENT_NEG.search(ei._clause_before(sentence, start)))


_ATTRIB = re.compile(r"\b(?:says?|said|wrote|suggest(?:s|ed)|recommend(?:s|ed)|told\s+(?:me|us)|wants?\s+(?:us|me|you)\s+to|"
                     r"asked\s+(?:us\s+|me\s+)?to|according\s+to|claims?|instructs?|tells\s+(?:you|us|me)\s+to)\b", re.I)


def _attributed(sentence: str, start: int) -> bool:
    """Instruction attributed to someone else ('CI says push', 'the README says run ...') or quoted."""
    ei = _ei()
    return bool(_ATTRIB.search(sentence[max(0, start - 80):start])) or \
        any(q.start() <= start < q.end() for q in ei._QUOTED.finditer(sentence))


def _branches_near(sentence: str, start: int, end: int, default: str | None) -> list[str]:
    window = sentence[start:min(len(sentence), end + 60)]
    names = []
    for w in re.findall(r"[\w./-]+", window[:end - start + 40]):
        wl = w.lower().rstrip(".,:")
        if wl.startswith("origin/"):
            wl = wl[len("origin/"):]
        if wl in _PROT_WORDS or wl == (default or "").lower() or re.match(r"^(?:feat|feature|fix|bugfix|hotfix|release|chore|exp|"
                                                                          r"wip|dev|test|docs|refactor|study|integrate|nightly-fix)/[\w./-]+$", wl):
            names.append(wl)
    for m in _BRANCH_TOK.finditer(window):
        b = m.group(1).rstrip(".,:)'\"").lower().removeprefix("origin/")
        if b and b not in _NOT_BRANCH and not b.isdigit() and (("/" in b or "-" in b or b in _PROT_WORDS or
                                                                 b == (default or "").lower())):
            names.append(b)
    if re.search(r"\bthe\s+default\s+branch\b", window, re.I) and default:
        names.append(default.lower())
    return list(dict.fromkeys(names))


def _hosts_after(sentence: str, pos: int) -> list[str]:
    """Host names right after an ssh-ish verb: 'ssh to gpu-2 and gpu-3', 'ssh'ing into 0', 'on build-01'."""
    toks = re.findall(r"[\w@.:-]+|,|&|/", sentence[pos:pos + 120])
    hosts: list[str] = []
    expect_more = True
    for t in toks[:14]:
        tl = t.lower().strip(".:")
        if tl in (",", "&", "/", "and", "or"):
            if hosts:
                expect_more = True
            continue
        if tl in _HOST_FILL:
            continue
        if not expect_more:
            break
        h = tl.split("@")[-1]
        hostish = bool(re.match(r"^[a-z0-9][\w.-]*$", h)) and h not in _HOSTISH_STOP and h not in _NOT_BRANCH and \
            (bool(re.search(r"[\d.-]", h)) or len(hosts) > 0 or True)
        if not hostish:
            break
        hosts.append(h)
        expect_more = False
    return list(dict.fromkeys(hosts))


def _paths_in(sentence: str) -> list[str]:
    return [m.group(1).rstrip(".,:") for m in _PATH.finditer(sentence)]


def _pkgs_near(sentence: str, start: int) -> tuple[list[str], bool]:
    window = sentence[start:start + 120]
    generic = bool(_GENERIC_PKGS.search(window[:60]))
    words = [w.lower().strip("`'\".,:()") for w in re.split(r"\s+", window)[1:9]]
    pk = []
    for w in words:
        if w in ("into", "in", "to", "on", "for", "globally", "locally", "with", "using", "via") or w.startswith(("~", "/", ".")):
            break
        if w and w not in _STOP and re.match(r"^[\w.-]+$", w):
            pk.append(re.split(r"[<>=!~\[@]", w)[0])
    return pk, generic


def _install_env_scope(sentence: str, start: int) -> dict[str, Any]:
    window = sentence[start:start + 160]
    m = re.search(r"\b(?:into|in|to|under)\s+(?:the\s+)?((?:~|/)[^\s,;)]+)", window)
    if m:
        return {"interp_paths": [m.group(1).rstrip(".,:")]}
    if re.search(r"\b(?:project|repo(?:sitory)?|local|the)\s*(?:'s\s+)?(?:\.?venv|virtualenv|node_modules|environment)\b|\binto\s+\.venv\b|"
                 r"\blocally\b|\bproject[- ]local\b|\bvenv\s+only\b|\.venv\s+only", window, re.I):
        return {"env": "local"}
    return {}


def _antecedent_unit(before: str) -> str | None:
    """'check the ingest-worker status; if it is down restart it' -> ingest-worker."""
    cands = re.findall(r"\b([a-z][\w@]*(?:[-.@][\w]+)+)(?:\s+(?:user\s+)?(?:service|unit|daemon|worker|timer|container|server))?\b|"
                       r"\b([a-z][\w-]*)\s+(?:user\s+)?(?:service|unit|daemon|timer|container)\b", before[-300:], re.I)
    names = [a or b for a, b in cands if (a or b) and not re.match(r"^(?:e\.g|i\.e|\w+\.(?:md|py|json|txt|log|sh|toml|ya?ml))$", (a or b), re.I)]
    return names[-1].lower() if names else None


def _service_verbs(verb: str) -> list[str]:
    v = verb.lower()
    return {"restart": ["restart", "start", "reload", "try-restart", "reload-or-restart", "kickstart", "up"],
            "start": ["start", "restart", "up", "kickstart", "load", "bootstrap"],
            "stop": ["stop", "kill", "down", "unload", "bootout"], "reload": ["reload", "reload-or-restart"],
            "enable": ["enable", "start", "load"], "disable": ["disable", "stop", "unload"],
            "kick": ["restart", "start", "kickstart"], "bounce": ["restart", "start", "kickstart"],
            "kill": ["kill", "stop"]}.get(v, [v])


def _order(turn: float, text: str, idx: int) -> float:
    return round(float(turn) + 0.9 * (max(0, idx) / (len(text) + 1)), 6)


# ------------------------------------------------------------------------------------------------ umbrellas
_U_GITHUB = re.compile(r"\b(?:github|gh|remote|upstream|origin)\s+(?:writes?|changes?|mutations?|actions?|activity|operations?|side[- ]effects?)\b|"
                       r"\b(?:write|writing|push(?:ing)?|post(?:ing)?|chang(?:e|ing)|touch(?:ing)?)\s+(?:anything\s+)?(?:to|on)\s+(?:github|gh|the\s+remote)\b|"
                       r"\bnothing\s+(?:on|to)\s+(?:github|the\s+remote)\b|\b(?:any(?:thing)?|stuff)\s+(?:to|on)\s+github\b", re.I)
_U_OUTSIDE = re.compile(r"\b(?:anything|stuff|files?|paths?|things?|dirs?|directories)\s+outside\s+(?:of\s+)?(?:the\s+|this\s+|my\s+|your\s+)?"
                        r"(?:repo|repository|project|worktree|workspace|working\s+(?:dir|directory|tree)|checkout|directory|dir|folder|sandbox)\b|"
                        r"\boutside\s+(?:of\s+)?(?:the\s+|this\s+)?(?:repo|repository|project|worktree|working\s+directory)\b", re.I)
_U_STAY_IN = re.compile(r"\b(?:stay|keep\s+(?:it|everything|all\s+(?:changes|writes|edits))|work\s+only|only\s+(?:touch|edit|modify|change|write)"
                        r"(?:\s+files)?)\s+(?:inside|within|in)\s+(?:the\s+|this\s+)?(?:repo|repository|project|worktree|working\s+directory)\b", re.I)
_U_READONLY = re.compile(r"\bread[- ]only\b|\b(?:don'?t|do\s+not|never)\s+(?:change|modify|touch|edit|write)\s+anything\b|"
                         r"\bno\s+(?:changes|edits|modifications|writes)\b|\blook\s+but\s+don'?t\s+touch\b|"
                         r"\b(?:don'?t|do\s+not)\s+make\s+any\s+changes\b|\bjust\s+(?:investigate|look|read|explore|report)\b[^.;\n]*\b(?:don'?t|do\s+not|no)\s+(?:change|fix|edit)", re.I)
_U_LEAVES = re.compile(r"\b(?:nothing|anything)\s+that\s+(?:leaves|touches|reaches|goes\s+(?:out|off))\s+(?:the|this|my)\s+(?:machine|box|laptop|computer|network|host)\b|"
                       r"\bno\s+(?:external|outbound|remote|network)\s+(?:calls?|requests?|writes?|side[- ]effects?|access|traffic|changes|mutations|actions)\b|"
                       r"\b(?:offline|local)[- ]only\b|\bno\s+network\b|\bdon'?t\s+(?:hit|call|touch)\s+(?:any\s+)?(?:external|remote)\s+(?:apis?|services?|systems?)\b", re.I)
_U_INSTALL = re.compile(r"\b(?:don'?t|do\s+not|never|no)\s+(?:\w+\s+){0,2}install(?:ing)?\s+(?:anything|any\s+(?:new\s+)?(?:packages?|deps|dependencies|tools?)|stuff)\b|"
                        r"\bno\s+(?:new\s+)?(?:installs?|dependencies|deps|packages)\b|\bnothing\s+(?:new\s+)?(?:gets\s+)?installed\b", re.I)
_U_DELETE = re.compile(r"\b(?:don'?t|do\s+not|never)\s+(?:delete|remove|rm|wipe|destroy)\s+(?:anything|any\s+(?:files?|data|dirs?|directories|branches|work))\b|"
                       r"\bno\s+(?:deletes|deletions|deleting)\b", re.I)
_U_SERVICE = re.compile(r"\b(?:don'?t|do\s+not|never)\s+(?:restart|stop|start|touch|bounce|kill|reload)\s+(?:any(?:thing)?|the)\s+(?:running\s+)?(?:services?|daemons?|units?|containers?|servers?)\b|"
                        r"\bno\s+(?:service\s+)?restarts\b|\bleave\s+(?:the\s+)?(?:services?|daemons?|containers?)\s+(?:alone|running|as\s+is)\b", re.I)
_U_SECRET = re.compile(r"\b(?:don'?t|do\s+not|never)\s+(?:read|print|show|cat|echo|open|look\s+at|touch|dump|log|display|access)\s+(?:any\s+|the\s+|my\s+)?"
                       r"(?:secrets?|tokens?|credentials?|\.env\w*|keys?|passwords?|api\s*keys?)\b", re.I)
_U_TOUCH = re.compile(r"\b(?:don'?t|do\s+not|never)\s+(?:touch|modify|change|mess\s+with|write\s+to|edit|go\s+near)\s+(?:my\s+|the\s+|any\s+)?([~/][^\s,;)]+|[\w./@-]+)|"
                      r"\b(?:leave|keep\s+your\s+hands\s+off|hands\s+off|stay\s+(?:off|away\s+from)|keep\s+(?:off|away\s+from))\s+(?:of\s+)?(?:my\s+|the\s+)?([~/][^\s,;)]+|[\w./@-]+)", re.I)


def _touch_target_scopes(t: str, default: str | None) -> list[dict[str, Any]] | None:
    raw = t.rstrip(".,:)'\"")
    tl = raw.lower().removeprefix("origin/")
    if tl in ("dotfiles", "dotfile", "configs", "config", "home", "shell", "rc"):
        if tl in ("dotfiles", "dotfile"):
            return [{"kind": "fs_outside_repo", "dotfiles": True}, {"kind": "delete", "dotfiles": True}]
        return None
    if raw.startswith(("~", "/")):
        return [{"kind": k, "paths": [raw]} for k in ("fs_outside_repo", "delete", "secret")]
    if tl in _PROT_WORDS or "/" in tl or tl == (default or "").lower() or tl in ("main", "master"):
        return [{"kind": k, "branches": [tl]} for k in ("push", "force", "merge", "commit", "rewrite", "delete")]
    if re.match(r"^[a-z0-9][\w.-]*$", tl) and (re.search(r"\d|-|\.", tl) or tl in ("prod", "production", "staging")) \
            and tl not in _NOT_BRANCH:
        return [{"kind": k, "hosts": [tl]} for k in ("ssh", "sudo", "service", "deploy")]
    return None


def _umbrellas(sentence: str, default: str | None) -> list[tuple[int, str, list[dict[str, Any]]]]:
    """(position, phrase, scopes) of every umbrella prohibition in a sentence."""
    out: list[tuple[int, str, list[dict[str, Any]]]] = []

    def neg_near(start: int) -> bool:
        before = sentence[max(0, start - 45):start]
        return bool(re.search(r"(?:\b(?:" + _NEG_WORDS + r")|n't)\b(?:\W+\w+){0,4}\W*$", before, re.I))

    for m in _U_GITHUB.finditer(sentence):
        if neg_near(m.start()) or re.match(r"^\s*nothing", m.group(0), re.I) or re.search(r"\bno\b", sentence[max(0, m.start() - 4):m.start()], re.I):
            out.append((m.start(), m.group(0), [{"kind": k, "github": True} for k in _GITHUB]))
    for m in _U_OUTSIDE.finditer(sentence):
        if neg_near(m.start()) or re.search(r"\b(?:only|just)\b", sentence[max(0, m.start() - 30):m.start()], re.I):
            out.append((m.start(), m.group(0), [{"kind": "fs_outside_repo"}, {"kind": "delete", "outside_repo": True}]))
    for m in _U_STAY_IN.finditer(sentence):
        out.append((m.start(), m.group(0), [{"kind": "fs_outside_repo"}, {"kind": "delete", "outside_repo": True}]))
    for m in _U_READONLY.finditer(sentence):
        if re.search(r"read[- ]only\s+(?:access\s+)?(?:token|key|mount|file ?system|fs|volume|mode\s+for\s+the|replica|user|api)", sentence[m.start():m.start() + 40], re.I):
            continue
        out.append((m.start(), m.group(0), [{"kind": k} for k in _ALL_PRIV] + [{"kind": "write"}]))
    for m in _U_LEAVES.finditer(sentence):
        out.append((m.start(), m.group(0), [{"kind": k} for k in _LEAVES]))
    for m in _U_INSTALL.finditer(sentence):
        out.append((m.start(), m.group(0), [{"kind": "install"}]))
    for m in _U_DELETE.finditer(sentence):
        out.append((m.start(), m.group(0), [{"kind": "delete"}, {"kind": "discard"}]))
    for m in _U_SERVICE.finditer(sentence):
        out.append((m.start(), m.group(0), [{"kind": "service"}]))
    for m in _U_SECRET.finditer(sentence):
        out.append((m.start(), m.group(0), [{"kind": "secret"}]))
    for m in _U_TOUCH.finditer(sentence):
        tgt = m.group(1) or m.group(2) or ""
        if m.group(0).lower().startswith("leave") and not re.search(r"^\W*(?:\w+\W+){0,2}(?:alone|untouched|as\s+is)\b",
                                                                     sentence[m.end():m.end() + 30], re.I):
            continue
        sc = _touch_target_scopes(tgt, default)
        if sc:
            out.append((m.start(), m.group(0), sc))
    return out


# ------------------------------------------------------------------------------------------------ paths
_CLAUSE_SPLIT = re.compile(r"[;:]|,\s*(?:then|and\s+then)\b|\bthen\b|\band\s+then\b|,\s+(?=(?:read|check|look|see|write|append|save|log|keep|commit|push|run)\b)", re.I)


def _path_grants(text: str, sentence: str, sent_off: int, base: dict, turn: float, neg_sentence: bool,
                 grants: list, prohibitions: list, mentioned_dirs: list[str]) -> None:
    """Write grants for paths that are the object of a write verb (and prohibitions for negated ones)."""
    # clause boundaries inside the sentence
    cuts = [0] + [m.end() for m in _CLAUSE_SPLIT.finditer(sentence)] + [len(sentence) + 1]
    occ: list[tuple[int, str, bool]] = [(m.start(), m.group(1).rstrip(".,:"), True) for m in _PATH.finditer(sentence)]
    taken = [(a, a + len(p)) for a, p, _ in occ]
    for m in _BARE_FILE.finditer(sentence):
        if any(a <= m.start() < b for a, b in taken):
            continue
        occ.append((m.start(), m.group(1), False))
    for pos, p, absolute in occ:
        c0 = max(c for c in cuts if c <= pos)
        clause_before = sentence[c0:pos]
        wv = [(mm.end(), "w") for mm in _WRITE_VERB.finditer(clause_before)]
        rv = [(mm.end(), "r") for mm in _READ_VERB.finditer(clause_before)]
        verbs = sorted(wv + rv)
        if not verbs or verbs[-1][1] != "w":
            continue
        if _attributed(sentence, pos):
            continue
        target = p
        if not absolute:
            same = [d for d in mentioned_dirs if d.rsplit("/", 1)[-1] == p]
            dirs = list(dict.fromkeys(d.rsplit("/", 1)[0] if "." in d.rsplit("/", 1)[-1] else d.rstrip("/") for d in mentioned_dirs))
            if same:
                target = same[0]
            elif len(dirs) == 1:
                target = dirs[0] + "/" + p
            else:
                continue
        neg = _negated(sentence, pos) or bool(_SENT_NEG.search(clause_before[-60:]))
        rec = {**base, "turn": _order(turn, text, sent_off + pos), "via": "path", "phrase": target,
               "scope": {"kind": "fs_outside_repo", "paths": [target]}}
        if neg:
            prohibitions.append(rec)
            prohibitions.append({**rec, "scope": {"kind": "delete", "paths": [target]}})
        else:
            grants.append(rec)
            if re.search(r"\b(?:delete|remove|rm|wipe|clean|clear|purge|prune|nuke)\b", clause_before[-60:], re.I):
                grants.append({**rec, "scope": {"kind": "delete", "paths": [target]}})


# ------------------------------------------------------------------------------------------------ main
def extract_grants(text: str, *, source: str, turn: float, ctx: Ctx | None = None, default: str | None = None,
                   branch: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(grants, prohibitions) that one user-authored text carries. Exact scope; negation -> prohibition."""
    grants: list[dict[str, Any]] = []
    prohibitions: list[dict[str, Any]] = []
    if not isinstance(text, str) or not text.strip():
        return grants, prohibitions
    ei = _ei()
    packet = {"current_claims": [{"key": "git.branch", "value": branch}, {"key": "git.default_branch", "value": default}]}
    base = {"source": source, "turn": turn}
    sentences = _sentences(text)
    offsets: list[int] = []
    cur = 0
    for snt in sentences:
        k = text.find(snt, cur)
        offsets.append(k if k >= 0 else cur)
        cur = (k + len(snt)) if k >= 0 else cur
    # 1) effect_inference v1 scoped grants (push / merge / commit / pr / comment_issue / publish / deploy / message ...)
    try:
        inferred = ei.infer_effects(text, packet)
        for g in inferred.get("prompt_grants") or []:
            scope = dict(g.get("scope") or {})
            snt = g.get("sentence") or ""
            if scope.get("kind") == "merge":
                nums = _PR_NUM.findall(snt or g.get("phrase") or "")
                if nums or re.search(r"\b(?:pr|pull\s+request)\b", snt, re.I):
                    scope["prs"] = nums or "any"
            k = text.find(snt[:80]) if snt else -1
            pk = text.find(g.get("phrase") or "", max(0, k)) if k >= 0 else -1
            o = _order(turn, text, pk if pk >= 0 else max(k, 0))
            if scope.get("kind") in ("push", "force"):
                remotes = re.findall(r"\b(?:to|on|into)\s+(?:the\s+)?(origin|upstream|my\s+fork|fork|[\w-]+(?=\s+remote\b))", snt, re.I)
                if remotes:
                    scope["remotes"] = sorted({x for r in remotes for x in
                                               (("origin", "fork") if "fork" in r.lower() else (r.lower(),))})
                tags = re.findall(r"\b(v\d+(?:\.\d+)+(?:[-.][\w.]+)?)\b", snt)
                if tags or re.search(r"\btags?\b", snt, re.I):
                    grants.append({**base, "turn": o, "via": "effect_inference", "phrase": g.get("phrase"),
                                   "scope": {"kind": "publish", "tags": tags or "any"}})
            grants.append({**base, "turn": o, "via": "effect_inference", "phrase": g.get("phrase"), "scope": scope})
    except Exception:
        pass
    mentioned = [p for p in _paths_in(text) if not p.endswith("/")] + [p.rstrip("/") + "/" for p in _paths_in(text) if p.endswith("/")]
    mentioned = [p.rstrip("/") for p in mentioned]
    # 2) lexicon + umbrellas over sentences
    for sentence, soff in zip(sentences, offsets):
        question = ei._is_question(sentence)
        exc = _EXCEPT.search(sentence)
        for pos, phrase, scopes in _umbrellas(sentence, default):
            if exc and exc.start() < pos:
                continue  # the umbrella sits inside the carve-out clause itself
            # one record per distinct non-kind scope: {"kind": k1, "kinds": [k1, k2, ...], ...}
            groups: dict[str, dict[str, Any]] = {}
            for sc in scopes:
                key = repr(sorted((k, repr(v)) for k, v in sc.items() if k != "kind"))
                g = groups.setdefault(key, {**sc, "kinds": []})
                g["kinds"].append(sc["kind"])
            for g in groups.values():
                if len(g["kinds"]) == 1:
                    g.pop("kinds")
                prohibitions.append({**base, "turn": _order(turn, text, soff + pos), "phrase": phrase, "via": "umbrella", "scope": g})
        for m in _NO_NOUN.finditer(sentence):
            noun = m.group(1).lower()
            kind = ("pr" if noun.startswith(("pr", "pull")) else "force" if noun.startswith("force") else
                    "push" if noun.startswith("push") else "merge" if noun.startswith("merg") else
                    "deploy" if noun.startswith("deploy") else "publish" if noun.startswith(("release", "publish")) else
                    "install" if noun.startswith("install") else "comment" if noun.startswith("comment") else
                    "issue" if noun.startswith("issue") else "commit" if noun.startswith("commit") else
                    "service" if noun.startswith("restart") else "delete" if noun.startswith("delet") else noun)
            prohibitions.append({**base, "turn": _order(turn, text, soff + m.start()), "phrase": m.group(0), "scope": {"kind": kind}})
        for kind, rx in LEXICON + _EXTRA_LEX:
            for m in rx.finditer(sentence):
                if _attributed(sentence, m.start()):
                    continue
                if exc and exc.start() < m.start():  # inside an "except ..." carve-out: negation is judged from there
                    neg = bool(_SENT_NEG.search(sentence[exc.end():m.start()]))
                else:
                    neg = _negated(sentence, m.start())
                if question and not neg:
                    continue
                in_exc = bool(exc and exc.start() < m.start() and not neg)
                order = _order(turn, text, soff + m.start()) + (0.00001 if in_exc else 0)
                scope: dict[str, Any] = {"kind": kind}
                if kind in _BRANCH_KINDS or kind == "delete":
                    br = _branches_near(sentence, m.start(), m.end(), default)
                    scope["branches"] = br if br else "non_default"
                if kind == "merge":
                    nums = _PR_NUM.findall(sentence[m.start():m.start() + 60])
                    if nums or re.search(r"\b(?:pr|pull\s+request)\b", sentence[m.start():m.start() + 60], re.I):
                        scope["prs"] = nums or "any"
                if kind in ("ssh", "sudo", "service"):
                    hosts = _hosts_after(sentence, m.end())
                    if kind == "ssh":
                        scope["hosts"] = hosts or "unnamed"
                    elif hosts and kind == "sudo":
                        scope["hosts"] = hosts
                if kind == "service" and m.group(1).lower() == "cron":
                    scope["units"] = ["crontab"]
                elif kind == "service":
                    unit = m.group(2).lower().rstrip(".,:;!?")
                    if unit in ("it", "them", "that", "this"):
                        unit = _antecedent_unit(text[:soff + m.start()]) or unit
                    if unit in ("the", "a", "any", "it", "them", "all", "everything", "services", "service", "that", "this"):
                        if not neg:
                            continue
                        unit = ""
                    scope["units"] = [unit] if unit else []
                    scope["verbs"] = _service_verbs(m.group(1))
                    hosts = re.findall(r"\bon\s+(?:the\s+)?(?:host\s+)?([a-z0-9][\w.-]*)", sentence[m.end():m.end() + 40], re.I)
                    hosts = [h.lower() for h in hosts if h.lower() not in _HOSTISH_STOP]
                    if hosts:
                        scope["hosts"] = hosts
                if kind == "install":
                    pk, generic = _pkgs_near(sentence, m.start())
                    scope["packages"] = "any" if generic else pk
                    scope.update(_install_env_scope(sentence, m.start()))
                if kind == "delete":
                    paths = _paths_in(sentence[m.start():])
                    if paths:
                        scope["paths"] = paths
                    if re.search(r"\b(?:merged|stale|old|feature)\s+(?:local\s+)?branches\b|\bworktrees?\b", sentence, re.I):
                        scope["branches"] = "non_default"
                if kind == "network":
                    scope["hosts"] = [m.group(1).lower()]
                if kind == "secret":
                    sp = _paths_in(sentence) + [b for b in re.findall(r"(?<![\w/])(\.env[\w.-]*|[\w-]+\.(?:pem|key|env))\b", sentence)]
                    if sp:
                        scope["paths"] = sp
                if neg:
                    if kind == "delete" and not (isinstance(scope.get("branches"), list) or scope.get("paths")):
                        continue  # "don't remove the tests" names no branch or path: nothing enforceable
                    if kind == "install" and not scope.get("packages"):
                        continue
                    if kind == "service" and not scope.get("units") and not re.search(r"\bany\b|\ball\b", sentence[m.start():m.end() + 10], re.I):
                        continue
                    if kind in ("push", "merge", "force", "pr", "deploy", "publish", "install", "sudo", "ssh", "commit",
                                "delete", "comment", "issue", "discard", "rewrite", "service", "ci", "upload", "secret"):
                        if kind == "ssh" and scope.get("hosts") == "unnamed":
                            scope.pop("hosts")
                        prohibitions.append({**base, "turn": order, "phrase": m.group(0), "scope": scope})
                    continue
                if kind in ("push", "commit", "deploy", "publish", "merge", "pr", "comment"):
                    if not in_exc:
                        continue  # granted (with exact scope) only through effect_inference above
                    if kind in ("push", "commit") and scope.get("branches") is None:
                        scope["branches"] = "non_default"
                if kind == "ssh" and scope["hosts"] == "unnamed":
                    continue
                grants.append({**base, "turn": order, "via": "lexicon" + ("_except" if in_exc else ""), "phrase": m.group(0), "scope": scope})
        # 3) paths that are the object of a write verb
        if not question:
            _path_grants(text, sentence, soff, base, turn, False, grants, prohibitions, mentioned)
        # 4) a later "you can edit files now" lifts a read-only hold on in-repo writes
        if not question and re.search(r"\b(?:go\s+ahead|you\s+(?:can|may)|now|please|ok(?:ay)?)\b[^.;\n]{0,40}\b(?:fix|edit|change|implement|write|apply|make\s+the\s+change|modify|update)\b", sentence, re.I) \
                and not _SENT_NEG.search(sentence[:40]):
            grants.append({**base, "turn": _order(turn, text, soff), "via": "lift_readonly", "phrase": sentence[:60], "scope": {"kind": "write"}})
    # 4b) naming a service restart / a system package install implies the sudo that command needs
    for g in list(grants):
        sc = g.get("scope") or {}
        if sc.get("kind") == "service" and g.get("via", "").startswith("lexicon"):
            grants.append({**g, "via": "implied_sudo", "scope": {"kind": "sudo", "for": ["systemctl", "service", "launchctl", "crontab",
                                                                                       "docker", "podman"],
                                                                 **({"hosts": sc["hosts"]} if sc.get("hosts") else {})}})
        if sc.get("kind") == "install" and g.get("via", "").startswith("lexicon") and sc.get("env") != "local":
            grants.append({**g, "via": "implied_sudo", "scope": {"kind": "sudo", "for": ["apt", "apt-get", "dnf", "yum", "pacman",
                                                                                       "zypper", "apk", "snap", "pip", "pip3",
                                                                                       "npm", "make"]}})
    # 5) literal commands the user wrote ("run `git push origin master`")
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
                grants.append({**base, "turn": _order(turn, text, m.start()), "via": "literal_command", "phrase": code[:120],
                               "scope": {"kind": e["kind"], "exact_target": e["target"]}})
    return grants, prohibitions


def proposal_to_prohibitions(assistant_text: str, turn: float, ctx: Ctx | None, default: str | None, branch: str | None,
                             reply: str) -> list[dict[str, Any]]:
    """'no' / 'not yet' to "Want me to push feat/x?" forbids exactly what was proposed."""
    tail = assistant_text[-1500:]
    proposals = [m.group(1) for m in _PROPOSAL.finditer(tail)][-2:]
    out = []
    for prop in proposals:
        g, _ = extract_grants(prop.strip() + ".", source="ask_answer", turn=turn, ctx=ctx, default=default, branch=branch)
        g2 = _proposal_kinds(prop, turn, default)
        for x in g + g2:
            out.append({"source": "ask_answer", "turn": turn, "phrase": f"declined: {prop[:80]}", "reply": reply[:60],
                        "scope": x["scope"]})
    return out


def _proposal_kinds(prop: str, turn: float, default: str | None) -> list[dict[str, Any]]:
    """Kinds named by a proposal even where effect_inference would not grant them (push/pr/merge/deploy/...)."""
    out = []
    for kind, rx in LEXICON + _EXTRA_LEX:
        m = rx.search(prop)
        if not m:
            continue
        scope: dict[str, Any] = {"kind": kind}
        if kind in _BRANCH_KINDS:
            br = _branches_near(prop, m.start(), m.end(), default)
            if br:
                scope["branches"] = br
        out.append({"scope": scope})
    return out
