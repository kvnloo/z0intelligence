"""AODL intent contract -> exact-scope action grants + prohibitions (z0int#55 enforcement layer).

The action-authority check (``action_authority.authority_check``) compares the concrete effects of a
tool call with grants. Grants so far came from the *conversation* (prompts, answers, literal commands).
This module adds the *authored contract*: a validated AODL (HOTL 0.2) document whose executor nodes
carry ``authorityScopes`` (kvnloo/aodl ``spec/authority-scopes.md``) compiles to grants whose scope is
exactly what the document names, and whose provenance is ``{source: "aodl", fingerprint, node}``.

Where the contract lives (both are read; each is validated, fingerprinted and pinned independently)::

    ~/.z0int/state/claude-code/intent.aodl.json      session-level, declared by the user or a planning step
    <repo root>/.aodl/intent.json                    per-repo

Rules (all fail closed -- an entry that cannot be enforced *exactly* grants nothing):

* The document must validate (``aodl_contract.validate``) and fingerprint (``aodl-canon-1``). Anything
  else -- missing ``aodl_contract``, unreadable JSON, unknown specVersion, any validator issue -- is
  ``status: rejected``: no grants, no prohibitions.
* A pin (``aodl-pins.json`` in the state dir, written only from an interactive terminal) names the
  fingerprint the user approved. A pinned contract whose fingerprint moved is ``pin_mismatch``: no
  grants (an edit, including an agent's edit of an in-repo contract, can only revoke). Unpinned
  contracts compile (``pinned: false``); the enforcing stages require a pin (``require_pin=True``).
* Grants come only from ``executor`` nodes whose ``harness`` is this harness (``claude``).
  Prohibitions come from *every* node and bind every executor.
* Each scope must carry the target keys that make its effect kind exact (``KIND_TARGETS``) and, for
  repo-relative kinds (branches, PRs, CI ...), ``repos``. ``repos`` is matched by git common dir, so a
  repo scope covers its worktrees and nothing else. Kinds whose effects carry no narrowable target
  (``secret``, ``sudo``, ``message``, ``external_system``, ``upload``) are never granted by a contract.
* Grants are ``turn = 0`` (a later in-session prohibition still wins); contract prohibitions are
  ``turn = AODL_PROHIBIT_TURN`` (they win over every grant, in-session or authored).
"""

from __future__ import annotations

import json
import os

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only (hook latency)
    from typing import Any, Iterable, Mapping  # noqa: F401

SCHEMA = "z0int.aodl_grants.v0"
HARNESS_ID = "claude"
AODL_GRANT_TURN = 0
AODL_PROHIBIT_TURN = 10 ** 9
SESSION_FILE = "intent.aodl.json"
REPO_FILE = os.path.join(".aodl", "intent.json")
PINS_FILE = "aodl-pins.json"

_BRANCH = frozenset({"push", "commit", "force", "rewrite"})
# effect kind -> target keys; a scope must name at least one key in ``need`` and may use only ``allowed``
KIND_TARGETS: dict[str, dict[str, frozenset]] = {
    **{k: {"need": frozenset({"branches"}), "allowed": frozenset({"branches"})} for k in _BRANCH},
    "merge": {"need": frozenset({"branches", "prs"}), "allowed": frozenset({"branches", "prs"})},
    "delete": {"need": frozenset({"branches", "paths"}), "allowed": frozenset({"branches", "paths"})},
    "fs_outside_repo": {"need": frozenset({"paths"}), "allowed": frozenset({"paths"})},
    "ssh": {"need": frozenset({"hosts"}), "allowed": frozenset({"hosts"})},
    "network": {"need": frozenset({"hosts"}), "allowed": frozenset({"hosts"})},
    "service": {"need": frozenset({"units"}), "allowed": frozenset({"units", "hosts"})},
    "install": {"need": frozenset({"packages"}), "allowed": frozenset({"packages"})},
    "deploy": {"need": frozenset({"envs"}), "allowed": frozenset({"envs"})},
    "publish": {"need": frozenset({"envs"}), "allowed": frozenset({"envs"})},
    # kind is the only narrowing inside a repo: ``repos`` is the scope
    **{k: {"need": frozenset(), "allowed": frozenset()} for k in ("pr", "comment", "issue", "ci", "discard")},
}
# kinds whose scope is meaningless without the repo it happens in
REPO_KINDS = _BRANCH | {"merge", "pr", "comment", "issue", "ci", "discard"}
NEVER_GRANTED = frozenset({"secret", "sudo", "message", "external_system", "upload"})
CLASS_WORDS = frozenset({"read", "write", "execute"})  # capability words, not action kinds: standing decides


# ------------------------------------------------------------------------------------------------ paths
def _home() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get("Z0INT_HOME") or "~/.z0int"))


def state_dir(harness: str = "claude-code") -> str:
    return os.path.join(_home(), "state", harness)


def contract_paths(ctx: Any = None) -> list[str]:
    """Candidate contract files for a call: session-level first, then the repo's own."""
    out = [os.path.join(state_dir(), SESSION_FILE)]
    root = getattr(ctx, "scope_root", None)
    if root:
        out.append(os.path.join(root, REPO_FILE))
    return out


def pins_path() -> str:
    return os.path.join(state_dir(), PINS_FILE)


def load_pins() -> dict[str, str]:
    try:
        with open(pins_path(), encoding="utf-8") as fh:
            pins = json.load(fh)
        return {str(k): str(v) for k, v in pins.items()} if isinstance(pins, dict) else {}
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------------------------------- repo identity
def _common_dir(path: str) -> str | None:
    from .action_effects import git_dirs
    g = git_dirs(path) if os.path.isdir(path) else None
    return os.path.realpath(g[2]) if g else None


def _origin_slug(common: str | None) -> str | None:
    """owner/name of the origin remote, read from the git config file (no subprocess)."""
    if not common:
        return None
    try:
        with open(os.path.join(common, "config"), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    import re
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text)
    if not m:
        return None
    url = m.group(1).removesuffix(".git").rstrip("/")
    parts = re.split(r"[/:]", url)
    return "/".join(parts[-2:]).lower() if len(parts) >= 2 else None


def _resolve_repo(entry: str, base: str | None) -> str | None:
    p = os.path.expanduser(entry)
    if not os.path.isabs(p):
        if base is None:
            return None  # a relative repo in the session-level contract names nothing
        p = os.path.join(base, p)
    return _common_dir(os.path.normpath(p))


# ------------------------------------------------------------------------------------------------ compile
def _fail(status: str, reason: str, path: str | None = None, fingerprint: str | None = None, **kw) -> dict[str, Any]:
    return {"schema": SCHEMA, "status": status, "reason": reason, "path": path, "fingerprint": fingerprint,
            "pinned": False, "grants": [], "prohibitions": [], "dropped": [], **kw}


def compile_contract(doc: Any, *, ctx: Any = None, harness: str = HARNESS_ID, path: str | None = None,
                     pin: str | None = None, require_pin: bool = False, repo_base: str | None = None) -> dict[str, Any]:
    """One AODL document -> {status, fingerprint, grants, prohibitions, dropped}. Never raises."""
    try:
        from aodl_contract import semantic_fingerprint, validate  # type: ignore
    except Exception as exc:  # the validator is the contract: without it nothing is granted
        return _fail("rejected", f"aodl_contract unavailable ({type(exc).__name__})", path)
    if not isinstance(doc, dict):
        return _fail("rejected", "document is not a JSON object", path)
    try:
        issues = validate(doc)
        if issues:
            return _fail("rejected", f"invalid AODL: {issues[0]}" + (f" (+{len(issues) - 1})" if len(issues) > 1 else ""),
                         path, issues=[str(i) for i in issues[:20]])
        fingerprint = semantic_fingerprint(doc)
    except Exception as exc:
        return _fail("rejected", f"cannot validate/fingerprint ({type(exc).__name__}: {str(exc)[:120]})", path)
    pinned = pin is not None
    if pinned and pin != fingerprint:
        return _fail("pin_mismatch", f"contract changed since it was pinned ({pin[:24]}... -> {fingerprint[:24]}...)",
                     path, fingerprint, pinned=True, pin=pin)
    if require_pin and not pinned:
        return _fail("unpinned", "enforcement requires a pinned contract", path, fingerprint)

    here = _common_dir(getattr(ctx, "scope_root", "") or "") if ctx is not None else None
    here_slug = _origin_slug(here)
    nodes = ((doc.get("intentGraph") or {}).get("nodes")) or []
    grants: list[dict[str, Any]] = []
    prohibitions: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for node in nodes:
        nid = node.get("id")
        prov = {"source": "aodl", "fingerprint": fingerprint, "node": nid, "path": path}
        for i, entry in enumerate(node.get("prohibitions") or []):
            p = _compile_prohibition(entry, prov, i, here, repo_base)
            if p is not None:
                prohibitions.append(p)
        if node.get("kind") != "executor" or node.get("harness") != harness:
            for i, entry in enumerate(node.get("authorityScopes") or []):
                dropped.append({"node": nid, "index": i, "effect": entry.get("effect"),
                                "reason": f"scope on executor harness {node.get('harness')!r}, not {harness!r}"})
            continue
        for i, entry in enumerate(node.get("authorityScopes") or []):
            g, why = _compile_grant(entry, prov, i, here, here_slug, repo_base)
            if g is None:
                dropped.append({"node": nid, "index": i, "effect": entry.get("effect"), "reason": why})
            elif why == "other_repo":
                continue  # exact scope for a different repository: not this call's authority
            else:
                grants.append(g)
    return {"schema": SCHEMA, "status": "active", "reason": "ok", "path": path, "fingerprint": fingerprint,
            "pinned": pinned, "grants": grants, "prohibitions": prohibitions, "dropped": dropped}


def _compile_grant(entry: Mapping[str, Any], prov: Mapping[str, Any], i: int, here: str | None,
                   here_slug: str | None, repo_base: str | None) -> tuple[dict[str, Any] | None, str]:
    kind = entry.get("effect")
    targets = dict(entry.get("targets") or {})
    if kind in CLASS_WORDS:
        return None, f"{kind!r} is a capability class, not an action kind (standing authority decides)"
    if kind in NEVER_GRANTED:
        return None, f"{kind!r} effects carry no target a scope can narrow: never granted by a contract"
    spec = KIND_TARGETS.get(kind)
    if spec is None:
        return None, f"unknown effect kind {kind!r} for this runtime"
    keys = set(targets) - {"repos"}
    extra = keys - spec["allowed"]
    if extra:
        return None, f"target keys {sorted(extra)} do not narrow {kind!r} here"
    if spec["need"] and not (keys & spec["need"]):
        return None, f"{kind!r} needs one of {sorted(spec['need'])} for exact scope"
    repos = targets.pop("repos", None)
    if kind in REPO_KINDS and not repos:
        return None, f"{kind!r} is repo-relative: the scope must name repos"
    if repos:
        resolved = [_resolve_repo(r, repo_base) for r in repos]
        if not any(resolved):
            return None, f"none of repos {repos} resolves to a git repository"
        if here is None or here not in resolved:
            return {}, "other_repo"
    scope = {"kind": kind, "strict": True, **{k: list(v) for k, v in targets.items()}}
    if repos:
        scope["repo_common"] = here
        if here_slug:
            scope["repo_slug"] = here_slug
    grant = {"source": "aodl", "via": "aodl", "turn": AODL_GRANT_TURN,
             "phrase": f"aodl:{prov['node']}.authorityScopes[{i}] {kind}", "scope": scope,
             "fingerprint": prov["fingerprint"], "node": prov["node"], "contract": prov.get("path")}
    return grant, "ok"


def _compile_prohibition(entry: Mapping[str, Any], prov: Mapping[str, Any], i: int, here: str | None,
                         repo_base: str | None) -> dict[str, Any] | None:
    kind = entry.get("effect")
    targets = dict(entry.get("targets") or {})
    repos = targets.pop("repos", None)
    if repos:
        resolved = [_resolve_repo(r, repo_base) for r in repos]
        # conservative: a prohibition whose repos cannot be resolved still binds
        if any(resolved) and here is not None and here not in resolved:
            return None
    scope: dict[str, Any] = {"kind": kind, **{k: list(v) for k, v in targets.items()}}
    return {"source": "aodl", "turn": AODL_PROHIBIT_TURN, "phrase": f"aodl:{prov['node']}.prohibitions[{i}] {kind}",
            "scope": scope, "fingerprint": prov["fingerprint"], "node": prov["node"], "contract": prov.get("path")}


# ----------------------------------------------------------------------------------------- strict coverage
def _norm_branch(b: Any) -> str:
    return str(b).lower().removeprefix("origin/").removeprefix("refs/heads/")


def _host(h: Any) -> str:
    return str(h or "local").lower().split("@")[-1]


def _unit(u: Any) -> str:
    return str(u or "").lower().removesuffix(".service")


def strict_covers(scope: Mapping[str, Any], effect: Mapping[str, Any]) -> bool:
    """Exact coverage for authored grants: same kind, every named target key matches the effect."""
    from .action_authority import _path_under
    if scope.get("kind") != effect.get("kind"):
        return False
    t = effect.get("target") or {}
    if t.get("catastrophic"):
        return False
    repo = t.get("repo")
    if repo and scope.get("repo_common"):
        r = str(repo)
        if r.startswith(("/", "~", ".")):  # a local repo path (git commit/merge/rebase effects)
            if _common_dir(os.path.expanduser(r)) != scope["repo_common"]:
                return False
        elif r.lower() != scope.get("repo_slug"):
            return False  # `gh --repo other/x` from inside the granted repo is another repo (or unverifiable)
    for key in ("branches", "prs", "paths", "hosts", "units", "packages", "envs"):
        want = scope.get(key)
        if want is None:
            continue
        if key == "branches":
            b = t.get("branch")
            if b in (None, "unknown", "all", "pr_base") or _norm_branch(b) not in {_norm_branch(x) for x in want}:
                return False
        elif key == "prs":
            if str(t.get("pr") or "") not in {str(x).lstrip("#") for x in want}:
                return False
        elif key == "paths":
            if not t.get("path") or not any(_path_under(t["path"], p) for p in want):
                return False
        elif key == "hosts":
            if _host(t.get("host")) not in {_host(h) for h in want}:
                return False
        elif key == "units":
            if _unit(t.get("unit")) not in {_unit(u) for u in want}:
                return False
        elif key == "packages":
            pk = {str(p).lower() for p in t.get("packages") or []}
            if not pk or not pk <= {str(p).lower() for p in want}:
                return False
        elif key == "envs":
            named = [t.get(k) for k in ("namespace", "env", "registry") if t.get(k)]
            if not named or not all(str(n).lower() in {str(e).lower() for e in want} for n in named):
                return False
    return True


# ------------------------------------------------------------------------------------------------ loading
def load_contracts(ctx: Any = None, *, harness: str = HARNESS_ID, require_pin: bool = False,
                   paths: Iterable[str] | None = None) -> dict[str, Any]:
    """Every contract file that exists for this call, compiled. Missing files are simply absent."""
    pins = load_pins()
    parts = []
    for path in (list(paths) if paths is not None else contract_paths(ctx)):
        if not os.path.exists(path):
            continue
        real = os.path.realpath(path)
        base = None
        if os.path.basename(os.path.dirname(real)) == ".aodl":
            base = os.path.dirname(os.path.dirname(real))
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            doc = json.loads(raw)
        except (OSError, ValueError) as exc:
            parts.append(_fail("rejected", f"unreadable contract ({type(exc).__name__})", path))
            continue
        # cache keyed on the exact bytes + everything else the result depends on; a hit skips importing
        # and running the validator (hook latency). Any edit is a different key, so it can only miss.
        import hashlib
        key = hashlib.sha256(json.dumps([hashlib.sha256(raw).hexdigest(), path, pins.get(real), require_pin, harness,
                                         base, getattr(ctx, "scope_root", None), SCHEMA]).encode()).hexdigest()
        cache = os.path.join(state_dir(), "aodl_cache", hashlib.sha256(real.encode()).hexdigest()[:16] + ".json")
        compiled = None
        try:
            with open(cache, encoding="utf-8") as fh:
                hit = json.load(fh)
            compiled = hit["compiled"] if hit.get("key") == key else None
        except (OSError, ValueError, KeyError, TypeError):
            compiled = None
        if compiled is None:
            compiled = compile_contract(doc, ctx=ctx, harness=harness, path=path, pin=pins.get(real),
                                        require_pin=require_pin, repo_base=base)
            try:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                tmp = f"{cache}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"key": key, "compiled": compiled}, fh, default=str)
                os.replace(tmp, cache)
            except OSError:
                pass
        parts.append(compiled)
    return {"schema": SCHEMA, "contracts": [{k: c[k] for k in ("path", "status", "reason", "fingerprint", "pinned")}
                                            | {"grants_n": len(c["grants"]), "prohibitions_n": len(c["prohibitions"]),
                                               "dropped_n": len(c["dropped"])} for c in parts],
            "grants": [g for c in parts for g in c["grants"]],
            "prohibitions": [p for c in parts for p in c["prohibitions"]]}


def current_grants(grants: Iterable[Mapping[str, Any]], fingerprint: str | None) -> list[dict[str, Any]]:
    """Drop authored grants that were compiled from a different contract revision (revocation on edit)."""
    return [dict(g) for g in grants if g.get("source") != "aodl" or (fingerprint and g.get("fingerprint") == fingerprint)]


# ------------------------------------------------------------------------------------------------------ cli
def pin(path: str, *, confirm: str | None = None, interactive: bool | None = None) -> dict[str, Any]:
    """Record the current fingerprint of ``path`` as the approved contract. Interactive terminals only:
    an agent's shell has no TTY, so it cannot approve its own contract."""
    import sys
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if not interactive:
        raise PermissionError("pinning an AODL contract requires an interactive terminal")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    compiled = compile_contract(doc, path=path)
    if compiled["status"] != "active":
        raise ValueError(f"contract is {compiled['status']}: {compiled['reason']}")
    fp = compiled["fingerprint"]
    short = fp.split(":")[-1][:8]
    if confirm is None:
        confirm = input(f"pin {path}\n  {fp}\ntype {short} to approve: ").strip()
    if confirm != short:
        raise PermissionError("confirmation did not match; nothing pinned")
    pins = load_pins()
    pins[os.path.realpath(path)] = fp
    os.makedirs(os.path.dirname(pins_path()), exist_ok=True)
    tmp = pins_path() + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(pins, fh, indent=2, sort_keys=True)
    os.replace(tmp, pins_path())
    return {"path": os.path.realpath(path), "fingerprint": fp}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m z0int.aodl_grants", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="compile the contracts that apply in a directory")
    s.add_argument("--cwd", default=os.getcwd())
    s.add_argument("--require-pin", action="store_true")
    p = sub.add_parser("pin", help="approve a contract's current fingerprint (interactive only)")
    p.add_argument("path")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        from .action_effects import Ctx
        print(json.dumps(load_contracts(Ctx.for_cwd(a.cwd), require_pin=a.require_pin), indent=2, default=str))
        return 0
    try:
        print(json.dumps(pin(a.path)))
    except (PermissionError, ValueError, OSError) as exc:
        print(f"not pinned: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
