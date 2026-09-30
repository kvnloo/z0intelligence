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

SCHEMA = "z0int.effect_inference.v0"
ORDER = {"read": 0, "write": 1, "privileged": 2}

_B = r"[^.?!\n]{0,40}"  # same-sentence gap
_NEG = re.compile(r"(?:\b(?:don'?t|dont|do not|never|without|not|no need to|avoid|hold off on)|n't)\W+(?:\w+\W+){0,2}$", re.I)

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
     r"\b(?:publish|deploy|redeploy|release|roll\s?out)(?:es|ed|ing|s)?\b(?!\s+(?:notes?|plan|process|checklist|schedule|cadence|candidate|branch|train|workflow))"
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
                        r"|\bjust\s+(?:a\s+)?plan\b|\bplan\s+only\b|\bcome\s+back\s+(?:to\s+me\s+)?(?:with|w/)\b[^.?!\n]{0,40}\bplan\b"
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


def infer_effects(request: str, packet: Mapping[str, Any] | None = None, *, slm: bool | None = None) -> dict[str, Any]:
    """Deterministic: identical request + facts give identical output. Returns effects (sorted subset of
    read/write/privileged, read always present), the max ``effect_class``, and per-rule evidence."""
    text = request or ""
    branch, default = _fact(packet, "git.branch"), _default_branch(packet)
    on_default = bool(branch and default and branch == default)
    evidence: list[dict[str, Any]] = []
    classes = {"read"}
    sentences = _sentences(text)
    plan_only = bool(_PLAN_ONLY.search(text))
    for s in sentences:
        question = _is_question(s)
        for name, effect, rx in _COMPILED:
            for m in rx.finditer(s):
                phrase = m.group(0).strip()
                row = {"rule": name, "effect": effect, "phrase": phrase}
                if question:
                    evidence.append({**row, "applied": False, "why": "mentioned in a question: answering needs read only"})
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
                if name == "ambiguous_ship_sync":
                    row["why"] = "ambiguous write-vs-privileged: resolved to privileged (ASK, not ACT)"
                evidence.append({**row, "applied": True})
                classes.add(row["effect"])
        if question:
            evidence.append({"rule": "question", "effect": "read", "phrase": s[:60], "applied": True})
        elif _READ.search(s):
            evidence.append({"rule": "read_verb", "effect": "read", "phrase": _READ.search(s).group(0), "applied": True})
    continuation = len(classes) == 1 and bool(_CONTINUATION.search(text))
    if continuation:
        # "go ahead" after a proposal inherits the prior turn's effects, which v0 cannot see.
        classes.add("write")
        evidence.append({"rule": "continuation", "effect": "write", "phrase": text.strip()[:40], "applied": True,
                         "why": "approval of a prior proposal; its effects are not visible to v0"})
    effects = sorted(classes, key=ORDER.__getitem__)
    out = {
        "schema": SCHEMA,
        "effects": effects,
        "effect_class": effects[-1],
        "evidence": evidence,
        "facts": {"git.branch": branch, "git.default_branch": default, "on_default_branch": on_default},
        "ambiguous": any(e.get("rule") in ("ambiguous_ship_sync", "continuation") and e["applied"] for e in evidence),
        # recorded, never applied: authority widens only via AODL or with_authority_grant()
        "user_grant_phrases": [m.group(0) for m in _GRANT.finditer(text)],
    }
    if slm if slm is not None else os.environ.get("Z0INT_EFFECTS_SLM") == "1":
        out["slm"] = slm_label(text)
        out["slm"]["agrees"] = out["slm"].get("class") == out["effect_class"]
    return out


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
