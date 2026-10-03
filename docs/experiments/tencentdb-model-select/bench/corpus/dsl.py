"""Tiny DSL for the frozen synthetic TencentDB benchmark corpus.

Everything here is synthetic. The persona ("Rowan Hale", "Ferrite Labs", projects
Lantern / Pebble / Atlas / ferrite-sync, host "forge", people Priya / Jonah / Dana)
is fictional. All "secrets" are random strings from a seeded RNG; none is a real
credential. Filler generators (logs, traces, code, configs) are deterministic and use
a neutral vocabulary so they never contain gold anchors.

Gold anchors are Python regexes applied (re.search) to the *normalised* memory text
(see score/normalize.py: lowercase, unicode quotes/dashes folded, whitespace collapsed).
A gold item matches a memory iff ALL its anchor regexes match. A trap/stale item fires
iff all its anchors match and none of its `unless` regexes match.
"""
from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone

# ----------------------------------------------------------------------------
# conversation structure helpers
# ----------------------------------------------------------------------------

ROUND_GAP_S = 150      # seconds between consecutive user turns
REPLY_DELAY_S = 40     # assistant reply lag


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def session(start: str, rounds: list[tuple[str, str]], gap_s: int = ROUND_GAP_S) -> dict:
    t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
    msgs = []
    for i, (u, a) in enumerate(rounds):
        tu = t0 + timedelta(seconds=i * gap_s)
        ta = tu + timedelta(seconds=REPLY_DELAY_S)
        msgs.append({"round": i, "user": {"content": u.strip(), "timestamp": iso(tu)},
                     "assistant": {"content": a.strip(), "timestamp": iso(ta)}})
    return {"start": start, "rounds": msgs}


def G(gid, statement, anchors, type="persona", session=1, **kw):
    """Required gold memory (counts for recall)."""
    return {"gid": gid, "statement": statement, "anchors": anchors, "type": type,
            "session": session, **kw}


def OPT(statement, anchors):
    """Acceptable extra memory: a memory matching it is a TP for precision; not in recall."""
    return {"statement": statement, "anchors": anchors}


def TRAP(tid, statement, anchors, unless=(), why=""):
    """A memory matching this (and no `unless`) is a hallucinated/misattributed memory."""
    return {"tid": tid, "statement": statement, "anchors": anchors, "unless": list(unless), "why": why}


def SUP(sid, current_gid, stale_anchors, unless=(), scope="within"):
    """Supersession: final store must hold current_gid and no record matching stale anchors
    unless it also matches current_gid or an `unless` regex (e.g. 'previously', 'no longer')."""
    return {"sid": sid, "current": current_gid, "stale_anchors": stale_anchors,
            "unless": list(unless), "scope": scope}


def DUP(gid, scope="within"):
    """Dedup: the fact is stated more than once in different L1 windows; the final store
    should contain exactly one record matching gid (scope within = same session)."""
    return {"gid": gid, "scope": scope}


def L2F(statement, anchors):
    return {"statement": statement, "anchors": anchors}


def conv(cid, cats, title, sessions, gold=None, opt=(), traps=(), sups=(), dups=(),
         secrets=(), l2=(), l3=(), notes=""):
    return {"id": cid, "categories": cats, "title": title, "sessions": sessions,
            "gold": list(gold or []), "optional": list(opt), "traps": list(traps),
            "supersessions": list(sups), "dedup": list(dups), "secrets": list(secrets),
            "l2_facts": list(l2), "l3_facts": list(l3), "notes": notes}


# Negation / "this is old" markers usable in `unless` lists.
NEG = r"\b(not|no longer|never|instead of|rather than|declin\w*|reject\w*|decided against|won'?t|doesn'?t|don'?t|avoid\w*|ruled out|dropp\w*|without|previously|formerly|used to|switched (away )?from|moved (away )?from|replac\w*|abandon\w*|no redis)\b"
# Chinese negation / attribution / "outdated" markers. Used ONLY for L2/L3 (scene/persona) text, which the
# gateway's Chinese L2/L3 prompts make models write in Chinese (PREREG amendment A1).
NEG_ZH = r"不|没|无|避免|禁止|拒绝|而非|放弃|取消|并非|别|勿|反对|未|不再|曾|之前|原先|原来|改为|转向|替代|取代|假设|如果|朋友|伙伴|合伙人|伴侣"
OLD = r"\b(previously|formerly|used to|originally|earlier|was (planned|set|scheduled)|moved from|switched from|changed from|instead of|no longer|replac\w*|from .{0,40} to )\b"

# ----------------------------------------------------------------------------
# deterministic filler generators (fact-free, neutral vocabulary)
# ----------------------------------------------------------------------------

_MODULES = ["worker", "scheduler", "ingest", "router", "cache_layer", "session", "planner", "batcher",
            "indexer", "dispatcher", "watcher", "compactor", "loader", "exporter", "gc"]
_EVENTS = ["poll", "tick", "flush", "commit", "retry", "spawn", "drain", "checkpoint", "rotate", "merge",
           "evict", "heartbeat", "lease.renew", "lease.expire", "snapshot"]
_LEVELS = ["DEBUG", "DEBUG", "INFO", "INFO", "INFO", "WARN", "ERROR"]
_WORDS = ["alpha", "bravo", "cobalt", "delta", "ember", "falcon", "garnet", "harbor", "ivory", "jasper",
          "kestrel", "lumen", "marble", "nimbus", "onyx", "prism", "quartz", "raven", "sable", "tundra",
          "umber", "vertex", "willow", "xenon", "yarrow", "zephyr"]
_B62 = string.ascii_letters + string.digits


def rand_token(seed: int, n: int, alphabet: str = _B62) -> str:
    r = random.Random(seed)
    return "".join(r.choice(alphabet) for _ in range(n))


def LOG(seed: int, n: int, start: str = "2026-09-14T15:01:00") -> str:
    r = random.Random(seed)
    t = datetime.fromisoformat(start)
    out = []
    for i in range(n):
        t += timedelta(milliseconds=r.randint(3, 900))
        lvl = r.choice(_LEVELS)
        mod = r.choice(_MODULES)
        ev = r.choice(_EVENTS)
        kv = " ".join(f"{r.choice(['batch','shard','attempt','queue_depth','latency_ms','bytes','items','slot','epoch'])}={r.randint(0, 4096)}"
                      for _ in range(r.randint(1, 4)))
        msg = f"{t.isoformat(timespec='milliseconds')}Z {lvl:<5} [{mod}-{r.randint(0, 7)}] {mod}.{ev} {kv}"
        if lvl == "ERROR":
            msg += f" err=\"{r.choice(['deadline exceeded','connection reset by peer','unexpected EOF','lock not held','checksum mismatch'])}\""
        out.append(msg)
    return "\n".join(out)


def TRACE(seed: int, depth: int = 12, lang: str = "ts") -> str:
    r = random.Random(seed)
    if lang == "py":
        lines = ["Traceback (most recent call last):"]
        for _ in range(depth):
            f = f"src/{r.choice(_WORDS)}/{r.choice(_MODULES)}.py"
            lines.append(f'  File "{f}", line {r.randint(10, 900)}, in {r.choice(_EVENTS).replace(".", "_")}_{r.choice(_WORDS)}')
            lines.append(f"    {r.choice(_WORDS)} = self.{r.choice(_MODULES)}.{r.choice(_EVENTS).replace('.', '_')}({r.choice(_WORDS)}, timeout={r.randint(1, 60)})")
        lines.append(f"{r.choice(['TimeoutError','KeyError','ValueError','RuntimeError'])}: {r.choice(_WORDS)} {r.choice(['not ready','missing','out of range','closed'])}")
        return "\n".join(lines)
    lines = [f"Error: {r.choice(['deadline exceeded','unexpected token','cannot read properties of undefined','socket hang up'])} ({r.choice(_WORDS)})"]
    for _ in range(depth):
        lines.append(f"    at {r.choice(_WORDS).capitalize()}{r.choice(_MODULES).capitalize()}.{r.choice(_EVENTS).replace('.', '_')} "
                     f"(file:///work/src/{r.choice(_WORDS)}/{r.choice(_MODULES)}.ts:{r.randint(10, 900)}:{r.randint(1, 80)})")
    return "\n".join(lines)


def CODE(seed: int, nfuncs: int = 4, lang: str = "ts") -> str:
    r = random.Random(seed)
    out = []
    for _ in range(nfuncs):
        name = f"{r.choice(_EVENTS).replace('.', '_')}{r.choice(_WORDS).capitalize()}"
        args = [r.choice(_WORDS) for _ in range(r.randint(1, 3))]
        if lang == "py":
            body = [f"def {name}({', '.join(args)}):"]
            for _ in range(r.randint(4, 9)):
                body.append(f"    {r.choice(_WORDS)} = {r.choice(args)}.{r.choice(_EVENTS).replace('.', '_')}({r.randint(0, 99)})")
            body.append(f"    return {r.choice(_WORDS)}")
        elif lang == "rs":
            body = [f"fn {name}({', '.join(a + ': u32' for a in args)}) -> u32 {{"]
            for _ in range(r.randint(4, 9)):
                body.append(f"    let {r.choice(_WORDS)} = {r.choice(args)}.wrapping_add({r.randint(0, 99)});")
            body.append(f"    {r.choice(args)}\n}}")
        else:
            body = [f"function {name}({', '.join(a + ': number' for a in args)}): number {{"]
            for _ in range(r.randint(4, 9)):
                body.append(f"  const {r.choice(_WORDS)}{r.randint(0, 9)} = {r.choice(args)} * {r.randint(1, 9)} + {r.randint(0, 99)};")
            body.append(f"  return {r.choice(args)};\n}}")
        out.append("\n".join(body))
    return "\n\n".join(out)


def JSONCFG(seed: int, n: int = 12) -> str:
    r = random.Random(seed)
    items = []
    for _ in range(n):
        k = f"{r.choice(_MODULES)}_{r.choice(['timeout_ms','max_items','retries','window','threshold','concurrency'])}"
        items.append(f'  "{k}": {r.randint(0, 5000)}')
    return "{\n" + ",\n".join(items) + "\n}"


def DIFF(seed: int, n: int = 10, lang: str = "ts") -> str:
    r = random.Random(seed)
    f = f"src/{r.choice(_WORDS)}/{r.choice(_MODULES)}.{lang}"
    out = [f"diff --git a/{f} b/{f}", f"--- a/{f}", f"+++ b/{f}", f"@@ -{r.randint(1, 200)},{n} +{r.randint(1, 200)},{n} @@"]
    for _ in range(n):
        sign = r.choice(["-", "+", " "])
        out.append(f"{sign}  const {r.choice(_WORDS)} = {r.choice(_MODULES)}.{r.choice(_EVENTS).replace('.', '_')}({r.randint(0, 99)});")
    return "\n".join(out)


def CSV(seed: int, rows: int = 20) -> str:
    r = random.Random(seed)
    out = ["id,bucket,count,mean_ms,p95_ms"]
    for i in range(rows):
        out.append(f"{i},{r.choice(_WORDS)},{r.randint(1, 900)},{r.randint(1, 300)}.{r.randint(0, 9)},{r.randint(100, 2000)}")
    return "\n".join(out)


def PROSE(seed: int, sentences: int = 8) -> str:
    """Neutral filler prose (a pasted doc section) with no personal facts."""
    r = random.Random(seed)
    subj = ["The scheduler", "Each worker", "The batcher", "A shard", "The indexer", "The exporter"]
    verb = ["retries failed items", "drains its queue", "compacts old segments", "renews its lease",
            "emits a heartbeat", "rotates its snapshot", "evicts cold entries"]
    cond = ["after the window closes", "when the threshold is reached", "on every tick",
            "if the lease expires", "once the epoch advances", "under back-pressure"]
    return " ".join(f"{r.choice(subj)} {r.choice(verb)} {r.choice(cond)}." for _ in range(sentences))
