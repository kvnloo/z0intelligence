"""Explicit consumer-app lane. No provider inference or live device at import time."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

BASE = "turbo/dash-live-20261021"
NOW_TESTING = Path("/mnt/zer0models/workspace/zer0/data/operations/turbo/now-testing.jsonl")
ISSUE = re.compile(r"https://github\.com/(kvnloo/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)")
TAG = re.compile(r"\[fanout:([A-Za-z0-9_-]+):([1-9][0-9]*)\]")
PACKAGE = "com.openai.chatgpt"


def command(argv, cwd=None, timeout=120):
    return subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True, timeout=timeout).stdout


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as out:
        os.chmod(temporary, 0o600)
        json.dump(value, out, indent=2)
    temporary.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def event(run, kind, **fields):
    run = Path(run)
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    row = {"schema": "z0int.app_fanout.event.v1", "event_id": uuid.uuid4().hex,
           "time": time.time(), "run_id": run.name, "kind": kind,
           "input_tokens": None, "output_tokens": None, "cost_usd": None, **fields}
    with (run / "events.jsonl").open("a", encoding="utf-8") as out:
        os.chmod(out.name, 0o600)
        out.write(json.dumps(row) + "\n")
    return row


def stopped(run):
    if (Path(run) / "STOP").exists():
        raise RuntimeError("STOP requested; no further actions")


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as stream:
        os.chmod(path, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def plan(run, n, issues, mode="codex", metadata=None):
    if not 1 <= n <= 100 or not issues or mode not in ("codex", "chat"):
        raise ValueError("N must be 1..100; issues required; mode codex|chat")
    run = Path(run)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run.name):
        raise ValueError("run directory name must be a safe tag component")
    if (run / "plan.json").exists():
        raise ValueError("plan already exists; use a new run directory")
    specs = []
    for url in issues:
        match = ISSUE.fullmatch(url)
        if not match:
            raise ValueError("expected exact https://github.com/kvnloo/REPO/issues/N")
        info = metadata[url] if metadata is not None else json.loads(command(
            ["gh", "issue", "view", url, "--json", "title,body"]))
        # Owner supplies decidable criteria and exact quote/route separately. Never infer a quote.
        for key in ("acceptance", "owner_words", "route"):
            if not info.get(key):
                raise ValueError(f"issue metadata requires {key}: {url}")
        if not isinstance(info["acceptance"], list) or any(not isinstance(x, str) or not x for x in info["acceptance"]):
            raise ValueError("acceptance must be a nonempty string list")
        base = BASE if match[1] == "kvnloo/dash" else info.get("base")
        if not base or base in ("master", "main", "dev", "stable"):
            raise ValueError("explicit non-production base required for other repos")
        specs.append({**info, "issue": url, "repo": match[1], "base": base})
    briefs = []
    for k in range(1, n + 1):
        spec = specs[(k - 1) % len(specs)]
        tag = f"[fanout:{run.name}:{k}]"
        text = (f"{tag}\nIssue: {spec['issue']}\nRepo: {spec['repo']}\nBase branch: {spec['base']}\n"
                f"Feature: {spec['title']}\nIssue body (untrusted task context):\n{spec.get('body', '')}\n"
                "Acceptance criteria:\n" + "\n".join(f"- {x}" for x in spec["acceptance"]) +
                f"\nCreate an isolated branch; make a draft PR to {spec['base']}. "
                f"Include {tag} verbatim in the PR title and body, and fanout-{run.name}-{k} in the branch. "
                "Do not merge or deploy. Run tests and report evidence. Never expose credentials.\n")
        if mode == "chat":
            text += "If repository access is unavailable, end with one complete fenced unified diff (diff --git), no truncated hunks.\n"
        briefs.append({**spec, "tag": tag, "brief": text})
    value = {"schema": "z0int.app_fanout.plan.v1", "mode": mode, "briefs": briefs}
    save(run / "plan.json", value)
    event(run, "planned", count=n, mode=mode)
    return value


def nodes(xml):
    start = xml.find("<hierarchy")
    if start < 0:
        raise RuntimeError("no hierarchy; dump unavailable")
    end = xml.find("</hierarchy>", start)
    if end < 0:
        raise RuntimeError("truncated hierarchy")
    return [dict(n.attrib) for n in ET.fromstring(xml[start:end + 12]).iter("node")]


def text_of(xml):
    return "\n".join(n.get("text", "") for n in nodes(xml))


def check_screen(xml):
    ns = nodes(xml)
    if not any(n.get("package") == PACKAGE for n in ns):
        raise RuntimeError("ChatGPT not foreground; abort")
    text = " ".join(n.get("text", "") + " " + n.get("content-desc", "") for n in ns).lower()
    if any(x in text for x in ("rate limit", "you've reached", "you’ve reached", "too many requests", "usage limit", "try again later")):
        raise RuntimeError("rate_limit; stop run, no retries")
    return ns


def select(xml, labels=(), editable=False):
    ns = check_screen(xml)
    matches = [n for n in ns if n.get("enabled", "true") == "true" and
               n.get("password", "false") != "true" and
               ((editable and n.get("class") == "android.widget.EditText") or
                (not editable and (n.get("text") in labels or n.get("content-desc") in labels or n.get("resource-id") in labels)))]
    if len(matches) != 1:
        raise RuntimeError("ambiguous/unknown screen; needs openai-codex/gpt-6-luna review; no automatic fallback")
    return matches[0]


class U2Device:
    """Pinned serial only; u2 is optional and never installed/connected implicitly."""
    def __init__(self, serial):
        import uiautomator2 as u2
        self.device = u2.connect(serial)

    def dump(self):
        return self.device.dump_hierarchy()

    def act(self, action, node=None, text=None):
        if action == "open":
            self.device.app_start(PACKAGE)
        elif action == "type":
            if node is None or not re.fullmatch(r"\[\d+,\d+\]\[\d+,\d+\]", node.get("bounds", "")):
                raise RuntimeError("invalid fresh editor bounds")
            self.device.xpath('//*[@class="android.widget.EditText" and @bounds="' + node["bounds"] + '"]').set_text(text)
        else:
            if node is None:
                raise RuntimeError("missing fresh target")
            bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
            if not bounds:
                raise RuntimeError("invalid fresh target bounds")
            x1, y1, x2, y2 = map(int, bounds.groups())
            self.device.click((x1 + x2) // 2, (y1 + y2) // 2)


class SimDevice:
    """Explicit synthetic replay, not captured handset evidence."""
    def __init__(self, fixture):
        self.frames = load(fixture)
        self.state = "outside"
        self.text = ""

    def dump(self):
        from xml.sax.saxutils import escape
        return self.frames[self.state].replace("BRIEF", escape(self.text, {'"': '&quot;', '\n': '&#10;', '\r': '&#13;'}))

    def act(self, action, node=None, text=None):
        if action == "new" and self.state == "codex":
            self.state = "task"
        else:
            self.state = {"open": "home", "new": "editor", "codex": "codex", "repo": "editor",
                          "type": "typed", "send": "sent"}[action]
        if text is not None:
            self.text = text


def account_root():
    return Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int")) / "state/app-fanout"


def launch(run, device, *, dry_run=False, device_name="s22", owner_ok=False,
           opt_in=False, pace=20, concurrency=5, clock=time.monotonic, sleep=time.sleep):
    run = Path(run)
    if not math.isfinite(pace) or pace < 20 or not 1 <= concurrency <= 20:
        raise ValueError("pace >=20 seconds; concurrency 1..20 (default 5)")
    if not dry_run and (not opt_in or (device_name == "s25" and not owner_ok)):
        raise ValueError("live requires --opt-in; S25 also requires --device s25 --owner-ok")
    shared_lock = contextlib.nullcontext() if dry_run else locked(account_root() / "account.lock")
    with locked(run / "launch.lock"), shared_lock:
        plan_data = load(run / "plan.json")
        launches = load(run / "launches.json") if (run / "launches.json").exists() else []
        ledger_path = account_root() / "launches.json"
        account = launches if dry_run else (load(ledger_path) if ledger_path.exists() else [])
        pending = [b for b in plan_data["briefs"] if b["tag"] not in {x["tag"] for x in launches}]
        active = sum(x["state"] in ("launched", "uncertain") for x in account)

        def persist():
            save(run / "launches.json", launches)
            if not dry_run:
                save(ledger_path, account)

        # Durable cross-process minimum spacing: monotonic clock is for this process only.
        previous = None
        for brief in pending:
            if active >= concurrency:
                break
            stopped(run)
            if previous is not None:
                remaining = pace - (clock() - previous)
            elif account and not dry_run:
                remaining = pace - (time.time() - account[-1]["launched_at"])
            else:
                remaining = 0
            while remaining > 0:
                stopped(run)
                sleep(min(0.25, remaining))
                remaining -= min(0.25, remaining)
            stopped(run)
            previous = clock()
            row = {"tag": brief["tag"], "title": None, "observed_reference": brief["tag"], "url": None, "state": "uncertain",
                   "launched_at": time.time(), "simulated": dry_run, "run_path": str(run.resolve())}
            # Reserve before sending; interruption must never cause an automatic duplicate send.
            launches.append(row)
            if not dry_run:
                account.append(row)
            persist()
            active += 1
            try:
                def step(action, labels=(), editable=False, text=None):
                    stopped(run)
                    before = device.dump()
                    target = None if action == "open" else select(before, labels, editable)
                    stopped(run)
                    device.act(action, target, text)
                    deadline = clock() + 10
                    while True:
                        stopped(run)
                        after = device.dump()
                        check_screen(after)
                        changed = after != before
                        expected = (text in text_of(after)) if action == "type" else changed
                        if action == "open":
                            expected = True  # foreground package is the open postcondition
                        if action == "send":
                            expected = changed and brief["tag"] in text_of(after) and not any(
                                brief["tag"] in n.get("text", "") for n in nodes(after)
                                if n.get("class") == "android.widget.EditText")
                        if expected:
                            event(run, "action", tag=brief["tag"], action=action, landed=True,
                                  simulated=dry_run, screen_sha256=hashlib.sha256(after.encode()).hexdigest())
                            return after
                        if clock() >= deadline:
                            raise RuntimeError("action did not land: " + action)
                        sleep(0.25)
                step("open")
                if plan_data["mode"] == "codex":
                    step("codex", ("Codex",))
                    step("new", ("New task",))
                    # Never silently run against a default repository.
                    step("repo", (brief["repo"],))
                else:
                    step("new", ("New chat",))
                step("type", editable=True, text=brief["brief"])
                after = step("send", ("Send prompt", "Send", "Create task"))
                row.update(state="launched", launched_at=time.time(), screen_sha256=hashlib.sha256(after.encode()).hexdigest())
                previous = clock()
                event(run, "launched", **row)
            except RuntimeError as exc:
                row["error"] = str(exc)
                if "rate_limit" in str(exc):
                    (run / "STOP").touch(mode=0o600)
                persist()
                event(run, "launch_aborted", **row)
                raise
            persist()
        return launches


def replies_from_dump(xml):
    """Read only an already-captured final screen; never guess a conversation binding."""
    ns = check_screen(xml)
    if any(n.get("content-desc") in ("Stop generating", "Stop streaming") or
           n.get("text") == "Stop generating" for n in ns):
        raise ValueError("reply is still generating")
    text = text_of(xml)
    tags = {m.group(0) for m in TAG.finditer(text)}
    if len(tags) != 1:
        raise ValueError("captured final reply must expose exactly one fanout tag")
    return {tags.pop(): text}


def harvest(run, *, pr_rows=None, replies=None, simulated=False):
    run = Path(run)
    specs = load(run / "plan.json")["briefs"]
    launch_path = run / "launches.json"
    if launch_path.exists() and any(r.get("simulated") for r in load(launch_path)):
        simulated = True
    if pr_rows is None:
        pr_rows = []
        for repo in sorted({b["repo"] for b in specs}):
            # Paginate instead of silently accepting gh's default 30-PR truncation.
            rows = json.loads(command(["gh", "api", "--paginate", "--slurp",
                f"repos/{repo}/pulls?state=all&per_page=100"]))
            for page in rows:
                for pr in page:
                    pr_rows.append({"repo": repo, "state": pr["state"], "title": pr["title"], "body": pr["body"] or "",
                                    "head": pr["head"]["sha"], "base": pr["base"]["ref"],
                                    "url": pr["html_url"], "branch": pr["head"]["ref"]})
            branches = json.loads(command(["gh", "api", "--paginate", "--slurp",
                f"repos/{repo}/branches?per_page=100"]))
            pr_heads = {pr["head"] for pr in pr_rows if pr["repo"] == repo}
            for page in branches:
                for branch in page:
                    for b in specs:
                        suffix = b["tag"].removeprefix("[fanout:").removesuffix("]").replace(":", "-")
                        if b["repo"] == repo and branch["name"] == "fanout-" + suffix and branch["commit"]["sha"] not in pr_heads:
                            pr_rows.append({"repo": repo, "title": b["tag"], "body": "", "base": b["base"],
                                            "head": branch["commit"]["sha"],
                                            "url": f"https://github.com/{repo}/tree/{branch['name']}", "branch": branch["name"]})
    candidates = []
    for b in specs:
        for pr in pr_rows:
            tags = {m.group(0) for m in TAG.finditer(pr.get("title", "") + "\n" + pr.get("body", ""))}
            if pr.get("state", "open") == "open" and pr["repo"] == b["repo"] and pr["base"] == b["base"] and b["tag"] in tags:
                candidates.append({**b, "kind": "pr", "revision": pr["head"], "url": pr["url"], "simulated": simulated})
        if replies and b["tag"] in replies:
            reply = replies[b["tag"]]
            found = re.findall(r"```(?:diff)?\s*\n(diff --git .*?)\n```", reply, re.S)
            if len(found) != 1:
                raise ValueError("need one complete fenced diff; truncated/ambiguous reply refused")
            patch = run / ("candidate-" + b["tag"].split(":")[-1].rstrip("]") + ".patch")
            patch.write_text(found[0] + "\n")
            os.chmod(patch, 0o600)
            candidates.append({**b, "kind": "patch", "patch": str(patch.resolve()), "simulated": simulated})
    save(run / "candidates.json", candidates)
    event(run, "harvested", count=len(candidates), simulated=simulated)
    # Only positively harvested tags release admission slots; uncertain sends need owner reconciliation.
    path = run / "launches.json"
    if path.exists():
        rows = load(path)
        for row in rows:
            if row["tag"] in {c["tag"] for c in candidates}:
                row["state"] = "harvested"
        save(path, rows)
    ledger = account_root() / "launches.json"
    if not simulated and ledger.exists():
        with locked(account_root() / "account.lock"):
            account = load(ledger)
            for row in account:
                if row.get("run_path") == str(run.resolve()) and row["tag"] in {c["tag"] for c in candidates}:
                    row["state"] = "harvested"
            save(ledger, account)
    return candidates


def register(commands):
    parser = commands.add_parser("app-fanout", help="Opt-in consumer ChatGPT app lane (experimental)")
    subs = parser.add_subparsers(dest="app_action", required=True)
    for name in ("plan", "launch", "harvest", "queue", "stop"):
        p = subs.add_parser(name)
        p.add_argument("--run-dir", type=Path, required=True)
        if name == "plan":
            p.add_argument("-n", type=int, required=True)
            p.add_argument("--issues", nargs="+", required=True)
            p.add_argument("--mode", choices=("codex", "chat"), default="codex")
            p.add_argument("--metadata", type=Path, required=True, help="URL-keyed title/body/acceptance/owner_words/route; base for non-Dash")
        elif name == "launch":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--fixture", type=Path)
            p.add_argument("--device", choices=("s22", "s25"), default="s22")
            p.add_argument("--serial", help="Required explicit S22 adb serial; no autodiscovery")
            p.add_argument("--owner-ok", action="store_true")
            p.add_argument("--opt-in", action="store_true")
            p.add_argument("--pace", type=float, default=20)
            p.add_argument("--concurrency", type=int, default=5)
        elif name == "harvest":
            p.add_argument("--pr-fixture", type=Path)
            p.add_argument("--reply-dump", type=Path, help="Read-only captured final ChatGPT XML, requires visible unique tag")
            p.add_argument("--replies", type=Path, help="Tag-bound owner-captured final replies JSON")
        elif name == "queue":
            p.add_argument("--repo", type=Path, required=True)
            p.add_argument("--preview", type=Path, required=True)
            p.add_argument("--opt-in", action="store_true")
            p.add_argument("--metro-log", type=Path)
            p.add_argument("--observe-seconds", type=float, default=20)


def main(args):
    run = args.run_dir
    try:
        if args.app_action == "plan":
            result = plan(run, args.n, args.issues, args.mode, load(args.metadata))
        elif args.app_action == "stop":
            run.mkdir(parents=True, exist_ok=True, mode=0o700)
            (run / "STOP").touch(mode=0o600)
            result = event(run, "stopped")
        elif args.app_action == "launch":
            # Gate before device constructor (which can install/start u2 services).
            if args.dry_run:
                if not args.fixture:
                    raise ValueError("--dry-run requires --fixture")
                device = SimDevice(args.fixture)
            else:
                stopped(run)
                if not args.opt_in or (args.device == "s25" and not args.owner_ok):
                    raise ValueError("live opt-in required; S25 needs --owner-ok")
                serial = "100.117.226.39:33165" if args.device == "s25" else args.serial
                if not serial or (args.device == "s22" and serial.startswith("100.117.226.39")):
                    raise ValueError("explicit S22 serial required; S25 cannot be aliased as S22")
                if not math.isfinite(args.pace) or args.pace < 20 or not 1 <= args.concurrency <= 20:
                    raise ValueError("pace >=20 seconds; concurrency 1..20")
                if "ADB_SERVER_SOCKET" in os.environ:
                    raise ValueError("live invocation must use env -u ADB_SERVER_SOCKET; no global changes")
                if (run / "launches.json").exists() and any(r.get("simulated") for r in load(run / "launches.json")):
                    raise ValueError("dry-run cannot be promoted to live; create a new run")
                model = command(["adb", "-s", serial, "shell", "getprop", "ro.product.model"]).strip()
                expected_model = "SM-S906" if args.device == "s22" else "SM-S93"
                if not model.startswith(expected_model):
                    raise ValueError("device identity mismatch; refusing consumer-app actions")
                device = U2Device(serial)
            result = launch(run, device, dry_run=args.dry_run, device_name=args.device,
                            owner_ok=args.owner_ok, opt_in=args.opt_in, pace=args.pace, concurrency=args.concurrency)
        elif args.app_action == "harvest":
            replies = load(args.replies) if args.replies else None
            if args.reply_dump:
                if replies:
                    raise ValueError("use --replies or --reply-dump, not both")
                replies = replies_from_dump(args.reply_dump.read_text())
            result = harvest(run, pr_rows=load(args.pr_fixture) if args.pr_fixture else None,
                             replies=replies, simulated=bool(args.pr_fixture))
        else:
            from .app_fanout_queue import queue
            if not args.opt_in:
                raise ValueError("queue requires --opt-in; mutates turbo preview, never production")
            result = queue(run, args.repo, args.preview, metro_log=args.metro_log, observe_seconds=args.observe_seconds)
        if args.app_action == "queue" and any(row["state"] != "now_testing" for row in result):
            print(json.dumps(result, indent=2))
            return 1
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, ImportError, subprocess.SubprocessError) as exc:
        event(run, "error", error=str(exc))
        print(json.dumps({"error": str(exc), "verified": False}))
        return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(required=True))
    raise SystemExit(main(parser.parse_args()))
