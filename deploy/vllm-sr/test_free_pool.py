"""python3 -m pytest deploy/vllm-sr/test_free_pool.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import free_pool as fp  # noqa: E402

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


def test_cascade_moves_on_from_any_backend_error():
    # One backend's 403 (Cloudflare 1010), 401/402 (key/balance), 404 (model gone) or 400 (context too long)
    # says nothing about the next backend, so sr:free must try it instead of failing the request.
    codes = set(fp._cascade("x", [{"model": "a"}, {"model": "b"}])["fallback"]["retryable_status_codes"])
    assert {400, 401, 402, 403, 404, 408, 413, 422, 429, 500, 502, 503, 504} <= codes


def test_capability_joins_hermes_and_ledger(tmp_path):
    led = tmp_path / "cap.jsonl"
    led.write_text("\n".join([
        json.dumps({"model": "glm-5.3-flash", "source": "bench-round1", "score0to1": 0.5, "n": 20, "date": "2026-11-12"}),
        json.dumps({"model": "newmodel", "source": "gepa-openusd", "score0to1": 0.8, "n": 2, "date": "2026-11-13"}),
        "not json", json.dumps({"model": "bad", "source": "x", "score0to1": 7, "n": 3, "date": "d"})]))
    cap = fp.capability(led)
    assert cap["glm-5.3-flash"]["source"] == "bench-round1"  # more trials beats the index
    assert cap["deepseek-v4.1-flash"]["source"] == "hermes-index"
    assert cap["deepseek-v4.1-flash"]["date"] == fp.HERMES_DATE
    assert "bad" not in cap and cap["newmodel"]["n"] == 2
    assert fp.capability(tmp_path / "missing.jsonl").keys() == fp.HERMES_INDEX.keys()


def test_cap_quality_is_real_score_times_confidence_not_a_fill_in():
    cap = fp.capability(Path("/nonexistent"))
    assert abs(fp.cap_quality("deepseek-v4.1-flash", cap) - 0.3691 * 4 / (4 + fp.CONF_K)) < 1e-9
    assert fp.cap_quality("deepseek-v4.1-flash", cap) > fp.cap_quality("glm-5.3-flash", cap) > \
        fp.cap_quality("nemotron-3.5-super-vl", cap)
    assert fp.cap_quality("unknown", cap) == 0
    cap["m"] = {"score0to1": 0.5, "conf": 0.9}
    cap["n"] = {"score0to1": 0.5, "conf": 0.3}
    assert fp.cap_quality("m", cap) > fp.cap_quality("n", cap)  # same score, more evidence wins


def test_rank_is_best_first_with_source_and_keeps_all_backends():
    r = fp.rank(fp.capability(Path("/nonexistent")))
    assert len(r) == len(fp.CANDIDATES)
    scored = [o for o in r if o["score"] is not None]
    assert scored and r[:len(scored)] == scored
    assert [o["score"] for o in scored] == sorted((o["score"] for o in scored), reverse=True)
    assert all(o["source"] == "hermes-index" and o["date"] for o in scored)
    assert all(o["source"] is None for o in r[len(scored):])


def test_weigh_gives_scored_models_weight_below_the_eval_floor(monkeypatch):
    refs = [{"model": "nim-a"}]
    monkeypatch.setattr(fp, "limits", lambda b: {"req": {fp.MIN: 10}})
    assert "weight" not in fp.weigh(refs, lambda m: 0.25)[0]
    assert fp.weigh(refs, lambda m: 0.25, lambda m: True)[0]["weight"] > 0


def test_rank_cli_json():
    import subprocess
    out = subprocess.run([sys.executable, str(Path(fp.__file__)), "rank", "--json"],
                         capture_output=True, text=True, check=True)
    rows = json.loads(out.stdout)
    assert rows[0]["source"] and set(rows[0]) == {"backend", "model", "score", "source", "n", "date"}
