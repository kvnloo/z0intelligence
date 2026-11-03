"""O18 privacy-tier model routing: host facts for ops.route (twin of ops.bend O18).

The decision (which lane, or wait, or refuse) is ops.route. This module gathers its inputs:

  tier      the most restrictive of: the profile's floor, every path the session read
            (path_tier), and a secret/PII scan of the outgoing messages (content_tier).
  task      agentic when the request carries tools, else simple.
  lanes up  the router's /v1/models lists the lane's virtual model.

and maps the chosen lane to a vLLM Semantic Router virtual model:

  local     vllm-sr/local         groot llama.cpp router (never leaves the host)
  paid      vllm-sr/private       Claude -> ChatGPT -> Grok subscriptions (Grok approved for T1 2026-11-03)
  free_ok   vllm-sr/free-private  Cerebras (approved for T1 on 2026-10-20)
  free_pub  vllm-sr/turbo         Groq/NIM/OpenRouter free pools (public data only)

Wait and refuse map to model names the router does not serve, so the router answers
400 model_not_found and no backend sees the request.

Kept import-light (stdlib only): the Hermes plugin calls it on every request.

    python3 -m z0int.fsm.privacy_route decide --tier personal --task agentic [--down paid]
    python3 -m z0int.fsm.privacy_route path PATH...
    python3 -m z0int.fsm.privacy_route lanes [--router URL]

Scope: path rules, the content scan and the lane map are heuristics and are not proven; only
ops.route's table is (bend/ops_fsm LAWS o18_*). A path the rules do not know is UNKNOWN, which
is local-only, so a missing rule fails toward the safer side.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from . import ops as P

HOME = Path.home()
ROUTER = os.environ.get("O18_ROUTER_URL", "http://10.43.216.55:8899/v1")
LANE_MODEL = {
    P.MLane.LOCAL: "vllm-sr/local",
    P.MLane.PAID: "vllm-sr/private",
    P.MLane.FREE_OK: "vllm-sr/free-private",
    P.MLane.FREE_PUB: "vllm-sr/turbo",
}
QUEUE_MODEL, REFUSE_MODEL = "vllm-sr/o18-queued", "vllm-sr/o18-refused"
TIER_NAMES = {t.name.lower(): t for t in P.Tier} | {"t0": P.Tier.PUBLIC, "t1": P.Tier.PERSONAL,
                                                    "t2": P.Tier.SENSITIVE, "t3": P.Tier.THIRD,
                                                    "t4": P.Tier.SECRET}

# ---------------------------------------------------------------- paths (tiered store)
# Order matters: first match wins, most specific first. Sources: /mnt/zer0models/store/README.md
# (llm/ raw/ sealed/), sft-svlm/data/privacy/README.md (T0-T4), and the owner's "do not point an
# agent at data/raw/google or data/raw/telegram".
_SFT = "/mnt/zer0models/sft-svlm/data"
_SECRET_GLOBS = (
    "/mnt/zer0models/store/sealed", f"{_SFT}/raw/google", f"{_SFT}/raw/telegram",
    "~/.ssh", "~/.gnupg", "~/.aws", "~/.kube", "~/.config/bws", "~/.config/gh/hosts.yml",
    "~/.local/share/keyrings", "~/.password-store", "~/.config/Proton Pass", "~/.config/proton",
    "~/.netrc", "~/.git-credentials", "~/.codex/auth.json", "~/.claude/.credentials.json",
    "~/.config/z0-guard/secrets", "/etc/shadow", "/etc/gshadow", "/proc/*/environ",
)
_SECRET_NAMES = re.compile(r"(^|/)(\.env(\..*)?|auth\.json|credentials(\.json)?|.*\.pem|.*\.key|id_(rsa|ed25519|ecdsa)(\.pub)?"
                           r"|.*secrets?\.(json|ya?ml|env)|.*\.kdbx|.*\.p12)$", re.I)
_RULES = (
    (f"{_SFT}/privacy", P.Tier.THIRD),
    (f"{_SFT}/raw", P.Tier.THIRD),
    (f"{_SFT}/exports", P.Tier.THIRD),
    (f"{_SFT}/postgres", P.Tier.THIRD),
    (_SFT, P.Tier.SENSITIVE),
    ("/mnt/zer0models/store/raw", P.Tier.SENSITIVE),
    ("/mnt/zer0models/home-offload", P.Tier.SENSITIVE),
    ("~/Documents", P.Tier.SENSITIVE),
    ("~/Downloads", P.Tier.SENSITIVE),
    ("/mnt/zer0models/store/llm", P.Tier.PERSONAL),
    ("/workspace", P.Tier.PERSONAL),
    ("/mnt/zer0models/workspace", P.Tier.PERSONAL),
    ("/mnt/zer0models/z0-wt", P.Tier.PERSONAL),
    ("/mnt/zer0models/project-artifacts", P.Tier.PERSONAL),
    ("~/.hermes", P.Tier.PERSONAL),
    ("~/.t3", P.Tier.PERSONAL),
    ("/tmp", P.Tier.PERSONAL),
    ("/usr", P.Tier.PUBLIC),
    ("/opt", P.Tier.PUBLIC),
    ("/etc", P.Tier.PUBLIC),
)


def _norm(p: str, cwd: str | None = None) -> str:
    p = os.path.expanduser(str(p).strip().strip("'\""))
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.normpath(p)


def _under(path: str, root: str) -> bool:
    root = os.path.normpath(os.path.expanduser(root))
    if "*" in root:
        return re.fullmatch(re.escape(root).replace(r"\*", "[^/]+") + r"(/.*)?", path) is not None
    return path == root or path.startswith(root.rstrip("/") + "/")


def path_tier(p: str, cwd: str | None = None) -> P.Tier:
    """Tier of a path. Resolves symlinks too and takes the stricter of the two."""
    lexical = _norm(p, cwd)
    candidates = {lexical}
    try:
        candidates.add(os.path.realpath(lexical))
    except OSError:
        pass
    tier = P.Tier.PUBLIC
    for c in candidates:
        tier = P.tier_join(tier, _one_path_tier(c))
    return tier


def _one_path_tier(path: str) -> P.Tier:
    if _SECRET_NAMES.search(path) or any(_under(path, g) for g in _SECRET_GLOBS):
        return P.Tier.SECRET
    for root, tier in _RULES:
        if _under(path, root):
            return tier
    return P.Tier.UNKNOWN


# ---------------------------------------------------------------- tool calls
_PATH_ARGS = ("path", "file_path", "filepath", "paths", "workdir", "cwd", "directory", "dir", "root")
_PATH_TOKEN = re.compile(r"""(?:(?<=\s)|(?<=^)|(?<=[=:'"(]))((?:~|/)[^\s'"`;|&<>()]*)""")
# Commands that print credentials whatever their path argument says.
_SECRET_CMD = re.compile(r"(^|[\s;|&(])(bws\s+secret|pass-cli|proton-pass|printenv|env\s*($|[;|&])|"
                         r"security\s+find-|gpg\s+(-d|--decrypt)|secret-tool\s+lookup|kubectl\s+get\s+secrets?)")


def tool_paths(tool: str, args: dict, cwd: str | None = None) -> list[str]:
    out: list[str] = []
    for k in _PATH_ARGS:
        v = args.get(k)
        if isinstance(v, str) and v:
            out.append(v)
        elif isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str))
    cmd = args.get("command") or args.get("cmd") or args.get("code") or ""
    if isinstance(cmd, str):
        out.extend(m.group(1) for m in _PATH_TOKEN.finditer(cmd))
    return out


def tool_tier(tool: str, args: dict, cwd: str | None = None) -> P.Tier:
    """The most restrictive tier a tool call would read. A secret-printing command is SECRET."""
    tier = P.Tier.PUBLIC
    cmd = args.get("command") or args.get("cmd") or ""
    if isinstance(cmd, str) and _SECRET_CMD.search(cmd):
        return P.Tier.SECRET
    for p in tool_paths(tool, args, cwd):
        tier = P.tier_join(tier, path_tier(p, args.get("workdir") or cwd))
    return tier


# ---------------------------------------------------------------- content scan
# Secret shapes (a subset of privacy/scripts/pcls/detect.py det-1.3): T4 is decided by regex only.
_SECRET_RE = re.compile("|".join((
    r"sk-ant-[A-Za-z0-9_-]{20,}", r"sk-(proj-)?[A-Za-z0-9]{32,}", r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{40,}", r"gsk_[A-Za-z0-9]{40,}", r"nvapi-[A-Za-z0-9_-]{40,}",
    r"csk-[a-z0-9]{40,}", r"hf_[A-Za-z0-9]{30,}", r"AKIA[0-9A-Z]{16}", r"xox[abpr]-[A-Za-z0-9-]{10,}",
    r"AIza[0-9A-Za-z_-]{35}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"tskey-[a-z]+-[A-Za-z0-9]{20,}",
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"0\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9+/=:]{20,}",
)))
_SSN_RE = re.compile(r"(?i)\b(ssn|social security)\b[^\n]{0,24}\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PW_RE = re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{6,}")


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits][::-1]
    return sum(x if i % 2 == 0 else (x * 2 - 9 if x > 4 else x * 2) for i, x in enumerate(d)) % 10 == 0


def content_tier(text: str) -> P.Tier:
    if not text:
        return P.Tier.PUBLIC
    if _SECRET_RE.search(text) or _SSN_RE.search(text) or _PW_RE.search(text):
        return P.Tier.SECRET
    for m in _CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and digits[0] in "3456" and _luhn(digits):
            return P.Tier.SECRET
    return P.Tier.PUBLIC


def request_text(request: dict) -> str:
    """Every string the provider would see (messages, Responses input, system, tool results)."""
    parts: list[str] = []

    def walk(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k not in ("tools", "tool_choice", "model", "stream_options"):
                    walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    for key in ("messages", "input", "system", "instructions"):
        walk(request.get(key))
    return "\n".join(parts)


# ---------------------------------------------------------------- lanes
_LANES_CACHE: dict = {"at": 0.0, "models": None}


def router_models(router: str = ROUTER, ttl: float = 30.0, timeout: float = 3.0) -> set[str] | None:
    """Model ids the router serves now; None when the router cannot be read (every lane down)."""
    now = time.monotonic()
    if _LANES_CACHE["models"] is not None and now - _LANES_CACHE["at"] < ttl:
        return _LANES_CACHE["models"]
    try:
        req = urllib.request.Request(router.rstrip("/") + "/models", headers={"User-Agent": "o18-privacy-route"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ids = {m.get("id") for m in json.load(r).get("data", [])}
    except Exception:
        ids = None
    _LANES_CACHE.update(at=now, models=ids)
    return ids


def lanes_up(models: set[str] | None) -> dict:
    return {lane: bool(models) and LANE_MODEL[lane] in models for lane in P.MLane}


# ---------------------------------------------------------------- decision
def decide(tier: P.Tier, task: P.Task, up: dict) -> dict:
    kind, lane = P.route(tier, task, up[P.MLane.LOCAL], up[P.MLane.PAID], up[P.MLane.FREE_OK],
                         up[P.MLane.FREE_PUB])
    model = {"use": LANE_MODEL[lane] if lane else REFUSE_MODEL, "queue": QUEUE_MODEL, "refuse": REFUSE_MODEL}[kind]
    return {"law": "O18", "decision": kind, "lane": lane.value if lane else None, "model": model,
            "tier": tier.name.lower(), "task": task.value,
            "up": sorted(k.value for k, v in up.items() if v)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="privacy_route")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--tier", required=True, choices=sorted(TIER_NAMES))
    d.add_argument("--task", default="agentic", choices=["agentic", "simple"])
    d.add_argument("--down", action="append", default=[], choices=[x.value for x in P.MLane])
    d.add_argument("--live", action="store_true", help="read lane availability from the router")
    pth = sub.add_parser("path")
    pth.add_argument("paths", nargs="+")
    ln = sub.add_parser("lanes")
    ln.add_argument("--router", default=ROUTER)
    a = ap.parse_args(argv)
    if a.cmd == "decide":
        up = lanes_up(router_models()) if a.live else {x: x.value not in a.down for x in P.MLane}
        r = decide(TIER_NAMES[a.tier], P.Task(a.task), up)
        print(json.dumps(r))
        return {"use": 0, "queue": 4, "refuse": 3}[r["decision"]]
    if a.cmd == "path":
        for p in a.paths:
            print(json.dumps({"path": p, "tier": path_tier(p).name.lower()}))
        return 0
    models = router_models(a.router)
    print(json.dumps({"router": a.router, "reachable": models is not None,
                      "lanes": {k.value: {"model": LANE_MODEL[k], "up": v} for k, v in lanes_up(models).items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
