"""Hermes capture child: what harness-adapters/hermes-z0intelligence hands to the installed z0int.

  python -m z0int.hermes_capture batch     # JSONL jobs on stdin, one ack line per job on stdout
  python -m z0int.hermes_capture project   # one opportunity build, holding an inherited build slot

The plugin runs inside Hermes, imports no z0int and keeps every hook to a dict build and a queue put. Its writer
thread starts one ``batch`` child at a time (so a turn's prompt is always processed before its outcome), and the
child writes the shared #62 records through ``harness_capture`` into ``$Z0INT_HOME/state/hermes/``:

  events.jsonl         z0int.hermes.event.v0   one content-free row per hook call (the observer log, #385)
  opportunities.jsonl  opportunity_record.v0   DecisionOpportunity + gate, built by a ``project`` child
  outcomes.jsonl       turn_outcome.v0         observed behaviour of the turn (never an optimal label)

A projection (1-3 s) never runs in the batch child: it takes one of ``harness_capture.MAX_CHILDREN`` build slots
without waiting and runs detached, or is refused and counted (drops.jsonl, reason ``fanout_cap``), so event
writes never queue behind it (A4). Shell tool calls keep only their check class and exit status; the command and
output reach this process in memory and are never written.
"""

import contextlib
import fcntl
import json
import os
import subprocess
import sys

from . import check_class
from . import harness_capture as hc
from .harness_id import _hermes_turn

HARNESS = 'hermes'
EVENT_SCHEMA = hc.schema(HARNESS, 'event')
PACKET_ADAPTERS = ('git', 'docs')  # as bend #385: the task repo only


def _trace(job):
    session, turn = str(job.get('session_id') or ''), job.get('turn_id')
    return _hermes_turn(session, str(turn)) if turn not in (None, '') else None


def _ids(job):
    """The hook coordinates as the capture core reads them (Hermes's ``<session>:<turn>`` spelling)."""
    return {'session_id': job.get('session_id'), 'turn_id': _trace(job), 'model': job.get('model'),
            'policy_revision': job.get('policy_revision')}


def _event(job, root=None, **parts):
    trace = _trace(job)
    row = {'schema': EVENT_SCHEMA, 'harness': HARNESS, 'event': job.get('event'), 'session_id': job.get('session_id'),
           'trace_id': trace, 'turn_key': hc.turn_key(HARNESS, job.get('session_id'), trace) if trace else None,
           'recorded_at': hc._now(), **{k: v for k, v in parts.items() if v not in (None, {}, [])}}
    for key in ('fields', 'usage', 'subagent'):
        if job.get(key):
            row[key] = job[key]
    hc._write_line(hc.state_dir(HARNESS, root) / 'events.jsonl', row)


def _spawn_project(job, slot):
    """Detached projection child; it inherits the locked ``slot`` file, so the slot is held until it exits."""
    child = subprocess.Popen([sys.executable, '-m', 'z0int.hermes_capture', 'project'], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             pass_fds=(slot.fileno(),))
    child.stdin.write(json.dumps(job, default=str).encode())
    child.stdin.close()


@contextlib.contextmanager
def plugin_gate(path):
    """Yields True while the plugin that asked for this build is loaded (P-2: nothing is written after close).

    ``path`` is the plugin's per-instance gate file. A build holds it shared while it writes and then appends one
    settle line; the plugin's close() takes it exclusively, counts the builds that never settled as dropped and
    unlinks it, so a build that finishes later finds it gone and writes nothing. No path: not gated."""
    if not path:
        yield True
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError:
        yield False
        return
    with os.fdopen(fd, 'a') as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        live = os.fstat(fh.fileno()).st_nlink > 0
        try:
            yield live
        finally:
            if live:
                fh.write('1\n')


def project(job, root=None):
    """One opportunity build. The State Packet reads the task repo's git and docs only (never another harness's
    transcript store) and keeps its own snapshot (latest.json, history.jsonl) only with persist_packet_text (P-9).
    The build is tied to the plugin's lifetime through its gate: after the plugin closed, nothing is written."""
    gate = job.get('gate')
    if gate and not os.path.exists(gate):  # closed before the build started: skip it
        return None
    keep = bool(job.get('persist_packet_text'))
    no_transcripts = hc.state_dir(HARNESS, root) / 'no-transcripts'  # never created: the revision probe scans nothing
    try:
        record, error = hc.opportunity_record(HARNESS, job['payload'], job['ctx'],
                                              packet_text='opt_in' if keep else 'redacted',
                                              packet_args={'adapters': PACKET_ADAPTERS, 'projects_root': no_transcripts,
                                                           'use_cache': keep, 'store': keep}), None
    except Exception as exc:
        record, error = None, type(exc).__name__
    with plugin_gate(gate) as live:
        if not live:
            return None
        if record is None:
            hc.record_failure(HARNESS, 'capture_error', ctx=job.get('ctx'), root=root,
                              detail={'job': 'opportunity', 'error': error})
            return None
        return hc.record_built_opportunity(HARNESS, record, root=root)


def _turn(job, root):
    """Record the turn; 'p' when a projection child was started for it."""
    payload = dict(_ids(job), user_message=job.get('text') or '', cwd=job.get('cwd'))
    ctx = hc.begin_turn(HARNESS, payload, root=root, cohort=job.get('cohort'))
    _event(job, root, cohort=job.get('cohort'), excluded=job.get('excluded'))
    if ctx is None or not job.get('opportunity'):
        return
    slot = hc.try_slot(root)
    if slot is None:
        hc.record_drop(HARNESS, 'opportunity_record', 'fanout_cap', root=root)
        return
    with slot:
        _spawn_project({'payload': payload, 'ctx': ctx, 'persist_packet_text': job.get('persist_packet_text'),
                        'gate': job.get('gate')}, slot)
    return 'p'  # the plugin counts the builds it must account for at close


def _outcome(job, root):
    ctx = hc.outcome_context(HARNESS, _ids(job), root=root)
    hc.record_outcome(HARNESS, ctx, job.get('behaviour') or {}, ended=job.get('ended') or 'completed', root=root,
                      extra=job.get('extra'))
    _event(job, root, ended=job.get('ended'))


def _tool(job, root):
    tool = dict(job.get('tool') or {})
    if isinstance(job.get('command'), str):
        tool.update(check_class.classify(job['command'], job.get('exit'), job.get('out_tail') or ''))
    else:
        tool.update(check_class=None, exit=job.get('exit'), piped=False)
    _event(job, root, tool=tool)


def process(job, root=None):
    kind = job.get('kind')
    if kind == 'turn':
        return _turn(job, root)
    if kind == 'outcome':
        _outcome(job, root)
    elif kind == 'tool':
        _tool(job, root)
    else:  # session / event
        if kind == 'session':
            hc.note_session(HARNESS, _ids(job), root=root)
        _event(job, root)


def batch(lines, out, root=None):
    """Process each job in order; ack each one with a line (the plugin counts what was never acked as dropped).
    The ack is ``p`` when a projection child was started for the job, else ``.``."""
    capture = hc.enabled()
    for line in lines:
        try:
            job = json.loads(line)
        except ValueError:
            job = None
        ack = '.'
        if capture and isinstance(job, dict):
            try:
                ack = process(job, root) or '.'
            except Exception as exc:  # a broken row never stops the batch; it is a failure row, not a silent loss
                hc.record_failure(HARNESS, 'capture_error', root=root,
                                  detail={'job': str(job.get('kind')), 'error': type(exc).__name__})
        out.write(ack + '\n')
        out.flush()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['batch']:
        batch(sys.stdin, sys.stdout)
    elif argv == ['project']:
        project(json.loads(sys.stdin.read()))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
