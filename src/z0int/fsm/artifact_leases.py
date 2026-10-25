"""Law G: audit supplied artifact observations; never delete, archive, or grant authority.

Sources are explicit caller observations, not authenticated by this finite guard.
The reaper must independently re-observe them immediately before any action.
"""
from __future__ import annotations

import math
from pathlib import PurePosixPath

SCHEMA = 'z0.artifact-lease.v1'
REQUIRED = ('expired', 'allowlisted', 'known_nonlive', 'no_symlink', 'archive_ready')
SOURCE_FIELDS = ('lease', 'clock', 'allowlist', 'live', 'symlink', 'archive')


def evaluate(facts: dict) -> str:
    """Unknown is never permission. Mirrors bend/artifact_lease_fsm/lease.bend."""
    if not isinstance(facts, dict) or any(type(facts.get(k)) is not bool for k in REQUIRED):
        return 'unknown'
    return 'pass' if all(facts[k] for k in REQUIRED) else 'violation'


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _absolute(value):
    return isinstance(value, str) and value.startswith('/') and '..' not in PurePosixPath(value).parts


def _sources(value):
    return (isinstance(value, dict) and all(
        isinstance(value.get(key), str) and bool(value[key].strip()) for key in SOURCE_FIELDS))


def inspect(candidate: dict) -> dict:
    """Evaluate one source-bound candidate. Pass means obligations only, not delete approval.

    paths must be canonical absolute paths observed without traversing symlinks;
    symlink_free covers every ancestor as well as the leaf. live is one of LIVE,
    NONLIVE, UNKNOWN. Archive verification must cover this exact worktree snapshot.
    """
    facts = {}
    reason = 'Missing or malformed source-bound observations'
    if isinstance(candidate, dict) and _sources(candidate.get('sources')):
        lease = candidate.get('lease')
        path = candidate.get('path')
        now = candidate.get('observed_at')
        roots = candidate.get('allowlist')
        valid = (isinstance(lease, dict) and lease.get('schema') == SCHEMA
                 and all(isinstance(lease.get(k), str) and lease[k].strip() for k in ('owner', 'node', 'kind'))
                 and _absolute(path) and lease.get('path') == path
                 and _number(now) and now >= 0 and _number(lease.get('created_at'))
                 and 0 <= lease['created_at'] <= now and _number(lease.get('ttl')) and lease['ttl'] > 0
                 and isinstance(lease.get('keep_receipts'), list)
                 and all(isinstance(p, str) and p not in ('', '.') and not PurePosixPath(p).is_absolute()
                         and '..' not in PurePosixPath(p).parts for p in lease['keep_receipts'])
                 and isinstance(roots, list) and all(_absolute(root) for root in roots)
                 and candidate.get('node') == lease.get('node'))
        if valid:
            live = candidate.get('live')
            worktree = candidate.get('git_worktree')
            archive = candidate.get('git_archive_verified')
            facts = {
                'expired': now >= lease['created_at'] + lease['ttl'],
                'allowlisted': any(PurePosixPath(path) != PurePosixPath(root)
                                   and PurePosixPath(root) in PurePosixPath(path).parents for root in roots),
                'known_nonlive': True if live == 'NONLIVE' else False if live == 'LIVE' else None,
                'no_symlink': candidate.get('symlink_free'),
                'archive_ready': (True if worktree is False else archive if worktree is True else None),
            }
            reason = 'All deletion obligations required; a pass is not authority to act'
    status = evaluate(facts)
    return {'id': candidate.get('path') if isinstance(candidate, dict) else None,
            'law': 'G', 'status': status, 'blocked': status != 'pass',
            'reason': reason, 'facts': facts,
            'sources': candidate.get('sources', {}) if isinstance(candidate, dict) else {},
            'escalate_to': 'Kevin' if status == 'unknown' else None}


def audit(spec: dict) -> dict:
    candidates = spec.get('artifacts') if isinstance(spec, dict) else None
    checks = [inspect(row) for row in candidates] if isinstance(candidates, list) and candidates else [inspect({})]
    counts = {state: sum(row['status'] == state for row in checks) for state in ('pass', 'violation', 'unknown')}
    return {'schema': 'z0.artifact-lease-guard.v1', 'mode': 'report-only',
            'enforcement_enabled': False, 'law': 'G', 'checks': checks, 'counts': counts,
            'status': 'violation' if counts['violation'] else 'unknown' if counts['unknown'] else 'pass',
            'source': spec.get('source') if isinstance(spec, dict) else None,
            'action': 'escalate_to_Kevin' if counts['unknown'] else 'alert' if counts['violation'] else 'none'}


def register(subparsers):
    """Register a read-only audit command; malformed input remains unknown."""
    parser = subparsers.add_parser('artifact-leases', help='Report Law G lease obligations; never act')
    parser.add_argument('--input', required=True)
    parser.set_defaults(fn=_command)


def _command(args):
    import json
    from pathlib import Path
    try:
        spec = json.loads(Path(args.input).read_text())
    except (OSError, ValueError):
        spec = {}
    print(json.dumps(audit(spec), sort_keys=True))
    return 0


def main(argv=None):
    """Standalone reachable report-only CLI, independent of shared registration."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    return _command(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
