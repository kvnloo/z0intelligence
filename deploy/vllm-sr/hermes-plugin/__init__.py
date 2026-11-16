"""sr: Hermes plugin for the free semantic router (vllm-sr, sr:free).

reuse-checked: wraps deploy/vllm-sr (free_pool.py CANDIDATES/inventory/lanes, bench_fanout.py) and Hermes'
own credential paths (hermes_cli.config.save_env_value_secure, secret_prompt.masked_secret_prompt, the
`secrets.bitwarden` config of `hermes secrets bitwarden`). No second provider list, router or vault.

  hermes sr status                       router health, endpoints, pool size, which provider keys are set
  hermes sr onboard --tier T --store S   key checklist for a tier; on a TTY, masked prompt per missing key
  hermes sr bench [N C] [--model M]      fanout throughput bench through the router (default turbo)
  hermes sr lanes X [--models Y]         free_pool.py lane plan
  /sr [status|tiers]                     same facts in chat (never values)

Tiers are cumulative and derived from free_pool.CANDIDATES, so a new provider lands in `all` without an edit
here (the test fails until it is given a signup URL). Values are never printed, logged or returned.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SR_DIR = HERE.parent  # deploy/vllm-sr (the plugin dir is symlinked into a profile's plugins/)
BASE = os.environ.get("VLLM_SR_BASE", "http://100.113.138.100:30899/v1")

# env -> (provider, where to get a free key). Tier = the first tier that lists it.
PROVIDERS = {
    "OPENROUTER_API_KEY": ("OpenRouter (:free models)", "https://openrouter.ai/settings/keys"),
    "GROQ_API_KEY": ("Groq", "https://console.groq.com/keys"),
    "NVIDIA_API_KEY": ("NVIDIA NIM", "https://build.nvidia.com/settings/api-keys"),
    "COHERE_API_KEY": ("Cohere (trial key)", "https://dashboard.cohere.com/api-keys"),
    "ZAI_API_KEY": ("Z.ai", "https://z.ai/manage-apikey/apikey-list"),
    "SAMBANOVA_API_KEY": ("SambaNova", "https://cloud.sambanova.ai/apis"),
    "SILICONFLOW_API_KEY": ("SiliconFlow", "https://cloud.siliconflow.com/account/ak"),
}
MINIMAL = ["OPENROUTER_API_KEY", "GROQ_API_KEY"]
POWERUSER = MINIMAL + ["NVIDIA_API_KEY", "COHERE_API_KEY", "ZAI_API_KEY"]
STORES = ("hermes", "bws")


def free_pool():
    sys.path.insert(0, str(SR_DIR))
    try:
        import free_pool as fp  # noqa: PLC0415 (module-level side effects are behind __main__)
    finally:
        sys.path.remove(str(SR_DIR))
    return fp


def tiers(fp=None) -> dict[str, list[str]]:
    fp = fp or free_pool()
    envs = sorted({c[2] for c in fp.CANDIDATES if c[2]})
    return {"minimal": MINIMAL, "poweruser": POWERUSER, "all": POWERUSER + [e for e in envs if e not in POWERUSER]}


def backends_per_key(fp=None) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in (fp or free_pool()).CANDIDATES:
        out[c[2] or "keyless"] = out.get(c[2] or "keyless", 0) + 1
    return out


def _bws_cfg(project: str | None) -> tuple[str, str]:
    cfg = {}
    try:
        from hermes_cli.config import load_config  # noqa: PLC0415
        cfg = ((load_config() or {}).get("secrets") or {}).get("bitwarden") or {}
    except Exception:
        pass
    return (project or str(cfg.get("project_id") or "") or os.environ.get("BWS_PROJECT_ID", ""),
            str(cfg.get("access_token_env") or "BWS_ACCESS_TOKEN"))


def _bws_env() -> dict:
    """bws needs BWS_ACCESS_TOKEN; Hermes may have it in the process env or in the profile .env."""
    _, tok = _bws_cfg(None)
    val = os.environ.get(tok)
    if not val:
        try:
            from hermes_cli.config import get_env_value  # noqa: PLC0415
            val = get_env_value(tok)
        except Exception:
            val = None
    return {**os.environ, "BWS_ACCESS_TOKEN": val} if val else {}


def _bws(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["bws", *args, "-o", "json"], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL, env=_bws_env() or None)


def present(keys: list[str], store: str, project: str | None = None) -> dict[str, bool]:
    """Which keys the store already holds (names only)."""
    if store == "bws":
        proj, tok = _bws_cfg(project)
        if not proj:
            raise SystemExit("bws: needs a project id (--bws-project, secrets.bitwarden.project_id from "
                             "`hermes secrets bitwarden setup`, or BWS_PROJECT_ID)")
        if not _bws_env():
            raise SystemExit(f"bws: {tok} is not in the environment or the profile .env "
                             "(run `hermes secrets bitwarden setup`, or export it for this call)")
        r = _bws(["secret", "list", proj])
        if r.returncode:
            raise SystemExit(f"bws secret list failed: {r.stderr.strip()[:200]}")
        have = {s.get("key") for s in json.loads(r.stdout or "[]")}
        return {k: k in have for k in keys}
    try:
        from hermes_cli.config import get_env_value  # noqa: PLC0415
    except Exception:
        get_env_value = os.environ.get
    return {k: bool(get_env_value(k)) for k in keys}


def store_key(key: str, value: str, store: str, project: str | None = None) -> str:
    if store == "hermes":
        from hermes_cli.config import save_env_value_secure  # noqa: PLC0415
        save_env_value_secure(key, value)
        return "saved to the profile .env"
    proj, _ = _bws_cfg(project)
    r = _bws(["secret", "list", proj])
    sid = next((s["id"] for s in json.loads(r.stdout or "[]") if s.get("key") == key), None)
    # bws takes the value as an argument (no stdin mode); it is visible in /proc for the call's lifetime.
    r = _bws(["secret", "edit", sid, "--value", value] if sid else ["secret", "create", key, value, proj])
    if r.returncode:
        raise SystemExit(f"bws failed for {key}: {r.stderr.strip()[:200]}")
    return f"{'updated' if sid else 'created'} in bws project {proj[:8]}…"


def onboard(tier: str, store: str, project: str | None = None, prompt=None, check_only: bool = False) -> list[dict]:
    fp = free_pool()
    keys, per = tiers(fp)[tier], backends_per_key(fp)
    have = present(keys, store, project)
    interactive = not check_only and sys.stdin.isatty() if prompt is None else not check_only
    if prompt is None:
        from hermes_cli.secret_prompt import masked_secret_prompt as prompt  # noqa: PLC0415
    rows = []
    for k in keys:
        name, url = PROVIDERS[k]
        row = {"key": k, "provider": name, "signup": url, "backends": per.get(k, 0), "status": "set" if have[k] else "missing"}
        if not have[k] and interactive:
            v = (prompt(f"{name} key ({url}), Enter to skip: ") or "").strip()
            row["status"] = store_key(k, v, store, project) if v else "skipped"
            del v
        rows.append(row)
    rows.append({"key": "(none)", "provider": "keyless: Kilo, OVH, LLM7", "signup": "-", "backends": per.get("keyless", 0),
                 "status": "always on; may log prompts, so public data only"})
    return rows


def status() -> dict:
    out: dict = {"base_url": BASE}
    try:
        with urllib.request.urlopen(BASE + "/models", timeout=10) as r:
            ids = [m["id"] for m in json.load(r).get("data", [])]
        out.update(up=True, models=len(ids), turbo="vllm-sr/turbo" in ids,
                   entrypoints=[i for i in ids if not i.startswith(("vllm-sr/m/", "vllm-sr/b/"))])
    except Exception as e:  # router down or unreachable: say so, never guess
        out.update(up=False, error=f"{type(e).__name__}: {e}"[:200])
    pace = Path.home() / ".z0int/state/posture/free-pace.json"
    if pace.exists():
        p = json.loads(pace.read_text())
        out.update(pool_alive=len(p.get("pool", [])), excluded=len(p.get("excluded", [])))
    keys = tiers()["all"]
    out["keys_set"] = {k: v for k, v in present(keys, "hermes").items()}
    return out


def _table(rows: list[dict]) -> str:
    return "\n".join(f"{r['status']:<34} {r['key']:<20} {r['backends']:>3} backends  {r['provider']}  {r['signup']}" for r in rows)


def setup_parser(p):
    sub = p.add_subparsers(dest="sr_cmd")
    sub.add_parser("status", help="router health, endpoints, pool, keys present")
    o = sub.add_parser("onboard", help="store free-provider keys for a tier")
    o.add_argument("--tier", choices=("minimal", "poweruser", "all"), default="minimal")
    o.add_argument("--store", choices=STORES, default="hermes")
    o.add_argument("--bws-project", default=None)
    o.add_argument("--check", action="store_true", help="checklist only, no prompts")
    b = sub.add_parser("bench", help="fanout throughput bench through the router")
    b.add_argument("n", nargs="?", type=int, default=60)
    b.add_argument("c", nargs="?", type=int, default=20)
    b.add_argument("--out", default=None)
    lp = sub.add_parser("lanes", help="free_pool.py lanes plan")
    lp.add_argument("rest", nargs="*")


def run(args) -> int:
    cmd = getattr(args, "sr_cmd", None) or "status"
    if cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "onboard":
        print(_table(onboard(args.tier, args.store, args.bws_project, check_only=args.check)))
    elif cmd == "bench":
        env = {**os.environ, "VSR_URL": BASE}
        return subprocess.call([sys.executable, str(SR_DIR / "bench_fanout.py"), str(args.n), str(args.c),
                                *([args.out] if args.out else [])], env=env)
    elif cmd == "lanes":
        return subprocess.call([sys.executable, str(SR_DIR / "free_pool.py"), "lanes", *args.rest])
    return 0


def slash(raw_args: str = "", invocation=None) -> str:
    a = raw_args.strip().lower()
    if a == "tiers":
        per = backends_per_key()
        return json.dumps({t: {k: per.get(k, 0) for k in ks} for t, ks in tiers().items()}, indent=1)
    if a not in ("", "status"):
        return "Usage: /sr [status|tiers]   (onboarding asks for keys, so it runs in a terminal: hermes sr onboard)"
    return json.dumps(status(), indent=1)


def register(ctx):
    ctx.register_cli_command(name="sr", help="Free semantic router: status, onboarding tiers, bench, lanes",
                             setup_fn=setup_parser, handler_fn=run)
    ctx.register_command("sr", handler=slash, description="Free router status and key tiers", args_hint="[status|tiers]")
