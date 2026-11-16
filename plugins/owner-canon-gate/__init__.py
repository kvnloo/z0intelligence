"""owner-canon-gate: runtime enforcement of the proven owner-canon laws (bend/owner_canon) for a Hermes profile.

Scope: LOCKED rules 1-15 of OWNER-CANON.md only (Kevin's OK 2026-11-05). Rules 16-60 are candidates, not gated.
Host boundary: this file turns a tool call into the typed facts the kernel decides on (kernel.py, the Python
twin of canon.bend). The kernel's refusal is final for the call; nothing here grants authorization.

Rules (law -> locked canon rule):
  gui_never_steals_focus  3:  GUI automation only on a virtual/headless display
  done_needs_receipt      6:  a done/fixed report (message_agent, send_message, kanban_complete) must survive
                              claimcheck.py: every typed claim (path, pushed, pr, merged, tests, deployed, service)
                              is dereferenced here. A failed or unlocated typed claim blocks; a bare done-word with
                              no typed claim, or a check that could not run (infra), is flagged. Mode key
                              "done_untyped": "enforce" also blocks the bare done-word. Lines starting with
                              "unverified:" are not claims. The final reply gets a "Checks:" footer instead of a block.
  quota_backs_off         15: after a 429/quota error, the same call waits for Retry-After (default 60 s)
Rule 8 (route by measured capability/quota) is proven in canon.bend route_ok over bend/model_routing; its runtime
gate is the existing router, not this plugin.

Standing rules (bend/owner_standing; Kevin's explicit rules, not canon; twin kernel_standing.py):
  gpt_only_via_fx_pi            S1: a GPT model dispatch (delegate_task or a model CLI launch) must go via fx/pi
                                (OpenAI-subscription models only; open-weight gpt-oss and OpenAI-compatible
                                /openai/v1 URLs on other providers are exempt, owner 11-12)
  delete_needs_creator_receipt  S2: deletes outside scratch need Kevin's 23:25 rule satisfied: the creating
                                session/job finished, one-line receipt {path, size, session|job, why} naming the target
                                in $HERMES_HOME/owner-canon-gate/creator-receipts/*.jsonl or the fleet-stage-lifecycle
                                receipt jsonl, or `canon-creator-receipt=<file>` in the command. $VAR and ~ in targets
                                are resolved (inline assignments, then the call's env) before judging; an unresolvable
                                target whose literal prefix is under a scratch dir is flagged, not blocked.
  public_needs_owner_ok         S3: public/upstream posts need the owner's OK ($HERMES_HOME/owner-canon-approvals.json,
                                owner-written; agent writes to it are blocked)

Modes per rule in $HERMES_HOME/owner-canon-gate.json {"mode": {"<law>": "enforce"|"flag"|"off"}}; default
enforce, except reuse_before_create = flag. Reuse (canon 5) reads
$HERMES_HOME/EXISTS.jsonl (id, name, where, seen_epoch); absent/invalid rows and rows older than 6h fail open.
Its action-slot log is $HERMES_HOME/owner-canon-gate/exists-injections.jsonl.
DONE.jsonl adds flag-only plan/edit lookups, logged to owner-canon-gate/done-injections.jsonl.
Receipts: $HERMES_HOME/owner-canon-gate/receipts.jsonl (law, tool, decision; no args) and
claim-receipts.jsonl (one line per typed claim: tool, type, ok, detail; no message text) and claim-calls.jsonl
(one line per message-tool call: outcome pass|flag|block, claims, bad, unverified_lines, session_id), so a block
followed in the same session by a pass shows whether the lane added a locator or wrote "unverified:".
Other enforce rules fail closed per call: a classifier error blocks the call.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_spec = importlib.util.spec_from_file_location("owner_canon_kernel", Path(__file__).with_name("kernel.py"))
assert _spec and _spec.loader
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)
_spec2 = importlib.util.spec_from_file_location("owner_standing_kernel", Path(__file__).with_name("kernel_standing.py"))
assert _spec2 and _spec2.loader
KS = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(KS)
_spec3 = importlib.util.spec_from_file_location("owner_canon_claimcheck", Path(__file__).with_name("claimcheck.py"))
assert _spec3 and _spec3.loader
claimcheck = importlib.util.module_from_spec(_spec3)
_spec3.loader.exec_module(claimcheck)

DEFAULT_MODE = {law: "enforce" for law in (*K.RULES, *KS.RULES)} | {"reuse_before_create": "flag"}
_MESSAGE_TOOLS = {"message_agent": ("message", "text"), "mcp__message_agent": ("message", "text"),
                  "send_message": ("message", "text"), "kanban_complete": ("summary", "result"),
                  "mcp__kanban_complete": ("summary", "result")}
_SHELL_TOOLS = {"terminal", "execute_code", "process", "mcp__terminal", "mcp__execute_code"}
_GUI = re.compile(r"(?i)\b(xdotool|ydotool|wmctrl|wtype|xte|hyprctl\s+dispatch|swaymsg\s+focus|osascript|cliclick|"
                  r"adb\b[^|;&]*\bshell\s+input\b|adb\b[^|;&]*\bam\s+start\b|pyautogui|pynput)\b")
_GUI_ISOLATED = re.compile(r"(?i)(--display[ =]?[1-9]\d*|DISPLAY=:(9\d|[1-9]\d\d+)|xvfb-run|Xvfb|--headless|"
                           r"WAYLAND_DISPLAY=\S*headless|fsdisplay|cage\s+-d|\bweston\b.*--backend=headless)")
_GPT = re.compile(r"(?i)\b(gpt-?\d[\w.\-]*|o[134](-mini|-pro)?\b|openai/(?!gpt-oss|v1\b)[\w.\-]+|codex-mini[\w.\-]*|"
                  r"gpt-6\.1-(sol|astra|terra|luna)|\b(sol|astra|terra|luna)-gpt)")
_FX_PI = re.compile(r"(^|[;&|]\s*|\bexec\s+|\btimeout\s+\d+\s+)(fx|pi)\s")
_MODEL_CLI = re.compile(r"(?i)\b(hermes|codex|openai|llm|aider|opencode|claude)\b[^\n]*(-m|--model|model[=:])")
_RM = re.compile(r"(?:^|[;&|(]\s*|\bsudo\s+)rm\s+((?:-[a-zA-Z]*[rRf][a-zA-Z]*\s+)+)([^;&|\n]+)", re.M)
_OTHER_DELETE = re.compile(r"(?:^|[;&|(]\s*)(git\s+branch\s+-D\b|git\s+worktree\s+remove\b|git\s+push\s+\S+\s+--delete\b|"
                           r"git\s+push\s+\S+\s+:\S|gh\s+repo\s+delete\b|docker\s+(rm|rmi|volume\s+rm)\b|"
                           r"hf\s+(repo|buckets)\s+(delete|rm)\b|rclone\s+(purge|delete)\b|\bDROP\s+(TABLE|DATABASE)\b)", re.M)
_SCRATCH_PREFIXES = ("/tmp/", "/var/tmp/", "/cache/scratch/", "/.cache/", "__pycache__", "/build/", ".pytest_cache")
_ASSIGN = re.compile(r"(?:^|[;&\s])(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s;&|]*)")
_RECEIPT_REF = re.compile(r"canon-creator-receipt[=:]\s*(\S+)")
_CREATOR_RECEIPT_GLOBS = ("owner-canon-gate/creator-receipts/*.jsonl",)
_FLEET_RECEIPTS = Path("/mnt/zer0models/workspace/zer0/data/operations/fleet-stage-lifecycle-20261104")
_PUBLIC = re.compile(r"(?i)\b(gh\s+(pr|issue)\s+(create|comment)\b[^\n]*--repo[ =]([\w.\-]+)/|"
                     r"gh\s+(release\s+create|gist\s+create\b[^\n]*--public)|xurl\s+(post|tweet)|twurl|"
                     r"npm\s+publish|twine\s+upload|cargo\s+publish|gh\s+repo\s+edit\b[^\n]*--visibility\s+public)")
_DONE = re.compile(r"(?i)\b(done|fixed|finished|complete[d]?|works now|verified|shipped|resolved)\b")
_QUOTA = re.compile(r"(?i)(\b429\b|rate.?limit|quota|too many requests|usage limit|retry-after)")
_last_limited: dict[str, float] = {}
_call = threading.local()  # session_id of the tool call being judged, for the claim receipts


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _mode(law: str) -> str:
    return (_json(_home() / "owner-canon-gate.json").get("mode") or {}).get(law, DEFAULT_MODE.get(law, "flag"))


def _receipt(rec: dict, filename: str = "receipts.jsonl") -> None:
    try:
        d = _home() / "owner-canon-gate"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / filename, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}) + "\n")
    except Exception:
        pass


def _text(tool: str, args: dict) -> str:
    if tool in _SHELL_TOOLS:
        return "\n".join(str(args.get(k) or "") for k in ("command", "code", "data"))
    return json.dumps(args, default=str)


def _key(tool: str, args: dict) -> str:
    return tool + ":" + _text(tool, args)[:300]


def _scratch(path: str) -> bool:
    tmp = os.environ.get("TMPDIR") or ""
    return any(x in path for x in _SCRATCH_PREFIXES) or bool(tmp and path.startswith(tmp.rstrip("/") + "/"))


def _resolve(word: str, env: dict) -> tuple[str, bool]:
    """Expand ~ and $VAR / ${VAR} with the call's env. Returns (path, fully_resolved)."""
    w = os.path.expanduser(word) if word.startswith("~") else word
    def sub(m):
        v = env.get(m.group(1) or m.group(2))
        return v if v is not None else m.group(0)
    out = re.sub(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)", sub, w)
    return out, "$" not in out and "`" not in out


def _receipt_names(target: str, text: str) -> bool:
    """Kevin 23:25: a one-line receipt naming the target with the creating session/job and why it finished."""
    t = os.path.normpath(target)
    def ok(rec):
        p = rec.get("path") or rec.get("target") or rec.get("stage") or ""
        return bool(p) and os.path.normpath(os.path.expanduser(str(p))) == t and any(
            rec.get(k) for k in ("session", "creating_session", "creator", "job", "job_id", "session_id"))
    files = []
    m = _RECEIPT_REF.search(text)
    if m:
        files.append(Path(os.path.expanduser(m.group(1))))
    for g in _CREATOR_RECEIPT_GLOBS:
        files += sorted(_home().glob(g))
    if _FLEET_RECEIPTS.is_dir():
        files += sorted(_FLEET_RECEIPTS.glob("*receipts*.jsonl"))
    for f in files:
        try:
            for line in f.read_text(errors="ignore").splitlines():
                if t in line or target in line:
                    try:
                        if ok(json.loads(line)):
                            return True
                    except Exception:
                        continue
        except Exception:
            continue
    return False


def delete_facts(text: str, env: dict | None = None) -> tuple[bool, bool, bool]:
    """(trigger, cleared, flag_only) for the deletes in one shell command."""
    env = dict(os.environ if env is None else env)
    for m in _ASSIGN.finditer(text):
        env[m.group(1)] = _resolve(m.group(2).strip("'\""), env)[0]
    trigger, cleared, flag_only = False, True, False
    unresolved_scratch, resolved_risky = [], False
    for m in _RM.finditer(text):
        try:
            words = shlex.split(m.group(2), comments=True)
        except ValueError:
            words = m.group(2).split()
        for w in words:
            if w.startswith("-"):
                continue
            path, full = _resolve(w, env)
            if full:
                if _scratch(path):
                    continue
                trigger, resolved_risky = True, True
                cleared = cleared and _receipt_names(os.path.abspath(path), text)
            else:
                literal = path.split("$", 1)[0]
                trigger, cleared = True, False
                unresolved_scratch.append(_scratch(literal) or w.startswith(("$TMPDIR", "${TMPDIR}")))
    # flag (not block) only when every risky target is an unresolvable path whose literal prefix is scratch
    flag_only = bool(unresolved_scratch) and all(unresolved_scratch) and not resolved_risky
    if _OTHER_DELETE.search(text):
        trigger = True
        ref = _RECEIPT_REF.search(text)
        cleared = cleared and bool(ref and Path(os.path.expanduser(ref.group(1))).is_file())
        flag_only = False
    return trigger, cleared if trigger else False, flag_only


def _reuse_name(value: str) -> str:
    name = value.rstrip("/").rsplit("/", 1)[-1]
    name = re.split(r"[<>=!~\[]|@", name, maxsplit=1)[0]
    name = re.sub(r"\.(git|service|timer)$", "", name, flags=re.I)
    return re.sub(r"[-_.]+", "-", name).lower()


def _reuse_targets(tool: str, args: dict) -> set[str]:
    if tool in ("write_file", "patch", "mcp__write_file", "mcp__patch"):
        path = Path(str(args.get("path") or ""))
        if path.name == "SKILL.md":
            return {_reuse_name(path.parent.name)}
        return {_reuse_name(path.name)} if path.suffix in (".service", ".timer") else set()
    if tool not in _SHELL_TOOLS:
        return set()
    targets = set()
    # shlex keeps quoted prose intact; only the command position can trigger.
    for line in _text(tool, args).splitlines():
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
            lexer.whitespace_split = True
            words, commands = [], []
            for word in lexer:
                if word and all(c in ";&|()" for c in word):
                    commands.append(words)
                    words = []
                else:
                    words.append(word)
            commands.append(words)
        except ValueError:
            continue
        for words in commands:
            while words and (words[0] in ("sudo", "env") or re.match(r"^\w+=", words[0])):
                words = words[1:]
            if not words:
                continue
            exe = Path(words[0]).name
            tail, first = [], False
            if exe == "git" and words[1:2] == ["clone"]:
                tail, first = words[2:], True
            elif exe == "gh" and words[1:3] == ["repo", "create"]:
                tail, first = words[3:], True
            elif re.fullmatch(r"python[\d.]*", exe) and words[1:3] == ["-m", "venv"]:
                tail = words[3:]
            elif exe in ("pip", "pip3", "uv", "npm", "cargo", "pacman", "yay"):
                rest = words[1:]
                if exe == "uv" and rest[:1] in (["pip"], ["tool"]):
                    rest = rest[1:]
                if rest and (rest[0] == "install" or
                             exe in ("pacman", "yay") and re.fullmatch(r"-S\w*|--sync", rest[0])):
                    tail = rest[1:]
            elif exe == "systemctl" and "--user" in words:
                rest = [w for w in words[1:] if w not in ("--user", "--now")]
                if rest[:1] in (["enable"], ["link"]):
                    tail = rest[1:]
            elif exe == "mkdir":
                tail = [w for w in words[1:] if "skills" in Path(w).parts[:-1] or
                        Path(os.path.expanduser(w)).is_relative_to(Path.home() / "bin")]
            values = []
            it = iter(tail)
            for word in it:
                if word in ("--depth", "--branch", "-b", "--origin", "-o", "--config", "-c",
                            "--version", "--root", "--target", "-t", "--index-url", "-i",
                            "--extra-index-url", "--registry", "--prompt", "-r", "--requirement"):
                    next(it, None)
                elif not word.startswith("-"):
                    values.append(word)
            targets.update(_reuse_name(w) for w in (values[:1] if first else values))
    return targets


def reuse_facts(tool: str, args: dict) -> tuple[bool, bool, str]:
    """Fresh exact-name inventory matches only; unavailable inventory fails open."""
    targets = _reuse_targets(tool, args)
    if not targets:
        return False, False, ""
    try:
        lines = (_home() / "EXISTS.jsonl").read_text().splitlines()
    except (OSError, UnicodeError):
        return False, False, ""
    text, rows = _text(tool, args), []
    now = time.time()
    for line in lines:
        try:
            row = json.loads(line)
            if not all(isinstance(row[k], str) and row[k] for k in ("id", "name", "where")):
                continue
            if not 0 <= now - float(row["seen_epoch"]) <= 6 * 3600:
                continue
            if _reuse_name(row["name"]) not in targets or row["where"] in text:
                continue
            ack = re.search(r"\bexists-ack=" + re.escape(row["id"]) + r"[ \t]+([^\n;&|]+)", text)
            if ack and re.sub(r"^(?:reason|why)\s*[=:]\s*", "", ack[1]).strip(" \t\"'}"):
                continue
            rows.append(row)
        except (ValueError, KeyError, TypeError):
            continue
    why = "; ".join(f"{r['id']} at {r['where']} seen_epoch={r['seen_epoch']}" for r in rows[:3])
    return bool(rows), False, why + ("; reuse that location or add exists-ack=<id> plus a reason" if rows else "")


def checked_claims(tool: str, msg: str, session_id=None) -> list[dict]:
    """Typed claims in msg, each dereferenced by claimcheck and written to claim-receipts.jsonl (no message text)."""
    claims = claimcheck.check(msg, str(_home()))
    session_id = session_id or getattr(_call, "session_id", None)
    for c in claims:
        _receipt({"tool": tool, "type": c["type"], "ok": c["ok"] is True, "detail": c["detail"],
                  "session_id": session_id}, "claim-receipts.jsonl")
    return claims


def claim_facts(tool: str, msg: str) -> tuple[bool, bool, str, bool]:
    """claim_verdict plus one claim-calls.jsonl line for the call (counts only, no message text)."""
    trigger, cleared, why, flag_only = verdict = claim_verdict(tool, msg)
    _receipt({"tool": tool, "outcome": "pass" if cleared or not trigger else "flag" if flag_only or _mode(
        "done_needs_receipt") != "enforce" else "block", "typed": why != "done claim with nothing checkable" and trigger,
        "unverified_lines": sum(ln.strip().lstrip("-*•> ").lower().startswith("unverified:") for ln in msg.splitlines()),
        "session_id": getattr(_call, "session_id", None)}, "claim-calls.jsonl")
    return verdict


def claim_verdict(tool: str, msg: str) -> tuple[bool, bool, str, bool]:
    """(trigger, cleared, why, flag_only) for done_needs_receipt. Blocks only on a typed claim that was checked
    and failed, or that names nothing to check."""
    try:
        claims = checked_claims(tool, msg)
    except Exception as e:  # a checker bug flags; it never blocks a teammate message
        return bool(_DONE.search(msg)), False, f"claim check failed to run ({type(e).__name__})", True
    bad = [c for c in claims if c["ok"] is not True]
    if claims and not bad:
        return True, True, "", False
    if not claims:
        return bool(_DONE.search(msg)), False, "done claim with nothing checkable", _mode("done_untyped") != "enforce"
    return True, False, ("claims NOT verified: " + claimcheck.summary(claims) +
                         '. Fix the claim, add its locator, or start that line with "unverified:"'), all(
        claimcheck.infra(c) for c in bad)


def reply_footer(response_text: str = "", **kw):
    """transform_llm_output: the final reply is never blocked; unverified typed claims get one honest line."""
    try:
        if not response_text or "\nChecks: " in response_text:
            return None
        claims = checked_claims("reply", response_text, kw.get("session_id"))
        bad = [c for c in claims if c["ok"] is not True]
        if not bad:
            return None
        return (f"{response_text.rstrip()}\n\nChecks: {len(claims) - len(bad)} verified · {len(bad)} NOT verified "
                f"({claimcheck.summary(claims)})")
    except Exception:
        return None


def _done_matches(tool: str, args: dict) -> list[tuple[str, str]]:
    """DONE rows: id, kind, scope, repo, branch, paths, seen_epoch.

    Scope defaults to the canonical repo path. The inventory expires after 6h;
    edit matches also expire 14 days after seen_epoch. Lookup failures fail open.
    """
    write = tool in ("write_file", "patch", "mcp__write_file", "mcp__patch")
    task = tool in ("kanban", "mcp__kanban") and args.get("action") in ("create", "create_task")
    if not (write or task) or _mode("reuse_before_create") == "off":
        return []
    try:
        inventory = _home() / "DONE.jsonl"
        now = time.time()
        if not 0 <= now - inventory.stat().st_mtime <= 6 * 3600:
            return []
        lines = inventory.read_text().splitlines()
        cwd = Path(args.get("cwd") or os.getcwd()).resolve()
        path = (cwd / str(args.get("path") or "")).resolve()
        plan = task or write and bool(re.fullmatch(r"PLAN.*|ROADMAP.*|.*plan.*\.md", path.name))
        # New plan directories may not exist yet. Git only needs an existing ancestor.
        base = path.parent if write else cwd
        while not base.exists():
            base = base.parent

        def git(*arguments):
            return subprocess.run(["git", "-C", str(base), *arguments], check=True,
                                  capture_output=True, text=True, timeout=1).stdout.strip()

        try:
            root = Path(git("rev-parse", "--show-toplevel"))
            repo = str(Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).parent)
        except subprocess.CalledProcessError:
            root, repo = cwd, ""
        scope = args.get("scope") or repo or str(cwd)
        branch, relative = "", ""
        if repo and write and path.is_file():
            relative = path.relative_to(root).as_posix()
            if git("ls-files", "--", relative) == relative:
                branch = git("symbolic-ref", "--short", "HEAD")
        matches = []
        for line in lines:
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                    continue
                if plan and row.get("kind") in ("plan", "task") and row.get("scope") == scope:
                    matches.append(("plan", row["id"]))
                if (branch and row.get("repo") == repo and row.get("branch")
                        and row["branch"] != branch and isinstance(row.get("paths"), list)
                        and relative in row["paths"] and 0 <= now - float(row["seen_epoch"]) <= 14 * 86400):
                    matches.append(("edit", row["id"]))
            except (ValueError, KeyError, TypeError):
                continue
        return matches
    except (OSError, UnicodeError, ValueError, TypeError, subprocess.SubprocessError):
        return []


def facts(tool: str, args: dict) -> dict:
    """Host facts for one tool call: {law: (trigger, cleared, why)}."""
    t = _text(tool, args)
    f = {"reuse_before_create": reuse_facts(tool, args)}
    gui = tool in _SHELL_TOOLS and bool(_GUI.search(t))
    f["gui_never_steals_focus"] = (gui, gui and bool(_GUI_ISOLATED.search(t)), "GUI input on the owner's display")
    if tool in _MESSAGE_TOOLS:
        f["done_needs_receipt"] = claim_facts(tool, "\n".join(str(args.get(k) or "") for k in _MESSAGE_TOOLS[tool]))
    dispatch = tool in ("delegate_task", "mcp__delegate_task") or (tool in _SHELL_TOOLS and bool(_MODEL_CLI.search(t)))
    gpt = dispatch and bool(_GPT.search(t))
    f["gpt_only_via_fx_pi"] = (gpt, gpt and bool(_FX_PI.search(t)), "GPT model dispatched outside fx/pi")
    if tool in _SHELL_TOOLS:
        x, xc, flag_only = delete_facts(t, args.get("env") if isinstance(args.get("env"), dict) else None)
        if x:
            f["delete_needs_creator_receipt"] = (x, xc, "delete outside scratch without a finished-creator receipt"
                                                 + (" (unresolved $VAR under scratch: flagged)" if flag_only else ""), flag_only)
        pub = _PUBLIC.search(t)
        if pub:
            owner = (pub.group(4) or "").lower()
            appr = _json(_home() / "owner-canon-approvals.json").get("public") or []
            f["public_needs_owner_ok"] = (owner != "kvnloo", any(a and a in t for a in appr),
                                          "public/upstream action without the owner's OK")
    until = _last_limited.get(_key(tool, args), 0)
    f["quota_backs_off"] = (until > 0, time.time() >= until, "retry before the quota/rate-limit back-off ended")
    return f


def decide(tool: str, args: dict) -> tuple[bool, list[tuple[str, str]]]:
    """Returns (block, violations[(law, why)]). Uses the twin kernel predicates, then per-law mode."""
    f = facts(tool, args)
    full = {law: f.get(law, (False, False, ""))[:2] for law in (*K.RULES, *KS.RULES)}
    canon = [b for law in ("gui_never_steals_focus", "done_needs_receipt", "quota_backs_off") for b in full[law]]
    standing = [b for law in ("gpt_only_via_fx_pi", "delete_needs_creator_receipt", "public_needs_owner_ok",
                              "reuse_before_create") for b in full[law]]
    if K.admits(*canon) and KS.admits(*standing):
        return False, []
    rules = {**K.RULES, **KS.RULES}
    viol = [(law, f[law][2]) for law, (trg, clr) in full.items() if not rules[law][1](trg, clr)]
    flag_only = {law for law, _ in viol if len(f[law]) > 3 and f[law][3]}
    block = any(_mode(law) == "enforce" and law not in flag_only for law, _ in viol)
    viol = [(law, why) for law, why in viol if _mode(law) != "off"]
    return block and bool(viol), viol


def on_pre_tool_call(tool_name: str = "", args: dict | None = None, **kw):
    args = args or {}
    _call.session_id = kw.get("session_id")
    try:
        raw = json.dumps(args, default=str)
        target = str(args.get("path") or "") if isinstance(args, dict) else ""
        if (tool_name in ("write_file", "patch", "mcp__write_file", "mcp__patch") and target.endswith("owner-canon-approvals.json")) or (
                "owner-canon-approvals.json" in raw and (tool_name in _SHELL_TOOLS and re.search(r"(>|tee|cp|mv|sed\s+-i|write_text|open\()[^\n]*owner-canon-approvals\.json", raw))):
            _receipt({"law": "approval_file_protected", "tool": tool_name, "decision": "block"})
            return {"action": "block", "message": "owner-canon: agents never write the owner's approvals file."}
        block, viol = decide(tool_name, args)
        for slot, row_id in _done_matches(tool_name, args):
            message = f"DONE {row_id}: review completed work before this {slot}"
            _receipt({"slot": slot, "law": "reuse_before_create", "tool": tool_name,
                      "message": message, "decision": "flag", "session_id": kw.get("session_id")},
                     "done-injections.jsonl")
            logger.warning("owner-canon flag: reuse_before_create (%s)", message)
        if not viol:
            return None
        _receipt({"laws": [v[0] for v in viol], "tool": tool_name, "decision": "block" if block else "flag",
                  "session_id": kw.get("session_id")})
        text = "; ".join(f"{law} ({why})" for law, why in viol)
        for law, why in viol:
            if law == "reuse_before_create":
                _receipt({"slot": "action", "law": law, "tool": tool_name, "message": why,
                          "decision": _mode(law), "session_id": kw.get("session_id")}, "exists-injections.jsonl")
        if block:
            return {"action": "block", "message": f"owner-canon gate blocked this call: {text}. "
                    "See bend/owner_canon/LAWS.bend and bend/owner_standing/LAWS.bend. Fix the call (a virtual "
                    "display, evidence, fx/pi for GPT, a finished-creator receipt, or the owner's OK, or wait for the "
                    "back-off) instead of retrying it."}
        logger.warning("owner-canon flag: %s", text)
        return None
    except Exception as e:
        _receipt({"law": "classifier_error", "tool": tool_name, "decision": "block", "error": type(e).__name__})
        return {"action": "block", "message": f"owner-canon gate could not classify this call ({type(e).__name__}); blocked."}


def on_post_tool_call(tool_name: str = "", args: dict | None = None, result=None, **kw):
    try:
        r = result if isinstance(result, str) else json.dumps(result, default=str)
        if r and _QUOTA.search(r[:4000]):
            m = re.search(r"(?i)retry.?after\D{0,5}(\d+)", r)
            _last_limited[_key(tool_name, args or {})] = time.time() + (int(m.group(1)) if m else 60)
    except Exception:
        pass


def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_llm_output", reply_footer)
