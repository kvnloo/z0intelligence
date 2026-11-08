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
