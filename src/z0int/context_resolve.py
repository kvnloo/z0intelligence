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
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import context_providers as provider_registry
from . import paths

SCHEMA = "z0int.context_resolve.v1"
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

