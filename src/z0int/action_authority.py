"""Action-level authority check (z0int#55 enforceability layer).

Grants come from *intent*: the user's own prompts (scoped, via ``effect_inference`` plus the lexicon
below), literal commands the user wrote, answers to an ask (a "yes" after the assistant proposed an
action, an ``AskUserQuestion`` answer), and authored AODL grants. Effects come from the *actual* tool
call (``action_effects``). ``authority_check`` compares them::

    authority_check(effects, grants, standing, prohibitions) -> {"decision": "allow"|"ask"|"deny", ...}

Rules (pre-registered in benchmarks/action_authority/PREREG_v0.md):

* read is always standing; write is standing outside plan mode (plan mode is read-only).
* privileged needs a covering grant with exact scope: same kind, and every target inside the grant's
  scope. A feature-branch ("non_default") push grant never covers a default/protected branch; an
  unknown target is never covered; force is never granted implicitly (only an explicit force grant).
* deny when the user prohibited that action ("don't push to master", "no PRs", a rejected permission
  prompt for the same action) and no later grant re-authorised it; deny force/rewrite of a protected
  branch and catastrophic deletes without an explicit grant.

``SessionAuthority`` folds transcript rows (live hook or historical replay) into grants/prohibitions.
"""

from __future__ import annotations

import re

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing is not imported at run time (hook latency)
    from typing import Any, Iterable, Mapping  # noqa: F401


from .action_effects import ORDER, Ctx, parse_tool_call

SCHEMA = "z0int.action_authority.v1"

HARNESS_PREFIXES = ("<agent-message", "<task-notification", "<system-reminder", "<local-command-stdout",
                    "<local-command-caveat", "<local-command-stderr", "Caveat:", "[Request interrupted",
                    "<user-memory-input", "<bash-stdout", "<bash-stderr", "<teammate-message")
_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)

# grant kind accepted for an effect kind (effect kind -> grant kinds)
_ACCEPT = {"comment": {"comment", "comment_issue"}, "issue": {"issue", "comment_issue"}, "upload": {"upload", "publish"},
           "rewrite": {"rewrite", "force"}}
_BRANCH_KINDS = {"push", "force", "merge", "commit", "rewrite"}

_PROT_WORDS = ("main", "master", "trunk", "develop", "dev", "nightly", "production", "prod", "stable", "gh-pages")


def extract_grants(*args, **kw):
    from .action_grants import extract_grants as _x
    return _x(*args, **kw)


# ---------------------------------------------------------------------------------------------- coverage
def _norm_branch(b: Any) -> str:
    return str(b).lower().removeprefix("origin/").removeprefix("refs/heads/")


def covers(grant: Mapping[str, Any], effect: Mapping[str, Any]) -> bool:
    scope = grant.get("scope") or {}
    kind = effect["kind"]
    gk = scope.get("kind")
    if gk != kind and gk not in _ACCEPT.get(kind, ()):
        return False
    t = effect.get("target") or {}
    if "exact_target" in scope:  # a literal command grants exactly what it parses to
        et = scope["exact_target"]
        return all(str(t.get(k)) == str(v) for k, v in et.items() if k != "protected")
    if t.get("catastrophic"):
        return False
    if kind in ("push", "force") and t.get("remote"):
        remotes = scope.get("remotes")
        r = str(t.get("remote")).lower()
        if remotes and r not in {x.lower() for x in remotes}:
            return False
        if not remotes and r == "upstream":
            return False  # "push it" means your own remote, not the canonical upstream
    if kind == "publish" and t.get("tag") and scope.get("tags") is not None:
        tags = scope.get("tags")
        return tags == "any" or (isinstance(tags, list) and str(t.get("tag")).lower() in {x.lower() for x in tags})
    if kind in _BRANCH_KINDS or (kind == "delete" and "branch" in t):
        b = t.get("branch")
        allowed = scope.get("branches")
        if kind == "merge" and b == "pr_base":
            prs = scope.get("prs")
            return prs == "any" or (isinstance(prs, list) and str(t.get("pr") or "") in prs) or \
                (isinstance(allowed, list) and bool(allowed) and bool(set(_norm_branch(x) for x in allowed) & set(_PROT_WORDS)))
        if b in (None, "unknown", "all"):
            return False
        if allowed == "non_default" or allowed is None:
            return not t.get("protected", True) if allowed == "non_default" else False
        return _norm_branch(b) in {_norm_branch(x) for x in allowed}
    if kind == "delete" and "path" in t:
        return any(_path_under(_local_part(t), p) for p in scope.get("paths") or [])
    if kind == "delete":
        return bool(scope.get("paths") is None and scope.get("branches") in (None, "non_default") and not t)
    if kind == "fs_outside_repo":
        return any(_path_under(_local_part(t), p) for p in scope.get("paths") or [])
    if kind == "secret" and scope.get("paths"):
        return bool(t.get("path")) and any(_path_under(_local_part(t), p) or str(t.get("path")).endswith("/" + p.lstrip("./"))
                                           or str(t.get("path")) == p for p in scope["paths"])
    if kind in ("ssh",):
        hosts = scope.get("hosts")
        return isinstance(hosts, list) and _host(t.get("host")) in {_host(h) for h in hosts}
    if kind == "sudo" and scope.get("for") and str(t.get("command") or "") not in scope["for"]:
        return False
    if kind in ("sudo", "service"):
        hosts = scope.get("hosts")
        if isinstance(hosts, list) and hosts and t.get("host") not in (None, "local") and \
                _host(t.get("host")) not in {_host(h) for h in hosts}:
            return False
        if kind == "service":
            verbs = scope.get("verbs")
            if verbs and t.get("verb") and str(t.get("verb")).lower() not in verbs:
                return False
            units = scope.get("units") or []
            return any(_unit_match(t.get("unit"), u) for u in units if u)
        return True
    if kind == "install":
        if scope.get("env") == "local":
            return False  # "into the project .venv only": a non-local install is outside the grant
        if scope.get("interp_paths"):
            ip = t.get("interp")
            if not ip or not any(_path_under(ip, x) for x in scope["interp_paths"]):
                return False
        pk = scope.get("packages")
        if pk == "any":
            return True
        want = {p.lower() for p in (t.get("packages") or [])}
        return bool(want) and want <= {p.lower() for p in (pk or [])}
    if kind == "network":
        hosts = scope.get("hosts") or []
        return _host(t.get("host")) in {_host(h) for h in hosts}
    if kind in ("deploy", "publish") and scope.get("envs") and t.get("namespace"):
        return str(t["namespace"]).lower() in {e.lower() for e in scope["envs"]}
    return True  # pr, comment, issue, publish, deploy, ci, secret, upload, message, external_system: kind is the scope


def _local_part(t: Mapping[str, Any]) -> str:
    """Path of an effect; on a remote host the 'host:' prefix is dropped (grants name the path)."""
    p = str(t.get("path") or "")
    if t.get("host") and p.startswith(str(t.get("host")) + ":"):
        p = p[len(str(t.get("host"))) + 1:]
    return p


def _host_loose(effect_host: Any, h: str) -> bool:
    """Prohibitions match loosely: 'prod' forbids prod-db-1 and api.prod.internal too."""
    e = str(effect_host or "").lower().split("@")[-1]
    h = h.lower()
    return _host(e) == _host(h) or e.startswith(h + "-") or e.startswith(h + ".") or ("." + h + ".") in ("." + e + ".") or \
        h in re.split(r"[-.]", e)


def _host(h: Any) -> str:
    h = str(h or "").lower().split("@")[-1]
    return h if re.match(r"^\d+(?:\.\d+){3}$", h) else h.split(".")[0]


def _unit_match(unit: Any, u: str) -> bool:
    a = str(unit or "").lower().removesuffix(".service").removesuffix(".timer")
    b = u.lower().removesuffix(".service").removesuffix(".timer")
    return bool(a and b) and (a == b or a.startswith(b + "-") or a.startswith(b + "@") or b.startswith(a + "-") or
                              a.split("@")[0] == b.split("@")[0])


def _path_under(path: str, prefix: str) -> bool:
    import os
    home = os.path.expanduser("~")
    if not path or not prefix:
        return False
    a = os.path.normpath(str(path).replace("~", home, 1)) if str(path).startswith("~") else os.path.normpath(str(path))
    b = os.path.normpath(prefix.replace("~", home, 1)) if prefix.startswith("~") else os.path.normpath(prefix)
    return a == b or a.startswith(b.rstrip("/") + "/")


_GH_LIKE = re.compile(r"^(?:gh |git push|mcp .*github)|github", re.I)


def _dotfile(path: str) -> bool:
    import os
    p = str(path or "")
    home = os.path.expanduser("~")
    if p.startswith(home + "/"):
        p = "~" + p[len(home):]
    return p.startswith("~/.")


def _prohibited(p: Mapping[str, Any], effect: Mapping[str, Any]) -> bool:
    scope = p.get("scope") or {}
    if scope.get("kinds"):  # umbrella: one record for several kinds sharing a target scope
        return any(_prohibited({"scope": {**scope, "kind": k, "kinds": None}}, effect) for k in scope["kinds"])
    if "exact_target" in scope:
        return covers(p, effect)
    k = effect["kind"]
    sk = scope.get("kind")
    t = effect.get("target") or {}
    if sk == "delete_remote":
        return k == "delete" and bool(t.get("remote") or t.get("repo") or t.get("issue") or t.get("host") == "api.github.com")
    if sk != k and sk not in _ACCEPT.get(k, ()):
        # "don't push" also forbids force-pushing; "no merges" forbids gh pr merge
        if not (sk == "push" and k == "force"):
            return False
    if scope.get("github"):
        why = f"{effect.get('reason', '')} {effect.get('cmd', '')}"
        if k == "merge" and t.get("branch") != "pr_base":
            return False
        if k in ("publish", "external_system", "upload") and not (_GH_LIKE.search(why) or t.get("host") == "api.github.com"
                                                                 or (k == "publish" and t.get("remote"))):
            return False
    if scope.get("outside_repo") and not t.get("path"):
        return False
    if scope.get("dotfiles"):
        return _dotfile(t.get("path"))
    if k in _BRANCH_KINDS and isinstance(scope.get("branches"), list) and scope["branches"]:
        return _norm_branch(t.get("branch", "")) in {_norm_branch(b) for b in scope["branches"]} or t.get("branch") in ("all", "unknown")
    if k == "delete" and isinstance(scope.get("branches"), list) and scope["branches"]:
        return bool(t.get("branch")) and _norm_branch(t.get("branch")) in {_norm_branch(b) for b in scope["branches"]}
    if k == "install" and isinstance(scope.get("packages"), list) and scope["packages"]:
        return bool({p.lower() for p in t.get("packages") or []} & {p.lower() for p in scope["packages"]})
    if k in ("ssh", "sudo", "service", "deploy") and isinstance(scope.get("hosts"), list) and scope["hosts"]:
        if k == "deploy":
            return any(h in str(t).lower() for h in scope["hosts"])
        return any(_host_loose(t.get("host"), h) for h in scope["hosts"])
    if k == "service":  # "stop the loop" / "don't restart the api" forbid only a unit (and verb) they name
        verbs = scope.get("verbs")
        if verbs and t.get("verb") and str(t.get("verb")).lower() not in verbs:
            return False
        units = scope.get("units") or []
        return not units or any(_unit_match(t.get("unit"), u) for u in units if u)
    if k in ("delete", "fs_outside_repo", "secret") and scope.get("paths"):
        return any(_path_under(_local_part(t), x) for x in scope["paths"])
    if k == "network" and isinstance(scope.get("hosts"), list) and scope["hosts"]:
        return _host(t.get("host")) in {_host(h) for h in scope["hosts"]}
    return True


def authority_check(effects: Iterable[Mapping[str, Any]], grants: Iterable[Mapping[str, Any]] = (),
                    standing: Mapping[str, Any] | Iterable[str] | None = None,
                    prohibitions: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """allow | ask(reason) | deny(reason), with provenance for every privileged effect."""
    if standing is None:
        standing_effects = {"read", "write"}
    elif isinstance(standing, dict):
        standing_effects = set(standing.get("effects") or ("read",))
    else:
        standing_effects = set(standing)
    grants = list(grants)
    prohibitions = list(prohibitions)
    per: list[dict[str, Any]] = []
    holds = [p for p in prohibitions if (p.get("scope") or {}).get("kind") == "write" or "write" in ((p.get("scope") or {}).get("kinds") or ())]
    lifts = [g for g in grants if (g.get("scope") or {}).get("kind") in ("write",) or g.get("via") == "lift_readonly"]
    write_hold = None
    if holds:
        h = max(holds, key=lambda x: x.get("turn", 0))
        if h.get("turn", 0) > max((g.get("turn", 0) for g in lifts), default=-1):
            write_hold = h
    for e in effects:
        cls = e.get("class", "write")
        row: dict[str, Any] = {"kind": e.get("kind"), "class": cls, "target": e.get("target") or {}}
        if cls in ("read", "write"):
            if cls not in standing_effects:
                row.update(decision="ask", reason=f"{cls} is outside standing authority ({sorted(standing_effects)})")
            elif cls == "write" and write_hold is not None:
                row.update(decision="deny", reason=f"user asked for read-only ({write_hold.get('phrase')!r}, turn {write_hold.get('turn')})",
                           provenance={"prohibition": _prov(write_hold)})
            else:
                row.update(decision="allow", reason=f"standing:{cls}")
            per.append(row)
            continue
        covering = [g for g in grants if covers(g, e)]
        forbidding = [p for p in prohibitions if _prohibited(p, e)]
        last_grant = max((g.get("turn", 0) for g in covering), default=-1)
        last_forbid = max((p.get("turn", 0) for p in forbidding), default=-1)
        t = e.get("target") or {}
        if forbidding and last_forbid >= last_grant:
            p = max(forbidding, key=lambda x: x.get("turn", 0))
            row.update(decision="deny", reason=f"user prohibited {e['kind']} ({p.get('phrase')!r}, turn {p.get('turn')})",
                       provenance={"prohibition": _prov(p)})
        elif covering:
            g = max(covering, key=lambda x: x.get("turn", 0))
            row.update(decision="allow", reason=f"granted {e['kind']} by {g.get('source')} ({g.get('phrase')!r})",
                       provenance={"grant": _prov(g)})
        elif e["kind"] in ("force", "rewrite") and t.get("protected", True):
            row.update(decision="deny", reason=f"{e['kind']} of a protected/unknown branch ({t.get('branch')}) is never implicit")
        elif t.get("catastrophic"):
            row.update(decision="deny", reason=f"catastrophic path {t.get('path')}")
        else:
            row.update(decision="ask", reason=f"no grant covers privileged:{e['kind']} {_tgt(t)}".rstrip())
        per.append(row)
    decision = "deny" if any(r["decision"] == "deny" for r in per) else \
        "ask" if any(r["decision"] == "ask" for r in per) else "allow"
    worst = next((r for r in per if r["decision"] == decision), per[0] if per else None)
    return {"schema": SCHEMA, "decision": decision, "reason": worst["reason"] if worst else "no effects",
            "per_effect": per, "standing": sorted(standing_effects, key=ORDER.__getitem__)}


def _prov(g: Mapping[str, Any]) -> dict[str, Any]:
    return {k: g.get(k) for k in ("source", "via", "turn", "phrase", "scope") if g.get(k) is not None}


def _tgt(t: Mapping[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in t.items() if k in ("remote", "branch", "host", "path", "tag", "pr", "unit", "env",
                                                              "packages", "server", "repo") and v not in (None, []))


# ---------------------------------------------------------------------------------------- session folding
def _user_text(row: Mapping[str, Any]) -> str | None:
    """User-authored prompt text of a transcript row, or None for harness / tool-result / meta rows."""
    if row.get("type") != "user" or row.get("isMeta") or row.get("isCompactSummary") or row.get("isSidechain"):
        return None
    if row.get("promptSource") == "system" or row.get("turnOrigin") in ("task_notification", "peer", "system"):
        return None  # harness-injected turns (task notifications, peer messages) carry no user intent
    msg = row.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str) or not content.strip():
        return None
    s = content.lstrip()
    if s.startswith("<command-name>") or s.startswith("<command-message>"):
        m = _ARGS.search(s)
        return m.group(1).strip() if m and m.group(1).strip() else None
    if s.startswith(HARNESS_PREFIXES):
        return None
    return content


def _call_key(tool: str, ti: Mapping[str, Any] | None) -> str:
    ti = ti or {}
    for k in ("command", "file_path", "notebook_path", "url", "path"):
        if ti.get(k):
            return " ".join(str(ti[k]).split())[:400]
    return ""


def _declines(text: str, negative) -> bool:
    t = text.strip()
    return bool(negative.match(t)) and len(t) <= 200 and not re.search(
        r"\b(?:go\s+ahead|do\s+it|yes|sure|proceed|sounds\s+good|no\s+(?:worries|problem|need\s+to\s+ask)|of\s+course)\b", t, re.I)


def _tool_results(row: Mapping[str, Any]) -> list[tuple[str, str, bool]]:
    msg = row.get("message") or {}
    content = msg.get("content")
    out = []
    if row.get("type") == "user" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                out.append((b.get("tool_use_id") or "", str(c or "")[:4000], bool(b.get("is_error"))))
    return out


_READONLY_HINT = re.compile(r"read[- ]only|\b(?:don'?t|do\s+not|never)\s+(?:change|modify|touch|edit|write)\s+anything|"
                           r"\bno\s+(?:changes|edits|modifications|writes)\b|look\s+but\s+don'?t\s+touch|make\s+any\s+changes", re.I)
_ANSWERS = r'"([^"]{1,400})"\s*=\s*"([^"]{0,400})"'
_REJECTED = r"doesn't want to proceed with this tool use|tool use was rejected|user rejected|denied by the user"


class SessionAuthority:
    """Grants and prohibitions accumulated from one session's rows, in order. JSON-serialisable state.

    ``feed`` only *records* user-authored texts (cheap); ``materialize`` turns them into grants when a
    privileged effect actually needs checking, so ordinary read/write calls never pay for grant parsing.
    """

    def __init__(self, state: Mapping[str, Any] | None = None):
        s = dict(state or {})
        self.grants: list[dict[str, Any]] = list(s.get("grants") or [])
        self.prohibitions: list[dict[str, Any]] = list(s.get("prohibitions") or [])
        self.pending: list[list] = list(s.get("pending") or [])
        self.turn: int = int(s.get("turn") or 0)
        self.last_assistant_text: str = s.get("last_assistant_text") or ""
        self.tool_uses: dict[str, list] = dict(s.get("tool_uses") or {})
        self.prompt_id: str | None = s.get("prompt_id")
        self.permission_mode: str | None = s.get("permission_mode")
        self.cwd: str | None = s.get("cwd")
        self.branch: str | None = s.get("branch")
        self._path: str | None = None  # hook cache bookkeeping (transcript path, byte offset)
        self._offset: int = 0

    def state(self) -> dict[str, Any]:
        return {"grants": self.grants[-400:], "prohibitions": self.prohibitions[-200:], "pending": self.pending[-200:],
                "turn": self.turn, "last_assistant_text": self.last_assistant_text[-3000:],
                "tool_uses": dict(list(self.tool_uses.items())[-30:]), "prompt_id": self.prompt_id,
                "permission_mode": self.permission_mode, "cwd": self.cwd, "branch": self.branch}

    def feed(self, row: Mapping[str, Any], ctx: Ctx | None = None) -> None:
        t = row.get("type")
        if row.get("isSidechain"):
            return
        if row.get("cwd"):
            self.cwd = row.get("cwd")
        if row.get("gitBranch") and row.get("gitBranch") != "HEAD":
            self.branch = row.get("gitBranch")
        if t == "assistant":
            msg = row.get("message") or {}
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text"):
                    self.last_assistant_text = b["text"][-3000:]
                elif b.get("type") == "tool_use":
                    ti = b.get("input") or {}
                    slim = {k: ti.get(k) for k in ("command", "file_path", "url", "action", "notebook_path") if k in ti}
                    self.tool_uses[b.get("id") or ""] = [b.get("name"), slim]
                    if len(self.tool_uses) > 60:
                        self.tool_uses = dict(list(self.tool_uses.items())[-30:])
            return
        if t != "user":
            return
        if row.get("permissionMode"):
            self.permission_mode = row.get("permissionMode")
        results = _tool_results(row)
        if results:
            for tool_use_id, text, is_error in results:
                use = self.tool_uses.get(tool_use_id)
                if (use and use[0] == "AskUserQuestion") or "User has answered your question" in text:
                    self.turn += 1
                    for q, a in re.findall(_ANSWERS, text):
                        self.pending.append(["answer", self.turn, a, q])
                elif is_error and use and re.search(_REJECTED, text, re.I):
                    # the user refused this exact action at the permission prompt
                    self.pending.append(["rejected", self.turn, use[0], use[1]])
            return
        text = _user_text(row)
        if text is None:
            return
        self.turn += 1
        self.prompt_id = row.get("promptId") or row.get("uuid") or self.prompt_id
        self.pending.append(["prompt", self.turn, text[:20000], self.last_assistant_text[-1500:]])
        self.last_assistant_text = ""

    def materialize(self, ctx: Ctx | None = None) -> None:
        """Extract grants/prohibitions from the recorded texts (idempotent: pending is consumed)."""
        if not self.pending:
            return
        from .action_grants import AFFIRM, NEGATIVE, STRONG_AFFIRM, proposal_to_prohibitions
        default = branch = None
        if ctx is not None:
            try:
                default, branch = ctx.default_of(ctx.cwd), ctx.branch_of(ctx.cwd) or self.branch
            except Exception:
                default, branch = None, self.branch
        pending, self.pending = self.pending, []
        for item in pending:
            kind, turn = item[0], item[1]
            if kind == "prompt":
                text, assistant = item[2], item[3]
                if assistant and _declines(text, NEGATIVE):
                    self.prohibitions.extend(proposal_to_prohibitions(assistant, turn, ctx, default, branch, reply=text))
                self._absorb(text, "prompt", turn, ctx, default, branch)
                if assistant and AFFIRM.match(text) and not _declines(text, NEGATIVE) and \
                        (len(text) <= 160 or STRONG_AFFIRM.match(text)):
                    self._absorb_proposal(assistant, turn, ctx, default, branch, reply=text)
            elif kind == "answer":
                answer, question = item[2], item[3]
                self._absorb_answer(question, answer, turn, ctx, default, branch)
            elif kind == "rejected" and ctx is not None:
                self.prohibitions.append({"source": "rejected_permission", "turn": turn + 0.95, "phrase": f"rejected {item[2]}",
                                          "scope": {"kind": "exact_call", "tool": item[2], "key": _call_key(item[2], item[3])}})
                parsed = parse_tool_call(item[2], item[3], ctx)
                for e in parsed["privileged"]:  # after the prompt that preceded it: ordered at the end of its turn
                    self.prohibitions.append({"source": "rejected_permission", "turn": turn + 0.95, "phrase": f"rejected {item[2]}",
                                              "scope": {"kind": e["kind"], "exact_target": e["target"]}})

    def _absorb(self, text: str, source: str, turn: int, ctx: Ctx | None, default: str | None, branch: str | None) -> None:
        from .action_grants import extract_grants
        try:
            g, p = extract_grants(text, source=source, turn=turn, ctx=ctx, default=default, branch=branch)
        except Exception:  # a grant-parsing bug must not erase the session's other grants/prohibitions
            return
        self.grants.extend(g)
        self.prohibitions.extend(p)

    def _absorb_answer(self, question: str, answer: str, turn: int, ctx: Ctx | None, default: str | None,
                       branch: str | None) -> None:
        """AskUserQuestion: the answer chooses among what the question proposed; 'no' forbids it."""
        from .action_grants import AFFIRM, NEGATIVE, _branches_near, _hosts_after, _paths_in, _proposal_kinds, \
            extract_grants, proposal_to_prohibitions
        if _declines(answer, NEGATIVE):
            self.prohibitions.extend(proposal_to_prohibitions("Should I " + question.rstrip("?") + "?", turn, ctx, default,
                                                              branch, reply=answer))
            for x in _proposal_kinds(question, turn, default):
                self.prohibitions.append({"source": "ask_answer", "turn": turn, "phrase": f"declined: {question[:80]}",
                                          "scope": x["scope"]})
            g, p = extract_grants(answer, source="ask_answer", turn=turn, ctx=ctx, default=default, branch=branch)
            self.grants.extend(g)
            self.prohibitions.extend(p)
            return
        g, p = extract_grants(answer, source="ask_answer", turn=turn, ctx=ctx, default=default, branch=branch)
        self.grants.extend(g)
        self.prohibitions.extend(p)
        if g:
            return
        kinds = _proposal_kinds(question, turn, default)
        added = False
        for x in kinds:
            scope = dict(x["scope"])
            k = scope["kind"]
            if k in ("push", "force", "merge", "commit", "rewrite", "delete"):
                br = _branches_near(" to " + answer, 0, len(answer) + 4, default)
                if br:
                    scope["branches"] = br
                elif not scope.get("branches"):
                    scope["branches"] = "non_default"
            if k in ("ssh", "sudo", "service"):
                hosts = _hosts_after(" to " + answer, 0)
                if hosts:
                    scope["hosts"] = hosts
                elif k == "ssh":
                    hosts = _hosts_after(question, 0)
                    scope["hosts"] = hosts
            if k in ("delete", "fs_outside_repo"):
                ps = _paths_in(answer) or _paths_in(question)
                if ps:
                    scope["paths"] = ps
            if k == "install":
                scope.setdefault("packages", "any")
            self.grants.append({"source": "ask_answer", "turn": turn, "via": "question_answer", "phrase": f"{question[:60]} -> {answer[:40]}",
                                "scope": scope})
            added = True
        if not added and AFFIRM.match(answer):
            self._absorb_proposal("Should I " + question.rstrip("?") + "?", turn, ctx, default, branch, reply=answer)

    def _absorb_proposal(self, assistant_text: str, turn: int, ctx: Ctx | None, default: str | None, branch: str | None,
                         reply: str = "") -> None:
        """A 'yes' to "Want me to push to origin/master?" grants what was proposed, nothing more."""
        from .action_grants import _CODE, _PROPOSAL, extract_grants
        tail = assistant_text[-1500:]
        proposals = [m.group(1) for m in _PROPOSAL.finditer(tail)][-3:]
        for prop in proposals:
            g, _ = extract_grants(prop.strip() + ".", source="ask_answer", turn=turn, ctx=ctx, default=default, branch=branch)
            for x in g:
                x["reply"] = reply[:60]
            self.grants.extend(g)
        if ctx is not None and proposals:  # literal commands inside the proposal paragraph
            for m in _CODE.finditer(tail[-800:]):
                code = m.group(1) or m.group(2) or ""
                try:
                    parsed = parse_tool_call("Bash", {"command": code}, ctx)
                except Exception:
                    continue
                for e in parsed["privileged"]:
                    self.grants.append({"source": "ask_answer", "turn": turn, "via": "literal_command",
                                        "phrase": code[:120], "scope": {"kind": e["kind"], "exact_target": e["target"]}})

    def _may_hold_writes(self) -> bool:
        """Cheap pre-check so ordinary writes only pay for grant parsing when a read-only hold may exist."""
        if any((p.get("scope") or {}).get("kind") == "write" or "write" in ((p.get("scope") or {}).get("kinds") or ())
               for p in self.prohibitions):
            return bool(self.pending)
        return any((it[0] == "prompt" and _READONLY_HINT.search(it[2])) or it[0] == "rejected" for it in self.pending)

    def standing(self) -> dict[str, Any]:
        return {"effects": ["read"] if self.permission_mode == "plan" else ["read", "write"], "mode": self.permission_mode}

    def check(self, tool_name: str, tool_input: Mapping[str, Any], ctx: Ctx, permission_mode: str | None = None) -> dict[str, Any]:
        parsed = parse_tool_call(tool_name, tool_input, ctx)
        if parsed["privileged"] or (parsed["effect_class"] == "write" and self._may_hold_writes()):
            self.materialize(ctx)
        mode = permission_mode or self.permission_mode
        standing = {"effects": ["read"] if mode == "plan" else ["read", "write"], "mode": mode}
        decision = authority_check(parsed["effects"], self.grants, standing, self.prohibitions)
        key = _call_key(tool_name, tool_input)
        rej = [p for p in self.prohibitions if (p.get("scope") or {}).get("kind") == "exact_call"
               and p["scope"].get("tool") == tool_name and p["scope"].get("key") == key]
        if rej and decision["decision"] != "deny":
            p = max(rej, key=lambda x: x.get("turn", 0))
            later = [g for g in self.grants if g.get("turn", 0) > p.get("turn", 0)
                     and any(covers(g, e) for e in parsed["privileged"])]
            if not later:
                decision = {**decision, "decision": "deny", "reason": f"the user rejected this exact call (turn {p.get('turn')})"}
        return {"effects": parsed, "decision": decision}
