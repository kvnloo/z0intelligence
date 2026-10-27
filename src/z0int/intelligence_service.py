"""Bounded host intelligence service; share one host authority, not model replicas."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import signal
import time
import sys
from collections import Counter
from .intelligence import dispatch, route, routing_snapshot, REGISTRY
from .automatic import dispatch_event, consume
from .decision_experiment import run_choice_experiment, run_noul_experiment
from .reliability_observation import ingest_observation
from .agentweb_context_packet import compile_agentweb_context_packet
from .agentweb_bridge_wire import unwrap_agentweb_bridge_request, wrap_agentweb_bridge_response
from .agentweb_bridge_capabilities import (
    MAX_ACTIVE,
    SOCKET_BACKLOG,
    agentweb_bridge_capabilities,
)
from .outcome_observation import ingest_outcome_observation
from .outcome_coverage import ingest_outcome_expectation, summarize_outcome_coverage
from .outcome_randomized_evidence import ingest_outcome_assignment, summarize_randomized_outcome_evidence
from .verified_event_ingress import ingest_verified_event_batch

SLOTS=threading.BoundedSemaphore(MAX_ACTIVE)
METRIC_LOCK=threading.Lock()
METRIC_TYPES={
    'admitted_connections_total':'counter', 'rejected_connections_total':'counter',
    'dispatch_active':'gauge', 'dispatch_peak':'gauge',
    'dispatch_finished_total':'counter', 'dispatch_new_total':'counter',
    'dispatch_replayed_total':'counter', 'dispatch_ok_total':'counter',
    'dispatch_failed_total':'counter', 'dispatch_seconds_sum':'counter',
}
METRICS=Counter({name:0 for name in METRIC_TYPES})
DRAINING=threading.Event()
PROVIDERS_TTL_SECONDS=30
PROVIDERS_LOCK=threading.Lock()
PROVIDERS_RESPONSE=None
PROVIDERS_EXPIRES=0.0


def aodl_admission_state():
    try:
        import aodl_contract
        version=getattr(aodl_contract,'CANON_VERSION',None)
        return {'aodl_admission_ready':version=='aodl-canon-1','aodl_canon_version':version}
    except Exception as exc:
        return {'aodl_admission_ready':False,'aodl_canon_version':None,'aodl_error':type(exc).__name__}


def governed_worker_state():
    import os
    if os.environ.get('Z0INT_GOVERNED_REMOTE') != '1':
        return {'governed_remote_enabled':False,'governed_remote_ready':False}
    try:
        from .governed_worker import load_contract
        import aodl_contract
        doc=load_contract()
        return {
            'governed_remote_enabled':True,
            'governed_remote_ready':True,
            'governed_contract_graph_id':doc.get('graphId'),
            'governed_contract_revision':doc.get('revision'),
            'governed_contract_fingerprint':aodl_contract.semantic_fingerprint(doc),
            'governed_contract_source_hash':doc.get('provenance',{}).get('sourceHash'),
        }
    except Exception as exc:
        return {
            'governed_remote_enabled':True,
            'governed_remote_ready':False,
            'governed_remote_error':type(exc).__name__,
        }


def providers_response():
    """Cache the encoded discovery response for 30s, sharing concurrent misses."""
    global PROVIDERS_RESPONSE, PROVIDERS_EXPIRES
    with PROVIDERS_LOCK:
        if PROVIDERS_RESPONSE is not None and time.monotonic()<PROVIDERS_EXPIRES:
            return PROVIDERS_RESPONSE
        from .provider_saturation import policy,snapshot
        config=policy()
        states={p:{k:v for k,v in snapshot(p,config).items() if k!='held_tokens'} for p in config['provider_caps']}
        value={
            'authority_protocol_version':3,
            'supported_authority_protocol_versions':[2,3],
            **aodl_admission_state(),**governed_worker_state(),
            'providers':states,
            'free_only':config.get('free_only',False),
            'validated_free_models':{
                p:[e['model'] for e in config.get('validated_free_routes',[])
                   if e['provider']==p and e.get('validated') and e.get('price_usd')==0]
                for p in states},
        }
        data=json.dumps(value,allow_nan=False).encode()
        PROVIDERS_RESPONSE=data
        PROVIDERS_EXPIRES=time.monotonic()+PROVIDERS_TTL_SECONDS
        return data


def metric(name, amount=1):
    with METRIC_LOCK: METRICS[name] += amount


def metrics_text():
    with METRIC_LOCK: values=dict(METRICS)
    from .provider_saturation import metrics_text as provider_metrics
    return provider_metrics()+"".join(f"# TYPE z0intelligence_{name} {METRIC_TYPES[name]}\nz0intelligence_{name} {value}\n" for name,value in sorted(values.items()))



class Service(ThreadingHTTPServer):
    daemon_threads=False
    request_queue_size=SOCKET_BACKLOG

    def process_request(self, request, address):
        if DRAINING.is_set() or not SLOTS.acquire(blocking=False):
            metric("rejected_connections_total")
            try:request.sendall(
                b'HTTP/1.1 503 Service Unavailable\r\n'
                b'Retry-After: 1\r\n'
                b'X-Z0-Execution: not_started\r\n'
                b'Content-Length: 0\r\n'
                b'Connection: close\r\n\r\n'
            )
            except (BrokenPipeError,ConnectionResetError):pass
            finally:self.shutdown_request(request)
            return
        metric("admitted_connections_total")
        try:super().process_request(request,address)
        except Exception:
            SLOTS.release();raise

    def process_request_thread(self, request, address):
        try:super().process_request_thread(request,address)
        finally:SLOTS.release()

    def handle_error(self, request, address):
        if not isinstance(sys.exc_info()[1],(BrokenPipeError,ConnectionResetError)):
            super().handle_error(request,address)


def plan_intelligence(args):
    """Pure shadow wrapper: route against a snapshot without dispatch or receipt writes."""
    selected=route(args,routing_snapshot(args))
    return {'ok':True,'mode':'shadow','executed':False,'route':selected}


def resolve_file_search(args):
    """Repository evidence through the shared resolver, never worker dispatch."""
    from pathlib import Path
    from .context_resolve import InformationNeed, append_context_packet_event, resolve_context

    fields = {'repository_root', 'query', 'kind', 'harness', 'trace_id', 'session_id',
              'limit', 'time_budget_ms'}
    if not isinstance(args, dict) or set(args) - fields:
        raise ValueError('invalid file-search fields')

    def text(name, maximum):
        value = args.get(name)
        if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
            raise ValueError(f'invalid {name}')
        return value

    root_raw = text('repository_root', 4096)
    query = text('query', 2048)
    harness = text('harness', 32)
    if harness not in {'omp', 'hermes', 'codex', 'claude-code', 'dsh'}:
        raise ValueError('unsupported harness')
    trace_id = text('trace_id', 256) if args.get('trace_id') is not None else None
    session_id = text('session_id', 256) if args.get('session_id') is not None else None
    kind = args.get('kind', 'natural_language')
    if kind not in ('exact_path', 'exact_symbol', 'natural_language'):
        raise ValueError('unsupported kind')
    for name, default, minimum, maximum in (
        ('limit', 5, 1, 20), ('time_budget_ms', 120, 10, 1000),
    ):
        value = args.get(name, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'invalid {name}')
    try:
        root = Path(root_raw)
        if not root.is_absolute():
            raise ValueError('repository_root must be absolute')
        root = root.resolve(strict=True)
        if not root.is_dir() or root == Path(root.anchor):
            raise ValueError('repository_root must be a bounded directory')
    except (OSError, RuntimeError) as exc:
        raise ValueError('invalid repository_root') from exc
    if kind == 'exact_path':
        try:
            target = (root / query).resolve()
            target.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError('exact_path must stay within repository_root') from exc
        query = str(target)
    need = InformationNeed(
        id='file-search', description=query, kind=kind,
        path=query if kind == 'exact_path' else None,
        symbol=query if kind == 'exact_symbol' else None,
    )
    packet = resolve_context(
        needs=[need], task_id=trace_id, project_root=root,
        allow_fff=True, allow_qmd=False, allow_memory=False, use_cache=False,
        repository_limit=args.get('limit', 5),
        repository_time_budget_ms=args.get('time_budget_ms', 120),
    )
    event_id = None
    event_error = None
    try:
        event = append_context_packet_event(
            packet, source=f"harness:{harness}", project=str(root), session_id=session_id,
        )
        event_id = event.event_id
    except Exception as exc:  # Preserve evidence but never claim it was recorded.
        event_error = type(exc).__name__
    unavailable = kind != 'exact_path' and packet.measurements['fff_status'] not in {'ready', 'warming'}
    out = {
        'ok': not unavailable, 'schema': 'z0int.harness_context.v1',
        'harness_id': harness, 'event_id': event_id,
        'packet': packet.to_dict(),
    }
    if event_error:
        out['event_log_error'] = event_error
    if unavailable:
        out.update(error='file_search_unavailable', instruction=(
            'Repository search unavailable; report the gap and use an explicit '
            'bounded local search fallback. Retrieval is not authorization.'
        ))
    return out


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(30)

    def log_message(self,*args):pass

    def reply(self,status,value):
        return self.reply_bytes(status,json.dumps(value,allow_nan=False).encode())

    def reply_bytes(self,status,data,content_type='application/json'):
        try:
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):
            self.close_connection=True

    def do_GET(self):
        if self.path=='/healthz':return self.reply(200,{'ok':True})
        if self.path=='/v1/bridge/capabilities':return self.reply(200,agentweb_bridge_capabilities())
        if self.path=='/v1/observe/outcome/coverage':return self.reply(200,summarize_outcome_coverage())
        if self.path=='/v1/observe/outcome/randomized-evidence':return self.reply(200,summarize_randomized_outcome_evidence())
        if self.path=='/v1/providers':return self.reply_bytes(200,providers_response())
        if self.path=='/metrics':
            return self.reply_bytes(200,metrics_text().encode(),'text/plain; version=0.0.4')
        if self.path=='/readyz':
            if DRAINING.is_set():return self.reply(503,{'ok':False,'draining':True})
            try:
                registry=json.loads(REGISTRY.read_text());assert registry['entries']
                aodl=aodl_admission_state();governed=governed_worker_state()
                if not aodl['aodl_admission_ready']:raise ValueError('AODL admission unavailable')
                if governed['governed_remote_enabled'] and not governed['governed_remote_ready']:
                    raise ValueError('governed remote contract unavailable')
                return self.reply(200,{'ok':True,'authority_protocol_version':3,'supported_authority_protocol_versions':[2,3],**aodl,**governed,'scope':'dispatch ready; model availability checked on call','max_active':4,'socket_backlog':8,'queue_policy':'reject excess with 503'})
            except Exception:return self.reply(503,{'ok':False})
        self.reply(404,{'error':'not_found'})

    def do_POST(self):
        if not self.path.startswith('/v1/authority/') and self.path not in ('/v1/file-search', '/v1/intelligence', '/v1/worker', '/v1/automatic', '/v1/automatic/consumed', '/v1/integrations/agent-orchestrator/spawn-decision', '/v1/integrations/agent-orchestrator/outcome', '/v1/plan', '/v1/experimental/choice', '/v1/experimental/noul', '/v1/observe/reliability', '/v1/observe/outcome', '/v1/observe/outcome/expectation', '/v1/observe/outcome/assignment', '/v1/observe/event-batch', '/v1/context/pack', '/v1/governed-worker', '/v1/agentweb', '/v1/agentweb/observation'):return self.reply(404,{'error':'not_found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            max_size = 262144 if self.path.startswith('/v1/authority/') else (48000 if self.path=='/v1/agentweb' else (8000 if self.path=='/v1/agentweb/observation' else 40000))
            if not 0<size<=max_size:return self.reply(413,{'error':'request_size'})
            args=json.loads(self.rfile.read(size))
            if self.path=='/v1/file-search':return self.reply(200,resolve_file_search(args))
            bridge_envelope=None
            if self.path in ('/v1/intelligence','/v1/plan','/v1/experimental/choice','/v1/experimental/noul'):
                bridge_envelope,args=unwrap_agentweb_bridge_request(self.path,args)
            if self.path.startswith('/v1/authority/'):
                from .dispatch_authority import rpc
                return self.reply(200,rpc(self.path.removeprefix('/v1/authority/'),args))
            if self.path=='/v1/worker':
                from .worker_routing import dispatch_worker
                return self.reply(200,dispatch_worker(args))
            if self.path=='/v1/integrations/agent-orchestrator/spawn-decision':
                from .ao_bridge import spawn_decision
                return self.reply(200,spawn_decision(args))
            if self.path=='/v1/integrations/agent-orchestrator/outcome':
                from .ao_bridge import join_ao_outcome
                return self.reply(200,join_ao_outcome(args))
            if self.path=='/v1/governed-worker':
                from .governed_worker import execute as execute_governed_worker
                return self.reply(200,execute_governed_worker(args))
            if self.path=='/v1/agentweb':
                from .agentweb_bridge import handle_bridge_request
                return self.reply(200,handle_bridge_request(args))
            if self.path=='/v1/agentweb/observation':
                from .agentweb_bridge import join_agentweb_observation
                return self.reply(200,join_agentweb_observation(args))
            if self.path=='/v1/automatic/consumed':return self.reply(200,consume(args))
            if self.path=='/v1/observe/reliability':return self.reply(200,ingest_observation(args))
            if self.path=='/v1/observe/outcome':return self.reply(200,ingest_outcome_observation(args))
            if self.path=='/v1/observe/outcome/expectation':return self.reply(200,ingest_outcome_expectation(args))
            if self.path=='/v1/observe/outcome/assignment':return self.reply(200,ingest_outcome_assignment(args))
            if self.path=='/v1/observe/event-batch':return self.reply(200,ingest_verified_event_batch(args))
            if self.path=='/v1/context/pack':return self.reply(200,compile_agentweb_context_packet(args))
            if self.path=='/v1/plan':
                result=plan_intelligence(args)
                return self.reply(200,wrap_agentweb_bridge_response(bridge_envelope,result))
            started=time.monotonic()
            with METRIC_LOCK:
                METRICS['dispatch_active']+=1
                METRICS['dispatch_peak']=max(METRICS['dispatch_peak'],METRICS['dispatch_active'])
            try:
                result=(dispatch_event(args) if self.path=='/v1/automatic' else (run_choice_experiment(args) if self.path=='/v1/experimental/choice' else (run_noul_experiment(args) if self.path=='/v1/experimental/noul' else dispatch(args))))
                metric('dispatch_replayed_total' if result.get('replayed') else 'dispatch_new_total')
                metric('dispatch_ok_total' if result.get('ok') else 'dispatch_failed_total')
            finally:
                metric('dispatch_active',-1)
                metric('dispatch_seconds_sum',time.monotonic()-started)
                metric('dispatch_finished_total')
            self.reply(200,wrap_agentweb_bridge_response(bridge_envelope,result))
        except (BrokenPipeError,ConnectionResetError):self.close_connection=True
        except (ValueError,TypeError):self.reply(400,{'error':'invalid_request_or_trace_conflict'})
        except Exception:self.reply(500,{'error':'dispatch_failed'})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind',action='append',default=[])
    parser.add_argument('--port',type=int,default=11501)
    args=parser.parse_args()
    hosts=args.bind or ['127.0.0.1']
    if any(h in ['0.0.0.0','::'] for h in hosts):parser.error('Use a specific trusted host interface; public wildcard binding is not supported')
    from .provider_saturation import startup_probe
    probe=startup_probe()
    print(json.dumps({'event':'default_provider_boot_check',**probe,'severity':'info' if probe['ok'] else 'WARNING'}),flush=True)
    servers=[Service((host,args.port),Handler) for host in hosts]
    def stop(*_):
        DRAINING.set()
        for server in servers:threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    for server in servers[:-1]:threading.Thread(target=server.serve_forever,daemon=True).start()
    try:servers[-1].serve_forever()
    except KeyboardInterrupt:pass
    finally:
        for server in servers:server.server_close()


if __name__=='__main__':main()
