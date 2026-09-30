"""Effect inference v0 (z0int#55): which effect classes does a request need? read < write < privileged.

The deterministic DecisionOpportunity gate can only ask for authority it can see. Today Claude Code
emits every prompt as ``effects=("read",)``, so a turn that will push to origin gates ACT. This module
maps the request (+ packet facts) to a subset of ``decision_opportunity.EFFECTS`` with evidence:
which rule, which phrase, which fact.

Deterministic first: a lexicon over sentences, plus packet facts (e.g. a commit while checked out on
the default branch is privileged). Conservative: ambiguous write-vs-privileged phrases ("ship it",
"sync with origin") resolve to privileged, so the gate ASKs instead of ACTing. Questions *about* an
action ("should we merge X?") need only read, and negated actions ("don't push") are suppressed.

Nothing here grants authority. Phrases in which the user pre-approves an action ("you have my ok")
are *recorded* as ``user_grant_phrases`` but never applied; authority widens only through AODL or
``with_authority_grant``. The optional local-SLM tie-breaker is shadow-only: it is recorded next to
the deterministic result and never changes ``effects``.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping

SCHEMA = "z0int.effect_inference.v1"
ORDER = {"read": 0, "write": 1, "privileged": 2}

_B = r"[^.?!\n]{0,40}"  # same-sentence gap
# Default/protected branch names (plus the packet's git.default_branch at run time).
PROTECTED = ("main", "master", "trunk", "develop", "dev", "nightly", "production", "prod", "stable", "gh-pages")
_PROT = r"(?:main|master|trunk|develop|dev|nightly|production|prod|stable|gh-pages|release/[\w.-]+|the\s+default\s+branch)"
_NEG = re.compile(r"(?:\b(?:don'?t|dont|do not|never|without|not|no|no need to|avoid|hold off on)|n't)\W+(?:\w+\W+){0,2}$", re.I)

# AODL tests/validate.py PRIVILEGED: payment, finance, pay, transfer, wallet, sudo, install, secret, credential.
RULES: tuple[tuple[str, str, str], ...] = (
    # ---- privileged: leaves the working copy, shared/irreversible state, or AODL-privileged capability
    ("git_push", "privileged",
     r"\b(?:git\s+)?push(?:es|ed|ing)?\b(?!\s+(?:the\s+|our\s+|its\s+)?(?:limits?|frontier|envelope|boundar\w*|system|back|out\b|forward|harder|through))"),
    ("force_or_hard_reset", "privileged", r"--force\b|\bforce[- ]?push|\bforce[- ]?(?:update|delete)\b|--hard\b|\breset\s+hard\b|\brewrite\s+(?:the\s+)?history"),
    ("merge", "privileged", r"\bmerg(?:e|es|ed|ing)\b(?!\s+conflicts?)"),
    ("to_default_branch", "privileged",
     r"\b(?:forward|fast[- ]forward|land|promote|move|bring|ship)\b" + _B + r"\b(?:to|into|onto)\s+(?:main|master|nightly|dev|trunk|the default branch)\b"),
    ("pull_request", "privileged",
     r"\b(?:open|create|file|submit|raise|make|send|put\s+up)\s+(?:up\s+)?(?:an?\s+|the\s+)?(?:draft\s+)?(?:pr|pull\s+request|merge\s+request|mr)s?\b|\bgh\s+pr\s+(?:create|merge|comment|close|review|edit)"),
    ("comment_or_issue", "privileged",
     r"\b(?:comment|reply|respond)\s+(?:on|to|in)\b|\b(?:open|create|file|close|reopen|label|assign)\s+(?:an?\s+|the\s+)?(?:new\s+)?(?:gh\s+|github\s+)?issues?\b|\bclose\s+(?:#|issue\s*#?)\d+|\bgh\s+issue\b"),
    ("publish_deploy_release", "privileged",
     r"(?<![#/@-])\b(?:publish|deploy|redeploy|release|roll\s?out)(?:es|ed|ing|s)?\b(?!\s+(?:notes?|plan|process|checklist|schedule|cadence|candidate|branch|train|workflow))"
     r"|\bgo\s+live\b|\bcut\s+(?:an?\s+)?(?:new\s+)?(?:release|tag|version)|\btag\s+(?:it\b|v?\d)"),
    ("send_message", "privileged",
     r"\b(?:post|send|tweet|email|dm|message|notify|announce|ping)\b" + _B + r"(?:\bslack\b|\bdiscord\b|\bchannel\b|#[a-z][\w-]*|\bmailing\s+list\b|\bteam\b|\beveryone\b)"
     r"|\bsend\s+(?:an?\s+)?(?:email|message|dm)\b"),
    ("destructive_delete", "privileged",
     r"\b(?:delete|remove|prune|drop|destroy|wipe|purge|nuke)\b[^.?!\n]{0,30}\b(?:remote|branch(?:es)?|tags?|releases?|repo(?:sitory)?|database|tables?|bucket|cluster|untracked)\b"
     r"|\brm\s+-\w*r\w*f?\b|\bgit\s+clean\s+-\w*f|\b(?:wipe|purge|nuke|destroy)\b"),
    ("aodl_privileged", "privileged",
     r"\bsudo\b|\bas\s+(?:root|admin)\b|\badmin\s+(?:commands?|rights|access)\b|\bchmod\b|\bchown\b|\bsystemctl\b"
     r"|\binstall(?:s|ed|ing)?\b|\bpayments?\b|\bpay\b|\btransfer\s+(?:funds|money|\$)|\bwallet\b|\bfinance\b"
     r"|\bsecrets?\b|\bcredentials?\b|\bapi[- ]?keys?\b|\b(?:api|auth|access|github|gh|bearer|oauth)\s+tokens?\b|\bpasswords?\b"
     r"|\bssh(?:'?ing|ed|es)?\b|\bauthorized_keys\b"),
    ("external_system", "privileged",
     r"\b(?:set\s*up|setup|configure|create|enable|disable|change|update|modify)\b" + _B +
     r"\b(?:github\s+(?:projects?|actions?\s+settings|settings|app)|gh\s+projects?|projects?\s+v2|linear|jira|branch\s+protection|webhooks?|dns|k8s|kubernetes|cluster|repo\s+settings|org\s+settings)\b"),
    ("remote_env", "privileged", r"\b(?:on|to|in|against|into)\s+(?:prod|production|staging)\b"),
    # v1: commit that names a default/protected branch as its target
    ("commit_protected", "privileged",
     r"\bcommit\w*\b" + _B + r"\b(?:to|on|onto|into)\s+(?:origin/)?(?:" + _PROT + r")\b"),
    # v1: package managers install without the word "install" (npm i, yarn add, ...)
    ("package_install", "privileged",
     r"\b(?:npm|pnpm|bun)\s+(?:i|add|ci)\b|\byarn\s+add\b|\buv\s+add\b|\bcargo\s+add\b|\bgo\s+get\b|\bgem\s+add\b|\bpoetry\s+add\b"),
    # v1: external-effect cues. Privileged, and never an in-prompt grant (colloquial, not explicit).
    ("external_cue", "privileged",
     r"\bsend\s+(?:it|this|that|them|these|everything)\s+up\b|\bupload(?:s|ed|ing)?\b"
     r"|\bslack\s+(?:the\b|them\b|him\b|her\b|everyone\b|@|#|(?-i:[A-Z]))|\be-?mail\s+(?:the\b|them\b|him\b|her\b|everyone\b|[\w.+-]+@|(?-i:[A-Z]))"
     r"|\b(?:get|put|send|bring|move)\b" + _B + r"\b(?:onto|into|to|on)\s+(?:origin/)?(?:" + _PROT + r")\b"
     r"|\bso\s+(?:that\s+)?(?:\w+\s+){1,2}(?:can|could)\s+(?:review|look|see|test|pull|check)\b"
     r"|\bmake\s+(?:it|this|that)\s+live\b"),
    # ambiguous between write and privileged -> privileged (conservative: ASK, not ACT)
    ("ambiguous_ship_sync", "privileged",
     r"\bship(?:\s+it|\s+this|\s+that)?\b|\bland\s+(?:it|this|that)\b|\bsync\b" + _B + r"\b(?:origin|remote|upstream|github)\b"
     r"|\broll\s?back\b|\bpromote\b"),
    # ---- write: local, reversible changes in the working copy / local repo
    ("local_commit", "write", r"\bcommit(?:s|ted|ting)?\b"),
    ("edit", "write",
     r"\b(?:edit|fix|implement|refactor|add|create|write|update|change|rename|move|modify|patch|build|scaffold|generate|bump|format"
     r"|rewrite|clean\s*up|tidy|remove|delete|replace|wrap|stub|convert|migrate|port|extract|apply|rebase|revert|resolve|dedupe"
     r"|optimi[sz]e|tweak|adjust|split|draft|improve|reduce|increase|lower|speed\s+up|enable|disable|wire|hook\s+up|document)\b(?!\s+(?:me\b|sure\b))|\bmake\b(?!\s+sure)"),
    ("execute", "write", r"\brun\b|\bexecute\b|\bkick\s+off\b|\brerun\b"),
)
_COMPILED = tuple((name, eff, re.compile(pat, re.I)) for name, eff, pat in RULES)

_READ = re.compile(r"\b(?:explain|summari[sz]e|list|show|describe|tell\s+me|find|look|check|review|read|compare|walk\s+me\s+through"
                   r"|what|which|why|how|where|who|analy[sz]e|investigate|research|figure\s+out|plan|assess|audit|diagnose)\b", re.I)
_WH = re.compile(r"^\W*(?:what|whats|what's|which|why|how|where|when|who|whose)\b", re.I)
_REQUEST = re.compile(r"^\W*(?:(?:hey|ok|okay|so|and|also|pls|please|then)\W+)*(?:can|could|would|will)\s+(?:you|u)\b"
                      r"|\bpls\b|\bplease\b|\blet'?s\b|\bgo\s+ahead\b|\bi\s+(?:want|need)\s+(?:you|u)\s+to\b", re.I)
_NOUN_COMMIT = re.compile(r"(?:\b(?:the|last|latest|recent|previous|each|every|this|that|which|what|a|\d+|five|few|some|many|those|these)\s+)(?:\w+\s+)?$", re.I)
_PLAN_ONLY = re.compile(r"\b(?:don'?t|dont|do\s+not|without)\s+(?:change|changing|modify|touch|edit|execute|run|implement)\w*\s+(?:anything|any\s+of\s+(?:it|this|that))"
                        r"|\bjust\s+(?:a\s+)?plan\b|\bplan\s+only\b|\bcome\s+back\s+(?:to\s+me\s+)?(?:with\b|w/)[^.?!\n]{0,40}\bplan\b"
                        r"|\bno\s+(?:code\s+)?changes\b|\bread[- ]only\b", re.I)
_CONTINUATION = re.compile(r"^\W*(?:ok(?:ay)?|ya|yes|yep|sure|sounds\s+good|go\s+ahead|do\s+it|proceed|continue|lgtm|makes\s+sense)\W*$", re.I)
_GRANT = re.compile(r"\b(?:you|u)\s+have\s+(?:my\s+)?(?:permission|ok|approval|go-?ahead)\b|\bgo\s+ahead\b|\bfeel\s+free\s+to\b"
                    r"|\b(?:ok|okay|fine)\s+to\s+(?:push|merge|deploy|publish)\b|\byou\s+(?:can|may)\s+(?:push|merge|deploy|publish)\b|\bapproved\b", re.I)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[?!])\s+|(?<=\.)\s+(?=[A-Za-z0-9])|\n+|;\s+|\s+(?=\d[.)]\s)", text)
    return [p.strip() for p in parts if p and p.strip()]


def _clause_before(sentence: str, start: int) -> str:
    """Text before a match within its own clause: 'don't push, just fix it' does not negate 'fix'."""
    window = sentence[max(0, start - 40):start]
    return re.split(r"[,:;]|\b(?:but|and|then|just|instead)\b", window, flags=re.I)[-1]


def _is_question(sentence: str) -> bool:
    """A question asks for an answer; an imperative wrapped in 'can you ...?' is still a request."""
    if _REQUEST.search(sentence):
        return False
    return sentence.rstrip().endswith("?") or bool(_WH.search(sentence))


def _fact(packet: Mapping[str, Any] | None, key: str) -> Any:
    for c in (packet or {}).get("current_claims") or []:
        if c.get("key") == key:
            return c.get("value")
    return None


def _default_branch(packet: Mapping[str, Any] | None) -> str | None:
    value = _fact(packet, "git.default_branch")
    if not isinstance(value, str) or not value:
        return None
    return value.split("/", 1)[1] if value.startswith("origin/") else value


# ---- v1: privileged action instances, scoped in-prompt grants, unknown imperatives ----------------------

# rule -> action kind. Kinds absent from _GRANTABLE are never granted in-prompt (AODL or a user answer only).
_KIND = {"git_push": "push", "force_or_hard_reset": "force", "merge": "merge", "to_default_branch": "merge",
         "pull_request": "pr", "comment_or_issue": "comment_issue", "publish_deploy_release": "publish",
         "send_message": "message", "destructive_delete": "destructive", "aodl_privileged": "aodl",
         "package_install": "aodl", "external_system": "external_system", "remote_env": "env",
         "ambiguous_ship_sync": "ambiguous", "external_cue": "ambiguous", "commit_protected": "commit",
         "local_commit": "commit"}
_GRANTABLE = {"push", "merge", "commit", "pr", "comment_issue", "publish", "deploy", "message", "external_system", "env"}
_BRANCH_KINDS = {"push", "merge", "commit", "force"}
# Only direct verbs grant; colloquial phrasings of the same rule do not ("land it", "roll out", "go live").
_EXPLICIT = {
    "to_default_branch": re.compile(r"^(?:forward|fast[- ]forward)\b", re.I),
    "publish_deploy_release": re.compile(r"^(?:publish|deploy|redeploy|release|cut\b|tag\b)", re.I),
}
_DEPLOY = re.compile(r"^(?:deploy|redeploy)", re.I)
_ENV = re.compile(r"\b(prod|production|staging|stage|qa|test\s+env|preview|dev\s+env)\b", re.I)
_NAMED_TARGET = re.compile(r"#[a-z][\w-]*|@[\w-]+|[\w.+-]+@[\w-]+\.[\w.]+", re.I)
_ALL = re.compile(r"\ball\s+(?:the\s+|of\s+the\s+|my\s+|our\s+)?(?:local\s+)?branches\b|\bevery\s+branch\b|\beverything\b|--all\b|--mirror\b", re.I)
_NON_DEFAULT = re.compile(r"\b(?:feature|topic|my|our|the|these|those|both|wip|local|non-default|other)\s+(?:\w+\s+)?branches\b|\bfeature\s+branch\b|\bnon-default\b", re.I)
_BRANCH_AFTER = re.compile(r"\b(?:to|into|onto|on)\s+(?:origin/|origin\s+)?([\w./-]+)|^\w+(?:\s+-u|\s+--set-upstream|\s+--force-with-lease)*\s+(?:origin\s+|upstream\s+)([\w./-]+)"
                           r"|^(?:push|merge|commit)\s+([\w.-]+/[\w./-]+)", re.I)
_NOT_BRANCH = {"the", "it", "this", "that", "my", "our", "them", "all", "everything", "changes", "change", "branch", "branches",
               "feature", "up", "a", "an", "origin", "remote", "upstream", "there", "here", "pr", "github", "ci", "npm", "pypi",
               "prod", "production", "staging", "slack", "every", "these", "those", "both"}
_ATTRIBUTED = re.compile(r"\b(?:says?|said|wrote|writes|suggest(?:s|ed)?|recommend(?:s|ed)?|told\s+(?:me|us)|wants?\s+(?:us|me|you)\s+to|asked\s+(?:us\s+|me\s+)?to)\b"
                         r"|^\W*(?:bot|subagent|agent|ci|reviewer|copilot)\s*:", re.I)
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|'[^']{6,}'")


def _is_protected(branch: str, default: str | None) -> bool:
    b = branch.lower().removeprefix("origin/")
    return b in PROTECTED or b == (default or "").lower() or b.startswith("release/") or b == "the default branch"


def _target(kind: str, sentence: str, m: re.Match, current: str | None, default: str | None) -> dict[str, Any] | None:
    """Where a branch-targeted action lands. Named branches win; 'feature branches' is non-default; 'all' and an
    unknown current branch are treated as protected (never covered by an unnamed grant)."""
    if kind not in _BRANCH_KINDS:
        return None
    tail = sentence[m.start():m.end() + 50]
    tail = re.split(r"[,;]|\b(?:and|then|but)\b", tail[len(m.group(0)):], maxsplit=1, flags=re.I)[0]
    window = m.group(0) + tail
    if re.search(r"\bmerge\s+(?:the\s+|this\s+|that\s+|my\s+)?(?:pr|pull\s+request|mr|#\d+)\b|\bgh\s+pr\s+merge\b", window, re.I) and \
            not re.search(r"\binto\b", window, re.I):
        return {"branches": "pr_base", "protected": True, "named": []}
    named = []
    for g in _BRANCH_AFTER.finditer(window):
        name = next((x for x in g.groups() if x), "").rstrip(".,:)").lower()
        if name and name not in _NOT_BRANCH and not name.isdigit():
            named.append(name)
    if re.search(r"\bthe\s+default\s+branch\b", window, re.I):
        named.append(default or "the default branch")
    if kind == "merge":  # the branch that changes is the one merged INTO
        into = re.search(r"\b(?:into|onto|to)\s+(?:origin/)?([\w./-]+)", window, re.I)
        into_name = into.group(1).rstrip(".,:)").lower() if into else ""
        named = [into_name] if into_name and into_name not in _NOT_BRANCH else []
    if named:
        prot = [b for b in named if _is_protected(b, default)]
        return {"branches": named, "protected": bool(prot), "protected_branches": prot, "named": named}
    if _ALL.search(window):
        return {"branches": "all", "protected": True, "named": []}
    if _NON_DEFAULT.search(window):
        return {"branches": "non_default", "protected": False, "named": []}
    if current and not current.startswith("("):  # "(detached)" HEAD is not a branch: unknown target
        prot = [current.lower()] if _is_protected(current, default) else []
        return {"branches": [current.lower()], "protected": bool(prot), "protected_branches": prot, "named": []}
    return {"branches": "unknown", "protected": True, "named": []}


def _action(name: str, sentence: str, m: re.Match, current: str | None, default: str | None) -> dict[str, Any]:
    phrase = m.group(0).strip()
    kind = _KIND.get(name, "ambiguous")
    if name == "publish_deploy_release" and _DEPLOY.search(phrase):
        kind = "deploy"
    if name == "external_cue" and re.match(r"(?:slack|e-?mail)\b", phrase, re.I) and _NAMED_TARGET.search(sentence):
        kind = "message"  # "slack #eng ..." names its channel: a direct messaging verb
    explicit = kind in _GRANTABLE and (name not in _EXPLICIT or bool(_EXPLICIT[name].search(phrase)))
    if name == "external_cue" and kind != "message":
        explicit = False
    if kind == "message" and name == "send_message" and not (_NAMED_TARGET.search(sentence) or re.search(r"\bslack\b|\bdiscord\b|\bchannel\b|\bmailing\s+list\b", sentence, re.I)):
        explicit = False
    row: dict[str, Any] = {"kind": kind, "rule": name, "phrase": phrase, "grantable": kind in _GRANTABLE, "explicit": explicit}
    tgt = _target(kind, sentence, m, current, default)
    if tgt is not None:
        row["target"] = tgt
    if kind in ("deploy", "publish", "env"):
        row["envs"] = sorted({e.lower() for e in _ENV.findall(sentence)})
    return row


def _grant_from(action: Mapping[str, Any], sentence: str, m: re.Match) -> dict[str, Any] | None:
    """An explicit, user-authored instruction grants exactly its own action on exactly its own scope."""
    if not (action["grantable"] and action["explicit"]):
        return None
    if _ATTRIBUTED.search(sentence) or any(q.start() <= m.start() < q.end() for q in _QUOTED.finditer(sentence)):
        return None  # instructions attributed to a bot / subagent / someone else grant nothing
    scope: dict[str, Any] = {"kind": action["kind"]}
    tgt = action.get("target")
    if tgt is not None:
        # an unnamed target never reaches a protected branch: the grant is non-default only
        scope["branches"] = list(tgt["named"]) if tgt["named"] else "non_default"
    if "envs" in action:
        scope["envs"] = list(action["envs"])
    return {"source": "prompt", "phrase": action["phrase"], "sentence": sentence[:160], "scope": scope}


def grant_covers(grant: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    """Exact-scope coverage: same kind, and every target the action reaches is inside the grant's scope."""
    scope = grant.get("scope") or {}
    if grant.get("source") != "prompt" or not action.get("grantable"):
        return False
    kind = action["kind"]
    if kind == "env":  # a named environment is covered by a deploy/publish grant that names it
        return scope.get("kind") in ("deploy", "publish") and bool(action.get("envs")) and \
            set(action["envs"]) <= set(scope.get("envs") or [])
    if scope.get("kind") != kind:
        return False
    if kind in ("deploy", "publish") and action.get("envs") and not set(action["envs"]) <= set(scope.get("envs") or []):
        return False
    tgt = action.get("target")
    if tgt is None:
        return True
    allowed = scope.get("branches")
    if tgt["branches"] in ("all", "unknown", "pr_base"):
        return False
    if tgt["branches"] == "non_default":
        return allowed == "non_default"
    protected = set(tgt.get("protected_branches") or [])
    for b in tgt["branches"]:
        if allowed == "non_default":
            if b in protected:
                return False  # a protected branch is reached only by a grant that names it
        elif b not in allowed:
            return False
    return True


_READ_LEAD = {"explain", "summarize", "summarise", "list", "show", "describe", "tell", "find", "look", "check", "review", "read",
              "compare", "walk", "analyze", "analyse", "investigate", "research", "figure", "plan", "assess", "audit", "diagnose",
              "search", "grep", "locate", "see", "identify", "trace", "understand", "evaluate", "estimate", "think", "consider",
              "suggest", "recommend", "propose", "outline", "brainstorm", "help", "give", "name", "count", "verify", "confirm",
              "inspect", "study", "map", "answer", "clarify", "remind", "guess", "sketch", "note", "ask", "wait", "hold",
              "rank", "weigh", "critique", "rate", "scan", "skim", "print", "cat", "ls", "tree", "rg", "wc", "diff", "keep",
              "stop", "don't", "dont", "never", "thanks", "thank", "hi", "hello", "hey", "nvm", "nevermind", "sorry", "lol"}
_NOT_VERB = {"i", "we", "you", "u", "it", "its", "it's", "this", "that", "these", "those", "the", "a", "an", "my", "our", "your",
             "there", "here", "is", "are", "was", "were", "do", "does", "did", "has", "have", "had", "can", "could", "would",
             "should", "will", "might", "may", "must", "not", "no", "yes", "ok", "okay", "so", "and", "but", "or", "if", "when",
             "then", "also", "just", "maybe", "probably", "seems", "looks", "all", "some", "any", "every", "each", "both",
             "he", "she", "they", "them", "his", "her", "their", "what", "which", "why", "how", "where", "who", "whose",
             "because", "since", "after", "before", "until", "while", "now", "today", "tomorrow", "again", "still", "only",
             "same", "other", "another", "one", "two", "first", "last", "next", "hmm", "ah", "oh", "well", "actually", "btw",
             "fyi", "re", "note", "for", "in", "on", "at", "to", "of", "with", "by", "from", "about", "like", "as", "cool", "nice",
             "great", "good", "bad", "weird", "wow", "idk", "imo", "tbh", "per", "via", "though", "although", "git"}
_LEAD_STRIP = re.compile(r"^\W*(?:(?:hey|hi|ok|okay|so|and|also|pls|please|then|now|next|first|finally|quickly|just|alright|right|cool|great|"
                         r"can\s+(?:you|u)|could\s+(?:you|u)|would\s+(?:you|u)|will\s+(?:you|u)|let'?s|go\s+ahead\s+and|i\s+(?:want|need)\s+(?:you|u)\s+to)\W+)*", re.I)
_GIT_READ = re.compile(r"^git\s+(?:log|status|diff|show|blame|grep|branch\s*$|remote\s+-v|shortlog|reflog)\b", re.I)


def _imperative_verb(sentence: str) -> str | None:
    """The leading verb of an imperative sentence, or None. No POS tagger: a first word that is not a pronoun,
    determiner, auxiliary or inflected form (-ed/-ing/-s) is taken as a base-form verb."""
    body = _LEAD_STRIP.sub("", sentence).strip()
    if _GIT_READ.search(body):
        return None
    m = re.match(r"([A-Za-z][A-Za-z'-]*)", body)
    if not m:
        return None
    w = m.group(1).lower()
    if w in _NOT_VERB or len(w) < 2:
        return None
    if w.endswith(("ed", "ing")) or (w.endswith("s") and not w.endswith("ss")):
        return None
    return w


def infer_effects(request: str, packet: Mapping[str, Any] | None = None, *, slm: bool | None = None,
                  source: str = "user") -> dict[str, Any]:
    """Deterministic: identical request + facts give identical output. Returns effects (sorted subset of
    read/write/privileged, read always present), the max ``effect_class``, per-rule evidence, the privileged
    action instances the request needs, and the scoped grants the user's own prompt carries.

    ``source`` is who authored ``request``. Only ``"user"`` text can carry grants; harness/subagent messages
    (and anything but a user prompt) produce effects but never grants."""
    text = request or ""
    branch, default = _fact(packet, "git.branch"), _default_branch(packet)
    on_default = bool(branch and default and branch == default)
    evidence: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    grants: list[dict[str, Any]] = []
    classes = {"read"}
    sentences = _sentences(text)
    plan_only = bool(_PLAN_ONLY.search(text))
    may_grant = source == "user" and not text.lstrip().startswith(_HARNESS_PREFIXES)
    for s in sentences:
        question = _is_question(s)
        explain_lead = _EXPLAIN_LEAD.match(s)
        sentence_effects = set()
        for name, effect, rx in _COMPILED:
            for m in rx.finditer(s):
                phrase = m.group(0).strip()
                row = {"rule": name, "effect": effect, "phrase": phrase}
                if question:
                    evidence.append({**row, "applied": False, "why": "mentioned in a question: answering needs read only"})
                    continue
                if explain_lead and m.start() >= explain_lead.end() and \
                        not re.search(r"[,;:]|\b(?:and|then|also)\b", s[explain_lead.end():m.start()], re.I):
                    evidence.append({**row, "applied": False, "why": "object of an explanation request: read only"})
                    continue
                if _NEG.search(_clause_before(s, m.start())):
                    evidence.append({**row, "applied": False, "why": "negated"})
                    continue
                if name == "local_commit" and _NOUN_COMMIT.search(s[max(0, m.start() - 30):m.start()]):
                    evidence.append({**row, "applied": False, "why": "noun use (a commit), not an action"})
                    continue
                if effect == "write" and plan_only:
                    evidence.append({**row, "applied": False, "why": "request limits itself to a plan / no changes"})
                    continue
                if name == "local_commit" and on_default:
                    row = {**row, "effect": "privileged", "fact": f"git.branch={branch} is the default branch",
                           "why": "a commit on the default branch changes shared history"}
                elif effect == "privileged" and on_default and name in ("git_push", "force_or_hard_reset", "ambiguous_ship_sync"):
                    row["fact"] = f"git.branch={branch} is the default branch"
                if name in ("ambiguous_ship_sync", "external_cue"):
                    row["why"] = "ambiguous/colloquial external effect: privileged, never an in-prompt grant"
                evidence.append({**row, "applied": True})
                classes.add(row["effect"])
                sentence_effects.add(row["effect"])
                if row["effect"] == "privileged" or name == "local_commit":
                    act = _action(name, s, m, branch, default)
                    if row["effect"] == "privileged":
                        actions.append(act)
                    g = _grant_from(act, s, m) if may_grant else None
                    if g is not None and g["scope"] not in [x["scope"] for x in grants]:
                        grants.append(g)
        if question:
            evidence.append({"rule": "question", "effect": "read", "phrase": s[:60], "applied": True})
            continue
        verb = _imperative_verb(s)
        if verb and verb not in _READ_LEAD and not sentence_effects - {"read"} and not plan_only and not explain_lead:
            # v1: an imperative whose verb is not a read verb defaults to write; standing authority decides.
            evidence.append({"rule": "unknown_imperative", "effect": "write", "phrase": verb, "applied": True,
                             "why": "imperative verb outside the read lexicon: write (not read)"})
            classes.add("write")
        elif _READ.search(s):
            evidence.append({"rule": "read_verb", "effect": "read", "phrase": _READ.search(s).group(0), "applied": True})
    continuation = len(classes) == 1 and bool(_CONTINUATION.search(text))
    if continuation:
        # "go ahead" after a proposal inherits the prior turn's effects, which v1 cannot see.
        classes.add("write")
        evidence.append({"rule": "continuation", "effect": "write", "phrase": text.strip()[:40], "applied": True,
                         "why": "approval of a prior proposal; its effects are not visible here"})
    effects = sorted(classes, key=ORDER.__getitem__)
    uncovered = [a for a in actions if not any(grant_covers(g, a) for g in grants)]
    out = {
        "schema": SCHEMA,
        "effects": effects,
        "effect_class": effects[-1],
        "evidence": evidence,
        "facts": {"git.branch": branch, "git.default_branch": default, "on_default_branch": on_default},
        "ambiguous": any(e.get("rule") in ("ambiguous_ship_sync", "external_cue", "continuation") and e["applied"] for e in evidence),
        "privileged_actions": actions,
        # candidates only: applied solely through decision_opportunity.with_prompt_grants()
        "prompt_grants": grants,
        "prompt_grants_cover_all": bool(actions) and not uncovered,
        "user_grant_phrases": [m.group(0) for m in _GRANT.finditer(text)],
    }
    if slm if slm is not None else os.environ.get("Z0INT_EFFECTS_SLM") == "1":
        out["slm"] = slm_label(text)
        out["slm"]["agrees"] = out["slm"].get("class") == out["effect_class"]
    return out


_HARNESS_PREFIXES = ("<agent-message", "<task-notification", "<system-reminder")
_EXPLAIN_LEAD = re.compile(r"^\W*(?:(?:pls|please|can\s+(?:you|u)|could\s+(?:you|u))\W+)*(?:explain|describe|summari[sz]e|tell\s+me|walk\s+me\s+through)\b", re.I)


SLM_PROMPT = """Classify the effect class a coding agent needs to fulfil this user request in a git repo.
read = answer/explain/review/plan only. write = local reversible edits, local commits on a feature branch, running tests.
privileged = push, merge or commit to the default branch, PRs, issue/PR comments, publish, deploy, release, delete branches/data,
force/reset --hard, messages, sudo/install/ssh/secrets/credentials, anything outside the local working copy.
Questions ABOUT an action are read. Negated actions ("don't push") do not count. If unsure between write and privileged, answer privileged.
Answer with exactly one word: read, write or privileged.

Request: {request}
/no_think"""


def slm_label(request: str, *, provider: str = "groot", model: str = "qwen3-8b-q4km", trace_id: str | None = None) -> dict[str, Any]:
    """Shadow tie-breaker via the worker router's explicit dispatch (local, keyless). Fail-open; never gates."""
    import hashlib

    try:
        from .worker_routing import dispatch_worker

        tid = trace_id or "effects-" + hashlib.sha256(request.encode()).hexdigest()[:24]
        res = dispatch_worker({"harness": "claude-code", "trace_id": tid, "function": "effect_inference_tiebreak",
                               "parent_agent": "z0int.effect_inference", "task": SLM_PROMPT.format(request=request[:6000]),
                               "provider": provider, "model": model, "reason": "shadow effect-class tie-breaker (z0int#55)",
                               "max_tokens": 64, "free_only": True})
        output = res.get("output") if isinstance(res, dict) else None
        text = re.sub(r"<think>.*?</think>", "", str(output or ""), flags=re.S).strip().lower()
        found = re.findall(r"\b(read|write|privileged)\b", text)
        return {"provider": provider, "model": model, "ok": bool(isinstance(res, dict) and res.get("ok")),
                "class": found[0] if found else None, "raw": text[:40]}
    except Exception as exc:  # shadow only: any failure is recorded, never raised
        return {"provider": provider, "model": model, "ok": False, "class": None, "error": type(exc).__name__}


def effect_class(effects: Iterable[str]) -> str:
    return max(effects, key=ORDER.__getitem__)
