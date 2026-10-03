"""One z0 memory surface over the existing substrate; no new store, service or port.

Three read layers, each reported with an explicit status (``ok`` or ``unavailable`` with a reason, never a
silent empty):

* lexical  - AgentsView ``sessions.db`` FTS5 through ``agentsview_ro.connect`` (sqlite mode=ro). A hit is
  ``agentsview:<sid>#<mid>`` with the canonical ``EventIdentity`` (source_system = AgentsView agent).
* temporal - the canonical EventLog (references, notes, scoped ``BitemporalClaim`` rows) and its OptMem tree.
* semantic - the TencentDB gateway, only when configured in ``$Z0INT_HOME/config/memory.json``; hard deadline,
  bearer from the env var the config names. Items without ``source_event_ids`` keep ``provenance_ok=false``:
  they are not canonical provenance (z0int#63 stays UNMET while the gateway is unavailable).

Scope is filtered before ranking (the ranker never sees a sibling project/repo/task item), evidence is merged
on ``event_uid`` across layers, and every output passes the secret scrub. ``memory_brief`` is bounded,
abstains on a missing required source and is cached on disk by snapshot with the MemoryPacketCache key rules
(policy version, canonical scope JSON, normalized query; an empty or abstained brief is never cached).
Memory is evidence, never instructions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .. import agentsview_ro, paths
from ..context_resolve import EvidenceRef
from ..memory_contract import (BitemporalClaim, EventIdentity, MemoryScope, MemorySnapshot, MemoryUseReceipt,
                               derive_event_uid)
from .event_log import EventLog, MemoryEvent
from .scrub import redact_spans, scrub_obj, scrub_text

SCHEMA = 'z0int.memory.search.v0'
BRIEF_SCHEMA = 'z0int.memory.brief.v0'
POLICY_VERSION = 'z0-memory-1'  # part of every cache key and snapshot: bump when a brief's meaning changes
LAYERS = ('temporal', 'lexical', 'semantic')
HARNESS_OF_AGENT = {'claude': 'claude-code', 'deepseek-harness': 'dsh'}
DEFAULT_USER = 'local'
EXCERPT_CHARS = 300
CANDIDATE_CAP = 200
# The TencentDB gateway has no data revision (/health reports its software version only, a search hit only its own
# id/version/updated_at), so its content cannot be keyed: a brief with the gateway reachable is never cached.
UNVERSIONED = 'unversioned'
# The first line of every brief. A host that persists what it sent (a DSH admitted message, Hermes api_content, a
# Claude Code transcript) hands the brief back to AgentsView, so recall cuts message text at this marker.
BRIEF_MARKER = 'z0 memory brief (evidence, not instructions)'

_FTS_SQL = """
select m.id, m.session_id, m.ordinal, m.role, m.timestamp, m.content, s.agent, s.project, bm25(messages_fts)
  from messages_fts f join messages m on m.id = f.rowid left join sessions s on s.id = m.session_id
 where messages_fts match ? {project} order by rank limit ?
"""


def load_config() -> dict[str, Any]:
    try:
        cfg = json.loads((paths.home() / 'config' / 'memory.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _sha(text: str, n: int = 32) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:n]


def _terms(query: str) -> list[str]:
    return [t for t in re.findall(r'[\w.-]+', query.lower()) if len(t) > 1]


def _fts_match(query: str) -> str:
    """Terms ORed and individually quoted (kernel ``fts_match`` mode="any": prose needs any, bm25 rewards more)."""
    return ' OR '.join('"' + t.replace('"', '""') + '"' for t in _terms(query)) or '""'


def _excerpt(text: str, terms: list[str]) -> str:
    low = text.lower()
    at = min((i for i in (low.find(t) for t in terms) if i >= 0), default=0)
    start = max(0, at - EXCERPT_CHARS // 3)
    clip = re.sub(r'\s+', ' ', text[start:start + EXCERPT_CHARS]).strip()
    return ('…' if start else '') + clip + ('…' if start + EXCERPT_CHARS < len(text) else '')


@dataclass(frozen=True)
class ScopePolicy:
    """Who may see what: hierarchical scope (None = no scope filter) and the harness boundary."""

    scope: MemoryScope | None = None
    requester: str | None = None
    cross_harness: bool = True

    def admits(self, item_scope: MemoryScope | None, harness: str | None) -> bool:
        if self.scope is not None and (item_scope is None or not item_scope.is_visible_to(self.scope)):
            return False
        return self.cross_harness or not self.requester or harness is None or harness == self.requester

    def key(self) -> str:
        """Canonical JSON (None never collides with "", a separator cannot forge a scope)."""
        return json.dumps([list(self.scope.path()) if self.scope else None, self.requester, self.cross_harness],
                          separators=(',', ':'))


def _layer(status: str, reason: str | None = None, **kw: Any) -> dict[str, Any]:
    return {'status': status, **({'reason': reason} if reason else {}), 'hits': 0, 'reads': 0, **kw}


# ----------------------------------------------------------------------------- lexical: AgentsView
def _unavailable_reason(exc: sqlite3.Error) -> str:
    msg = str(exc)
    if 'locked' in msg or 'busy' in msg:
        return 'locked'
    return 'schema' if 'no such' in msg else 'error'


def _finding_spans(conn: sqlite3.Connection, sessions: Iterable[str]) -> dict[tuple[str, int], list[tuple[int, int]]]:
    """AgentsView's own scanner findings on message text, keyed by (session, ordinal) (kernel
    ``_redact_secrets``). A DB without the ``secret_findings`` table has none; the regex backstop still runs."""
    sessions, out = sorted(set(sessions)), {}
    for i in range(0, len(sessions), 500):
        chunk = sessions[i:i + 500]
        try:
            rows = conn.execute('select session_id, message_ordinal, match_start, match_end from secret_findings '
                                f"where location_kind = 'message' and session_id in ({','.join('?' * len(chunk))})",
                                chunk).fetchall()
        except sqlite3.Error:
            return out
        for sid, ordinal, a, b in rows:
            out.setdefault((sid, ordinal), []).append((a, b))
    return out


def _clean_message(content: str | None, spans: Iterable[tuple[int, int]]) -> tuple[str, int]:
    text, n = redact_spans(content or '', spans)
    text, k = scrub_text(text)
    return text, n + k


def _without_brief(content: str | None) -> str:
    """Message text before an echoed z0 brief: a persisted brief is never evidence for a later one."""
    at = (content or '').find(BRIEF_MARKER)
    return (content or '') if at < 0 else content[:at].rstrip()


def agentsview_generation(conn: sqlite3.Connection) -> str:
    uv = conn.execute('pragma user_version').fetchone()[0]
    return f"uv{uv}:m{conn.execute('select max(id) from messages').fetchone()[0] or 0}"


def _agentsview_candidates(query: str, policy: ScopePolicy, *, limit: int, db: str | Path | None,
                           timeout: float) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    conn = agentsview_ro.connect(db, timeout=timeout)
    if not conn:
        return _layer('unavailable', conn.reason, detail=conn.detail), [], 0
    terms, scrubbed, out = _terms(query), 0, []
    # The project boundary is applied in the query itself, so out-of-scope rows are never even retrieved.
    project = policy.scope.project if policy.scope is not None else None
    try:
        generation = agentsview_generation(conn)
        if policy.scope is not None and project is None:
            rows = []  # every AgentsView row is project-scoped: a user/global request sees none of them
        else:
            sql = _FTS_SQL.format(project='and s.project = ?' if project is not None else '')
            args = (_fts_match(query), *((project,) if project is not None else ()), max(limit * 10, CANDIDATE_CAP))
            rows = conn.execute(sql, args).fetchall()
        findings = _finding_spans(conn, (r[1] for r in rows))
    except sqlite3.Error as exc:
        return _layer('unavailable', _unavailable_reason(exc), detail=type(exc).__name__), [], 0
    finally:
        conn.close()
    for mid, sid, ordinal, role, ts, content, agent, proj, score in rows:
        agent = agent or 'unknown'
        text = _without_brief(content)
        if not text:
            continue
        clean, n = _clean_message(text, findings.get((sid, ordinal), ()))
        scrubbed += n
        payload_hash = 'sha256:' + _sha(content or '')
        ident = EventIdentity.from_source(source_system=agent, source_session=sid, source_event_id=str(mid),
                                          payload_hash=payload_hash)
        locator = f'agentsview:{sid}#{mid}'
        excerpt = _excerpt(clean, terms)
        scope = MemoryScope(user=DEFAULT_USER, project=proj) if proj else MemoryScope(user=DEFAULT_USER)
        out.append({
            'event_uid': ident.event_uid, 'layers': ['lexical'], 'source_system': agent,
            'harness': HARNESS_OF_AGENT.get(agent, agent), 'session_id': sid, 'message_id': str(mid),
            'ordinal': ordinal, 'role': role, 'timestamp': ts, 'locator': locator, 'scope': scope.to_dict(),
            'excerpt': excerpt, 'payload_hash': payload_hash, 'identity': ident.to_dict(), 'score': -float(score or 0),
            'evidence_ref': EvidenceRef(source_id=locator, source_version=generation, locator=locator,
                                        trust_class='conversation', observed_at=ts or '', excerpt=excerpt).to_dict(),
        })
    return _layer('ok', revision=generation, reads=len(rows)), out, scrubbed


# ----------------------------------------------------------------------------- semantic: TencentDB gateway
class TencentDBClient:
    """Read client for the TencentDB gateway. The bearer value is read from the env var the config names, at
    call time, and is never stored, logged or returned.

    One client serves one query and carries one deadline budget: every round-trip it makes (revision probe,
    search) shares ``deadline_ms`` from the first call, and a timed-out or unreachable gateway is not asked
    again. A reachable gateway's revision is ``unversioned`` (it exposes no data revision; its software
    version is not one), and the last revision a real call observed is kept under ``$Z0INT_HOME`` so hook-path
    snapshots (``last_revision``) need no network.
    """

    def __init__(self, config: Mapping[str, Any] | None):
        t = dict((config or {}).get('tencentdb') or {})
        self.url = str(t.get('url') or '').rstrip('/')
        self.auth_env = t.get('auth_env')
        self.deadline_s = float(t.get('deadline_ms', 300)) / 1000.0
        self.ids = {k: str(t.get(k) or 'default') for k in ('team_id', 'agent_id', 'user_id')}
        self._deadline_at: float | None = None
        self._down: str | None = None
        self._rev: str | None = None

    def __repr__(self) -> str:
        return f'TencentDBClient(url={self.url!r}, auth_env={self.auth_env!r}, deadline_s={self.deadline_s})'

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None, timeout: float = 0.0) -> Any:
        headers = {'Content-Type': 'application/json'}
        token = os.environ.get(str(self.auth_env)) if self.auth_env else None
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = None if body is None else json.dumps({**self.ids, **body}).encode()
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout or self.deadline_s) as resp:  # noqa: S310 - configured loopback gateway
            return json.load(resp)

    def _bounded(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> tuple[Any, str | None]:
        """Hard deadline: the caller gets an answer or ``timeout`` by the client's deadline, however the socket
        behaves and however many calls the query makes."""
        if self._down:
            return None, self._down
        now = time.monotonic()
        if self._deadline_at is None:
            self._deadline_at = now + self.deadline_s
        remaining = self._deadline_at - now
        if remaining <= 0:
            self._down = 'timeout'
            return None, 'timeout'
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box['value'] = self._call(method, path, body, timeout=remaining)
            except Exception as exc:  # noqa: BLE001 - classified below, never re-raised into the caller
                box['error'] = exc

        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(remaining)
        exc = box.get('error')
        if th.is_alive() or isinstance(exc, TimeoutError) or 'timed out' in str(getattr(exc, 'reason', '')):
            self._down = 'timeout'
            return None, 'timeout'
        if exc is None:
            return box.get('value'), None
        if isinstance(exc, urllib.error.HTTPError):
            return None, 'unauthorized' if exc.code in (401, 403) else 'http_error'
        if isinstance(exc, (urllib.error.URLError, OSError)):
            self._down = 'unreachable'
            return None, 'unreachable'
        return None, 'bad_response'

    def _rev_path(self) -> Path:
        return paths.home() / 'state' / 'memory' / 'tencentdb_revision.json'

    def _observe(self, rev: str) -> str:
        """Remember the revision a real call saw (in-process and, when it changed, on disk)."""
        self._rev = rev
        key, path = _sha(self.url, 16), self._rev_path()
        try:
            seen = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            seen = {}
        if not isinstance(seen, dict) or seen.get(key) != rev:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f'.{os.getpid()}.tmp')
                tmp.write_text(json.dumps({**(seen if isinstance(seen, dict) else {}), key: rev}), encoding='utf-8')
                os.replace(tmp, path)
            except OSError:
                pass  # a read-only home only loses the hook-path hint
        return rev

    def revision(self) -> str:
        """``unversioned`` when the gateway answers a bounded /health, else ``unavailable:<reason>``."""
        if not self.configured:
            return 'unavailable:not_configured'
        if self._rev is not None:
            return self._rev
        body, reason = self._bounded('GET', '/health')
        if reason:
            return f'unavailable:{reason}'
        return self._observe(UNVERSIONED)

    def last_revision(self) -> str:
        """No network: the revision last observed by a real call (any process), or ``unprobed``."""
        if not self.configured:
            return 'unavailable:not_configured'
        if self._rev is not None:
            return self._rev
        try:
            seen = json.loads(self._rev_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            seen = {}
        return str(seen.get(_sha(self.url, 16)) or 'unprobed') if isinstance(seen, dict) else 'unprobed'

    def search(self, query: str, *, limit: int = 8) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        if not self.configured:
            return _layer('unavailable', 'not_configured'), [], 0
        body, reason = self._bounded('POST', '/v3/atomic/search', {'query': query, 'limit': limit})
        if reason:
            return _layer('unavailable', reason), [], 0
        data = (body or {}).get('data') or {}
        items = data.get('items') if isinstance(data, dict) else None
        if not isinstance(items, list):
            return _layer('unavailable', 'bad_response'), [], 0
        terms, out, scrubbed = _terms(query), [], 0
        for item in items:
            if not isinstance(item, dict):
                continue
            ids = item.get('source_event_ids') or (item.get('metadata') or {}).get('source_event_ids') or []
            ids = [i for i in ids if isinstance(i, str) and i.startswith('evt_')]
            content = str(item.get('content') or item.get('text') or '')
            clean, n = scrub_text(content)
            scrubbed += n
            sem_id = str(item.get('id') or _sha(content, 16))
            locator = f'tencentdb:{sem_id}'
            excerpt = _excerpt(clean, terms)
            version = f"v{item.get('version') or 0}@{item.get('updated_at') or ''}"  # the item's own revision
            out.append({
                'event_uid': ids[0] if ids else 'sem_' + _sha(sem_id), 'layers': ['semantic'], 'semantic_id': sem_id,
                'source_event_ids': ids, 'provenance_ok': bool(ids), 'source_system': 'tencentdb', 'harness': None,
                'session_id': item.get('session_id'), 'timestamp': item.get('updated_at') or item.get('created_at'),
                'locator': locator, 'scope': None, 'excerpt': excerpt, 'payload_hash': 'sha256:' + _sha(content),
                'score': float(item.get('score') or 0),
                'evidence_ref': EvidenceRef(source_id=locator, source_version=version, locator=locator,
                                            trust_class='derived_memory', observed_at=str(item.get('updated_at') or ''),
                                            excerpt=excerpt).to_dict(),
            })
        return _layer('ok', revision=UNVERSIONED, reads=len(items),
                      canonical_provenance=sum(1 for e in out if e['provenance_ok'])), out, scrubbed


# ----------------------------------------------------------------------------- temporal: EventLog + claims
def _claim_rows(events: Iterable[MemoryEvent]) -> list[dict[str, Any]]:
    """Scoped supersession: within one (scope, subject, predicate) the latest recorded claim is current."""
    claims = [dict(e.payload, event_id=e.event_id, checksum=e.checksum) for e in events
              if e.event_type == 'memory.claim' and isinstance(e.payload, dict)]
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for c in claims:
        scope = c.get('scope') or {}
        key = (tuple(scope.get(k) for k in ('user', 'project', 'repo', 'task')), c.get('subject'), c.get('predicate'))
        groups.setdefault(key, []).append(c)
    for rows in groups.values():
        rows.sort(key=lambda c: (str(c.get('recorded_at')), c['event_id']))
        for older, newer in zip(rows, rows[1:]):
            older['superseded_by'] = older.get('superseded_by') or newer['claim_id']
        for c in rows:
            c['current'] = c is rows[-1]
    return claims


def _scope_of(raw: Any) -> MemoryScope | None:
    if not isinstance(raw, dict):
        return None
    return MemoryScope(**{k: raw.get(k) for k in ('user', 'project', 'repo', 'task')})


def _temporal_candidates(query: str, *, ledger_root: str | Path | None,
                         join_uids: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    log = EventLog(ledger_root, read_only=True)
    if not log.events_path.is_file():
        return _layer('ok', revision='empty', events=0), [], 0
    try:
        events = list(log.iter_events())
    except Exception as exc:  # noqa: BLE001 - a corrupt ledger is reported, not raised
        return _layer('unavailable', 'corrupt', detail=type(exc).__name__), [], 0
    tree = log.root / 'TREE' / 'manifest.json'
    try:
        tree_rev = json.loads(tree.read_text(encoding='utf-8')).get('coarse_history_hash') if tree.is_file() else None
    except (OSError, ValueError):
        tree_rev = None
    terms, out, scrubbed = _terms(query), [], 0
    claims = {c['event_id']: c for c in _claim_rows(events)}
    for e in events:
        ident = e.event_identity()
        text = json.dumps(e.payload, ensure_ascii=False, sort_keys=True).lower() if e.payload is not None else ''
        score = sum(1 for t in terms if t in text)
        joined = ident is not None and ident.event_uid in join_uids
        if not score and not joined:
            continue
        claim = claims.get(e.event_id)
        if claim is not None and not claim['current']:
            continue  # superseded claims stay in history(), never in current evidence
        if claim is not None:
            raw = f"{claim.get('subject')} {claim.get('predicate')} = {json.dumps(claim.get('value'))}"
            uid = derive_event_uid(source_system='z0-memory', source_session='claims', source_event_id=claim['claim_id'])
        else:
            raw = json.dumps(e.payload, ensure_ascii=False) if e.payload is not None else ''
            uid = ident.event_uid if ident else derive_event_uid(
                source_system=e.source, source_session=e.session_id or 'ledger', source_event_id=e.checksum[:32])
        clean, n = scrub_text(raw)
        scrubbed += n
        payload = e.payload if isinstance(e.payload, dict) else {}
        locator = f'eventlog:{e.event_id}'
        out.append({
            'event_uid': uid, 'layers': ['temporal'], 'source_system': ident.source_system if ident else e.source,
            'harness': HARNESS_OF_AGENT.get(ident.source_system, ident.source_system) if ident else None,
            'session_id': ident.source_session if ident else e.session_id,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e.ts)), 'locator': locator,
            'source_locator': payload.get('locator'), 'scope': (claim or payload).get('scope'),
            'excerpt': _excerpt(clean, terms), 'score': float(score), 'event_id': e.event_id,
            **({'claim_id': claim['claim_id'], 'subject': claim.get('subject'), 'predicate': claim.get('predicate'),
                'value': claim.get('value')} if claim else {}),
            'evidence_ref': EvidenceRef(source_id=locator, source_version=e.checksum[:16], locator=locator,
                                        trust_class='derived_memory', observed_at=time.strftime(
                                            '%Y-%m-%dT%H:%M:%SZ', time.gmtime(e.ts)),
                                        excerpt=_excerpt(clean, terms)).to_dict(),
        })
    return _layer('ok', revision=events[-1].checksum[:16] if events else 'empty', events=len(events),
                  reads=len(events), **({'optmem_tree': tree_rev} if tree_rev else {})), out, scrubbed


# ----------------------------------------------------------------------------- snapshot
def _repo_sha(repo: str | Path) -> str:
    try:
        proc = subprocess.run(['git', '--no-optional-locks', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 'unreadable'
    return proc.stdout.strip() if proc.returncode == 0 else 'unreadable'


def source_revisions(*, repo: str | Path | None = None, repo_sha: str | None = None, av_db: str | Path | None = None,
                     config: Mapping[str, Any] | None = None, ledger_root: str | Path | None = None,
                     av_timeout: float = 0.2, gateway: TencentDBClient | None = None,
                     probe_gateway: bool = True) -> dict[str, str]:
    """Cheap revision probe of every memory source: one ro query, one state-file read and the gateway revision
    (a bounded /health on ``gateway``'s budget, or with ``probe_gateway=False`` the last observed one, no
    network: the hook path)."""
    cfg = load_config() if config is None else config
    gateway = gateway or TencentDBClient(cfg)
    conn = agentsview_ro.connect(av_db, timeout=av_timeout)
    if conn:
        try:
            av = agentsview_generation(conn)
        except sqlite3.Error as exc:
            av = f'unavailable:{_unavailable_reason(exc)}'
        finally:
            conn.close()
    else:
        av = f'unavailable:{conn.reason}'
    state = EventLog(ledger_root, read_only=True).state_path
    try:
        ledger = str(json.loads(state.read_text(encoding='utf-8')).get('last_checksum') or 'empty')[:16]
    except (OSError, ValueError):
        ledger = 'empty'
    sha = repo_sha or (_repo_sha(repo) if repo is not None else 'none')
    tdb = gateway.revision() if probe_gateway else gateway.last_revision()
    return {'agentsview': av, 'tencentdb': tdb, 'eventlog': ledger, 'repo': sha}


def _snapshot_of(scope: MemoryScope | None, revisions: Mapping[str, str]) -> str:
    return MemorySnapshot.build(scope=scope or MemoryScope(), state_revision=POLICY_VERSION,
                                source_revisions=dict(revisions)).snapshot_id


def memory_snapshot_id(scope: MemoryScope | None = None, **kw: Any) -> str:
    """Content-addressed id of the memory view: changes iff a source revision (or the policy version) changes.
    An ``unversioned`` TencentDB gateway contributes no content revision (see ``UNVERSIONED``)."""
    return _snapshot_of(scope, source_revisions(**kw))


# ----------------------------------------------------------------------------- search
def _default_rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda e: -(e.get('score') or 0))


def search(query: str, policy: ScopePolicy | None = None, *, layers: Iterable[str] = LAYERS, limit: int = 8,
           ranker: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
           required: Iterable[str] = ('lexical',), av_db: str | Path | None = None, av_timeout: float = 0.5,
           ledger_root: str | Path | None = None, config: Mapping[str, Any] | None = None,
           snapshot_id: str | None = None, gateway: TencentDBClient | None = None) -> dict[str, Any]:
    """Resolve one query across the requested layers. Scope first, then ranking, then the limit.

    The gateway is only called when the semantic layer is requested, within one deadline for the whole query.
    """
    if not str(query or '').strip():
        raise ValueError('query is required')
    t0 = time.perf_counter()
    policy = policy or ScopePolicy()
    cfg = load_config() if config is None else config
    layers = tuple(layer for layer in LAYERS if layer in set(layers))
    gateway = gateway or TencentDBClient(cfg)
    status: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    scrubbed = 0
    if 'lexical' in layers:
        status['lexical'], found, n = _agentsview_candidates(query, policy, limit=limit, db=av_db, timeout=av_timeout)
        candidates += found
        scrubbed += n
    if 'semantic' in layers:
        status['semantic'], found, n = gateway.search(query, limit=limit)
        candidates += found
        scrubbed += n
    if 'temporal' in layers:
        status['temporal'], found, n = _temporal_candidates(
            query, ledger_root=ledger_root, join_uids={c['event_uid'] for c in candidates})
        candidates += found
        scrubbed += n
    admitted, out_of_scope = [], 0
    for c in candidates:
        if policy.admits(_scope_of(c.get('scope')), c.get('harness')):
            admitted.append(c)
        else:
            out_of_scope += 1
    merged: dict[str, dict[str, Any]] = {}
    by_hash: dict[str, str] = {}
    duplicates = 0
    for c in admitted:  # lexical first, then semantic, then temporal: the first record of an event is kept
        keys = [c['event_uid'], *c.get('source_event_ids', [])]
        hit = next((k for k in keys if k in merged), None) or by_hash.get(c.get('payload_hash') or '')
        if hit is not None:
            duplicates += 1
            kept = merged[hit]
            kept['layers'] = sorted(set(kept['layers']) | set(c['layers']))
            if c['layers'] == ['temporal']:
                kept.setdefault('ledger_locator', c['locator'])
            continue
        merged[c['event_uid']] = c
        if c.get('payload_hash'):
            by_hash.setdefault(c['payload_hash'], c['event_uid'])
    ranked = (ranker or _default_rank)(list(merged.values()))[:max(1, int(limit))]
    for layer, st in status.items():
        st['hits'] = sum(1 for e in ranked if layer in e['layers'])
    req = [r for r in required if r in layers]
    ok = any(s['status'] == 'ok' for s in status.values()) and all(status[r]['status'] == 'ok' for r in req)
    snap = snapshot_id or memory_snapshot_id(policy.scope, av_db=av_db, config=cfg, ledger_root=ledger_root,
                                             gateway=gateway, probe_gateway='semantic' in layers)
    result = {
        'schema': SCHEMA, 'ok': ok, 'query': query, 'evidence': ranked, 'layers': status,
        'duplicates_removed': duplicates, 'out_of_scope': out_of_scope, 'memory_snapshot_id': snap,
        'raw_reads': sum(s.get('reads', 0) for s in status.values()),
        'latency_ms': round((time.perf_counter() - t0) * 1000, 2),
    }
    result, n = scrub_obj(result)
    result['scrubbed'] = scrubbed + n
    return result


# ----------------------------------------------------------------------------- writes (z0 only) and claims
def worker_ledger(root: str | Path | None = None) -> EventLog:
    """The worker-facing view of the canonical ledger: reads work, every write raises PermissionError."""
    return EventLog(root, read_only=True)


_WRITERS: dict[Path, EventLog] = {}


def _writer(root: str | Path | None) -> EventLog:
    """One writable EventLog per ledger per process, so its uid map stays warm: bulk ingest reads each ledger
    line once instead of re-reading the ledger on every call."""
    path = (Path(root) if root is not None else paths.home() / 'memory').resolve()
    log = _WRITERS.get(path)
    if log is None:
        log = _WRITERS[path] = EventLog(path)
    else:
        log._ensure_layout()
    return log


def ingest_reference(identity: EventIdentity, locator: str, *, scope: MemoryScope | None = None,
                     ledger_root: str | Path | None = None) -> MemoryEvent:
    """Record a source event in the ledger as a reference (EventIdentity + locator; never a body)."""
    payload = {'locator': locator, **({'scope': scope.to_dict()} if scope else {})}
    source = locator.split(':', 1)[0] if ':' in locator else identity.source_system
    return _writer(ledger_root).append('source.reference', payload, source=source, identity=identity)


def record_claim(claim: BitemporalClaim, *, ledger_root: str | Path | None = None) -> MemoryEvent:
    payload, _ = scrub_obj(claim.to_dict())
    return _writer(ledger_root).append('memory.claim', payload, source='z0-memory')


def claim_history(subject: str, predicate: str | None = None, policy: ScopePolicy | None = None, *,
                  ledger_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Every recorded version of a claim, oldest first, with ``current`` and ``superseded_by``."""
    policy = policy or ScopePolicy()
    log = EventLog(ledger_root, read_only=True)
    events = list(log.iter_events()) if log.events_path.is_file() else []
    rows = [c for c in _claim_rows(events) if c.get('subject') == subject
            and (predicate is None or c.get('predicate') == predicate) and policy.admits(_scope_of(c.get('scope')), None)]
    rows.sort(key=lambda c: (str(c.get('recorded_at')), c['event_id']))
    return [{k: c.get(k) for k in ('claim_id', 'subject', 'predicate', 'value', 'status', 'observed_at',
                                   'recorded_at', 'scope', 'superseded_by', 'current', 'event_id')} for c in rows]


INJECTOR_MARKER_TTL_S = 86400.0


def prune_markers(d: Path, keep: Path, ttl_s: float = INJECTOR_MARKER_TTL_S) -> None:
    """Drop per-turn marker files older than ``ttl_s`` (long after any turn ends), keeping ``keep``."""
    cutoff = time.time() - ttl_s
    for old in d.iterdir():
        try:
            if old != keep and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def claim_injection(turn_key: str, owner: str) -> bool:
    """Single injection owner per turn (cross-process): the first owner to claim a turn keeps it. Markers older
    than a day (long after any turn ends) are pruned whenever a new turn is claimed."""
    d = paths.home() / 'state' / 'memory' / 'injectors'
    d.mkdir(parents=True, exist_ok=True)
    path = d / f'{_sha(str(turn_key))}.json'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            return json.loads(path.read_text(encoding='utf-8')).get('owner') == owner
        except (OSError, ValueError):
            return False
    prune_markers(d, path)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps({'owner': owner, 'at': time.time()}) + '\n')
    return True


# ----------------------------------------------------------------------------- brief + persistent cache
def _normalize(query: str) -> str:
    return ' '.join((query or '').split()).casefold()


def _cache_path(key: str) -> Path:
    return paths.home() / 'state' / 'memory' / 'brief_cache' / f'{key}.json'


def memory_brief(query: str, policy: ScopePolicy | None = None, *, max_tokens: int = 600,
                 required: Iterable[str] = ('lexical',), use_cache: bool = True, limit: int = 8,
                 config: Mapping[str, Any] | None = None, av_db: str | Path | None = None,
                 ledger_root: str | Path | None = None, layers: Iterable[str] = LAYERS) -> dict[str, Any]:
    """A bounded, scrubbed, provenance-carrying brief. ``use_cache=False`` skips the lookup but still stores.
    With the TencentDB gateway reachable (``unversioned``) the cache is bypassed: no lookup, no store.
    ``layers`` narrows the sources (a host that already injects TencentDB memory passes the other two)."""
    t0 = time.perf_counter()
    policy = policy or ScopePolicy()
    cfg = load_config() if config is None else config
    required = tuple(required)
    layers = tuple(layer for layer in LAYERS if layer in set(layers))
    gateway = TencentDBClient(cfg)  # one deadline budget for the snapshot probe and the search
    revisions = source_revisions(av_db=av_db, config=cfg, ledger_root=ledger_root, gateway=gateway,
                                 probe_gateway='semantic' in layers)
    snap = _snapshot_of(policy.scope, revisions)
    cacheable = 'semantic' not in layers or revisions['tencentdb'] != UNVERSIONED
    key = _sha('\x1f'.join((POLICY_VERSION, policy.key(), _normalize(query), str(max_tokens), ','.join(required),
                            ','.join(layers), snap)))
    if use_cache and cacheable:
        try:
            cached = json.loads(_cache_path(key).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            cached = None
        if isinstance(cached, dict) and cached.get('memory_snapshot_id') == snap:
            return {**cached, 'cache': 'hit'}
    res = search(query, policy, layers=layers, limit=limit, required=required, av_db=av_db, config=cfg,
                 ledger_root=ledger_root, snapshot_id=snap, gateway=gateway)
    gaps = [f"{layer}: unavailable ({st.get('reason')})" for layer, st in res['layers'].items()
            if st['status'] != 'ok']
    abstained = any(res['layers'].get(r, {}).get('status') != 'ok' for r in required)
    lines = [BRIEF_MARKER]
    used: list[dict[str, Any]] = []
    claims = [e for e in res['evidence'] if e.get('claim_id')]
    if abstained:
        lines.append('ABSTAIN: a required memory source is unavailable; no evidence is offered.')
        lines += [f'gap: {g}' for g in gaps]
    else:
        lines += [f'gap: {g}' for g in gaps]
        candidates = [(f"current: {c['subject']} {c['predicate']} = {json.dumps(c['value'])} ({c['claim_id']})", c)
                      for c in claims]
        candidates += [(f"- [{e.get('harness') or e['source_system']} {e.get('timestamp') or ''}] "
                        f"{(e.get('excerpt') or '')[:160]} ({e['locator']})", e)
                       for e in res['evidence'] if not e.get('claim_id')]
        if not candidates:
            lines.append('no evidence found')
        for line, item in candidates:
            if estimate_tokens('\n'.join([*lines, line])) > max_tokens:
                break
            lines.append(line)
            used.append(item)
    text, _ = scrub_text('\n'.join(lines))
    receipt = MemoryUseReceipt(
        snapshot_id=snap, capability_ids=tuple(k for k, v in res['layers'].items() if v['status'] == 'ok'),
        query_ids=(key,), included_claim_ids=tuple(c['claim_id'] for c in used if c.get('claim_id')),
        evidence_event_uids=tuple(e['event_uid'] for e in used),
        retrieval_latency_ms=round((time.perf_counter() - t0) * 1000, 2), input_tokens=estimate_tokens(text),
        raw_source_reads=res['raw_reads'])
    brief = {
        'schema': BRIEF_SCHEMA, 'query': query, 'text': text, 'tokens': estimate_tokens(text), 'max_tokens': max_tokens,
        'abstained': abstained, 'gaps': gaps, 'memory_snapshot_id': snap,
        'cache': 'miss' if cacheable else 'bypass:tencentdb_unversioned',
        'current_claims': [{k: c.get(k) for k in ('claim_id', 'subject', 'predicate', 'value')} for c in used
                           if c.get('claim_id')],
        'evidence': [e['locator'] for e in used], 'evidence_refs': [e['evidence_ref'] for e in used],
        'scrubbed': res['scrubbed'], 'receipt': receipt.to_dict(),
    }
    if used and not abstained and cacheable:  # an empty or abstained brief is never cached: an outage must not persist
        path = _cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(brief, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)
    return brief


# ----------------------------------------------------------------------------- read tools (MCP)
def inspect(locator: str, *, chars: int = 400, context: int = 1, av_db: str | Path | None = None,
            ledger_root: str | Path | None = None) -> dict[str, Any]:
    """Bounded, scrubbed read of one locator this surface produced; anything else is an explicit error."""
    m = re.fullmatch(r'agentsview:(.+)#(\d+)', locator or '')
    if m:
        conn = agentsview_ro.connect(av_db)
        if not conn:
            return {'ok': False, 'error': f'agentsview unavailable ({conn.reason})'}
        try:
            row = conn.execute('select m.session_id, m.ordinal, s.agent from messages m left join sessions s '
                               'on s.id = m.session_id where m.id = ? and m.session_id = ?',
                               (int(m.group(2)), m.group(1))).fetchone()
            rows = [] if row is None else conn.execute(
                'select ordinal, role, timestamp, content from messages where session_id = ? and ordinal between ? '
                'and ? order by ordinal', (row[0], row[1] - context, row[1] + context)).fetchall()
            findings = _finding_spans(conn, [row[0]]) if rows else {}
        except sqlite3.Error as exc:
            return {'ok': False, 'error': f'agentsview unavailable ({_unavailable_reason(exc)})'}
        finally:
            conn.close()
        if row is None:
            return {'ok': False, 'error': f'no such message {locator}'}
        # Scrub the whole message, then bound it: a credential cut by the bound would no longer match a pattern.
        out = {'ok': True, 'locator': locator, 'session_id': row[0], 'source_system': row[2],
               'harness': HARNESS_OF_AGENT.get(row[2], row[2]),
               'messages': [{'ordinal': o, 'role': r, 'timestamp': ts, 'target': o == row[1],
                             'text': _clean_message(content, findings.get((row[0], o), ()))[0][:chars]}
                            for o, r, ts, content in rows]}
        return scrub_obj(out)[0]
    m = re.fullmatch(r'eventlog:(\d+)', locator or '')
    if m:
        log = worker_ledger(ledger_root)
        try:
            event = log.get(int(m.group(1)), resolve_blob=True)
        except (KeyError, OSError):
            return {'ok': False, 'error': f'no such event {locator}'}
        return scrub_obj({'ok': True, 'locator': locator, 'event_type': event.event_type, 'source': event.source,
                          'identity': event.identity,
                          'payload': scrub_text(json.dumps(event.payload, ensure_ascii=False))[0][:chars]})[0]
    return {'ok': False, 'error': f'unsupported locator {locator!r}: expected agentsview:<sid>#<mid> or eventlog:<id>'}


def unknowns(query: str, policy: ScopePolicy | None = None, **kw: Any) -> dict[str, Any]:
    res = search(query, policy, **kw)
    gaps = [f"{layer}: unavailable ({st.get('reason')})" for layer, st in res['layers'].items() if st['status'] != 'ok']
    return {'ok': True, 'query': query, 'unresolved_gaps': gaps, 'answerable': res['ok'] and bool(res['evidence']),
            'evidence_count': len(res['evidence']), 'layers': res['layers'],
            'memory_snapshot_id': res['memory_snapshot_id']}


def verify(query: str, requires: Iterable[str] = (), policy: ScopePolicy | None = None, **kw: Any) -> dict[str, Any]:
    """USE iff every declared requirement is grounded in returned evidence; otherwise FALLBACK (kernel rule)."""
    res = search(query, policy, **kw)
    blob = ' '.join(f"{e['locator']} {e.get('excerpt') or ''}" for e in res['evidence']).lower()
    requires = [str(r) for r in requires]
    missing = [r for r in requires if r.lower() not in blob]
    verdict = ('USE' if not missing else 'FALLBACK') if requires else ('USE' if res['ok'] and res['evidence']
                                                                         else 'FALLBACK')
    return {'ok': True, 'query': query, 'verdict': verdict, 'requires': requires, 'missing': missing,
            'grounded': len(requires) - len(missing), 'evidence_count': len(res['evidence']),
            'memory_snapshot_id': res['memory_snapshot_id'],
            'rule': 'USE iff every declared requirement is grounded in returned evidence; otherwise FALLBACK'}


# ----------------------------------------------------------------------------- bench (z0int#22 measurement)
def bench(queries: Iterable[Mapping[str, Any]], *, config: Mapping[str, Any] | None = None,
          av_db: str | Path | None = None, ledger_root: str | Path | None = None) -> dict[str, Any]:
    """Per query: cold (resolver) and warm (snapshot cache) latency, raw source reads, brief tokens, hit."""
    queries, rows = list(queries), []
    for q in queries:
        scope = MemoryScope(user=DEFAULT_USER, project=q['project']) if q.get('project') else None
        policy = ScopePolicy(scope=scope, requester=q.get('harness'), cross_harness=q.get('cross_harness', True))
        kw = {'config': config, 'av_db': av_db, 'ledger_root': ledger_root}
        t0 = time.perf_counter()
        cold = memory_brief(q['query'], policy, use_cache=False, **kw)
        t1 = time.perf_counter()
        warm = memory_brief(q['query'], policy, **kw)
        t2 = time.perf_counter()
        expect = q.get('expect')
        rows.append({'query': q['query'], 'cold_ms': round((t1 - t0) * 1000, 3), 'warm_ms': round((t2 - t1) * 1000, 3),
                     'warm_cache': warm['cache'], 'raw_reads': cold['receipt'].get('raw_source_reads') or 0,
                     'tokens': cold['tokens'], 'abstained': cold['abstained'],
                     'hit': bool(expect) and any(str(loc).startswith(expect) for loc in cold['evidence'])})
    scored = [r for r, q in zip(rows, queries) if q.get('expect')]
    return {'schema': 'z0int.memory.bench.v0', 'queries': rows,
            'hit_rate': (sum(r['hit'] for r in scored) / len(scored)) if scored else None}
