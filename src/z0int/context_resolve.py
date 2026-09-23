"""Resident provenance-preserving context resolver.

First reusable primitive for the evidence-to-action loop. Resolves bounded
information requirements into source-backed evidence without loading GPU
models or inventing a second scheduler.

Does NOT authorize work, flip bridge execution live, or mark verified_success.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

from . import context_providers as provider_registry
from . import paths

SCHEMA = "z0int.context_resolve.v1"
FAST_SCHEMA = "z0int.resolve_fast.v1"
RECIPE_SCHEMA = "z0int.resolution_recipe.v1"

TrustClass = Literal[
    "authoritative_task",
    "project_constraint",
    "code",
    "conversation",
    "derived_memory",
    "index_hit",
    "unknown",
]


@dataclass(frozen=True)
class EvidenceRef:
    source_id: str
    source_version: str
    locator: str
    trust_class: TrustClass
    observed_at: str
    excerpt: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("excerpt") is None:
            d.pop("excerpt", None)
        if d.get("note") is None:
            d.pop("note", None)
        return d


@dataclass(frozen=True)
class InformationNeed:
    """One bounded thing the caller must know.

    ``kind`` names the *semantic type* of the answer, not its spelling. That
    distinction is what lets a prose question reach an identifier it shares no
    lexical overlap with: the caller states that it wants a path, a config value
    or a model id, and the provider registry applies the type filter. The typed
    kinds declared here originally (``exact_symbol`` among them) had no handling
    branch at all and fell through to "unsupported kind"; they are now routed.
    """

    id: str
    description: str
    kind: Literal[
        "exact_path",
        "exact_symbol",
        "path",
        "symbol",
        "config_value",
        "model",
        "command",
        "url",
        "revision",
        "repository",
        "issue",
        "identifier",
        "natural_language",
        "memory",
    ] = "natural_language"
    path: str | None = None
    symbol: str | None = None
    required: bool = True


@dataclass(frozen=True)
class ResolutionRecipe:
    capability_id: str
    request_signature: str
    scope_fingerprint: str
    policy_revision: str
    source_epochs: dict[str, str]
    operations: tuple[dict[str, Any], ...]
    required_evidence_fields: tuple[str, ...]
    verifier_revision: str
    hardware_profile: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RECIPE_SCHEMA,
            "capability_id": self.capability_id,
            "request_signature": self.request_signature,
            "scope_fingerprint": self.scope_fingerprint,
            "policy_revision": self.policy_revision,
            "source_epochs": dict(self.source_epochs),
            "operations": list(self.operations),
            "required_evidence_fields": list(self.required_evidence_fields),
            "verifier_revision": self.verifier_revision,
            "hardware_profile": self.hardware_profile,
        }


@dataclass
class ContextPacket:
    """Source-backed requirements packet. Map into AODL provenance/evidence — not a new HOTL kind."""

    schema: str = SCHEMA
    task_id: str | None = None
    needs: list[InformationNeed] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    unresolved_gaps: list[str] = field(default_factory=list)
    recipe: ResolutionRecipe | None = None
    measurements: dict[str, Any] = field(default_factory=dict)
    aodl_projection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "needs": [asdict(n) for n in self.needs],
            "evidence": [e.to_dict() for e in self.evidence],
            "contradictions": list(self.contradictions),
            "unresolved_gaps": list(self.unresolved_gaps),
            "recipe": self.recipe.to_dict() if self.recipe else None,
            "measurements": dict(self.measurements),
            "aodl_projection": dict(self.aodl_projection),
        }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _file_version(path: Path) -> str:
    try:
        st = path.stat()
        return f"mtime={int(st.st_mtime)}:size={st.st_size}"
    except OSError:
        return "missing"


def _scope_fingerprint(project_root: Path | None, collections: tuple[str, ...]) -> str:
    parts = [
        str(project_root.resolve()) if project_root else "",
        ",".join(collections),
        os.environ.get("Z0INT_HOME", ""),
    ]
    return _sha16("|".join(parts))


def _request_signature(needs: list[InformationNeed], task_id: str | None) -> str:
    blob = json.dumps(
        {
            "task_id": task_id,
            "needs": [asdict(n) for n in needs],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return _sha16(blob)


def _qmd_bin() -> str | None:
    return shutil.which("qmd")


def _qmd_search(query: str, *, limit: int = 5, timeout_s: float = 8.0) -> list[dict[str, Any]]:
    """Lexical BM25 via qmd search. No GPU. Fail-open on missing CLI/index."""
    bin_path = _qmd_bin()
    if not bin_path:
        return []
    try:
        proc = subprocess.run(
            [bin_path, "search", query, "--json", "-n", str(limit)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        # older qmd may not support --json; try plain
        try:
            proc2 = subprocess.run(
                [bin_path, "search", query],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc2.returncode != 0:
            return []
        # non-json: return empty structured hits (status only)
        return [{"raw_preview": proc2.stdout[:200], "transport": "qmd_search_text"}]
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [{"raw_preview": proc.stdout[:200], "transport": "qmd_search_nonjson"}]
    if isinstance(data, list):
        return data[:limit]
    if isinstance(data, dict):
        hits = data.get("results") or data.get("hits") or data.get("documents") or []
        if isinstance(hits, list):
            return hits[:limit]
    return []


def _fetch_exact_path(path_str: str, project_root: Path | None) -> EvidenceRef | None:
    p = Path(path_str).expanduser()
    if not p.is_absolute() and project_root is not None:
        p = (project_root / p).resolve()
    else:
        try:
            p = p.resolve()
        except OSError:
            return None
    if not p.is_file():
        return None
    # Bound excerpt — no full private dump
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    excerpt = text[:400].replace("\n", "\\n")
    return EvidenceRef(
        source_id=f"file:{p}",
        source_version=_file_version(p),
        locator=str(p),
        trust_class="code" if p.suffix in {".py", ".ts", ".tsx", ".js", ".rs", ".go"} else "project_constraint",
        observed_at=_now_iso(),
        excerpt=excerpt,
    )


def _cache_dir() -> Path:
    root = paths.home() / "state" / "context_resolve"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_recipe_cache(signature: str) -> dict[str, Any] | None:
    path = _cache_dir() / f"recipe_{signature}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_recipe_cache(recipe: ResolutionRecipe, packet_summary: dict[str, Any]) -> Path:
    path = _cache_dir() / f"recipe_{recipe.request_signature}.json"
    blob = {
        "recipe": recipe.to_dict(),
        "packet_summary": packet_summary,
        "saved_at": _now_iso(),
    }
    path.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def invalidate_recipe_cache(scope_fingerprint: str | None = None) -> int:
    """Mark cached recipes stale by scope. **Never deletes.**

    Folded from ``MemoryPacketCache.invalidate`` (hermes-agent branch
    ``memory-packet-cache``): the entry stays readable and gains
    ``invalidated_at``, so a consumer can still be handed the last known good
    recipe while its staleness is explicit rather than silent. ``put``-style
    correctness is enforced on the write side in :func:`resolve_context`, which
    refuses to cache a failed or empty resolution at all.

    Returns the number of entries touched (already-invalidated ones included).
    """
    touched = 0
    for path in _cache_dir().glob("recipe_*.json"):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if scope_fingerprint is not None:
            if blob.get("recipe", {}).get("scope_fingerprint") != scope_fingerprint:
                continue
        touched += 1
        if blob.get("invalidated_at"):
            continue
        blob["invalidated_at"] = _now_iso()
        path.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return touched


def recipe_cache_state(signature: str, *, scope_fingerprint: str | None = None) -> str:
    """``FRESH`` | ``STALE`` | ``MISS`` for a signature.

    Staleness is anchored on ``invalidated_at`` (correctness), not on age --
    there is no TTL here, matching the invariant that a packet is superseded
    because a source changed, not because a timer elapsed.
    """
    blob = load_recipe_cache(signature)
    if not blob:
        return "MISS"
    if scope_fingerprint is not None:
        if blob.get("recipe", {}).get("scope_fingerprint") != scope_fingerprint:
            return "MISS"
    return "STALE" if blob.get("invalidated_at") else "FRESH"


def project_to_aodl_fields(packet: ContextPacket) -> dict[str, Any]:
    """Map packet into existing AODL-ish provenance/evidence/constraint bags.

    Does not invent HOTL kinds or top-level intentContract fields.
    """
    return {
        "provenance": {
            "resolver": SCHEMA,
            "task_id": packet.task_id,
            "recipe_signature": packet.recipe.request_signature if packet.recipe else None,
            "source_epochs": dict(packet.recipe.source_epochs) if packet.recipe else {},
        },
        "evidence": [e.to_dict() for e in packet.evidence],
        "constraints": {
            "unresolved_gaps": list(packet.unresolved_gaps),
            "contradictions": list(packet.contradictions),
        },
        "observation": {
            "measurements": dict(packet.measurements),
        },
    }


def resolve_context(
    *,
    needs: list[InformationNeed] | None = None,
    query: str | None = None,
    task_id: str | None = None,
    project_root: Path | str | None = None,
    collections: tuple[str, ...] = (),
    policy_revision: str = "v0",
    use_cache: bool = True,
    allow_qmd: bool = False,
    allow_memory: bool = False,
    providers: tuple[str, ...] | None = None,
    provider_limit: int = 8,
    typed_limit: int = 12,
    typed_fallback: bool = True,
) -> ContextPacket:
    """Resolve information needs into a provenance-preserving packet.

    Evidence is served by the local provider registry
    (:mod:`z0int.context_providers`): AgentsView conversational/hybrid search,
    the Hermes profile stores, the Claude hosted-app export, the off-root
    Claude/Cursor indexes, contentless tool-output FTS, and ``coverage.db``
    primitives. ``qmd`` is no longer the evidence substrate -- it is an optional
    *supplement* (``allow_qmd``) for callers who have a markdown index worth
    consulting, because it was lexically blind to conversation and tool output.

    ``typed_fallback`` enables the two-hop identifier hop for prose needs. A
    question such as "where is the key that the DSH plugin reads stored on disk"
    shares no tokens with ``~/.hermes/profiles/chiefstaff/.env``; the hop reaches
    it by harvesting identifier-shaped tokens out of the surrounding evidence and
    looking those up in the typed fact store.

    Memory recall is off by default (avoid double-inject with TencentDB proxy).
    No model is loaded and no network model call is made on this path.
    """
    t0 = time.perf_counter()
    root = Path(project_root).expanduser().resolve() if project_root else None
    need_list: list[InformationNeed] = list(needs or [])
    if query and not need_list:
        need_list.append(
            InformationNeed(id="q0", description=query, kind="natural_language", required=True)
        )
    if not need_list:
        raise ValueError("resolve_context requires needs or query")

    sig = _request_signature(need_list, task_id)
    scope_fp = _scope_fingerprint(root, collections)
    ops: list[dict[str, Any]] = []
    evidence: list[EvidenceRef] = []
    gaps: list[str] = []
    contradictions: list[str] = []
    epochs: dict[str, str] = {"policy": policy_revision}
    provider_errors: list[str] = []
    provider_status: dict[str, dict[str, Any]] = {}

    if use_cache:
        cached = load_recipe_cache(sig)
        if cached and cached.get("recipe", {}).get("scope_fingerprint") == scope_fp:
            ops.append({"op": "recipe_cache_hit", "signature": sig})

    qmd_status = "disabled"
    if allow_qmd:
        qmd_status = "absent"
        if _qmd_bin():
            qmd_status = "installed"
            try:
                st = subprocess.run(
                    [_qmd_bin() or "qmd", "status"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                if st.returncode == 0:
                    qmd_status = "ready"
                    epochs["qmd_status_sha"] = _sha16(st.stdout[:2000])
            except (OSError, subprocess.TimeoutExpired):
                qmd_status = "error"

    def _absorb(hits: list[Any], need_id: str, *, op: str) -> int:
        """Append provider hits as EvidenceRefs. Returns how many were added."""
        added = 0
        for h in hits:
            evidence.append(
                EvidenceRef(
                    source_id=f"{h.provider}:{h.locator}",
                    source_version=h.source_version,
                    locator=h.locator,
                    trust_class=h.trust_class,
                    observed_at=h.timestamp or _now_iso(),
                    excerpt=(h.excerpt or None),
                    note=" ".join(
                        x for x in (
                            f"via={op}",
                            f"bridge={h.bridge}" if h.bridge else None,
                            f"tool={h.tool_name}" if h.tool_name else None,
                        ) if x
                    ),
                )
            )
            added += 1
        return added

    def _record_status(status: list[Any], need_id: str) -> None:
        for s in status:
            ops.append(
                {
                    "op": "provider", "need": need_id, "name": s.provider,
                    "ok": s.ok, "hits": s.hits, "wall_ms": round(s.wall_ms, 1),
                    **({"error": s.error} if s.error else {}),
                }
            )
            provider_status[s.provider] = {"ok": s.ok, "hits": s.hits, "error": s.error}
            if not s.ok:
                provider_errors.append(f"{s.provider}: {s.error}")

    for need in need_list:
        satisfied = False

        if need.kind in provider_registry.SOURCE_KINDS or need.path:
            path_s = need.path or need.description
            ref = _fetch_exact_path(path_s, root)
            ops.append({"op": "exact_path", "need": need.id, "path": path_s, "hit": ref is not None})
            if ref:
                evidence.append(ref)
                continue
            # A literal path that exists was handled above. A path-shaped need
            # that does *not* resolve is still allowed to fall through to
            # identifier resolution, because the caller may have given a symbol,
            # a basename or a prose description rather than a real path -- so the
            # gap is recorded only for a need that explicitly named a path.
            if need.path is not None and need.required:
                gaps.append(f"{need.id}: missing path {path_s}")
                continue

        if need.kind == "memory":
            ops.append({"op": "memory_skipped", "need": need.id, "reason": "allow_memory=False by default"})
            if need.required and not allow_memory:
                gaps.append(f"{need.id}: memory recall disabled (avoid double-inject)")
            continue

        query_text = need.description or need.symbol or ""
        typed = need.kind in provider_registry.KIND_FACTS

        if typed or need.kind == "natural_language":
            lex = provider_registry.search_lexical(
                query_text, limit=provider_limit, only=providers
            )
            _record_status(lex.status, need.id)

            # Ordering is semantic, not cosmetic. When the caller DECLARED the
            # answer's type, the type-correct fact *is* the answer and the
            # surrounding conversation is only the bridge to it -- so facts lead.
            # For a natural_language need the conversation is the substance, so it
            # leads and the identifier hop follows. Either way both are returned.
            def _identifier_hop() -> int:
                fhits, _ = provider_registry.resolve_identifier_need(
                    query_text, need_kind=need.kind, limit=typed_limit,
                    lexical_limit=provider_limit, lex=lex,
                )
                n = _absorb(fhits, need.id, op="identifier")
                ops.append(
                    {
                        "op": "identifier", "need": need.id, "kind": need.kind,
                        "fact_kinds": list(provider_registry.kinds_for(need.kind)),
                        "hits": n,
                    }
                )
                return n

            fn = 0
            if typed and typed_fallback:
                fn = _identifier_hop()
            n = _absorb(lex.hits, need.id, op="lexical")
            ops.append({"op": "lexical", "need": need.id, "hits": n})
            if not typed and typed_fallback:
                fn = _identifier_hop()
            if n or fn:
                satisfied = True

        if allow_qmd and qmd_status in {"ready", "installed"}:
            hits = _qmd_search(query_text, limit=5)
            ops.append({"op": "qmd_search", "need": need.id, "hits": len(hits), "qmd_status": qmd_status})
            for i, hit in enumerate(hits):
                if not isinstance(hit, dict):
                    continue
                loc = str(hit.get("path") or hit.get("file") or hit.get("id") or f"hit:{i}")
                evidence.append(
                    EvidenceRef(
                        source_id=f"qmd:{loc}",
                        source_version=str(hit.get("version") or hit.get("score") or "unknown"),
                        locator=loc,
                        trust_class="index_hit",
                        observed_at=_now_iso(),
                        excerpt=str(
                            hit.get("snippet") or hit.get("text") or hit.get("raw_preview") or ""
                        )[:400] or None,
                    )
                )
                satisfied = True

        if not satisfied and need.required:
            # An empty provider result is a gap, never a license to invent.
            gaps.append(f"{need.id}: no evidence for {query_text!r} (kind={need.kind})")

    if allow_memory:
        contradictions.append(
            "memory tier requested: ensure TencentDB proxy injection is not also active on the same turn"
        )

    recipe = ResolutionRecipe(
        capability_id="context_resolve",
        request_signature=sig,
        scope_fingerprint=scope_fp,
        policy_revision=policy_revision,
        source_epochs=epochs,
        operations=tuple(ops),
        required_evidence_fields=tuple(n.id for n in need_list if n.required),
        verifier_revision="none",
        hardware_profile=os.environ.get("Z0INT_HW_PROFILE", "local"),
    )

    packet = ContextPacket(
        task_id=task_id,
        needs=need_list,
        evidence=evidence,
        contradictions=contradictions,
        unresolved_gaps=gaps,
        recipe=recipe,
        measurements={
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
            "qmd_status": qmd_status,
            "qmd_bin": bool(_qmd_bin()),
            "allow_qmd": allow_qmd,
            "allow_memory": allow_memory,
            "providers": provider_status,
            "provider_errors": provider_errors,
            "gpu_loaded": False,
            "network_model_calls": 0,
        },
    )
    packet.aodl_projection = project_to_aodl_fields(packet)

    if use_cache:
        # Correctness semantics folded in from `MemoryPacketCache` (hermes-agent
        # branch `memory-packet-cache`), applied to this module's existing cache
        # rather than via a second cache abstraction:
        #
        #   * a FAILED source is never cached. Caching a provider error would
        #     turn a transient outage into a durable false negative for every
        #     later caller of the same signature.
        #   * an EMPTY result is never cached for the same reason -- an empty
        #     read is indistinguishable from an unavailable source.
        #   * `policy_revision` already participates in `request_signature`,
        #     which is the policy-version-in-key rule.
        #
        # A previously-cached entry is invalidated (never deleted) by
        # `invalidate_recipe_cache` when the source epoch for its scope changes.
        if provider_errors or not evidence:
            packet.measurements["cache_write"] = (
                "skipped:provider-error" if provider_errors else "skipped:empty-evidence"
            )
        else:
            save_recipe_cache(
                recipe,
                {
                    "evidence_count": len(evidence),
                    "gaps": gaps,
                    "wall_ms": packet.measurements["wall_ms"],
                    "source_epochs": dict(epochs),
                },
            )
            packet.measurements["cache_write"] = "saved"
    return packet


# ===========================================================================
# Fast concrete-resolution path
# ===========================================================================
#
# The full resolver answers by breadth: it fans out over every provider, then
# compiles a packet. That is the right shape for "compile me the context of this
# task" and the wrong shape for "where is the key stored". Measured on this
# machine, the identifier question below costs ~1.4 s and ~80 evidence items
# through `resolve_context`, while the provider that actually holds the answer
# (AgentsView HTTP) returns it in **69 ms**.
#
# `resolve_fast` instead states what values the question needs (slots), races the
# cheap providers in measured-latency order, and stops the moment every required
# slot has a candidate that is present in returned evidence. Anything it cannot
# finish falls through to `resolve_context`, so the coverage win is retained.
#
# Models are used only to choose among a bounded set of retrieval operators when
# the deterministic cues are silent, and never to produce a value. Every slot
# value must be found in evidence or it does not count as resolved.

SlotKind = Literal[
    "PATH", "MODEL", "CONFIG_VALUE", "COMMAND", "URL",
    "REVISION", "REPOSITORY", "ISSUE", "IDENTIFIER", "CLAIM",
]

#: Requested-slot vocabulary -> the `InformationNeed.kind` the fact store uses.
SLOT_NEED_KIND: dict[str, str] = {
    "PATH": "path",
    "MODEL": "model",
    "CONFIG_VALUE": "config_value",
    "COMMAND": "command",
    "URL": "url",
    "REVISION": "revision",
    "REPOSITORY": "repository",
    "ISSUE": "issue",
    "IDENTIFIER": "identifier",
    "CLAIM": "natural_language",
}

#: Deterministic slot cues. Ordered most-specific first: a question that matches
#: several cues takes the earliest as its primary slot, so "where is the plugin
#: key stored?" resolves to PATH alone rather than PATH+CONFIG_VALUE.
SLOT_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PATH", re.compile(r"\b(where(?:'s| is| are)?\b.*\b(stored|located|live|on disk|saved)|which file|what file|file path|path to|config file|settings file)\b", re.I)),
    ("MODEL", re.compile(r"\b(which|what)\s+(model|models)\b|\bmodel id\b|\bfail(?:s|ed)? over to\b", re.I)),
    ("URL", re.compile(r"\b(url|uri|endpoint|base url|gateway address|host:port)\b", re.I)),
    ("COMMAND", re.compile(r"\b(what|which)\s+command\b|\bcommand (?:fixed|ran|to run)\b|\bhow do i run\b", re.I)),
    ("REVISION", re.compile(r"\b(revision|commit sha|\bsha\b|pinned version|version pin)\b", re.I)),
    ("ISSUE", re.compile(r"\b(issue|pull request|\bpr\b|ticket)\s*#?\d*", re.I)),
    ("REPOSITORY", re.compile(r"\b(which|what)\s+(repo|repository)\b", re.I)),
    ("CONFIG_VALUE", re.compile(r"\b(config value|setting|backend|env(?:ironment)? variable|which .{0,20}backend|what .{0,20}backend)\b", re.I)),
    ("IDENTIFIER", re.compile(r"\b(which|what)\s+(identifier|symbol|function|helper|module)\b", re.I)),
    ("CLAIM", re.compile(r"\b(did we decide|what did we decide|should happen to|agreed|policy on)\b", re.I)),
)

#: Shape tests for candidate extraction, per slot. `CLAIM` has no shape: a claim
#: is prose and is verified by presence in evidence rather than by pattern.
SLOT_PATTERNS: dict[str, re.Pattern[str] | None] = {
    "PATH": re.compile(r"~?/(?:[\w.\-+@]+/)*[\w.\-+@]*\.[A-Za-z0-9]{1,8}"),
    "MODEL": re.compile(r"\b[a-z][a-z0-9]*(?:[-.][a-z0-9]+)*\d[\w.\-]*\b"),
    "URL": re.compile(r"https?://[^\s\"'<>)\]]+"),
    "REVISION": re.compile(r"\b[0-9a-f]{7,40}\b"),
    "COMMAND": re.compile(r"\b[\w.\-/]+\.(?:sh|py|mjs|js|ts)\b"),
    # ENV_VAR names, and the `dotted.key:=value` form the fact store records for
    # tool-argument config (e.g. `memory.backend:=off`).
    "CONFIG_VALUE": re.compile(
        r"\b[A-Z][A-Z0-9]{2,}_[A-Z0-9_]{2,}\b|\b[\w]+(?:\.[\w]+)+:?=\s*[\w.\-/]+"
    ),
    "REPOSITORY": re.compile(r"\b(?:[\w.\-]+/){1,2}[\w.\-]+\b"),
    "ISSUE": re.compile(r"#\d+\b"),
    "IDENTIFIER": re.compile(r"\b[a-zA-Z_][\w]*(?:[.\-/][\w]+){1,}\b"),
    "CLAIM": None,
}

#: Temporal cues select the history operator.
_TEMPORAL_RE = re.compile(r"\b(when|before|after|earlier|history|changed|over time|last (?:week|month))\b", re.I)

#: Bounded action set. The model, when consulted, picks from exactly this list.
OPERATORS = (
    "EXACT_LOOKUP",
    "FACT_KIND_LOOKUP",
    "SESSION_TO_IDENTIFIER",
    "TEMPORAL_HISTORY",
    "CLAIM_LOOKUP",
    "FULL_RECALL",
)

#: Operators the fast path can actually execute today. The rest of `OPERATORS`
#: is the declared bounded action set; naming an operator the fast path cannot
#: run yet must cost nothing, so those short-circuit to the full resolver.
FAST_EXECUTABLE = frozenset({"EXACT_LOOKUP", "FACT_KIND_LOOKUP", "SESSION_TO_IDENTIFIER"})

#: Providers actually exercised by a fast-path run, in measured cost order.
FAST_LEXICAL_ONLY = ("conversation", "misc-extra", "anthropic")


@dataclass
class Slot:
    """One exact value the question needs. Resolved only by evidence."""

    kind: SlotKind
    required: bool = True
    value: str | None = None
    evidence_pointer: str | None = None
    verification: str | None = None
    provider: str | None = None

    @property
    def resolved(self) -> bool:
        return self.value is not None and self.verification is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "required": self.required,
            "resolved": self.resolved,
            "value": self.value,
            "evidence_pointer": self.evidence_pointer,
            "verification": self.verification,
            "provider": self.provider,
        }


@dataclass
class FastAnswer:
    schema: str = FAST_SCHEMA
    question: str = ""
    slots: list[Slot] = field(default_factory=list)
    operators: list[str] = field(default_factory=list)
    operator_source: str = "rule"
    providers_used: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    evidence_opened: int = 0
    ttfa_ms: float | None = None
    total_ms: float = 0.0
    fallback_used: bool = False
    answer: list[dict[str, Any]] = field(default_factory=list)
    verification_sources: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        req = [s for s in self.slots if s.required]
        return bool(req) and all(s.resolved for s in req)

    @property
    def unnecessary_evidence_read(self) -> int:
        """Evidence opened beyond what the verified answer needed.

        A fast path that opens 80 items to return one verified path is counted
        against, exactly as the brief requires.
        """
        return max(0, self.evidence_opened - len(self.answer))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "question": self.question,
            "slots": [s.to_dict() for s in self.slots],
            "complete": self.complete,
            "operators": list(self.operators),
            "operator_source": self.operator_source,
            "providers_used": list(self.providers_used),
            "evidence_opened": self.evidence_opened,
            "unnecessary_evidence_read": self.unnecessary_evidence_read,
            "ttfa_ms": self.ttfa_ms,
            "total_ms": self.total_ms,
            "fallback_used": self.fallback_used,
            "answer": list(self.answer),
            "verification_sources": list(self.verification_sources),
            "evidence": list(self.evidence),
        }


def infer_slots(question: str) -> list[Slot]:
    """Which exact values does this question need?

    Deterministic and deliberately shallow. It must not try to model an
    ontology: it answers only "what kind of value is being asked for". A second
    slot is added only when the question explicitly conjoins two cues.
    """
    matches = [kind for kind, rx in SLOT_CUES if rx.search(question)]
    if not matches:
        return []
    primary = matches[0]
    slots = [Slot(kind=primary)]  # type: ignore[arg-type]
    conjoined = re.search(r"\band\b|,", question, re.I) is not None
    if conjoined:
        for kind in matches[1:]:
            if kind != primary:
                slots.append(Slot(kind=kind))  # type: ignore[arg-type]
                break
    return slots


def _slot_candidates(text: str, kind: str, limit: int = 4) -> list[str]:
    """Identifier-shaped values of the requested kind, de-duplicated in order."""
    rx = SLOT_PATTERNS.get(kind)
    if rx is None:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in rx.finditer(text or ""):
        val = m.group(0).strip().strip("`'\".,;:)]}")
        if len(val) < 2 or val.lower() in seen:
            continue
        seen.add(val.lower())
        out.append(val)
        if len(out) >= limit:
            break
    return out


def _verify_slot(slot: Slot, value: str, hit: Any) -> str | None:
    """Verify a candidate against evidence. Returns the verification source.

    The invariant: a value is never accepted because a model proposed it. It is
    accepted only because it appears in returned evidence, with the evidence
    pointer naming where. A filesystem check, where the value is a path and the
    path is local, upgrades the verdict but is never required -- a legitimately
    deleted file must still be reportable.
    """
    blob = ((getattr(hit, "excerpt", "") or "") + " " + (getattr(hit, "locator", "") or "")).lower()
    if value.lower() not in blob:
        return None
    if slot.kind == "PATH":
        try:
            if Path(value).expanduser().exists():
                return "evidence+fs-exists"
        except (OSError, RuntimeError):
            pass
    return "evidence"


#: Terms too common to establish that evidence is about the question.
_RELEVANCE_STOP = frozenset(
    """
    the a an of to in on for and or that this it is are was were be been do does did
    what which where when who why how we you i they he she them us our your their
    before after now then than with from at by as into over under about
    use used using does did should would could can will shall may might must
    """.split()
)


def _content_terms(question: str, limit: int = 12) -> list[str]:
    """Content words of the question, used as a deterministic relevance gate."""
    out: list[str] = []
    for t in re.findall(r"[A-Za-z0-9_][\w.\-]{2,}", question or ""):
        low = t.lower()
        if low in _RELEVANCE_STOP or low in out:
            continue
        out.append(low)
        if len(out) >= limit:
            break
    return out


def _absorb_candidates(
    slots: list[Slot], hits: Sequence[Any], providers: set[str], *, question: str = ""
) -> None:
    """Try to fill unresolved slots from a batch of hits.

    A candidate is accepted only if the hit it came from is *about the question*
    -- at least one question content term must appear in the hit. Shape matching
    alone is not enough, and this gate is why: on "what memory backend did OMP
    use", a shape-only rule accepted the first `CONFIG_VALUE`-shaped token in the
    union (``BUILT_IN``) and reported a confident, verified, wrong answer. Value
    shapes are cheap to satisfy and say nothing about relevance; the envelope is
    what carries relevance, which is the same reason the two-hop bridge exists.
    """
    terms = _content_terms(question) if question else []
    # Corroboration threshold. A single shared content term is far too weak: on
    # the 17-miss set it produced 5 verified-but-WRONG answers, including two
    # returned without fallback (a `gamma.app` URL for "what is the TencentDB
    # memory gateway URL", and a directory for "which file implements the jev
    # auth registry helper"). One term is satisfied by any message that merely
    # shares the topic. Requiring TWO distinct question terms in the same
    # evidence -- when the question has two to give -- keeps the genuine answers
    # (the chiefstaff .env hit carries key+DSH+plugin; the cleanup path carries
    # cleanup+cachyos) and rejects topical near-misses.
    min_score = 2 if len(terms) >= 2 else 1
    # Collect EVERY candidate with its score, then require an unambiguous
    # winner. Presence in relevant evidence establishes topicality, not
    # answerhood: measured on the 17-miss set, a corroborated presence rule
    # still produced 4 verified-but-wrong answers (a `/.env` fragment for "which
    # file implements the jev auth registry helper", a commit-sha fragment for
    # "which model does the system fail over to"). When several same-shaped
    # candidates compete, the honest verdict is "this evidence does not single
    # out an answer", so the slot stays unresolved and the question falls back to
    # the full resolver -- which is the difference between a slow answer and a
    # confident false memory.
    cands: dict[int, dict[str, int]] = {}
    owner: dict[tuple[int, str], Any] = {}
    for hit in hits:
        text = (getattr(hit, "excerpt", "") or "") + " " + (getattr(hit, "locator", "") or "")
        # Score on the EXCERPT only. The locator is a pointer, not evidence, and
        # it embeds the harness name -- session ids look like `omp:01a0...` -- so
        # scoring on it awarded relevance for the string "omp" to a fact whose
        # entire content was `BUILT_IN`.
        semantic = (getattr(hit, "excerpt", "") or "").lower()
        score = sum(1 for t in terms if t in semantic)
        if terms and score < min_score:
            continue
        for index, slot in enumerate(slots):
            if slot.resolved:
                continue
            if slot.kind == "CLAIM":
                excerpt = (getattr(hit, "excerpt", "") or "").strip()
                if len(excerpt) < 40:
                    continue
                cands.setdefault(index, {})[excerpt[:400]] = score
                owner.setdefault((index, excerpt[:400]), hit)
                continue
            for cand in _slot_candidates(text, slot.kind):
                if _verify_slot(slot, cand, hit) is None:
                    continue
                bucket = cands.setdefault(index, {})
                bucket[cand] = max(bucket.get(cand, 0), score)
                owner.setdefault((index, cand), hit)
                break
    resolved: dict[int, tuple[str, Any]] = {}
    for index, scores in cands.items():
        if not scores:
            continue
        top = max(scores.values())
        winners = [value for value, s in scores.items() if s == top]
        if len(winners) != 1:
            continue  # ambiguous -> stays unresolved -> fallback
        resolved[index] = (winners[0], owner[(index, winners[0])])
    best = {i: (0, v, h) for i, (v, h) in resolved.items()}
    for index, (_score, value, hit) in best.items():
        slot = slots[index]
        slot.value = value
        slot.evidence_pointer = getattr(hit, "locator", None)
        slot.verification = (
            "evidence:prose" if slot.kind == "CLAIM" else (_verify_slot(slot, value, hit) or "evidence")
        )
        slot.provider = getattr(hit, "provider", None)
        providers.add(str(getattr(hit, "provider", "")))


def _tokens_from(slots: list[Slot], hits: Sequence[Any]) -> list[str]:
    """Harvest bridge tokens for the fact hop. Richer snippets first."""
    ordered: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        for tok in provider_registry.fact_tokens(getattr(hit, "excerpt", "") or ""):
            if tok.lower() not in seen:
                seen.add(tok.lower())
                ordered.append(tok)
    return ordered[:32]


def _advisory_operator(question: str, *, timeout_s: float = 5.0) -> str | None:
    """Ask a small z0 decision backend which operator to use -- advisory only.

    Only called when the deterministic cues are silent, i.e. when the
    alternative is paying full-resolver cost anyway. It cannot introduce a fact:
    whatever it returns is used to pick a retrieval strategy, and the resulting
    candidate still has to be found in evidence. Any failure returns None and the
    caller falls back to FULL_RECALL.
    """
    try:
        from .backends.registry import create_backend, register_builtin_backends
        from .backends.base import DecisionQuestion, DecisionRequest

        register_builtin_backends()
        backend = create_backend("laya_421m")
        request = DecisionRequest(
            capability_id="resolve.operator_select",
            questions=[
                DecisionQuestion(
                    id="op",
                    prompt=(
                        "Pick the retrieval operator for a question about local "
                        "engineering history. Answer with the option id only.\n"
                        f"Question: {question}"
                    ),
                    options=list(OPERATORS[:-1]),
                )
            ],
        )
        result = backend.evaluate(request)
        for answer in getattr(result, "answers", []) or []:
            choice = getattr(answer, "choice", None)
            if choice in OPERATORS:
                return str(choice)
    except Exception:  # noqa: BLE001 - advisory; never fatal to the fast path
        return None
    return None


def _episode_path() -> Path:
    root = paths.home() / "episodes"
    root.mkdir(parents=True, exist_ok=True)
    return root / "resolve_fast.jsonl"


def emit_episode(answer: FastAnswer) -> None:
    """Append one lightweight episode. Never fatal, never blocks the answer."""
    try:
        row = {
            "question": answer.question,
            "requested_slots": [s.kind for s in answer.slots if s.required],
            "operator": answer.operators[0] if answer.operators else None,
            "operator_source": answer.operator_source,
            "providers_used": answer.providers_used,
            "candidates": [s.value for s in answer.slots if s.value],
            "verified": answer.complete,
            "fallback_used": answer.fallback_used,
            "ttfa_ms": answer.ttfa_ms,
            "total_ms": answer.total_ms,
            "evidence_opened": answer.evidence_opened,
            "unnecessary_evidence_read": answer.unnecessary_evidence_read,
        }
        path = _episode_path()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001 - telemetry must never break an answer
        pass


def select_operators(question: str, slots: list[Slot], *, allow_model: bool) -> tuple[list[str], str]:
    """Choose the bounded retrieval operators to execute, cheapest first."""
    ops: list[str] = []
    # A value the caller already spelled out is an exact lookup, not a search.
    if any(c in question for c in ("/", "://", "=")) or re.search(r"\b[0-9a-f]{7,40}\b", question):
        ops.append("EXACT_LOOKUP")
    if any(s.kind == "CLAIM" for s in slots):
        ops.append("CLAIM_LOOKUP")
    if _TEMPORAL_RE.search(question):
        ops.append("TEMPORAL_HISTORY")
    ops.append("SESSION_TO_IDENTIFIER")
    ops.append("FACT_KIND_LOOKUP")
    ops.append("FULL_RECALL")

    source = "rule"
    if not slots and allow_model:
        chosen = _advisory_operator(question)
        if chosen:
            ops = [chosen] + [o for o in ops if o != chosen]
            source = "model:laya_421m"
    return ops, source


def resolve_fast(
    question: str,
    *,
    allow_model: bool = True,
    fallback: bool = True,
    emit: bool = True,
    stage_limit: int = 8,
) -> FastAnswer:
    """Return the first complete verified answer with the least work.

    Shape::
        slots -> cheapest operator -> staged lexical race -> fact hop
              -> deterministic verify -> complete? return : full fallback
    """
    t0 = time.perf_counter()
    answer = FastAnswer(question=question)
    answer.slots = infer_slots(question)
    answer.operators, answer.operator_source = select_operators(
        question, answer.slots, allow_model=allow_model
    )
    providers: set[str] = set()

    def _finish(*, fallback_used: bool) -> FastAnswer:
        answer.providers_used = sorted(p for p in providers if p)
        # NOTE: `evidence_opened` is maintained incrementally by `_note` and by
        # the fallback. It must not be recomputed from `answer.evidence` here --
        # that list is a bounded sample, and recomputing silently reported 0 for
        # every fallback, which made the fallback look free.
        answer.total_ms = (time.perf_counter() - t0) * 1000.0
        answer.fallback_used = fallback_used
        answer.answer = [
            {
                "slot": s.kind,
                "value": s.value,
                "evidence_pointer": s.evidence_pointer,
                "verification": s.verification,
                "provider": s.provider,
            }
            for s in answer.slots
            if s.resolved
        ]
        answer.verification_sources = sorted({s.verification or "" for s in answer.slots if s.resolved})
        if emit:
            emit_episode(answer)
        return answer

    def _note(hits: Sequence[Any]) -> None:
        answer.evidence_opened += len(hits)
        for h in hits[:12]:
            answer.evidence.append(
                {
                    "provider": getattr(h, "provider", None),
                    "locator": getattr(h, "locator", None),
                    "excerpt": (getattr(h, "excerpt", "") or "")[:240],
                    "bridge": getattr(h, "bridge", None),
                }
            )

    # No slots at all -> nothing to be complete about. Go straight to the full
    # resolver rather than pretending a fast path answered.
    if not answer.slots:
        if not fallback:
            return _finish(fallback_used=False)
        return _full_fallback(answer, question, _finish)

    # An operator the fast path cannot execute (TEMPORAL_HISTORY, CLAIM_LOOKUP)
    # must fall back immediately rather than run a staged race first. Measured:
    # doing the race and then falling back cost 2,537 ms on the temporal case
    # against 1,472 ms for the full resolver alone -- the fast path made that
    # question slower than not having one.
    #
    # This tests the PRIMARY operator, not `any` of them: SESSION_TO_IDENTIFIER
    # is always present as a later fallback within the action list, so an `any`
    # test is never false and the guard silently does nothing.
    if not answer.operators or answer.operators[0] not in FAST_EXECUTABLE:
        if not fallback:
            return _finish(fallback_used=False)
        return _full_fallback(answer, question, _finish)

    # Operator 1: EXACT_LOOKUP -- a value the caller already spelled out.
    if "EXACT_LOOKUP" in answer.operators:
        try:
            direct = provider_registry.facts_lexical(
                question, kinds=provider_registry.kinds_for("natural_language"), limit=stage_limit
            )
            providers.add("coverage_facts")
            _note(direct)
            _absorb_candidates(answer.slots, direct, providers, question=question)
            if answer.complete:
                answer.ttfa_ms = (time.perf_counter() - t0) * 1000.0
                return _finish(fallback_used=False)
        except Exception:  # noqa: BLE001 - a dead fact store degrades, never crashes
            pass

    # Operator 2: the staged lexical race. Stop at the first stage that fills
    # every required slot; this is what makes an easy query cheap.
    harvested: list[Any] = []
    try:
        for _stage, result in provider_registry.search_staged(question, limit=stage_limit):
            _note(result.hits)
            _absorb_candidates(answer.slots, result.hits, providers, question=question)
            if answer.complete:
                answer.ttfa_ms = (time.perf_counter() - t0) * 1000.0
                return _finish(fallback_used=False)
            harvested = list(result.hits)
    except Exception:  # noqa: BLE001
        harvested = list(harvested)

    # Operator 3: FACT_KIND_LOOKUP / SESSION_TO_IDENTIFIER -- the two-hop bridge.
    # This is the only path that can reach an identifier sharing no tokens with
    # the question, so it runs before we give up and pay full-resolver cost.
    try:
        tokens = _tokens_from(answer.slots, harvested) or provider_registry.fact_tokens(question)
        kinds: tuple[str, ...] = ()
        for slot in answer.slots:
            kinds += provider_registry.kinds_for(SLOT_NEED_KIND[slot.kind])
        facts = provider_registry.facts_for_tokens(tokens, kinds=tuple(dict.fromkeys(kinds)) or (), limit=stage_limit)
        providers.add("coverage_facts")
        _note(facts)
        _absorb_candidates(answer.slots, facts, providers, question=question)
        if answer.complete:
            answer.ttfa_ms = (time.perf_counter() - t0) * 1000.0
            return _finish(fallback_used=False)
    except Exception:  # noqa: BLE001
        pass

    if not fallback:
        return _finish(fallback_used=False)
    return _full_fallback(answer, question, _finish)


def _full_fallback(answer: FastAnswer, question: str, finish: Any) -> FastAnswer:
    """Pay full-resolver cost, then let it try to fill the same slots.

    The fallback keeps the coverage win: the slots are filled from the packet's
    evidence under the same verification rule, so a fallback answer is held to
    the identical standard as a fast one. Its evidence cost is recorded too --
    otherwise a fallback would look free in the metrics.
    """
    try:
        packet = resolve_context(query=question, use_cache=True)
    except Exception:  # noqa: BLE001 - a failed fallback is still an answer
        return finish(fallback_used=True)
    providers: set[str] = set()
    for e in packet.evidence:
        providers.add(str(e.source_id).split(":", 1)[0])
    answer.evidence_opened += len(packet.evidence)
    _absorb_candidates(answer.slots, packet.evidence, providers, question=question)
    return finish(fallback_used=True)


def needs_from_mapping(raw: dict[str, Any] | list[Any]) -> list[InformationNeed]:
    if isinstance(raw, list):
        items = raw
    else:
        items = raw.get("needs") or []
    out: list[InformationNeed] = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            out.append(InformationNeed(id=f"n{i}", description=item))
            continue
        if not isinstance(item, dict):
            raise ValueError("need must be string or object")
        out.append(
            InformationNeed(
                id=str(item.get("id") or f"n{i}"),
                description=str(item.get("description") or item.get("query") or ""),
                kind=item.get("kind") or ("exact_path" if item.get("path") else "natural_language"),  # type: ignore[arg-type]
                path=item.get("path"),
                symbol=item.get("symbol"),
                required=bool(item.get("required", True)),
            )
        )
    return out


def context_observation_event(
    *,
    packet: ContextPacket,
    source_hash_hex: str,
    revision: int,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """AODL ``stateUpdate`` carrying resolved context as observation (not a new event type)."""
    if trace_id:
        tid = trace_id
    elif packet.task_id:
        tid = packet.task_id
    elif packet.recipe is not None:
        tid = packet.recipe.request_signature
    else:
        tid = "ctx"
    payload = {
        "traceId": tid,
        "kind": "context_resolve",
        "evidenceCount": len(packet.evidence),
        "unresolvedGaps": list(packet.unresolved_gaps),
        "contradictions": list(packet.contradictions),
        "recipeSignature": packet.recipe.request_signature if packet.recipe else None,
        "evidence": [e.to_dict() for e in packet.evidence],
        "measurements": dict(packet.measurements),
        # Explicit: observation is not verified task success.
        "execution_completed": False,
        "verified_success": None,
    }
    digest = hashlib.blake2b(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    return {
        "eventId": f"stateUpdate-ctx-{digest}",
        "type": "stateUpdate",
        "sourceHash": source_hash_hex,
        "revision": int(revision),
        "causalParents": [],
        "payload": payload,
    }


def attach_context_to_aodl(
    doc: dict[str, Any],
    packet: ContextPacket,
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Merge a context packet into an existing AODL document without new HOTL kinds.

    - Does **not** rewrite intentGraph or provenance.sourceHash (intent stays stable).
    - Writes runtime projection under ``provenance.runtimeContext`` and
      ``constraints.context`` (open bags, not top-level intentContract).
    - Appends a ``stateUpdate`` observation to eventLog.
    - Never sets verified_success from resolve alone.
    """
    out = json.loads(json.dumps(doc))  # deep copy via json
    sh = str(out.get("provenance", {}).get("sourceHash") or "")
    if not sh:
        raise ValueError("AODL document missing provenance.sourceHash")
    rev = int(out.get("revision") or 0)
    proj = packet.aodl_projection or project_to_aodl_fields(packet)

    prov = out.setdefault("provenance", {})
    # sourceHash unchanged — context is runtime observation, not intent material
    prov["runtimeContext"] = {
        "schema": SCHEMA,
        "task_id": packet.task_id,
        "recipe_signature": packet.recipe.request_signature if packet.recipe else None,
        "attached_at": _now_iso(),
        "source_epochs": dict(packet.recipe.source_epochs) if packet.recipe else {},
    }

    cons = out.setdefault("constraints", {})
    cons["context"] = {
        "unresolved_gaps": list(packet.unresolved_gaps),
        "contradictions": list(packet.contradictions),
        "evidence_count": len(packet.evidence),
    }

    # Plan binding for the resolver as strategy (not intent node)
    plan = out.setdefault("plan", {})
    bindings = plan.setdefault("bindings", {})
    bindings["context-resolver"] = {
        "runtime": "z0int.context_resolve",
        "schema": SCHEMA,
        "implementationStage": "context_resolve",
        "allow_memory": bool(packet.measurements.get("allow_memory")),
        "gpu_loaded": False,
    }

    event = context_observation_event(
        packet=packet,
        source_hash_hex=sh,
        revision=rev,
        trace_id=trace_id or packet.task_id,
    )
    out.setdefault("eventLog", []).append(event)

    # Keep projection for consumers that read aodl_projection-style bags
    out.setdefault("observation", {})
    out["observation"]["context"] = proj
    return out

