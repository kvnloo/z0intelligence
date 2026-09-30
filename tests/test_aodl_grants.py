"""AODL intent contract -> exact-scope action grants (z0int#55): classes, narrowing, precedence, fail-closed."""

import copy
import json
import os
import subprocess
import sys

import pytest

aodl_contract = pytest.importorskip("aodl_contract")
from aodl_contract import semantic_fingerprint  # noqa: E402

from z0int import aodl_grants as ag  # noqa: E402
from z0int.action_authority import SessionAuthority, authority_check, extract_grants  # noqa: E402
from z0int.action_effects import Ctx, parse_tool_call  # noqa: E402

H64 = "0" * 64


def git_repo(path, slug="acme/widget"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "feat/a", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", f"https://github.com/{slug}.git"], check=True)
    return path


@pytest.fixture
def repo(tmp_path):
    return git_repo(tmp_path / "repo")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    return tmp_path / "z0"


def ctx(repo, branch="feat/a", default="master"):
    return Ctx(cwd=str(repo), scope_root=str(repo), home="/home/nobody-z0test",
               branch_of=lambda d: branch, default_of=lambda d: default)


def node(nid, kind="executor", harness="claude", ceiling=(), scopes=None, prohibitions=None):
    n = {"id": nid, "kind": kind, "ports": [{"id": "in", "direction": "in", "schema": "Task"},
                                           {"id": "out", "direction": "out", "schema": "Task"}],
         "capabilities": ["execute"], "authorityCeiling": ["execute", *ceiling]}
    if kind == "executor":
        n["harness"] = harness
    if scopes is not None:
        n["authorityScopes"] = scopes
    if prohibitions is not None:
        n["prohibitions"] = prohibitions
    return n


def doc(*nodes):
    edges = [{"id": f"e{i}", "relation": "dependency", "from": a["id"], "to": b["id"], "fromPort": "out", "toPort": "in",
              "delivery": {"order": "ordered", "idempotent": True}, "authority": {"grant": [], "delegationDepth": 0},
              "provenance": {"sourceHash": H64}} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]
    return {"specVersion": "0.2", "graphId": "intent", "revision": 0,
            "intentGraph": {"nodes": list(nodes), "edges": edges},
            "policies": {"kinds": ["sequence"]}, "constraints": {"budgets": {"tokens": 1000}, "termination": {"on": "done"}},
            "provenance": {"sourceHash": H64}}


def scoped(kind, targets, prohibitions=None):
    return doc(node("agent", ceiling=[kind], scopes=[{"effect": kind, "targets": targets}], prohibitions=prohibitions))


def decide(cmd, d, c, text=None, require_pin=False):
    compiled = ag.compile_contract(d, ctx=c, require_pin=require_pin)
    g, p = (extract_grants(text, source="prompt", turn=1, ctx=c, default="master", branch="feat/a") if text else ([], []))
    eff = parse_tool_call("Bash", {"command": cmd}, c)["effects"]
    return authority_check(eff, compiled["grants"] + g, {"effects": ["read", "write"]}, compiled["prohibitions"] + p)


# ------------------------------------------------------------------------------------------- authority classes
R = "@repo"  # placeholder: replaced by the fixture repo path
CLASSES = [
    # kind, targets, (cmd in scope, branch), (cmd out of scope, branch)
    ("push", {"repos": [R], "branches": ["feat/a"]}, ("git push origin feat/a", "feat/a"), ("git push origin feat/b", "feat/a")),
    ("force", {"repos": [R], "branches": ["feat/a"]}, ("git push -f origin feat/a", "feat/a"), ("git push -f origin feat/b", "feat/a")),
    ("commit", {"repos": [R], "branches": ["master"]}, ("git commit -m x", "master"), ("git commit -m x", "main")),
    ("rewrite", {"repos": [R], "branches": ["master"]}, ("git rebase -i HEAD~3", "master"), ("git rebase -i HEAD~3", "main")),
    ("merge", {"repos": [R], "prs": ["12"]}, ("gh pr merge 12", "feat/a"), ("gh pr merge 13", "feat/a")),
    ("delete", {"repos": [R], "branches": ["feat/old"]}, ("git branch -D feat/old", "feat/a"), ("git branch -D feat/keep", "feat/a")),
    ("delete", {"paths": ["/srv/data"]}, ("rm -rf /srv/data/x", "feat/a"), ("rm -rf /srv/other/x", "feat/a")),
    ("fs_outside_repo", {"paths": ["/srv/out"]}, ("cp a /srv/out/x", "feat/a"), ("cp a /srv/else/x", "feat/a")),
    ("ssh", {"hosts": ["box1"]}, ("ssh box1 uptime", "feat/a"), ("ssh box2 uptime", "feat/a")),
    ("network", {"hosts": ["api.example.com"]}, ("curl -X POST https://api.example.com/x", "feat/a"),
     ("curl -X POST https://api.example.org/x", "feat/a")),
    ("service", {"units": ["nginx"]}, ("systemctl restart nginx", "feat/a"), ("systemctl restart postgresql", "feat/a")),
    ("install", {"packages": ["ruff"]}, ("uv tool install ruff", "feat/a"), ("uv tool install ruff black", "feat/a")),
    ("deploy", {"envs": ["staging"]}, ("kubectl apply -n staging -f x.yaml", "feat/a"), ("kubectl apply -n prod -f x.yaml", "feat/a")),
    ("publish", {"envs": ["npm"]}, ("npm publish", "feat/a"), ("cargo publish", "feat/a")),
    ("pr", {"repos": [R]}, ("gh pr create --title t", "feat/a"), ("gh pr create --repo other/x --title t", "feat/a")),
    ("comment", {"repos": [R]}, ("gh pr comment 5 --body x", "feat/a"), ("gh pr comment 5 --repo other/x --body x", "feat/a")),
    ("issue", {"repos": [R]}, ("gh issue create --title t", "feat/a"), ("gh issue create --repo other/x --title t", "feat/a")),
    ("ci", {"repos": [R]}, ("gh run rerun 123", "feat/a"), (None, None)),
    ("discard", {"repos": [R]}, ("git reset --hard", "master"), (None, None)),
]


def _sub(targets, repo):
    return {k: [str(repo) if x == R else x for x in v] for k, v in targets.items()}


@pytest.mark.parametrize("kind,targets,inside,outside", CLASSES, ids=[f"{c[0]}-{'-'.join(c[1])}" for c in CLASSES])
def test_every_authority_class(repo, kind, targets, inside, outside):
    d = scoped(kind, _sub(targets, repo))
    base = decide(inside[0], doc(node("agent")), ctx(repo, inside[1]))
    assert base["decision"] != "allow", "precondition: the call is privileged and ungranted without the contract"
    got = decide(inside[0], d, ctx(repo, inside[1]))
    assert got["decision"] == "allow", got
    prov = got["per_effect"][0]["provenance"]["grant"]
    assert prov["source"] == "aodl" and prov["node"] == "agent" and prov["fingerprint"] == semantic_fingerprint(d)
    if outside[0]:
        assert decide(outside[0], d, ctx(repo, outside[1]))["decision"] != "allow"


@pytest.mark.parametrize("kind,targets", [("push", {"repos": [R], "branches": ["feat/a"]}), ("pr", {"repos": [R]}),
                                          ("discard", {"repos": [R]})])
def test_repo_scope_is_the_repo_and_its_worktrees_only(repo, tmp_path, kind, targets):
    other = git_repo(tmp_path / "other", slug="acme/other")
    d = scoped(kind, _sub(targets, repo))
    cmd = {"push": "git push origin feat/a", "pr": "gh pr create --title t", "discard": "git reset --hard"}[kind]
    assert decide(cmd, d, ctx(other, "master"))["decision"] != "allow"
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"], check=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "wt", str(wt)], check=True)
    assert decide(cmd, d, ctx(wt, "master" if kind == "discard" else "feat/a"))["decision"] == "allow"


@pytest.mark.parametrize("kind,targets,cmd", [
    ("sudo", {"hosts": ["local"]}, "sudo ls"),
    ("secret", {"paths": [".env"]}, "cat .env"),
    ("upload", {"hosts": ["claude.ai"]}, None),
    ("message", {"hosts": ["slack.com"]}, None),
    ("external_system", {"hosts": ["api.github.com"]}, None),
])
def test_untargetable_kinds_are_never_granted(repo, kind, targets, cmd):
    compiled = ag.compile_contract(scoped(kind, targets), ctx=ctx(repo))
    assert compiled["status"] == "active" and compiled["grants"] == []
    assert "never granted" in compiled["dropped"][0]["reason"]
    if cmd:
        assert decide(cmd, scoped(kind, targets), ctx(repo))["decision"] != "allow"


# --------------------------------------------------------------------------------------------- scope narrowing
def test_ceiling_without_scope_grants_nothing(repo):
    d = doc(node("agent", ceiling=["push", "pr"]))
    compiled = ag.compile_contract(d, ctx=ctx(repo))
    assert compiled["status"] == "active" and compiled["grants"] == []
    assert decide("git push origin feat/a", d, ctx(repo))["decision"] == "ask"


def test_scope_narrows_kind_and_branch(repo):
    d = scoped("push", {"repos": [str(repo)], "branches": ["feat/a"]})
    c = ctx(repo)
    assert decide("git push origin feat/a", d, c)["decision"] == "allow"
    assert decide("git push origin master", d, c)["decision"] == "ask"      # default branch not named
    assert decide("git push -f origin feat/a", d, c)["decision"] == "ask"   # push scope never covers force
    assert decide("git push --all", d, c)["decision"] != "allow"             # "all" is never an exact target
    assert decide("gh pr create --title t", d, c)["decision"] == "ask"      # other kind


@pytest.mark.parametrize("kind,targets,why", [
    ("push", {"repos": ["@repo"]}, "needs one of ['branches']"),
    ("push", {"branches": ["feat/a"]}, "must name repos"),
    ("pr", {"branches": ["feat/a"], "repos": ["@repo"]}, "do not narrow"),
    ("install", {"hosts": ["pypi.org"]}, "do not narrow"),
    ("teleport", {"hosts": ["mars"]}, "unknown effect kind"),
    ("write", {"paths": ["/srv"]}, "capability class"),
    ("push", {"repos": ["/nonexistent/z0"], "branches": ["feat/a"]}, "resolves to a git repository"),
])
def test_inexact_scopes_are_dropped(repo, kind, targets, why):
    compiled = ag.compile_contract(scoped(kind, _sub(targets, repo)), ctx=ctx(repo))
    assert compiled["status"] == "active" and compiled["grants"] == []
    assert why in compiled["dropped"][0]["reason"]


def test_other_harness_scopes_grant_nothing_but_their_prohibitions_bind(repo):
    codex = node("codex", harness="codex", ceiling=["push"], scopes=[{"effect": "push", "targets": {
        "repos": [str(repo)], "branches": ["feat/a"]}}], prohibitions=[{"effect": "pr"}])
    d = doc(codex, node("agent"))
    compiled = ag.compile_contract(d, ctx=ctx(repo))
    assert compiled["grants"] == [] and "not 'claude'" in compiled["dropped"][0]["reason"]
    assert decide("git push origin feat/a", d, ctx(repo))["decision"] == "ask"
    assert decide("gh pr create --title t", d, ctx(repo))["decision"] == "deny"


# ------------------------------------------------------------------------------------------ prohibition precedence
def test_prohibition_beats_scope_on_same_node(repo):
    d = scoped("push", {"repos": [str(repo)], "branches": ["feat/a", "master"]},
               prohibitions=[{"effect": "push", "targets": {"branches": ["master"]}}])
    assert decide("git push origin feat/a", d, ctx(repo))["decision"] == "allow"
    r = decide("git push origin master", d, ctx(repo))
    assert r["decision"] == "deny" and "contract aodl-canon-1:" in r["reason"]
    assert r["per_effect"][0]["provenance"]["prohibition"]["node"] == "agent"


def test_prohibition_on_any_node_binds_the_executor(repo):
    task = node("intent", kind="task", prohibitions=[{"effect": "merge"}])
    agent = node("agent", ceiling=["merge"], scopes=[{"effect": "merge", "targets": {"repos": [str(repo)], "prs": ["12"]}}])
    assert decide("gh pr merge 12", doc(task, agent), ctx(repo))["decision"] == "deny"


def test_contract_prohibition_beats_in_session_grant(repo):
    d = doc(node("agent", prohibitions=[{"effect": "push", "targets": {"branches": ["master"]}}]))
    assert decide("git push origin master", doc(node("agent")), ctx(repo), text="push to master")["decision"] == "allow"
    assert decide("git push origin master", d, ctx(repo), text="push to master")["decision"] == "deny"


def test_in_session_prohibition_beats_contract_grant(repo):
    d = scoped("pr", {"repos": [str(repo)]})
    assert decide("gh pr create --title t", d, ctx(repo), text="fix it; no PRs")["decision"] == "deny"


def test_targetless_push_prohibition_also_forbids_force(repo):
    d = scoped("force", {"repos": [str(repo)], "branches": ["feat/a"]}, prohibitions=[{"effect": "push"}])
    assert decide("git push -f origin feat/a", d, ctx(repo))["decision"] == "deny"


def test_prohibition_repo_scoping_is_conservative(repo, tmp_path):
    other = git_repo(tmp_path / "other")
    elsewhere = doc(node("agent", prohibitions=[{"effect": "pr", "targets": {"repos": [str(other)]}}]))
    assert ag.compile_contract(elsewhere, ctx=ctx(repo))["prohibitions"] == []
    unresolved = doc(node("agent", prohibitions=[{"effect": "pr", "targets": {"repos": ["/nonexistent/z0"]}}]))
    assert len(ag.compile_contract(unresolved, ctx=ctx(repo))["prohibitions"]) == 1


# ------------------------------------------------------------------------------------------------- fail closed
def _invalid_docs(repo):
    good = scoped("push", {"repos": [str(repo)], "branches": ["feat/a"]})
    unknown_version = copy.deepcopy(good)
    unknown_version["specVersion"] = "0.3"
    no_graph = copy.deepcopy(good)
    del no_graph["intentGraph"]
    widened = copy.deepcopy(good)
    widened["intentGraph"]["nodes"][0]["authorityCeiling"] = ["execute"]
    wildcard = scoped("push", {"repos": [str(repo)], "branches": ["*"]})
    extra = copy.deepcopy(good)
    extra["openQuestions"] = []
    bad_value = copy.deepcopy(good)
    bad_value["intentGraph"]["nodes"][0]["authorityScopes"][0]["targets"]["branches"] = [["feat/a"]]
    return {"not-an-object": ["x"], "unknown-version": unknown_version, "no-intent-graph": no_graph,
            "scope-widens-ceiling": widened, "wildcard": wildcard, "unknown-field": extra, "non-scalar": bad_value}


@pytest.mark.parametrize("name", ["not-an-object", "unknown-version", "no-intent-graph", "scope-widens-ceiling",
                                  "wildcard", "unknown-field", "non-scalar"])
def test_invalid_docs_fail_closed(repo, name):
    d = _invalid_docs(repo)[name]
    compiled = ag.compile_contract(d, ctx=ctx(repo))
    assert compiled["status"] == "rejected" and compiled["grants"] == [] and compiled["prohibitions"] == []
    assert decide("git push origin feat/a", d if isinstance(d, dict) else {}, ctx(repo))["decision"] == "ask"


def test_missing_validator_fails_closed(repo, monkeypatch):
    monkeypatch.setitem(sys.modules, "aodl_contract", None)
    compiled = ag.compile_contract(scoped("push", {"repos": [str(repo)], "branches": ["feat/a"]}), ctx=ctx(repo))
    assert compiled["status"] == "rejected" and "unavailable" in compiled["reason"] and compiled["grants"] == []


def test_unreadable_contract_file(repo, home):
    path = home / "state" / "claude-code" / ag.SESSION_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    out = ag.load_contracts(ctx(repo))
    assert out["grants"] == [] and out["contracts"][0]["status"] == "rejected"


# --------------------------------------------------------------------------------------- fingerprint and pinning
def test_fingerprint_mismatch_after_edit_revokes(repo, home):
    d = scoped("push", {"repos": [str(repo)], "branches": ["feat/a"]})
    path = home / "state" / "claude-code" / ag.SESSION_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(d))
    fp = semantic_fingerprint(d)
    with pytest.raises(PermissionError):
        ag.pin(str(path), interactive=False)       # an agent's shell has no TTY
    with pytest.raises(PermissionError):
        ag.pin(str(path), confirm="nope", interactive=True)
    assert ag.pin(str(path), confirm=fp.split(":")[1][:8], interactive=True)["fingerprint"] == fp
    c = ctx(repo)
    sa = SessionAuthority()
    loader = lambda cx: ag.load_contracts(cx, require_pin=True)  # noqa: E731
    first = sa.check("Bash", {"command": "git push origin feat/a"}, c, contract=loader)
    assert first["decision"]["decision"] == "allow"
    assert first["aodl"]["contracts"][0]["pinned"] is True
    old_grants = ag.load_contracts(c)["grants"]

    # the contract is edited (here: widened to master) -- by anyone, including the agent
    d2 = copy.deepcopy(d)
    d2["intentGraph"]["nodes"][0]["authorityScopes"][0]["targets"]["branches"].append("master")
    path.write_text(json.dumps(d2))
    for cmd in ("git push origin feat/a", "git push origin master"):
        r = sa.check("Bash", {"command": cmd}, c, contract=loader)
        assert r["decision"]["decision"] == "ask", cmd
        assert r["aodl"]["contracts"][0]["status"] == "pin_mismatch"
    # grants compiled from the old revision do not survive against the new fingerprint
    assert ag.current_grants(old_grants, semantic_fingerprint(d2)) == []
    assert ag.current_grants(old_grants, fp) == old_grants
    # reordering keys/whitespace is not an edit
    path.write_text(json.dumps(d, indent=4, sort_keys=True))
    assert ag.load_contracts(c, require_pin=True)["contracts"][0]["status"] == "active"


def test_require_pin_blocks_unpinned(repo):
    d = scoped("push", {"repos": [str(repo)], "branches": ["feat/a"]})
    assert ag.compile_contract(d, ctx=ctx(repo), require_pin=True)["status"] == "unpinned"
    assert decide("git push origin feat/a", d, ctx(repo), require_pin=True)["decision"] == "ask"


def test_repo_contract_relative_repos(repo, home):
    d = scoped("pr", {"repos": ["."]})
    (repo / ".aodl").mkdir()
    (repo / ".aodl" / "intent.json").write_text(json.dumps(d))
    out = ag.load_contracts(ctx(repo))
    assert out["contracts"][0]["status"] == "active" and len(out["grants"]) == 1
    # the same relative repo in the session-level contract names nothing
    session = home / "state" / "claude-code" / ag.SESSION_FILE
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(json.dumps(d))
    out = ag.load_contracts(ctx(repo), paths=[str(session)])
    assert out["grants"] == []


def test_contract_grants_are_not_persisted_in_session_state(repo):
    d = scoped("pr", {"repos": [str(repo)]})
    sa = SessionAuthority()
    r = sa.check("Bash", {"command": "gh pr create --title t"}, ctx(repo), contract=lambda c: ag.compile_contract(d, ctx=c))
    assert r["decision"]["decision"] == "allow"
    assert all(g.get("source") != "aodl" for g in sa.state()["grants"])
    # non-privileged calls never pay for the contract
    calls = []
    sa.check("Bash", {"command": "git status"}, ctx(repo), contract=lambda c: calls.append(1) or {})
    assert calls == []


def test_shadow_hook_logs_contract_provenance(repo, tmp_path):
    z0 = tmp_path / "z0hook"
    d = scoped("push", {"repos": [str(repo)], "branches": ["feat/a"]},
               prohibitions=[{"effect": "push", "targets": {"branches": ["master"]}}])
    contract = z0 / "state" / "claude-code" / ag.SESSION_FILE
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps(d))
    (tmp_path / "t.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
    env = dict(os.environ, Z0INT_HOME=str(z0))
    for cmd in ("git push origin feat/a", "git push origin master"):
        hook = {"session_id": "s1", "transcript_path": str(tmp_path / "t.jsonl"), "cwd": str(repo),
                "tool_name": "Bash", "tool_input": {"command": cmd}, "permission_mode": "default"}
        out = subprocess.run([sys.executable, "-m", "z0int.action_hook"], input=json.dumps(hook), capture_output=True,
                             text=True, env=env)
        assert out.returncode == 0 and out.stdout == ""
    rows = [json.loads(x) for x in (z0 / "state" / "claude-code" / "actions.jsonl").read_text().splitlines()]
    assert [r["decision"] for r in rows] == ["allow", "deny"]
    assert rows[0]["privileged"][0]["provenance"]["grant"]["fingerprint"] == semantic_fingerprint(d)
    assert rows[0]["aodl"]["contracts"][0]["status"] == "active" and rows[0]["shadow"] is True


def test_compile_cache_hits_only_on_identical_bytes(repo, home, monkeypatch):
    d = scoped("pr", {"repos": [str(repo)]})
    path = home / "state" / "claude-code" / ag.SESSION_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(d))
    first = ag.load_contracts(ctx(repo))
    assert first["contracts"][0]["status"] == "active"
    monkeypatch.setitem(sys.modules, "aodl_contract", None)  # a hit must not need the validator
    assert ag.load_contracts(ctx(repo)) == first
    path.write_text(json.dumps(d) + " ")  # any byte change misses the cache and recompiles (here: fails closed)
    assert ag.load_contracts(ctx(repo))["contracts"][0]["status"] == "rejected"
