"""Ops plane: the Python twin of bend/ops_fsm/ops.bend, plus the input classifiers.

The decision functions here (`admits`, `place`, `wt_allows`, `rollout`,
`push_allows`, `phone_allows`, `a11y_ok`, `route_ok`, `nstep`/`neff`,
`rstep`) are the same tables as ops.bend; `ops_parity.py` checks them
against Bend's normalizer (exhaustively where the domain is finite). The
gates that real tools call (`z0-guard`, git hooks, fleet-build, fleet health,
the install wrapper, the z0live broker) decide with these functions only.

The classifiers below them (`classify_command`, `owner_phone_intent`,
`parse_a11y`, `provider_flags`, `design_role`) turn real inputs into the
abstract ones. They are heuristics: not proven, documented in docs/fsm/ops.md.
Stdlib only, so other repos can import or vendor this file.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum

# ------------------------------------------------------------------ O1


class Cls(Enum):
    TEST = "test"
    MUTATION = "mutation"
    TYPECHECK = "typecheck"
    GRADLE = "gradle"
    BUNDLE = "bundle"
    EVAL = "eval"
    SHOT = "screenshot-run"
    GPU = "gpu"
    BEND = "bend"
    LEAN = "lean"
    SERVICE = "service"
    AGENT = "agent-process"
    TINY = "tiny"


GROOT_CLASSES = frozenset({Cls.GPU, Cls.BEND, Cls.LEAN, Cls.SERVICE, Cls.AGENT, Cls.TINY})
HEAVY_CLASSES = frozenset(set(Cls) - GROOT_CLASSES)
TINY_SECONDS = 10


class Lane(Enum):
    GROOT = "groot"
    MBP = "mbp"
    PHONE = "phone"


def groot_class(c: Cls) -> bool:
    return c in GROOT_CLASSES


def admits(lane: Lane, c: Cls) -> bool:
    return groot_class(c) if lane is Lane.GROOT else True


def place(c: Cls, mbp_room: bool, phone_room: bool) -> Lane | None:
    """The lane a job of class c runs on now, or None: wait (never groot for heavy)."""
    if groot_class(c):
        return Lane.GROOT
    if mbp_room:
        return Lane.MBP
    if phone_room:
        return Lane.PHONE
    return None


def phone_admits(ready: bool, memory: bool, cpu: bool, battery: bool, thermal: bool) -> bool:
    """O10: resource observations do not change phone readiness."""
    return ready


def phone_place(ready: bool, memory: bool, cpu: bool, battery: bool, thermal: bool) -> Lane | None:
    return Lane.PHONE if phone_admits(ready, memory, cpu, battery, thermal) else None


# ------------------------------------------------------------------ O2


class GitOp(Enum):
    EDIT = "edit"
    COMMIT = "commit"
    CHECKOUT = "checkout"
    STASH = "stash"
    MERGE = "merge"
    MERGE_FF = "merge-ff"
    ROLLBACK = "rollback"   # the rollout transaction's own revert to the revision it replaced


def wt_allows(served: bool, op: GitOp, txn: bool) -> bool:
    if not served:
        return True
    return op in (GitOp.MERGE_FF, GitOp.ROLLBACK) and txn


# ------------------------------------------------------------------ O3


@dataclass(frozen=True)
class Req:
    clean: bool     # candidate built from a non-served worktree
    ff: bool        # served worktree fast-forwards to it
    gated: bool     # PR marked review-gated
    receipt: bool   # orchestrator review receipt present
    call: bool      # device work blocked now (owner z0live call live per O5, or no device)
    idle: bool      # service idle: no request in flight


class Out(Enum):
    REFUSED = "refused"
    DEFERRED = "deferred"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled-back"


def review_ok(gated: bool, receipt: bool) -> bool:
    return (not gated) or receipt


def pre_ok(q: Req) -> bool:
    return q.clean and q.ff and review_ok(q.gated, q.receipt)


def ready(q: Req) -> bool:
    return (not q.call) and q.idle


def rollout(q: Req, check: bool) -> Out:
    if not pre_ok(q):
        return Out.REFUSED
    if not ready(q):
        return Out.DEFERRED
    return Out.PROMOTED if check else Out.ROLLED_BACK


def admission(q: Req) -> Out | None:
    """What the gate does before touching anything: REFUSED, DEFERRED, or None = run the
    transaction (whose result is rollout(q, check) once the launch check reports)."""
    out_pass, out_fail = rollout(q, True), rollout(q, False)
    if out_pass is out_fail:
        return out_pass
    return None


def why_refused(q: Req) -> list[str]:
    out = []
    if not q.clean:
        out.append("O3a: candidate not built from a separate, clean, non-served worktree")
    if not q.ff:
        out.append("O2: served worktree does not fast-forward to the candidate")
    if not review_ok(q.gated, q.receipt):
        out.append("O3c: review-gated PR without an orchestrator review receipt")
    return out


def why_deferred(q: Req) -> list[str]:
    out = []
    if q.call:
        out.append("O3xO5: device launch check cannot run (owner z0live call live, or no device)")
    if not q.idle:
        out.append("O2: service not idle")
    return out


# ------------------------------------------------------------------ O4


class Vis(Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    UNKNOWN = "unknown"


def push_allows(private: bool, vis: Vis) -> bool:
    return vis is Vis.PRIVATE if private else True


# ------------------------------------------------------------------ O5


class PKind(Enum):
    TAP = "tap"          # any UI input: tap, type, swipe, key
    INSTALL = "install"
    RESTART = "restart"


def phone_allows(kind: PKind, *, intent: bool = False, fg: bool = False,
                 chain: bool = False, call: bool = False) -> bool:
    if kind is PKind.TAP:
        return intent and fg
    return not (chain and call)


# ------------------------------------------------------------------ O6


def mem(x, xs) -> bool:
    return any(h == x for h in xs)


def a11y_ok(pre, post, removed=()) -> bool:
    """post ⊇ pre minus the services the owner removed."""
    return all(mem(h, post) or mem(h, removed) for h in pre)


def a11y_missing(pre, post, removed=()) -> list:
    return [h for h in pre if not (mem(h, post) or mem(h, removed))]


# ------------------------------------------------------------------ O7


def route_ok(cancelled: bool, sol: bool, design: bool) -> bool:
    return (not cancelled) and not (design and sol)


# ------------------------------------------------------------------ O8

NODE_LIMIT_S = 30 * 60


@dataclass(frozen=True)
class NSt:
    kind: str = "healthy"   # healthy | bad | notified
    since: int = 0


def late(now: int, base: int, lim: int) -> bool:
    return now > base + lim


def nstep(s: NSt, ok: bool, now: int, lim: int = NODE_LIMIT_S) -> NSt:
    if ok:
        return NSt()
    if s.kind == "healthy":
        return NSt("bad", now)
    if s.kind == "bad":
        return NSt("notified", s.since) if late(now, s.since, lim) else s
    return s


def neff(s: NSt, ok: bool, now: int, lim: int = NODE_LIMIT_S) -> bool:
    """Does this probe emit the one owner notification?"""
    return s.kind == "bad" and (not ok) and late(now, s.since, lim)


# ------------------------------------------------------------------ O9


@dataclass(frozen=True)
class RSt:
    kind: str           # active | interrupted | failed | done | resumed | reported
    gw: int = 0


def rstep(s: RSt, ev: str, x: int = 0, session: bool = False) -> RSt:
    """ev: gw-restart(x) | error | done | resume(session) | report."""
    if s.kind == "active":
        if ev == "gw-restart":
            return RSt("interrupted", s.gw) if x == s.gw else s
        if ev == "error":
            return RSt("failed")
        if ev == "done":
            return RSt("done")
        return s
    if s.kind == "interrupted":
        if ev == "resume":
            return RSt("resumed") if session else s
        if ev == "report":
            return RSt("reported")
        return s
    return s


# O13: the caller validates compiled evidence and encodes full physical scopes.
def option_limit(credited: bool, actual_scope: int, named_scope: int, measured: int) -> int:
    return measured if credited and actual_scope == named_scope else 0


def option_capacity_allows(credited: bool, actual_scope: int, named_scope: int,
                           measured: int, count: int) -> bool:
    return count <= option_limit(credited, actual_scope, named_scope, measured)


def option_risk_allows(risk: int) -> bool:
    """Only read (0) and explicitly reversible mutation (1) have SLM authority."""
    return type(risk) is int and risk in (0, 1)


def risk_option_capacity_allows(risk: int, credited: bool, actual_scope: int,
                               named_scope: int, measured: int, count: int) -> bool:
    return (option_risk_allows(risk)
            and option_capacity_allows(credited, actual_scope, named_scope, measured, count))


# =================================================================== classifiers
# Heuristics that map real inputs to the abstract ones above. Not proven.

_HEAVY_PATTERNS: list[tuple[Cls, re.Pattern]] = [
    (Cls.MUTATION, re.compile(r"\b(mutmut|stryker|mutate(\.ts|\.py)?|cosmic-ray|cargo[- ]mutants)\b")),
    (Cls.GRADLE, re.compile(r"(^|[\s/])(\./)?gradlew?\b")),
    (Cls.TYPECHECK, re.compile(r"\b(tsc|mypy|pyright|vue-tsc|flow check)\b|\bbun run typecheck\b")),
    (Cls.BUNDLE, re.compile(r"\bexpo export\b|\bvite build\b|\bwebpack\b|\besbuild\b|\bbun build\b|\bnext build\b|\brollup\b|\bnpm run build\b|\bbun run build\b|\bmetro (build|bundle)\b")),
    (Cls.SHOT, re.compile(r"\b(playwright|puppeteer|shot-scraper|agent-browser)\b|\bscreenshot")),
    (Cls.EVAL, re.compile(r"\b(evals?|lm_eval|inspect eval|promptfoo|replay)\b")),
    (Cls.TEST, re.compile(r"\b(pytest|unittest|jest|vitest|mocha|ctest|go test|cargo test|bun test|npm test|bun run test|npm run test|deno test|node --test|gradle test|mvn test)\b")),
]
_GROOT_PATTERNS: list[tuple[Cls, re.Pattern]] = [
    (Cls.BEND, re.compile(r"(^|[\s/])bend(\s|$)|\bhermes bend\b|\bbend_verify\b")),
    (Cls.LEAN, re.compile(r"(^|[\s/])(lean|lake)(\s|$)")),
    (Cls.GPU, re.compile(r"\b(nvidia-smi|cuda|vllm|llama-server|comfyui|torchrun)\b")),
]


def classify_command(cmd: str | list[str]) -> Cls | None:
    """Best-effort class of a shell command. None = unknown: the caller must declare one.
    Commands already routed off groot (fleet-build, fleet run, ssh) are not local work."""
    text = cmd if isinstance(cmd, str) else shlex.join(cmd)
    first = text.strip().split()[0] if text.strip() else ""
    if first.endswith(("fleet-build", "fleet")) or first in ("ssh",):
        return None
    for c, pat in _GROOT_PATTERNS:
        if pat.search(text):
            return c
    for c, pat in _HEAVY_PATTERNS:
        if pat.search(text):
            return c
    return None


def parse_class(name: str) -> Cls:
    aliases = {"screenshot": Cls.SHOT, "shot": Cls.SHOT, "agent": Cls.AGENT, "tests": Cls.TEST,
               "build": Cls.BUNDLE, "android": Cls.GRADLE}
    try:
        return Cls(name)
    except ValueError:
        if name in aliases:
            return aliases[name]
        raise ValueError(f"unknown job class {name!r}; one of: {', '.join(c.value for c in Cls)}")


# O2: git hook / argv -> op
def git_op(hook: str, args: list[str] | None = None) -> GitOp:
    if hook in ("pre-commit", "commit-msg", "prepare-commit-msg", "pre-applypatch"):
        return GitOp.COMMIT
    if hook in ("pre-merge-commit",):
        return GitOp.MERGE
    if hook in ("post-checkout",):
        return GitOp.CHECKOUT
    if hook in ("pre-rebase",):
        return GitOp.MERGE
    return GitOp.EDIT


# O5: explicit owner phone-operation intent (verb + UI object). The z0live broker has
# its own grammar (kvnloo/z0live#42 phone_control.py); this is the shared fallback.
_PHONE_VERB = re.compile(r"\b(open|tap|press|click|scroll|swipe|type|enter|go to|navigate|launch|close|toggle|turn (on|off)|switch to|select|pick|dial|call)\b", re.I)
_PHONE_NOUN = re.compile(r"\b(app|screen|button|tab|settings?|menu|page|phone app|dialer|keyboard|notification|chrome|spotify|maps|camera|messages|whatsapp|lawnchair|noop|oura|youtube|gmail|calendar|home screen|wifi|bluetooth|volume|brightness)\b", re.I)
_PHONE_NEG = re.compile(r"\b(did (it|my message|that|you send)|go(ne)? through|check(ing)? (the )?(relay|status|backend|bridge)|status|what did .* say|not on my phone|(use|through) the (backend|bridge)|don'?t need to)\b", re.I)


def owner_phone_intent(utterance: str) -> bool:
    u = utterance or ""
    if _PHONE_NEG.search(u) and not re.search(r"\b(tap|press|click|scroll|swipe|type)\b", u, re.I):
        return False
    return bool(_PHONE_VERB.search(u) and _PHONE_NOUN.search(u))


# O6
def parse_a11y(value: str | None) -> list[str]:
    v = (value or "").strip().strip("'\"")
    if v in ("", "null"):
        return []
    return [s for s in (p.strip() for p in v.split(":")) if s]


# O7
CANCELLED_PROVIDERS = {"xai": "2026-10-21", "xai-oauth": "2026-10-21", "grok": "2026-10-21"}
_DESIGN_ROLE = re.compile(r"(design|\bux\b|\bui\b|visual|frontend|taste|styl|brand|layout|art.?direct)", re.I)


def provider_flags(provider: str | None, model: str | None) -> tuple[bool, bool]:
    """(cancelled, sol) for a provider/model selector like 'xai-oauth' + 'grok-4.6' or
    'openai-codex/gpt-6.1-sol'."""
    p = (provider or "").lower()
    m = (model or "").lower()
    if "/" in m and not p:
        p, _, m = m.partition("/")
    cancelled = p in CANCELLED_PROVIDERS or m.startswith("grok") or "/grok" in m or m.startswith("xai/")
    sol = bool(re.search(r"(^|[-/._])sol($|[-/._])", m))
    return cancelled, sol


def design_role(role: str) -> bool:
    return bool(_DESIGN_ROLE.search(role or ""))


# O16: SQLite lifecycle. This table checks observed facts, never authorizes IO.
def sqlite_replace_allows(complete: bool, empty: bool) -> bool:
    return complete and empty


def sqlite_holders_ok(complete: bool, ghosts: bool) -> bool:
    return complete and not ghosts


def sqlite_auth_ok(known: bool, before: int, after: int) -> bool:
    return known and before <= after


def sqlite_sidecar_allows(current: bool) -> bool:
    return current
