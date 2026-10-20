"""Private run ledger, host-wide provider admission, concurrent DAG execution."""

from __future__ import annotations
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from . import adapters
from .graph import PLAN_PROMPT, compile_graph, validate_jobs

TERMINAL = {"completed", "failed", "cancelled", "deadline"}


def root():
    path = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int")) / "state/turbo"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp-" + uuid.uuid4().hex)
    with tmp.open("x", encoding="utf-8") as out:
        os.chmod(tmp, 0o600)
        json.dump(value, out, indent=2)
    tmp.replace(path)


def duration(value):
    match = re.fullmatch(r"([1-9][0-9]*)(s|m|h)?", value)
    if not match:
        raise ValueError("duration must be positive integer with s, m or h suffix")
    seconds = int(match[1]) * {None: 1, "s": 1, "m": 60, "h": 3600}[match[2]]
    if seconds > 3600:
        raise ValueError("maximum deadline is 1h")
    return seconds


def run_folder(run_id):
    if not re.fullmatch(r"turbo-[0-9a-f]{16}", run_id):
        raise ValueError("invalid run id")
    return root() / run_id


def status(run_id=None):
    if run_id:
        folder = run_folder(run_id)
        path = folder / "receipt.json"
        if not path.exists():
            path = folder / "status.json"
        return json.loads(path.read_text())
    return {
        "runs": [
            json.loads(p.read_text())
            for p in sorted(root().glob("turbo-*/status.json"))
        ]
    }


def stop(run_id):
    folder = run_folder(run_id)
    if not (folder / "status.json").is_file():
        raise ValueError("unknown run id")
    (folder / "stop").touch(mode=0o600)
    return {
        "run_id": run_id,
        "stop_requested": True,
        "terminal_confirmed": status(run_id).get("state")
        in {"completed", "failed", "cancelled", "deadline"},
    }


class Admission:
    """Locks live across HTTP/process execution: same provider cannot stampede across runs."""

    def __init__(self, provider, deadline, stop):
        self.provider, self.deadline, self.stop = provider, deadline, stop
        self.file = None

    def __enter__(self):
        self.file = (root() / ("admit-" + self.provider + ".lock")).open("a+")
        try:
            return self.acquire()
        except BaseException:
            self.file.close()
            raise

    def acquire(self):
        while True:
            adapters.check(self.deadline, self.stop)
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.1)
        self.state_path = root() / ("admit-" + self.provider + ".json")
        try:
            state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            state = {}
        while time.time() < state.get("next_at", 0):
            adapters.check(self.deadline, self.stop)
            time.sleep(0.1)
        quota = state.get("quota", {})
        waits = []
        for dim in quota.get("dimensions", {}).values():
            if dim.get("remaining") is not None and dim["remaining"] <= 0:
                if dim.get("reset_at") is None:
                    self.file.close()
                    raise adapters.Halt("quota_exhausted_unknown_reset")
                waits.append(dim["reset_at"])
        while waits and time.time() < max(waits):
            adapters.check(self.deadline, self.stop)
            time.sleep(0.1)
        save(self.state_path, {"next_at": time.time() + 10, "policy_rpm": 6})
        return self

    def observe(self, result):
        from kerdoios.quota import quota_state_from_headers

        headers = result.get("rate_headers", {})
        if headers:
            # Groq documents unsuffixed request headers as RPD, not RPM.
            # Adapt at this boundary without changing the pinned allocator API.
            parsed_headers = dict(headers)
            if self.provider == "groq":
                for field in ("limit", "remaining", "reset"):
                    key = "x-ratelimit-" + field + "-requests"
                    if key in parsed_headers:
                        parsed_headers[key + "-day"] = parsed_headers.pop(key)
            quota = quota_state_from_headers(
                parsed_headers, provider=self.provider, model=result.get("model", "")
            ).to_dict()
            result["quota"] = quota
            save(
                self.state_path,
                {"next_at": time.time() + 10, "policy_rpm": 6, "quota": quota},
            )

    def __exit__(self, typ, exc, tb):
        if isinstance(exc, adapters.ProviderError) and exc.status == 429:
            save(
                self.state_path,
                {"next_at": time.time() + exc.retry_after, "last_status": 429},
            )
        if self.file:
            self.file.close()


def inventory(probe=False):
    from kerdoios.turbo import discover_turbo_offers, DEFAULT_ADMISSION

    adapters.load_keys()
    offers = discover_turbo_offers()
    rows = []
    for offer in offers:
        row = {
            "id": offer.id,
            "provider": offer.provider,
            "model": offer.model,
            "capacity": offer.to_dict()["capacity"],
            "quota": offer.to_dict()["economics"].get("quota"),
            "admission_policy": DEFAULT_ADMISSION[offer.provider],
            "source": offer.source,
            "limits_note": "capacity rpm/concurrency are client policy; provider limits only in quota/rate_headers",
            "latency_ms": None,
            "output_tok_s": None,
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
            "measurement": "not_probed",
        }
        if probe:
            deadline = time.monotonic() + 45
            probe_folder = root() / "probes" / uuid.uuid4().hex
            probe_folder.mkdir(parents=True, mode=0o700)
            try:
                with Admission(
                    offer.provider, deadline, probe_folder / "stop"
                ) as admission:
                    prompt = "No tools. Write one paragraph of 120 words explaining git worktree isolation."
                    if offer.provider == "opencode":
                        result = adapters.opencode(
                            offer,
                            prompt,
                            probe_folder,
                            probe_folder,
                            deadline,
                            probe_folder / "stop",
                        )
                    else:
                        result = adapters.chat(
                            offer, prompt, 768, deadline, probe_folder / "stop"
                        )
                    admission.observe(result)
                row.update({k: v for k, v in result.items() if k != "text"})
                row["measurement"] = "one_live_sample"
                row["answer_nonempty"] = bool(result.get("text", "").strip())
            except Exception as exc:
                row.update(
                    measurement="failed",
                    error=str(exc),
                    http_status=getattr(exc, "status", None),
                    retry_after=getattr(exc, "retry_after", None),
                    rate_headers=getattr(exc, "rate_headers", {}),
                )
        rows.append(row)
    result = {
        "schema": "z0int.turbo.inventory.v1",
        "observed_at": time.time(),
        "models": rows,
        "notes": "output_tok_s = output tokens / full request wall seconds, not steady-state decode rate; prices/quota unknown unless observed",
    }
    save(root() / "inventory.json", result)
    return result


def worktree(repo, folder):
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(folder), "HEAD"],
        check=True,
        capture_output=True,
    )
    return folder


class Runner:
    def __init__(
        self,
        task,
        repo=None,
        deadline_seconds=600,
        max_tokens=12000,
        max_requests=10,
        concurrency=3,
        context="",
        checks=(),
        normal=False,
        emit=None,
        no_code_workers=False,
    ):
        if not 1 <= deadline_seconds <= 3600:
            raise ValueError("deadline_seconds must be 1..3600")
        if not isinstance(task, str) or not task.strip() or len(task) > 24000:
            raise ValueError("task must be nonempty, at most 24000 characters")
        if (
            not 1 <= concurrency <= 3
            or not 1 <= max_requests <= 20
            or not 256 <= max_tokens <= 100000
        ):
            raise ValueError("invalid concurrency, request or token budget")
        self.repo = Path(repo).expanduser().resolve() if repo else None
        if self.repo:
            subprocess.run(
                ["git", "-C", str(self.repo), "rev-parse", "--verify", "HEAD"],
                capture_output=True,
                check=True,
            )
        self.run_id = "turbo-" + uuid.uuid4().hex[:16]
        self.folder = run_folder(self.run_id)
        self.folder.mkdir(mode=0o700)
        self.stop = self.folder / "stop"
        self.start = time.monotonic()
        self.deadline = self.start + deadline_seconds
        self.seconds = deadline_seconds
        self.task, self.context, self.checks, self.normal = (
            task,
            context,
            list(checks),
            normal,
        )
        self.no_code_workers = no_code_workers
        self.budget, self.request_budget, self.concurrency = (
            max_tokens,
            max_requests,
            concurrency,
        )
        self.calls, self.reserved, self.active, self.peak = 0, 0, 0, 0
        self.lock = threading.Lock()
        self.busy_providers = set()
        self.emit_callback = emit
        self.results, self.failures = {}, []
        save(
            self.folder / "request.json",
            {
                "task": task,
                "repo": str(self.repo) if self.repo else None,
                "deadline_seconds": deadline_seconds,
                "normal": normal,
            },
        )
        self.event(
            "started",
            "Turbo started" if not normal else "Single-agent reference started",
        )

    def event(self, name, summary, **fields):
        with self.lock:
            row = {
                "schema": "z0int.turbo.event.v1",
                "run_id": self.run_id,
                "event": name,
                "summary": summary,
                "elapsed_seconds": time.monotonic() - self.start,
                **fields,
            }
            with (self.folder / "events.jsonl").open("a") as out:
                os.chmod(self.folder / "events.jsonl", 0o600)
                out.write(json.dumps(row) + "\n")
            save(
                self.folder / "status.json",
                {
                    "run_id": self.run_id,
                    "state": name,
                    "summary": summary,
                    "elapsed_seconds": row["elapsed_seconds"],
                    "pid": os.getpid(),
                },
            )
            if self.emit_callback:
                self.emit_callback(row)

    def call(self, fn, *args, reserve=0, **kwargs):
        with self.lock:
            adapters.check(self.deadline, self.stop)
            if (
                self.calls >= self.request_budget
                or self.reserved + reserve > self.budget
            ):
                raise adapters.Halt("budget_exhausted")
            self.calls += 1
            self.reserved += reserve
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            return fn(*args, **kwargs)
        finally:
            with self.lock:
                self.active -= 1

    def native(self, prompt, role, cwd=None):
        result = self.call(
            adapters.native,
            prompt,
            role,
            cwd or self.folder,
            self.folder,
            self.deadline,
            self.stop,
        )
        self.results[role] = result
        return result

    def worker(self, job, offers, measurements):
        from kerdoios.turbo import choose_turbo_offer

        coding = job["lane"] == "code"
        if coding and not self.repo:
            raise ValueError("code lane requires --repo")
        if choose_turbo_offer(offers, coding=coding, measurements=measurements) is None:
            raise RuntimeError("no_live_capacity_for_" + job["lane"])
        while True:
            adapters.check(self.deadline, self.stop)
            with self.lock:
                cooling = set()
                for provider in {o.provider for o in offers}:
                    try:
                        state = json.loads(
                            (root() / ("admit-" + provider + ".json")).read_text()
                        )
                        if (
                            state.get("last_status") == 429
                            and state.get("next_at", 0) > time.time()
                        ):
                            cooling.add(provider)
                    except (OSError, ValueError):
                        pass
                offer = choose_turbo_offer(
                    offers,
                    coding=coding,
                    excluded=self.busy_providers | cooling,
                    measurements=measurements,
                )
                if offer:
                    self.busy_providers.add(offer.provider)
                    break
            time.sleep(0.1)
        folder = self.folder / job["id"]
        folder.mkdir(mode=0o700)
        cwd = worktree(self.repo, folder / "worktree") if coding else folder
        prompt = (
            job["prompt"] + "\n\nTask: " + self.task + "\nContext:\n" + self.context
        )
        for dep in job["depends_on"]:
            prompt += "\nDependency result " + dep + ":\n" + self.results[dep]["text"]
        if coding:
            prompt += "\nRead AGENTS.md. Work ONLY in this detached worktree; do not merge, push, deploy, alter credentials, or edit another worktree. Run tests. Report exact changed files/checks/gaps."
        else:
            prompt += "\nNo tools available. Do not pretend you fetched/read files. Cite supplied sources or state gaps."
        self.event(
            "node_waiting",
            "Waiting for " + offer.provider,
            node=job["id"],
            placement=offer.id,
        )
        try:
            with Admission(offer.provider, self.deadline, self.stop) as admission:
                self.event(
                    "node_started",
                    job["id"] + " on " + offer.provider,
                    node=job["id"],
                    placement=offer.id,
                )
                if coding:
                    result = self.call(
                        adapters.opencode,
                        offer,
                        prompt,
                        cwd,
                        folder,
                        self.deadline,
                        self.stop,
                        reserve=2048,
                    )
                    result["worktree"] = str(cwd)
                else:
                    reserve = len(prompt.encode()) + 1024
                    result = self.call(
                        adapters.chat,
                        offer,
                        prompt,
                        1024,
                        self.deadline,
                        self.stop,
                        reserve=reserve,
                    )
                admission.observe(result)
        finally:
            with self.lock:
                self.busy_providers.discard(offer.provider)
        result["placement"] = offer.id
        save(folder / "result.json", result)
        self.event(
            "node_completed",
            job["id"] + " completed; verification pending",
            node=job["id"],
        )
        return result

    def execute(self):
        adapters.load_keys()
        verdict = {"verified": False, "reason": "not_verified"}
        state = "failed"
        try:
            if self.normal:
                work = (
                    worktree(self.repo, self.folder / "reference-worktree")
                    if self.repo
                    else self.folder
                )
                result = self.native(
                    "Complete this task as a normal single-agent run, without delegating, fanout or Turbo. Read repository rules if relevant. Never merge/push/deploy. Report evidence and gaps.\nTask: "
                    + self.task
                    + "\nSource context:\n"
                    + self.context,
                    "reference",
                    work,
                )
                if self.repo:
                    result["worktree"] = str(work)
                plan = {
                    "jobs": [],
                    "acceptance": [
                        "Satisfy the owner task with cited evidence and executed checks"
                    ],
                }
            else:
                from kerdoios.turbo import discover_turbo_offers

                offers = discover_turbo_offers()
                save(self.folder / "offers.json", [o.to_dict() for o in offers])
                privacy = (
                    "Only fast text jobs are allowed. Workers must only propose source-free test/safety designs from the supplied public task; the native verifier must implement and test the selected design in its integration worktree. Do not create code jobs.\n"
                    if self.no_code_workers
                    else ""
                )
                plan = validate_jobs(
                    adapters.object_from_text(
                        self.native(
                            PLAN_PROMPT
                            + privacy
                            + json.dumps(
                                {
                                    "task": self.task,
                                    "context": self.context,
                                    "code_repo": bool(self.repo)
                                    and not self.no_code_workers,
                                }
                            ),
                            "planner",
                        )["text"]
                    )
                )
                if self.no_code_workers and any(
                    j["lane"] == "code" for j in plan["jobs"]
                ):
                    raise ValueError("code_workers_disabled_for_private_source")
                save(self.folder / "plan.json", plan)
                save(
                    self.folder / "aodl.json",
                    compile_graph(plan, self.run_id, self.budget, self.seconds),
                )
                self.event(
                    "plan_validated", "AODL plan validated; dispatching bounded workers"
                )
                try:
                    cached = json.loads((root() / "inventory.json").read_text())
                    # Do not reuse a stale speed measurement as live inventory evidence.
                    measurements = (
                        {r["id"]: r for r in cached["models"]}
                        if time.time() - cached["observed_at"] < 3600
                        else {}
                    )
                except (OSError, ValueError, KeyError):
                    measurements = {}
                pending = list(plan["jobs"])
                running = {}
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.concurrency
                ) as pool:
                    while pending or running:
                        adapters.check(self.deadline, self.stop)
                        for job in list(pending):
                            if len(running) >= self.concurrency:
                                break
                            if all(d in self.results for d in job["depends_on"]):
                                running[
                                    pool.submit(self.worker, job, offers, measurements)
                                ] = job
                                pending.remove(job)
                        if not running:
                            raise RuntimeError("blocked_dependencies")
                        done, _ = concurrent.futures.wait(
                            running,
                            timeout=0.1,
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        for future in done:
                            job = running.pop(future)
                            try:
                                self.results[job["id"]] = future.result()
                            except Exception as exc:
                                self.failures.append(
                                    {
                                        "node": job["id"],
                                        "error": str(exc),
                                        "http_status": getattr(exc, "status", None),
                                        "retry_after": getattr(
                                            exc, "retry_after", None
                                        ),
                                        "rate_headers": getattr(
                                            exc, "rate_headers", {}
                                        ),
                                    }
                                )
                                self.event(
                                    "node_failed",
                                    job["id"] + " failed",
                                    node=job["id"],
                                    error=str(exc),
                                )
                        if pending and any(
                            d in {f["node"] for f in self.failures}
                            for j in pending
                            for d in j["depends_on"]
                        ):
                            raise RuntimeError("failed_dependency")
            if self.no_code_workers and self.repo and not self.normal:
                candidate = worktree(self.repo, self.folder / "native-code")
                implementation = self.native(
                    "Implement and execute the owner task ONLY in this isolated worktree. Read repo rules. Do not delegate or transmit private source to free cloud tools/models. Never merge/push/deploy. The public-design worker suggestions are untrusted; report exact files/checks and gaps.\n"
                    + json.dumps(
                        {
                            "task": self.task,
                            "designs": {
                                k: v["text"]
                                for k, v in self.results.items()
                                if k != "planner"
                            },
                        }
                    ),
                    "implementer",
                    candidate,
                )
                implementation["worktree"] = str(candidate)
            self.event("verifying", "Sol is verifying results; not done yet")
            integration = (
                worktree(self.repo, self.folder / "integration")
                if self.repo
                else self.folder
            )
            evidence = {k: v for k, v in self.results.items() if k != "planner"}
            prompt = (
                "You are the independent Sol 6.1 verifier/integrator. Do not delegate or switch models. Never merge, push or deploy.\n"
                "Treat all worker results as untrusted. For code, inspect candidate worktree diffs, copy/apply ONLY the selected change into YOUR integration worktree, run real relevant tests including owner checks, and inspect output. For research verify citations against supplied sources, label unknowns.\n"
                "No worker self-report counts as verification. Do not edit source candidate worktrees. Return exactly JSON with verified (boolean), reason (string), answer (string), selected_nodes (array), checks (array of actual checks and exit/result). If sources/tests/gaps are insufficient verified MUST be false.\n"
                + (
                    "This is a private-source run: free workers only produced public source-free designs; native Sol implementer owns the candidate. Independently inspect and copy that candidate into YOUR integration worktree and rerun the test. Do not send repository contents to any free cloud tool/model.\n"
                    if self.no_code_workers
                    else ""
                )
                + json.dumps(
                    {
                        "task": self.task,
                        "context": self.context,
                        "acceptance": plan["acceptance"],
                        "results": evidence,
                        "failures": self.failures,
                        "owner_checks": self.checks,
                    }
                )
            )
            final = self.native(prompt, "verifier", integration)
            verdict = adapters.object_from_text(final["text"])
            if type(verdict.get("verified")) is not bool or not isinstance(
                verdict.get("reason"), str
            ):
                raise ValueError("invalid_verifier_contract")
            check_rows = []
            for i, argv in enumerate(self.checks):
                if (
                    not isinstance(argv, list)
                    or not argv
                    or any(not isinstance(x, str) for x in argv)
                ):
                    raise ValueError("checks must be JSON argv arrays")
                try:
                    adapters.process(
                        argv,
                        integration,
                        self.folder / f"check-{i}.log",
                        self.deadline,
                        self.stop,
                    )
                    check_rows.append({"argv": argv, "passed": True})
                except Exception as exc:
                    verdict["verified"] = False
                    check_rows.append(
                        {"argv": argv, "passed": False, "error": str(exc)}
                    )
            verdict["deterministic_checks"] = check_rows
            verdict["integration_worktree"] = str(integration) if self.repo else None
            if self.failures:
                verdict["verified"] = False
                verdict["reason"] = "required_node_failure: " + verdict["reason"]
            state = "completed" if verdict["verified"] is True else "failed"
        except Exception as exc:
            state = (
                str(exc)
                if isinstance(exc, adapters.Halt)
                and str(exc) in {"cancelled", "deadline"}
                else "failed"
            )
            self.failures.append(
                {"error": str(exc), "http_status": getattr(exc, "status", None)}
            )
            verdict = {"verified": False, "reason": str(exc)}
        finally:
            wall = time.monotonic() - self.start
            providers = {}
            for result in self.results.values():
                p = providers.setdefault(
                    result["provider"],
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "accounting_complete": True,
                        "calls": 0,
                        "cost_usd": None,
                    },
                )
                p["calls"] += 1
                for key in ("input_tokens", "output_tokens"):
                    if result.get(key) is None:
                        p["accounting_complete"] = False
                    else:
                        p[key] += result[key]
            for p in providers.values():
                p["output_tok_s_wall"] = (
                    p["output_tokens"] / wall if p["accounting_complete"] else None
                )
                if not p["accounting_complete"]:
                    p["input_tokens"] = p["output_tokens"] = None
            outputs = [v.get("output_tokens") for v in self.results.values()]
            output_total = (
                sum(outputs)
                if outputs
                and all(isinstance(v, int) for v in outputs)
                and not self.failures
                else None
            )
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[3],
                capture_output=True,
                text=True,
            ).stdout.strip()
            receipt = {
                "schema": "z0int.turbo.receipt.v1",
                "run_id": self.run_id,
                "state": state,
                "source_revision": revision,
                "results": self.results,
                "output_tokens": output_total,
                "output_tok_s_wall": output_total / wall
                if output_total is not None
                else None,
                "http_429s": sum(f.get("http_status") == 429 for f in self.failures),
                "wall_seconds": wall,
                "peak_parallelism": self.peak,
                "requests": self.calls,
                "worker_reserved_tokens": self.reserved,
                "worker_token_budget": self.budget,
                "token_budget_scope": "conservative worker reservation; native planner/verifier token usage is observed, not hard-capped",
                "providers": providers,
                "cost_usd": None,
                "billing_status": "unknown_unless_provider_supplied",
                "free_tier_usage": [
                    {
                        "provider": v["provider"],
                        "model": v["model"],
                        "billing": v.get("billing"),
                        "input_tokens": v.get("input_tokens"),
                        "output_tokens": v.get("output_tokens"),
                    }
                    for v in self.results.values()
                ],
                "failures": self.failures,
                "quality": verdict,
                "mode": "normal" if self.normal else "turbo",
                "jev_gate": "not_invoked_private_or_unavailable; Sol verification required",
            }
            save(self.folder / "receipt.json", receipt)
            self.event(
                state,
                "Verified result ready"
                if verdict.get("verified") is True
                else "Run ended without verified success",
                quality_verified=verdict.get("verified") is True,
            )
        return receipt
