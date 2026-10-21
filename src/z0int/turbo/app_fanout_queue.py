"""Separate integration opt-in; no production merge, no force reset on rollback."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
import uuid

from .app_fanout import BASE, NOW_TESTING, command, event, load, locked, save, stopped

AUTHOR = ["-c", "user.name=Kevin Rajan", "-c", "user.email=7121943+kvnloo@users.noreply.github.com"]
RED = re.compile(r"redbox|syntaxerror|unable to resolve|error:|error bundling|transformerror|referenceerror|typeerror", re.I)


def git(repo, *args):
    return command(["git", *AUTHOR, *args], cwd=repo).strip()


def clean(repo):
    if git(repo, "status", "--porcelain"):
        raise RuntimeError("dirty worktree; refusing integration")


class Metro:
    def __init__(self, log=None):
        self.log = Path(log) if log else None
        if self.log:
            self.offset = self.log.stat().st_size
        else:
            out = command(["journalctl", "--user", "-u", "dash-metro-preview-8098", "-n", "0", "--show-cursor", "--no-pager"])
            match = re.search(r"-- cursor: (\S+)", out)
            if not match:
                raise RuntimeError("cannot pin preview Metro journal cursor")
            self.cursor = match[1]

    def read(self):
        if self.log:
            if self.log.stat().st_size < self.offset:
                raise RuntimeError("Metro log rotated; verification unavailable")
            with self.log.open() as stream:
                stream.seek(self.offset)
                return stream.read()
        return command(["journalctl", "--user", "-u", "dash-metro-preview-8098",
                        "--after-cursor", self.cursor, "--no-pager", "-o", "cat"])


def queue(run, repo, preview, *, metro_log=None, observe_seconds=20, now_testing=NOW_TESTING,
          build=None, sleep=time.sleep, simulated=False):
    run, repo, preview = Path(run).resolve(), Path(repo).resolve(), Path(preview).resolve()
    if observe_seconds < 20:
        raise ValueError("Metro observation must be >=20 seconds")
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    with locked(common / "app-fanout-queue.lock"):
        stopped(run)
        clean(preview)
        if git(preview, "branch", "--show-current") != BASE:
            raise ValueError("preview must be checked out on " + BASE)
        if git(preview, "rev-parse", "--path-format=absolute", "--git-common-dir") != str(common):
            raise ValueError("preview and repo must belong to the same Git repository")
        candidates = load(run / "candidates.json")
        results_path = run / "queue.json"
        results = load(results_path) if results_path.exists() else []
        if any(r["state"] == "checking" or r["state"].startswith("rollback_blocked") for r in results):
            raise RuntimeError("interrupted queue transaction; reconcile recorded worktree and preview before continuing")
        seen = {r["tag"] for r in results}
        for candidate in candidates:
            stopped(run)
            if candidate["tag"] in seen:
                continue
            if candidate["repo"] != "kvnloo/dash" or candidate["base"] != BASE:
                raise ValueError("automatic preview queue supports Dash turbo only")
            if candidate.get("simulated") and not simulated:
                raise ValueError("simulation candidates cannot enter live preview")
            if not candidate.get("owner_words") or not candidate.get("route") or not candidate.get("acceptance"):
                raise ValueError("missing Now testing provenance")
            clean(preview)
            base = git(preview, "rev-parse", "HEAD")
            target = run / ("integration-" + uuid.uuid4().hex[:12])
            git(repo, "worktree", "add", "--detach", str(target), base)
            row = {"tag": candidate["tag"], "state": "checking", "worktree": str(target), "simulated": simulated}
            results.append(row)
            save(results_path, results)
            seen.add(candidate["tag"])
            merged = None
            try:
                if candidate["kind"] == "pr":
                    revision = candidate["revision"]
                    if not re.fullmatch(r"[0-9a-f]{40}", revision):
                        raise ValueError("candidate revision must be a full pinned Git SHA")
                    git(repo, "fetch", "origin", revision)
                    git(target, "merge", "--no-ff", revision, "-m", "App fanout candidate " + candidate["tag"])
                elif candidate["kind"] == "patch":
                    patch = str(Path(candidate["patch"]).resolve())
                    git(target, "apply", "--check", patch)
                    git(target, "apply", "--index", patch)
                    git(target, "commit", "-m", "App fanout candidate " + candidate["tag"])
                else:
                    raise ValueError("unknown candidate kind")
                stopped(run)
                if build:
                    build(target)
                else:
                    output = command(["fleet-build", "--node", "mbp", "--wait", "1800", "--fetch", "app/dist", "--",
                                      "bash", "-c", "bun install --frozen-lockfile && "
                                      "(cd bridge && bun install --frozen-lockfile) && "
                                      "(cd app && bun install --frozen-lockfile) && "
                                      "bun test app/src bridge/src shared && "
                                      "(cd app && EXPO_PUBLIC_DASH_TURBO=1 bunx expo export --platform android)"],
                                     cwd=target, timeout=3600)
                    save(run / ("build-" + target.name + ".json"), {"output": output})
                stopped(run)
                clean(preview)
                if git(preview, "rev-parse", "HEAD") != base:
                    raise RuntimeError("preview advanced during build; refuse merge")
                metro = Metro(metro_log)
                git(preview, "merge", "--ff-only", str(git(target, "rev-parse", "HEAD")))
                merged = git(preview, "rev-parse", "HEAD")
                # Incrementally observe new output only; preexisting historical errors do not veto.
                for _ in range(int(observe_seconds * 4)):
                    stopped(run)
                    if RED.search(metro.read()):
                        raise RuntimeError("preview Metro error/redbox")
                    sleep(0.25)
                stopped(run)
                record = {"schema": "z0int.now_testing.v1", "time": time.time(), "feature": candidate["title"],
                          "owner_words": candidate["owner_words"], "acceptance": candidate["acceptance"],
                          "route": candidate["route"], "issue": candidate["issue"], "tag": candidate["tag"],
                          "revision": merged, "simulated": simulated,
                          "verdict": "awaiting_owner", "verification": "android_export_and_metro_observation"}
                # Tests must pass an isolated destination; synthetic evidence never touches the live card.
                if simulated and Path(now_testing) == NOW_TESTING:
                    raise ValueError("simulation cannot write live Now testing")
                with locked(Path(str(now_testing) + ".lock")):
                    Path(now_testing).parent.mkdir(parents=True, exist_ok=True)
                    with Path(now_testing).open("a") as out:
                        out.write(json.dumps(record) + "\n")
                row.update(state="now_testing", revision=merged)
                event(run, "now_testing", **record)
            except Exception as exc:
                row.update(state="rejected", error=str(exc))
                if merged:
                    # Never overwrite unrelated work or rewrite preview history.
                    clean(preview)
                    if git(preview, "rev-parse", "HEAD") != merged:
                        row["state"] = "rollback_blocked_preview_advanced"
                        save(results_path, results)
                        raise RuntimeError("preview advanced; manual rollback required") from exc
                    parents = git(preview, "rev-list", "--parents", "-n", "1", merged).split()
                    options = ["-m", "1"] if len(parents) > 2 else []
                    git(preview, "revert", "--no-edit", *options, merged)
                    row.update(state="reverted", revert=git(preview, "rev-parse", "HEAD"))
                event(run, "queue_rejected", **row)
            save(results_path, results)
            if row["state"] != "now_testing":
                break
        return results
