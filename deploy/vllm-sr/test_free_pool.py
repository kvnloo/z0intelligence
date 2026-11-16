"""python3 -m pytest deploy/vllm-sr/test_free_pool.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import free_pool as fp  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_state(monkeypatch, tmp_path):
    # Tests render from the committed seed probe, never from this host's live re-probe or inventory.
    monkeypatch.setattr(fp, "POOL", tmp_path / "no-live-pool.json")
    monkeypatch.setattr(fp, "INVENTORY", tmp_path / "no-inventory")
    monkeypatch.setattr(fp, "CATALOGS", tmp_path / "no-catalogs.json")

GROQ_TPD = ("Rate limit reached for model `openai/gpt-oss-20b` in organization `org_x` service tier `on_demand` on "
            "tokens per day (TPD): Limit 200000, Used 199645, Requested 1234. Please try again in 7m12.5s.")


def _attempt_line(model, status, error, ts="2026-11-06T04:00:00.000"):
    rec = {"attempts": [{"attempt_id": f"{model}-{status}-{ts}", "model": model, "status_code": status, "error": error}]}
    return json.dumps({"ts": ts, "msg": "fallback_execution_complete", "execution_record": json.dumps(rec)})


def test_reset_parsing():
    assert fp.reset_after(GROQ_TPD, 0) == 432.5
    assert fp.reset_after("Please try again in 1h2m3s", 0) == 3723
    assert fp.reset_after("try again in 250ms", 0) == 0.25
    assert fp.reset_after('{"code":"PROMOTION_MODEL_LIMIT_REACHED"}', 0) == 3600
    assert fp.reset_after('"X-RateLimit-Reset":"1793990000000"', 1793980000) == 10000
    assert fp.reset_after("high demand, try later", 0) == 60


def test_buckets_share_accounts_but_not_groq_models():
    assert fp.bucket("or-nemotron-3-ultra") == fp.bucket("or-dots-3-note") == "openrouter"
    assert fp.bucket("groq-gpt-oss-20b") != fp.bucket("groq-qwen3.8-27b")


def test_429_blocks_only_that_groq_model_until_its_reset():
    st = {"events": [], "blocked": {}}
    now = 1793937600.0  # 2026-11-06T04:00:00Z
    fp.ingest(st, [_attempt_line("groq-gpt-oss-20b", 429, GROQ_TPD)], now)
    fp.assess(st, now)
    assert st["blocked"]["groq-gpt-oss-20b"]["until"] == now + 432.5
    assert "groq-qwen3.8-27b" not in st["blocked"]


def test_preemptive_block_at_95pct_of_daily_requests():
    now = 1_800_000_000.0
    st = {"events": [[now - 3000 + i, "openrouter", 10] for i in range(950)], "blocked": {}}
    fp.assess(st, now)
    b = st["blocked"]["openrouter"]
    assert "pre-emptive" in b["why"] and b["until"] > now + 80000


def test_long_blocks_exclude_every_backend_in_the_bucket():
    now = 1_800_000_000.0
    st = {"blocked": {"openrouter": {"until": now + 5000, "why": ""}, "nim": {"until": now + 30, "why": ""}}}
    ex = fp.excluded(st, now)
    assert ex and all(b.startswith("or-") for b in ex)  # NIM's 30 s block stays with the router's own fallback


def test_alive_rejects_quota_dead_429_and_slow_backends():
    import free_pool as f
    assert f.alive({"tool_call": True, "tool_s": 1.0})
    assert not f.alive({"tool_call": True, "tool_s": 55.0})
    assert f.alive({"tool_status": 429, "error": "rate limit exceeded, retry after 20s"})
    assert not f.alive({"tool_status": 429, "error": '{"code":"1113","message":"Insufficient balance or no resource package. Please recharge"}'})
    assert not f.alive({"tool_status": 429, "error": '{"code":"PROMOTION_MODEL_LIMIT_REACHED"}'})
    assert not f.alive({"tool_status": 402, "error": "payment required"})


def test_weights_spread_by_bucket_capacity_and_eval():
    # Owner: all free models in parallel within their limits; best model for the job.
    refs = [{"model": m} for m in ("nim-a", "nim-b", "groq-gpt-oss-20b", "or-x", "samba-y", "nim-weak")]
    q = {"nim-a": 1.0, "nim-b": 1.0, "groq-gpt-oss-20b": 1.0, "or-x": 1.0, "samba-y": 1.0, "nim-weak": 0.3}
    w = {r["model"]: r.get("weight", 0) for r in fp.weigh(refs, q.get)}
    assert w["nim-a"] == w["nim-b"] == 20      # NIM's 40 rpm account bucket split over its two good models
    assert w["groq-gpt-oss-20b"] == 30         # Groq limits are per model
    assert w["or-x"] == 20
    assert w["samba-y"] == 0 and w["nim-weak"] == 0   # no published limit / below the eval floor: fallback only
    assert [r["model"] for r in fp.weigh(refs, q.get)] == [r["model"] for r in refs]  # fallback order kept


def test_free_entrypoint_routes_task_keywords_to_family_decisions():
    import yaml, tempfile
    out = Path(tempfile.mkdtemp()) / "c.yaml"
    fp.render(out=out)
    c = yaml.safe_load(out.read_text())
    names = {d["name"]: d for d in c["routing"]["decisions"]}
    assert {"auto-classify", "auto-extract", "auto-format", "auto-verify", "free-quality"} <= set(names)
    assert names["auto-verify"]["rules"]["conditions"] == [{"type": "keyword", "name": "task_verify"}]
    assert all(d["algorithm"]["type"] == "random" for d in names.values())
    assert sum(1 for r in names["free-quality"]["modelRefs"] if r.get("weight")) >= 4  # spread, not one model


def test_audit_flags_failures_and_one_model_doing_everything():
    def rec(first, status=""):
        return json.dumps({"msg": "fallback_execution_complete", "execution_record": json.dumps(
            {"decision_name": "free-quality", "visited_models": [first], "final_status": status})})
    assert fp.audit([rec("nim-a")] * 30)["ok"] is False                      # R1: one bucket took it all
    assert fp.audit([rec("nim-a"), rec("groq-gpt-oss-20b", "candidates_exhausted")])["ok"] is False  # R2
    assert fp.audit([rec("nim-a")] * 12 + [rec("groq-gpt-oss-20b")] * 10 + [rec("or-x", "succeeded")] * 5)["ok"]


def test_every_free_model_and_backend_gets_its_own_entrypoint():
    # Owner 11-15: specific endpoints for each free model, so a caller can pin lanes to models.
    import yaml, tempfile
    out = Path(tempfile.mkdtemp()) / "c.yaml"
    fp.render(out=out)
    c = yaml.safe_load(out.read_text())
    eps = {n: e["recipe"] for e in c["entrypoints"] for n in e["model_names"]}
    recipes = {r["name"]: r["routing"]["decisions"][0] for r in c["recipes"]}
    assert {"vllm-sr/free", "vllm-sr/auto", "vllm-sr/turbo", "vllm-sr/free-verify"} <= set(eps)  # clients keep working
    backends = [m["name"] for m in c["providers"]["models"]]
    for b in backends:                                   # pinned: exactly this backend, never another one
        d = recipes[eps[f"vllm-sr/b/{b}"]]
        assert [r["model"] for r in d["modelRefs"]] == [b] and d["fallback"]["enabled"] is False
    for b in backends:                                   # per model: every host of that model, and only those
        d = recipes[eps[f"vllm-sr/m/{fp.model_key(b)}"]]
        assert b in [r["model"] for r in d["modelRefs"]]
        assert {fp.model_key(r["model"]) for r in d["modelRefs"]} == {fp.model_key(b)}
    assert len({fp.model_key(b) for b in backends}) < len(backends)  # some model has several hosts to fall over to


def test_every_backend_has_a_rate_limit_row():
    # A backend with no limits row gets no rpm cap, so the lane planner silently gives it zero lanes.
    import yaml
    c = yaml.safe_load((fp.HERE / "config.yaml").read_text())
    missing = [m["name"] for m in c["providers"]["models"] if not fp.limits(fp.bucket(m["name"])).get("req", {}).get(fp.MIN)]
    assert not missing, f"add these to limits.json: {missing}"


def test_daily_cap_bounds_rpm_headroom():
    # 18 of 20 requests used today: 20 rpm free this minute is really 2 requests until reset.
    cfg = {"providers": {"models": [{"name": "samba-glm-5.3"}]}, "entrypoints": []}
    st = {"usage": {"samba-glm-5.3": {"req_60s": "0/20", "req_86400s": "18/20"}}}
    h = fp.inventory(cfg, st)["models"][0]["hosts"][0]
    assert h["headroom_rpm"] == 2 and h["caps"]["rpd"] == 20 and h["left"]["rpd"] == 2


def _inv():
    m = lambda key, q, hosts: {"model": key, "endpoint": f"vllm-sr/m/{key}", "quality": {"quality": q, "reason": q},
                               "hosts": hosts, "headroom_rpm": sum(h["headroom_rpm"] or 0 for h in hosts)}
    h = lambda b, rpm, blocked=False: {"backend": b, "endpoint": f"vllm-sr/b/{b}", "bucket": fp.bucket(b),
                                       "headroom_rpm": 0 if blocked else rpm, "blocked": blocked}
    return {"models": [m("best", 0.9, [h("nim-best", 5), h("or-best", 20)]), m("good", 0.8, [h("groq-good", 30)]),
                       m("capped", 1.0, [h("groq-capped", 30, blocked=True)]), m("meh", 0.75, [h("nim-meh", 5)])]}


def test_lane_plan_spreads_lanes_over_best_models_with_headroom():
    p = fp.plan_lanes(_inv(), lanes=6, models=2)
    assert len(p["lanes"]) == 6
    assert {l["endpoint"] for l in p["lanes"]} == {"vllm-sr/m/best", "vllm-sr/m/good"}  # capped model never planned
    per = {e: sum(1 for l in p["lanes"] if l["endpoint"] == e) for e in ("vllm-sr/m/best", "vllm-sr/m/good")}
    assert per["vllm-sr/m/good"] >= per["vllm-sr/m/best"] - 1  # lanes follow headroom (30 vs 25 rpm)
    for l in p["lanes"]:                                         # lane budgets never exceed the model's headroom
        assert l["rpm_budget"] <= {"vllm-sr/m/best": 25, "vllm-sr/m/good": 30}[l["endpoint"]] / per[l["endpoint"]] + 1e-9


def test_pinned_lane_plan_uses_single_backends_and_warns_when_short():
    p = fp.plan_lanes(_inv(), lanes=4, models=1, pinned=True)
    assert {l["endpoint"] for l in p["lanes"]} <= {"vllm-sr/b/nim-best", "vllm-sr/b/or-best"}
    assert all(l["model"] == "best" for l in p["lanes"])
    short = fp.plan_lanes(_inv(), lanes=200, models=9)
    assert short["warnings"] and len(short["lanes"]) == 200


def test_cascade_moves_on_from_any_backend_error():
    # One backend's 403 (Cloudflare 1010), 401/402 (key/balance), 404 (model gone) or 400 (context too long)
    # says nothing about the next backend, so sr:free must try it instead of failing the request.
    codes = set(fp._cascade("x", [{"model": "a"}, {"model": "b"}])["fallback"]["retryable_status_codes"])
    assert {400, 401, 402, 403, 404, 408, 413, 422, 429, 500, 502, 503, 504} <= codes


def test_payment_required_blocks_backend_as_not_free():
    # Owner: never call a backend free unless it is proven free; a 402 must not keep getting lanes.
    rec = {"attempts": [{"attempt_id": "x1", "model": "samba-glm-5.3", "status_code": 402, "error": "Payment Required"}]}
    line = json.dumps({"msg": "fallback_execution_complete", "ts": "2026-11-16T00:33:13.205",
                       "request_id": "r1", "execution_record": json.dumps(rec)})
    st = {"events": [], "blocked": {}}
    fp.ingest(st, [line], now=1_800_000_000)
    blk, (ts, b, _) = st["blocked"]["samba-glm-5.3"], st["events"][-1]
    assert b == "samba-glm-5.3" and "402" in blk["why"]
    assert blk["until"] - ts >= fp.EXCLUDE_S  # long enough that free-pace pulls it from the cascade


def test_undecodable_402_still_blocks_backend():
    # Live 11-15: SambaNova's 402 body failed the router's decoder, so its execution_record had attempts: null and
    # the status only appeared on a separate neutral_transport_error_decode_failed line (logged after it).
    rec = {"request_id": "r2", "decision_name": "m-glm-5.3", "initial_model": "samba-glm-5.3", "attempts": None}
    lines = [json.dumps({"msg": "fallback_execution_complete", "ts": "2026-11-16T00:33:13.205", "request_id": "r2",
                         "execution_record": json.dumps(rec)}),
             json.dumps({"msg": "neutral_transport_error_decode_failed", "ts": "2026-11-16T00:33:13.206",
                         "request_id": "r2", "status": 402})]
    st = {"events": [], "blocked": {}}
    fp.ingest(st, lines, now=1_800_000_000)
    assert "402" in st["blocked"]["samba-glm-5.3"]["why"]


def test_undecodable_402_on_pinned_endpoint_blocks_backend():
    # b/ endpoints have fallback off: the router writes only routing_decision + decode_failed, no execution_record.
    lines = [json.dumps({"msg": "routing_decision", "request_id": "r3", "selected_model": "samba-glm-5.3"}),
             json.dumps({"msg": "neutral_transport_error_decode_failed", "request_id": "r3", "status": 402})]
    st = {"events": [], "blocked": {}}
    fp.ingest(st, lines, now=1_800_000_000)
    assert "402" in st["blocked"]["samba-glm-5.3"]["why"]
    fp.ingest(st, lines, now=1_800_000_060)  # re-read log window: counted once
    assert sum(1 for e in st["events"] if e[1] == "samba-glm-5.3") == 1


def _inventory(tmp_path, models):
    d = tmp_path / "inv"
    d.mkdir()
    (d / "2026-11-15.json").write_text(json.dumps({"providers": {"openrouter": {"models": models}}}))
    return d


def test_new_free_models_join_the_router_without_a_code_edit(tmp_path, monkeypatch):
    # Owner 11-15: autoscale across ALL free models; a model that turns free must not wait for a hand edit.
    monkeypatch.setattr(fp, "INVENTORY", _inventory(tmp_path, [
        {"id": "acme/new-1:free", "class": "free", "context_length": 65536},
        {"id": "acme/paid-1", "class": "paid"},
        {"id": "google/gemma-4-31b-it:free", "class": "free"},          # no-Google owner rule
        {"id": "openai/gpt-5.5:free", "class": "free"},                 # closed models have their own routes
        {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "class": "free"},  # already a candidate
    ]))
    fp.CATALOGS.write_text(json.dumps({"nim": ["acme/nim-chat-2", "nvidia/nemotron-3.5-embed-8b", "openai/gpt-oss-20b"]}))
    assert [c[0] for c in fp.discovered()] == ["or-new-1", "kilo-new-1", "nim-nim-chat-2"]  # embed and known ids skipped
    assert {c[3] for c in fp.discovered()} == {"acme/new-1:free", "acme/nim-chat-2"}
    seed = json.loads((fp.HERE / "pool.json").read_text())
    seed["rows"].append({"name": "or-new-1", "tool_call": True, "tool_s": 2.0, "max_ok_tokens": 30000})
    fp.POOL.write_text(json.dumps(seed))
    import yaml
    out = tmp_path / "c.yaml"
    fp.render(out=out)
    c = yaml.safe_load(out.read_text())
    assert "or-new-1" in [m["name"] for m in c["providers"]["models"]]
    assert "vllm-sr/m/new-1" in {n for e in c["entrypoints"] for n in e["model_names"]}


def test_reprobe_skips_blocked_backends_and_reuses_known_context(tmp_path, monkeypatch):
    # Never poll an exhausted provider (owner rule); a known context window is not re-measured with 100K prompts.
    now = 1_800_000_000.0
    fp.POOL.write_text(json.dumps({"rows": [{"name": "groq-gpt-oss-20b", "tool_call": True, "max_ok_tokens": 30000},
                                            {"name": "samba-glm-5.3", "tool_status": 402}]}))
    calls = []
    monkeypatch.setattr(fp, "probe", lambda c, sizes=True: calls.append((c[0], sizes)) or {"name": c[0], "tool_call": True, "tool_s": 1.0})
    rows = fp.reprobe({"blocked": {"samba-glm-5.3": {"until": now + 3600, "why": "402"}}}, now,
                      cands=[c for c in fp.CANDIDATES if c[0] in ("groq-gpt-oss-20b", "samba-glm-5.3", "nim-kimi-k2.6")])
    assert ("groq-gpt-oss-20b", False) in calls and ("nim-kimi-k2.6", True) in calls
    assert "samba-glm-5.3" not in [n for n, _ in calls]
    by = {r["name"]: r for r in rows}
    assert by["samba-glm-5.3"]["tool_status"] == 402 and by["groq-gpt-oss-20b"]["max_ok_tokens"] == 30000
    monkeypatch.setattr(fp, "probe", lambda c, sizes=True: {"name": c[0], "tool_status": "no-key"})
    rows = fp.reprobe({}, now, cands=[c for c in fp.CANDIDATES if c[0] == "groq-gpt-oss-20b"])
    assert fp.alive(rows[0])  # a prober without the key never drops a working backend


def test_pace_refuses_a_reprobe_that_kills_half_the_pool(monkeypatch):
    # 11-15 live: a prober without keys marked 36 of 43 backends dead and shrank the router to 7. Never again.
    st_file = fp.POOL.parent / "free-pace.json"
    monkeypatch.setattr(fp, "OUT", st_file)
    monkeypatch.setattr(fp, "STATE", fp.POOL.parent)
    monkeypatch.setattr(fp, "pod_log", lambda s: [])
    monkeypatch.setattr(fp, "reprobe", lambda st, now: [{"name": "groq-gpt-oss-20b", "tool_call": True, "tool_s": 1.0}])
    deploys = []
    monkeypatch.setattr(fp, "render", lambda **k: deploys.append(k))
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: deploys.append(a))
    monkeypatch.setattr(sys, "argv", ["free_pool.py", "pace"])
    st_file.write_text(json.dumps({"pool": sorted(n for n, r in fp.pool_rows().items() if fp.alive(r))}))
    fp.pace()
    assert not deploys and not fp.POOL.exists()


def test_scout_queues_only_unseen_providers():
    queue = [{"domain": "openrouter.ai", "state": "done"}]
    found = [{"domain": "openrouter.ai", "status": "open_catalog"},
             {"domain": "newfree.ai", "status": "open_catalog", "base_url": "https://api.newfree.ai/v1", "lists": 3,
              "free_ids": ["a/b:free", "google/gemma-x:free"]},
             {"domain": "keyed.ai", "status": "needs_key", "lists": 2},
             {"domain": "dead.ai", "status": "unreachable"},
             {"domain": "gemini.google.com", "status": "needs_key"},
             {"domain": "nvidia.com", "status": "open_catalog"}]                   # already on the router
    new = fp.merge_scan(queue, found, "20261115")
    assert [(r["domain"], r["free_models"], r["keyless"], r["state"]) for r in new] == [
        ("newfree.ai", 1, True, "todo"), ("keyed.ai", 0, False, "todo"), ("nvidia.com", 0, True, "routed")]


def test_pace_retries_a_failed_deploy_next_minute(monkeypatch):
    # 11-15 live: helm timed out after a re-probe grew the pool, and nothing retried for 6 h.
    monkeypatch.setattr(fp, "OUT", fp.POOL.parent / "free-pace.json")
    monkeypatch.setattr(fp, "STATE", fp.POOL.parent)
    monkeypatch.setattr(fp, "pod_log", lambda s: [])
    monkeypatch.setattr(fp, "render", lambda **k: None)
    rcs = [1, 0]

    class R:
        def __init__(self):
            self.returncode, self.stdout, self.stderr = rcs.pop(0), "", ""
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(sys, "argv", ["free_pool.py", "pace"])
    fp.POOL.write_text(json.dumps({"rows": [{"name": "groq-gpt-oss-20b", "tool_call": True, "tool_s": 1.0}]}))
    fp.OUT.write_text(json.dumps({"probed_at": 1e12, "pool": []}))
    fp.pace()
    assert json.loads(fp.OUT.read_text())["pool"] == []           # failed: not recorded as deployed
    fp.pace()
    assert json.loads(fp.OUT.read_text())["pool"] == ["groq-gpt-oss-20b"] and not rcs


def test_pace_redeploys_when_a_reprobe_only_resizes_a_card(monkeypatch):
    # 11-16: a 429 had carded step-5-preview at 6K; the fixed re-probe re-sized it, but the alive set was
    # unchanged, so pace never redeployed and the router kept answering 422 context_length_exceeded.
    monkeypatch.setattr(fp, "OUT", fp.POOL.parent / "free-pace.json")
    monkeypatch.setattr(fp, "STATE", fp.POOL.parent)
    monkeypatch.setattr(fp, "pod_log", lambda s: [])
    monkeypatch.setattr(fp, "render", lambda **k: None)
    deploys = []

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: deploys.append(a) or R())
    monkeypatch.setattr(sys, "argv", ["free_pool.py", "pace"])
    row = {"name": "groq-gpt-oss-20b", "tool_call": True, "tool_s": 1.0, "max_ok_tokens": 6000}
    fp.POOL.write_text(json.dumps({"rows": [row]}))
    fp.OUT.write_text(json.dumps({"probed_at": 1e12, "pool": ["groq-gpt-oss-20b"], "excluded": []}))
    fp.pace()
    assert not deploys                                              # nothing changed: no deploy
    fp.POOL.write_text(json.dumps({"rows": [dict(row, max_ok_tokens=131072)]}))
    fp.pace()
    assert len(deploys) == 1 and json.loads(fp.OUT.read_text())["cards"] == {"groq-gpt-oss-20b": 131072}
    fp.pace()
    assert len(deploys) == 1                                        # deployed state recorded: no repeat


def test_no_decision_exceeds_the_routers_32_candidate_limit(tmp_path):
    # 11-15 live: autoscale grew the pool to 38 and the router crash-looped on "exceeding ... limit of 32".
    import yaml
    seed = json.loads((fp.HERE / "pool.json").read_text())
    rows = {r["name"]: r for r in seed["rows"]}
    for c in fp.CANDIDATES:
        rows[c[0]] = {"name": c[0], "tool_call": True, "tool_s": 1.0, "max_ok_tokens": 30000}
    fp.POOL.write_text(json.dumps({"rows": list(rows.values())}))
    out = tmp_path / "c.yaml"
    fp.render(out=out)
    c = yaml.safe_load(out.read_text())
    decisions = c["routing"]["decisions"] + [d for r in c["recipes"] for d in r["routing"]["decisions"]]
    assert len(c["providers"]["models"]) > fp.MAX_REFS
    assert max(len(d["modelRefs"]) for d in decisions) <= fp.MAX_REFS
    assert all(d["fallback"]["max_attempts"] <= fp.MAX_REFS for d in decisions if d["fallback"]["enabled"])
