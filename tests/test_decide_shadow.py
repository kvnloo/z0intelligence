import json
import sqlite3

from z0int import decide_shadow as S
from z0int.decide import load_roles


def sig(**over):
    lane = lambda b, p, a: {"band": b, "pace_ratio": p, "max_workflow_agents": a, "effort": "medium"}
    s = {"roles": load_roles(), "saturation": {}, "free_inventory": {"disabled": [], "paid": []},
         "burn": {"lanes": {"claude": lane("under-burn", .4, 12), "claude2": lane("on-pace", .9, 6),
                            "claude3": lane("hard-throttle", 58, 0), "codex": lane("on-pace", .93, 6)}}}
    s.update(over)
    return s


def test_privacy_classifier():
    assert S.classify_privacy("use api_key=sk-abcdefghijklmnopqrstuvwx now") == "credentials"
    assert S.classify_privacy("No secrets, tokens or credentials in output") == "public"
    assert S.classify_privacy("meet at 0.12345, -0.67890 tonight") == "location"
    assert S.classify_privacy("summarize Kevin's state.db sessions") == "public"
    assert S.personal_hint("Input: eval/task-source.json (domain -> Kevin's own messages)")
    assert not S.personal_hint("Use when working on owner hosts and Kevin's HUD style")
    assert S.classify_privacy("write a haiku about rust") == "public"


def _statedb(path):
    c = sqlite3.connect(path)
    c.executescript("""create table sessions(id text, model text, billing_provider text, parent_session_id text,
        started_at real, ended_at real, input_tokens int, output_tokens int, cache_read_tokens int, cache_write_tokens int);
        create table messages(id integer primary key, session_id text, role text, content text);""")
    c.execute("insert into sessions values('kid','claude-opus-5-5','anthropic','p',100,150,10,20,30,0)")
    c.execute("insert into sessions values('kid2','vllm-sr/paid','custom:vllm-sr','p',110,160,5,5,0,0)")
    c.execute("insert into sessions values('root','claude-opus-5-5','anthropic',null,100,150,1,1,1,1)")
    c.execute("insert into messages(session_id,role,content) values('kid','user','write a haiku')")
    c.commit(); c.close()


def test_hermes_tap_reads_only_delegated_children(tmp_path):
    (tmp_path / "clean").mkdir(); _statedb(tmp_path / "clean" / "state.db")
    ev = S.hermes_events(tmp_path, 0, 1000)
    assert {e["id"] for e in ev} == {"clean:kid", "clean:kid2"}
    kid = next(e for e in ev if e["id"] == "clean:kid")
    assert kid["actual"]["provider"] == "anthropic" and kid["req"] == {"role": "default", "privacy": "public", "executors": ["hermes"]}
    assert kid["tokens"]["output"] == 20


def test_fx_tap(tmp_path):
    d = tmp_path / "v2" / "abc"; d.mkdir(parents=True)
    rows = [{"kind": "session_created", "ts": 5000},
            {"kind": "set", "key": "prefs", "value": {"provider": "codex", "model": "gpt-6.1-terra", "effort": "medium"}},
            {"kind": "item", "type": "user", "data": {"text": "write a haiku"}},
            {"kind": "item", "type": "turn_end", "data": {"turn_summary": {"token_progress": {"input_tokens": 7, "output_tokens": 9}}}}]
    (d / "log.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    ev = S.fx_events(tmp_path, 0, 10)
    assert len(ev) == 1 and ev[0]["actual"] == {"harness": "fx", "provider": "openai-codex", "model": "gpt-6.1-terra", "effort": "medium"}
    assert ev[0]["tokens"] == {"input": 7, "output": 9, "cache_read": 0, "cache_write": 0}


def _sr(rid, ts, model, code):
    return [json.dumps({"msg": "routing_decision", "ts": ts, "request_id": rid, "selected_model": "groq-" + model}),
            json.dumps({"msg": "access", "ts": ts, "request_id": rid, "response_code": code})]


def test_vllm_sr_tap_marks_saturation_from_prior_429():
    lines = (_sr("a", "2026-11-05T22:40:00.000", "gpt-oss-20b", 429) + _sr("b", "2026-11-05T22:40:10.000", "gpt-oss-120b", 200)
             + _sr("c", "2026-11-05T22:40:20.000", "qwen3.8-27b", 429) + _sr("d", "2026-11-05T22:40:30.000", "gpt-oss-120b", 429)
             + _sr("e", "2026-11-05T22:40:40.000", "gpt-oss-20b", 429))
    ev = S.vllm_sr_events(lines, 0, 2e9)
    assert [e["actual"]["model"] for e in ev] == ["gpt-oss-20b", "gpt-oss-120b", "qwen3.8-27b", "gpt-oss-120b", "gpt-oss-20b"]
    assert ev[0]["saturation"] == {} and ev[1]["saturation"]["groq/gpt-oss-20b"]["available"] is False
    rows = S.shadow_rows(ev, sig())
    assert rows[0]["agree"] is True            # 20b is role-table first and was not yet saturated
    assert rows[1]["decided"]["model"] == "qwen3.8-27b" and rows[1]["agree"] is False
    assert rows[4]["decided"]["kind"] == "HOLD"   # all three Groq models inside the 60 s cooldown
    assert rows[4]["decided_failover"]["provider"] == "cerebras"
    cd = S.report(rows, lambda *a: 0.0)["vllm_sr_groq_cooldown"]
    assert cd["sent_during_groq_cooldown"] == 1 and cd["cerebras_failover_recovers_failed_calls"] == 1


def test_shadow_rows_and_report():
    ev = [{"source": "fx", "id": "x", "ts": 1, "req": {"role": "task", "privacy": "public", "executors": ["fx"]},
           "actual": {"harness": "fx", "provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "medium"},
           "tokens": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}},
          {"source": "hermes-delegation", "id": "y", "ts": 1, "req": {"role": "default", "privacy": "public", "executors": ["hermes"]},
           "actual": {"harness": "hermes", "provider": "anthropic", "model": "claude-opus-5-5", "effort": None},
           "tokens": {"input": 10, "output": 0, "cache_read": 0, "cache_write": 0}}]
    rows = S.shadow_rows(ev, sig())
    assert [r["agree"] for r in rows] == [False, True]
    price = lambda provider, model, t: {"gpt-6.1-terra": 1.0, "gpt-6.1-sol": 2.0}.get(model, 0.0) * t["input"] / 1e6
    rep = S.report(rows + rows[:1], price)       # duplicate row is deduped
    assert rep["n"] == 2 and rep["agree_pct"] == 50.0
    assert rep["by_source"]["fx"]["agree_pct"] == 0.0
    top = rep["top_disagreements"][0]
    assert top["actual"] == "openai-codex/gpt-6.1-sol" and top["decided"] == "openai-codex/gpt-6.1-terra" and top["n"] == 1
    assert rep["cost_delta_usd"] == -1.0
    assert rep["quota_delta_tokens"] == {"codex": 0}


def test_one_bad_event_does_not_drop_the_batch(monkeypatch):
    ev = [{"source": "fx", "id": i, "ts": 1, "req": {"role": "task", "privacy": "public", "executors": ["fx"]},
           "actual": {"provider": "openai-codex", "model": "gpt-6.1-terra"}, "tokens": {}} for i in ("ok", "bad")]
    real = S.decide
    monkeypatch.setattr(S, "decide", lambda req, s: (_ for _ in ()).throw(TypeError("boom")) if req.get("boom") else real(req, s))
    ev[1]["req"]["boom"] = True
    rows = S.shadow_rows(ev, sig())
    assert [r["id"] for r in rows] == ["ok", "bad"]
    assert rows[1]["decided"]["kind"] == "ERROR" and "boom" in rows[1]["decided"]["reason"] and rows[1]["agree"] is False


def test_coverage_reports_hole():
    rows = [{"source": "vllm-sr", "ts": t} for t in (0, 600, 1200, 1200 + 83 * 60, 1200 + 83 * 60 + 600)]
    c = S.coverage(rows)
    assert len(c["gaps"]) == 1 and c["gaps"][0]["minutes"] == 83 and c["pct"] == 26.5


def test_signal_digest_logs_unknown_pace():
    s = sig()
    s["burn"]["lanes"]["claude"]["pace_ratio"] = None
    d = S.signal_digest(s, 5)
    assert d["pace_unknown"] == ["claude"] and d["bands"]["claude"] == s["burn"]["lanes"]["claude"]["band"]


def test_coverage_includes_silent_leading_and_trailing_window_edges():
    # Two observed events 10 minutes apart do not cover a two-hour request window.
    rows = [{"source": "vllm-sr", "ts": 3600}, {"source": "vllm-sr", "ts": 4200}]
    c = S.coverage(rows, start=0, end=7200)
    assert c["pct"] == 8.3
    assert [gap["minutes"] for gap in c["gaps"]] == [60, 50]
    empty = S.coverage([], start=0, end=7200)
    assert empty["pct"] == 0.0
    assert [gap["minutes"] for gap in empty["gaps"]] == [120]


def test_report_cli_preserves_full_24_hour_window_when_no_events(tmp_path, monkeypatch, capsys):
    (tmp_path / "rows.jsonl").write_text("")
    monkeypatch.setattr(S, "OUT", tmp_path)
    monkeypatch.setattr(S.time, "time", lambda: 1_800_000_000)
    S.main(["report", "--hours", "24"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["n"] == 0
    assert rep["window_utc"]["start"].endswith("+00:00")
    assert rep["window_utc"]["end"].endswith("+00:00")
    assert rep["vllm_sr_coverage"]["pct"] == 0.0
    assert [gap["minutes"] for gap in rep["vllm_sr_coverage"]["gaps"]] == [1440]
