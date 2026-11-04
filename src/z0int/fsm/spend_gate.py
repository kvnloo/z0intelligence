"""O19 spend gate: no real money without the owner's approval.

The decision is ops.spend_allows (twin of ops.bend O19). This module gathers the
host facts the kernel takes as inputs and names the surface a call would spend on:

  funding   what a surface costs: free tier, flat subscription the owner already
            pays, free credit (may overflow onto a card), metered pay-as-you-go,
            or unknown. Defaults below; the owner overrides them in
            ~/.config/z0-guard/spend-funding.json. Unlisted surfaces are unknown.
  approved  an unexpired owner approval in ~/.config/z0-guard/spend-approvals.json
            naming the surface. Agents cannot write that file (the Hermes plugin
            refuses any tool call that mentions it) and `approve` needs a real TTY.
            Chat text is never an approval.
  fits      credit surfaces only: this month's spend plus the call's estimate stays
            under the surface's cap (Modal: $26 of the $30 free credit), read live
            from the provider's billing API. Unreadable billing counts as not fitting.
  armed     credit surfaces only: the hard-stop timer that stops every job of that
            account at the cap is active (systemd --user z0-spend-stop@<surface>.timer).

Kept import-light (no guard.py) because the Hermes plugin calls it per tool call:

    python3 -m z0int.fsm.spend_gate check-cmd --cmd "modal run x.py"      exit 0 allow, 3 refuse
    python3 -m z0int.fsm.spend_gate check-llm --provider openrouter --model x/y
    python3 -m z0int.fsm.spend_gate decide --funding metered [--approved] [--fits] [--armed]
    python3 -m z0int.fsm.spend_gate stop-tick --surface modal            the hard stop (timer)
    python3 -m z0int.fsm.spend_gate approve --surface S --cap-usd N --hours H   owner only (TTY)
    python3 -m z0int.fsm.spend_gate status

Scope: classifying commands and providers is heuristic and not proven. A program
that spends through an SDK this module does not recognise, or a harness without
the plugin, bypasses it; the credit hard stop still bounds Modal at the cap.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import ops as P

HOME = Path.home()
CONFIG = Path(os.environ.get("Z0_GUARD_CONFIG", HOME / ".config/z0-guard"))
FUNDING_FILE = CONFIG / "spend-funding.json"
APPROVALS_FILE = CONFIG / "spend-approvals.json"
STATE = Path(os.environ.get("Z0_SPEND_STATE", HOME / ".z0int/state/spend"))
RECEIPTS = STATE / "receipts.jsonl"
EXIT_REFUSED = 3
F = P.Fund

# Files and switches an agent must never touch: editing them would forge an approval,
# reclassify a metered surface as free, or turn the gate off.
PROTECTED = ("spend-approvals", "spend-funding", "Z0_GUARD_O19", "z0-spend-stop", "spend_gate approve",
             "spend approve")

# ------------------------------------------------------------------ funding tables

# LLM providers by Hermes provider name. "rule:openrouter" = free only for :free models.
LLM_PROVIDERS: dict[str, str] = {
    "anthropic": "subscription",      # Claude Max/Pro OAuth pool (claude-ultra, claude-sub2)
    "openai-codex": "subscription",   # ChatGPT plan
    "xai-oauth": "subscription",      # SuperGrok OAuth
    "nous": "subscription",           # Nous Portal plan
    "copilot": "subscription",        # GitHub Copilot plan
    "openrouter": "rule:openrouter",
    "nvidia": "free",                 # build.nvidia.com free credits, no card
    "groq": "free", "cerebras": "free", "sambanova": "free", "kilo-free": "free", "llm7": "free",
    "ovh-free": "free", "pollinations": "free", "zai-free": "free", "siliconflow": "free", "cohere": "free",
    "vllm-sr": "rule:vllm-sr",
    "openai": "metered", "ai-gateway": "metered", "deepseek": "metered", "xai": "metered",
    "gemini": "metered", "google": "metered", "mistral": "metered", "together": "metered",
    "fireworks": "metered", "perplexity": "metered", "bedrock": "metered", "vertex": "metered",
    "azure": "metered",
}

# Free-tier endpoints by host, for providers that reach Hermes as `custom`/`custom:<name>`.
FREE_HOSTS: dict[str, str] = {
    "api.groq.com": "groq", "api.cerebras.ai": "cerebras", "api.sambanova.ai": "sambanova",
    "api.kilo.ai": "kilo-free", "api.llm7.io": "llm7", "oai.endpoints.kepler.ai.cloud.ovh.net": "ovh-free",
    "text.pollinations.ai": "pollinations", "api.z.ai": "zai-free", "api.siliconflow.com": "siliconflow",
    "api.cohere.ai": "cohere", "integrate.api.nvidia.com": "nvidia",
}

# Command-line surfaces: (surface, funding, regex over the command text).
CLI_SURFACES: list[tuple[str, str, str]] = [
    ("modal", "credit", r"(?:^|[\s;&|(`$])modal\s+(?:run|deploy|serve|shell|launch)\b"),
    ("hf-compute", "metered", r"\b(?:hf|huggingface-cli)\s+(?:jobs\s+(?:run|uv)|endpoints?\s+(?:create|deploy|resume))\b"),
    ("runpod", "metered", r"\brunpodctl\s+(?:create|start|deploy)\b"),
    ("vast", "metered", r"\bvastai\s+(?:create|launch|start)\b"),
    ("skypilot", "metered", r"\bsky\s+(?:launch|start|jobs\s+launch|serve\s+up)\b"),
    ("aws", "metered", r"\baws\s+(?:ec2\s+run-instances|sagemaker|bedrock-runtime|lightsail\s+create)"),
    ("gcloud", "metered", r"\bgcloud\s+(?:compute\s+instances\s+create|ai\s|run\s+deploy|ml\s)"),
    ("azure", "metered", r"\baz\s+(?:vm\s+create|ml\s)"),
    ("prime-intellect", "metered", r"\bprime\s+pods?\s+create\b"),
    ("fal", "metered", r"\bfal\s+(?:run|deploy)\b"),
    ("replicate", "metered", r"\breplicate\s+(?:run|predictions?\s+create|deployments?\s+create)\b"),
    ("stripe", "metered", r"(?:^|[\s;&|(])stripe\s"),
]

# Paid API hosts reached directly (curl, requests, SDK scripts). OpenRouter is free
# only for :free models; the text check below handles that.
METERED_HOSTS: dict[str, str] = {
    "api.openai.com": "openai-api", "api.anthropic.com": "anthropic-api", "api.deepseek.com": "deepseek-api",
    "api.replicate.com": "replicate", "fal.run": "fal", "queue.fal.run": "fal", "api.together.xyz": "together",
    "api.fireworks.ai": "fireworks", "api.mistral.ai": "mistral", "api.x.ai": "xai-api",
    "generativelanguage.googleapis.com": "gemini-api", "api.elevenlabs.io": "elevenlabs",
    "api.stripe.com": "stripe", "api.runpod.io": "runpod", "console.vast.ai": "vast",
    "ai-gateway.vercel.sh": "ai-gateway", "api.perplexity.ai": "perplexity",
    "openrouter.ai": "openrouter-api",
}

# Credit surfaces: how to read the month's spend and the cap that keeps it inside the credit.
CREDIT: dict[str, dict] = {
    "modal": {"cap_usd": 26.0, "credit_usd": 30.0, "billing": "modal"},
}

_MODAL_SDK = re.compile(r"^\s*(?:import modal|from modal\b)", re.M)
_MODAL_SPEND = re.compile(r"\.remote\(|\.spawn\(|\.map\(|Function\.from_name|Cls\.from_name|app\.run\(|Sandbox\.create")


def mode() -> str:
    """Same lookup as guard.mode('O19'), without importing guard.py."""
    m = os.environ.get("Z0_GUARD_O19")
    if not m:
        try:
            m = json.loads((CONFIG / "modes.json").read_text()).get("O19")
        except (OSError, ValueError):
            m = None
    return m if m in ("off", "report", "enforce") else "enforce"


def _overrides() -> dict:
    try:
        return json.loads(FUNDING_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _fund(name: str) -> P.Fund:
    return {f.value: f for f in F}.get(name, F.UNKNOWN)


# ------------------------------------------------------------------ classification

def llm_surface(provider: str | None, base_url: str | None, model: str | None) -> tuple[str, P.Fund]:
    """The surface and funding class of one provider request."""
    prov = (provider or "").removeprefix("custom:").lower()
    model = model or ""
    host = (urlparse(base_url).hostname or "") if base_url else ""
    over = _overrides().get("llm", {})
    if prov in over:
        return prov, _fund(over[prov])
    rule = LLM_PROVIDERS.get(prov)
    if rule is None and host:
        if host in ("localhost", "127.0.0.1") or host.startswith(("10.", "100.", "192.168.")):
            rule = "rule:vllm-sr"
        elif "openrouter.ai" in host:
            rule = "rule:openrouter"
        elif host in FREE_HOSTS:
            prov, rule = FREE_HOSTS[host], LLM_PROVIDERS.get(FREE_HOSTS[host], "free")
        else:
            for h, surface in METERED_HOSTS.items():
                if host == h or host.endswith("." + h):
                    return surface, F.METERED
    if rule == "rule:openrouter":
        free = model.endswith(":free") or model in ("openrouter/free", "free")
        return "openrouter", F.FREE if free else F.METERED
    if rule == "rule:vllm-sr":
        return "vllm-sr", F.SUB if "paid" in model.split("/")[-1] else F.FREE
    if rule is None:
        return prov or host or "unknown-provider", F.UNKNOWN
    return prov, _fund(rule)


def _script_files(cmd: str) -> list[Path]:
    out = []
    try:
        words = shlex.split(cmd, posix=True)
    except ValueError:
        words = cmd.split()
    for w in words:
        if w.endswith(".py"):
            p = Path(os.path.expanduser(w))
            if p.is_file():
                out.append(p)
    return out


def code_spends_modal(text: str) -> bool:
    return bool(_MODAL_SDK.search(text) and _MODAL_SPEND.search(text))


def _openrouter_all_free(text: str) -> bool:
    """OpenRouter is free only when every model the text names is a :free model."""
    models = re.findall(r"""model["']?\s*[:=]\s*["']([^"']+)["']""", text)
    return bool(models) and all(m.endswith(":free") or m == "openrouter/free" for m in models)


def command_surfaces(cmd: str, code: str | None = None) -> list[tuple[str, P.Fund, str]]:
    """Every spending surface a shell command (or Python source) would reach."""
    found: dict[str, tuple[str, P.Fund, str]] = {}
    over = _overrides().get("cli", {})
    text = cmd or ""
    for surface, fund, rx in CLI_SURFACES:
        if re.search(rx, text):
            found[surface] = (surface, _fund(over.get(surface, fund)), f"command matches {surface}")
    for src in ([code] if code else []) + [p.read_text(errors="replace")[:400_000] for p in _script_files(text)]:
        if code_spends_modal(src):
            found["modal"] = ("modal", _fund(over.get("modal", "credit")), "Python source launches Modal functions")
    blob = text + "\n" + (code or "")
    for host, surface in METERED_HOSTS.items():
        # a URL to the host (a call), not a bare mention such as `grep api.openai.com`
        if re.search(r"https?://(?:[\w-]+\.)*" + re.escape(host) + r"(?![\w.-])", blob):
            if host == "openrouter.ai" and _openrouter_all_free(blob):
                continue
            found[surface] = (surface, _fund(over.get(surface, "metered")), f"reaches {host}")
    return list(found.values())


# ------------------------------------------------------------------ host facts

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def approvals() -> list[dict]:
    try:
        rows = json.loads(APPROVALS_FILE.read_text())
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def approved(surface: str) -> dict | None:
    """The unexpired owner approval naming this surface (or '*'), if any."""
    for row in approvals():
        try:
            if row.get("surface") not in (surface, "*"):
                continue
            if dt.datetime.fromisoformat(row["expires"]) <= _now():
                continue
            return row
        except (KeyError, ValueError, TypeError):
            continue
    return None


def _modal_cli() -> str | None:
    over = _overrides().get("modal_cli")
    for c in (over, os.environ.get("Z0_SPEND_MODAL_CLI"), shutil.which("modal"),
              str(HOME / ".local/lib/z0-guard/spend/modal-venv/bin/modal")):
        if c and Path(c).exists():
            return c
    return None


def month_spend(surface: str, max_age: float = 60.0) -> float | None:
    """This calendar month's spend on a credit surface, cached for max_age seconds. None if unreadable."""
    cfg = CREDIT.get(surface)
    if not cfg:
        return None
    cache = STATE / f"{surface}-spend.json"
    try:
        c = json.loads(cache.read_text())
        if time.time() - c["at"] < max_age:
            return c["usd"]
    except (OSError, ValueError, KeyError):
        pass
    if cfg["billing"] != "modal" or not (cli := _modal_cli()):
        return None
    start = _now().date().replace(day=1)
    end = (start + dt.timedelta(days=32)).replace(day=1)
    try:
        p = subprocess.run([cli, "billing", "report", "--start", start.isoformat(), "--end", end.isoformat(), "--json"],
                           capture_output=True, text=True, timeout=60)
        usd = round(sum(float(r["cost"]) for r in json.loads(p.stdout)), 4)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": time.time(), "usd": usd}))
    except OSError:
        pass
    return usd


def cap(surface: str) -> float | None:
    cfg = dict(CREDIT.get(surface, {}))
    cfg.update(_overrides().get("credit", {}).get(surface, {}))
    c = cfg.get("cap_usd")
    if c is None:
        return None
    # the cap can never exceed the free credit itself
    return min(float(c), float(cfg.get("credit_usd", c)))


def fits(surface: str, estimate: float = 0.0) -> bool:
    s, c = month_spend(surface), cap(surface)
    return s is not None and c is not None and s + max(estimate, 0.0) < c


def armed(surface: str) -> bool:
    unit = f"z0-spend-stop@{surface}.timer"
    try:
        p = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=10)
        return p.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------ decision

LAW_FOR = {F.FREE: "o19_free_allowed", F.SUB: "o19_subscription_allowed", F.METERED: "o19_metered_needs_approval",
           F.UNKNOWN: "o19_unknown_refused"}


def decision(surface: str, fund: P.Fund, is_approved: bool, does_fit: bool, is_armed: bool) -> dict:
    ok = P.spend_allows(fund, is_approved, does_fit, is_armed)
    if fund is F.CREDIT:
        law = ("o19_approval_allows" if is_approved else "o19_credit_guarded_allowed" if ok
               else "o19_credit_unguarded_refused" if not is_armed else "o19_credit_over_cap_refused")
    else:
        law = "o19_approval_allows" if (is_approved and fund in (F.METERED, F.UNKNOWN)) else LAW_FOR[fund]
    return {"surface": surface, "funding": fund.value, "approved": is_approved, "fits": does_fit,
            "armed": is_armed, "allowed": ok, "law": law}


def evaluate(surfaces: list[tuple[str, P.Fund, str]], estimate: float = 0.0) -> dict:
    """Decide every surface a call reaches; the call is allowed only if all are."""
    rows = []
    for surface, fund, why in surfaces:
        appr = approved(surface) is not None
        f = a = False
        if fund is F.CREDIT:
            f, a = fits(surface, estimate), armed(surface)
        d = decision(surface, fund, appr, f, a)
        d["why"] = why
        if fund is F.CREDIT:
            d["month_spend_usd"], d["cap_usd"] = month_spend(surface), cap(surface)
        rows.append(d)
    allowed = all(r["allowed"] for r in rows)
    return {"allowed": allowed, "mode": mode(), "surfaces": rows}


def refusal_text(res: dict) -> str:
    bad = [r for r in res["surfaces"] if not r["allowed"]]
    parts = []
    for r in bad:
        if r["funding"] == "credit":
            why = ("the hard stop is not armed (systemctl --user enable --now z0-spend-stop@%s.timer)" % r["surface"]
                   if not r["armed"] else
                   "this month's spend $%s would reach the $%s cap" % (r.get("month_spend_usd"), r.get("cap_usd")))
            parts.append(f"{r['surface']} free credit: {why}")
        else:
            parts.append(f"{r['surface']} is {r['funding']} (real money) and has no owner approval")
    return ("O19 refused: " + "; ".join(parts) + ". No real money is spent without the owner's approval. "
            "Ask the owner; he approves with `z0-spend approve --surface S --cap-usd N --hours H` "
            "in his own terminal. Agents never write the approval file.")


def receipt(row: dict) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with RECEIPTS.open("a") as f:
            f.write(json.dumps({"ts": _now().isoformat(timespec="seconds"), **row}) + "\n")
    except OSError:
        pass


def touches_protected(text: str) -> bool:
    return any(p in (text or "") for p in PROTECTED) or bool(re.search(r"modes\.json[\s\S]*O19|O19[\s\S]*modes\.json", text or ""))


# ------------------------------------------------------------------ the hard stop

def stop_tick(surface: str) -> dict:
    """At or past the cap, stop every running app of the account. Unreadable billing stops nothing
    but is reported; new launches are refused meanwhile because `fits` is False."""
    s, c = month_spend(surface, max_age=0), cap(surface)
    row = {"event": "stop_tick", "surface": surface, "month_spend_usd": s, "cap_usd": c, "stopped": []}
    if surface == "modal" and s is not None and c is not None and s >= c and (cli := _modal_cli()):
        try:
            apps = json.loads(subprocess.run([cli, "app", "list", "--json"], capture_output=True, text=True,
                                             timeout=60).stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            apps = []
        for a in apps:
            if a.get("state") != "stopped":
                subprocess.run([cli, "app", "stop", "-y", a["app_id"]], capture_output=True, timeout=60)
                row["stopped"].append(a["app_id"])
    receipt(row)
    return row


# ------------------------------------------------------------------ CLI

def _print(res: dict) -> None:
    print(json.dumps(res, indent=2))


def _exit(res: dict) -> int:
    receipt({"event": "check", **res})
    if res["allowed"] or mode() != "enforce":
        return 0
    print(refusal_text(res), file=sys.stderr)
    return EXIT_REFUSED


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="z0-guard spend", description="O19: no real money without owner approval")
    sub = ap.add_subparsers(dest="op", required=True)
    c = sub.add_parser("check-cmd")
    c.add_argument("--cmd", required=True)
    c.add_argument("--code-file")
    c.add_argument("--estimate-usd", type=float, default=0.0)
    l = sub.add_parser("check-llm")
    l.add_argument("--provider", default="")
    l.add_argument("--base-url", default="")
    l.add_argument("--model", default="")
    d = sub.add_parser("decide")
    d.add_argument("--funding", required=True, choices=[f.value for f in F])
    for flag in ("--approved", "--fits", "--armed"):
        d.add_argument(flag, action="store_true")
    s = sub.add_parser("stop-tick")
    s.add_argument("--surface", required=True)
    a = sub.add_parser("approve")
    a.add_argument("--surface", required=True)
    a.add_argument("--cap-usd", type=float, required=True)
    a.add_argument("--hours", type=float, default=24.0)
    a.add_argument("--note", default="")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    if args.op == "check-cmd":
        code = Path(args.code_file).read_text() if args.code_file else None
        res = evaluate(command_surfaces(args.cmd, code), args.estimate_usd)
        _print(res)
        return _exit(res)
    if args.op == "check-llm":
        surface, fund = llm_surface(args.provider, args.base_url, args.model)
        res = evaluate([(surface, fund, "provider request")])
        _print(res)
        return _exit(res)
    if args.op == "decide":
        _print(decision("-", _fund(args.funding), args.approved, args.fits, args.armed))
        return 0
    if args.op == "stop-tick":
        _print(stop_tick(args.surface))
        return 0
    if args.op == "approve":
        if not (sys.stdin.isatty() and sys.stdout.isatty()) or os.environ.get("HERMES_SESSION_ID"):
            print("O19: approvals are made by the owner in his own terminal, not by an agent.", file=sys.stderr)
            return EXIT_REFUSED
        rows = [r for r in approvals() if r.get("surface") != args.surface]
        rows.append({"surface": args.surface, "cap_usd": args.cap_usd, "note": args.note,
                     "created": _now().isoformat(timespec="seconds"),
                     "expires": (_now() + dt.timedelta(hours=args.hours)).isoformat(timespec="seconds")})
        CONFIG.mkdir(parents=True, exist_ok=True)
        APPROVALS_FILE.write_text(json.dumps(rows, indent=2) + "\n")
        os.chmod(APPROVALS_FILE, 0o600)
        print(f"approved {args.surface} up to ${args.cap_usd} until {rows[-1]['expires']}")
        return 0
    if args.op == "status":
        _print({"mode": mode(), "approvals": approvals(),
                "credit": {k: {"month_spend_usd": month_spend(k), "cap_usd": cap(k), "armed": armed(k)}
                           for k in CREDIT}})
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
