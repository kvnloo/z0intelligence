"""Bounded host intelligence service; share one host authority, not model replicas."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import signal
import time
from collections import Counter
from .intelligence import dispatch, REGISTRY
from .automatic import dispatch_event, consume

SLOTS=threading.BoundedSemaphore(4)
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


def aodl_admission_state():
    try:
        import aodl_contract
        version=getattr(aodl_contract,'CANON_VERSION',None)
        return {'aodl_admission_ready':version=='aodl-canon-1','aodl_canon_version':version}
    except Exception as exc:
        return {'aodl_admission_ready':False,'aodl_canon_version':None,'aodl_error':type(exc).__name__}


def metric(name, amount=1):
    with METRIC_LOCK: METRICS[name] += amount


def metrics_text():
    with METRIC_LOCK: values=dict(METRICS)
    from .provider_saturation import metrics_text as provider_metrics
    return provider_metrics()+"".join(f"# TYPE z0intelligence_{name} {METRIC_TYPES[name]}\nz0intelligence_{name} {value}\n" for name,value in sorted(values.items()))



class Service(ThreadingHTTPServer):
    daemon_threads=False
    request_queue_size=8

    def process_request(self, request, address):
        if DRAINING.is_set() or not SLOTS.acquire(blocking=False):
            metric("rejected_connections_total")
            try:request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nRetry-After: 1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally:self.shutdown_request(request)
            return
        metric("admitted_connections_total")
        try:super().process_request(request,address)
        except Exception:
            SLOTS.release();raise

    def process_request_thread(self, request, address):
        try:super().process_request_thread(request,address)
        finally:SLOTS.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(30)

    def log_message(self,*args):pass

    def reply(self,status,value):
        data=json.dumps(value,allow_nan=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

    def do_GET(self):
        if self.path=='/healthz':return self.reply(200,{'ok':True})
        if self.path=='/v1/providers':
            from .provider_saturation import policy,snapshot
            config=policy()
            states={p:{k:v for k,v in snapshot(p).items() if k!='held_tokens'} for p in policy()['provider_caps']}
            aodl=aodl_admission_state()
            return self.reply(200,{'authority_protocol_version':3,'supported_authority_protocol_versions':[2,3],**aodl,'providers':states,
                'free_only':config.get('free_only',False),
                'validated_free_models':{p:[e['model'] for e in config.get('validated_free_routes',[]) if e['provider']==p and e.get('validated') and e.get('price_usd')==0] for p in states}})
        if self.path=='/metrics':
            data=metrics_text().encode()
            self.send_response(200);self.send_header('Content-Type','text/plain; version=0.0.4');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        if self.path=='/readyz':
            if DRAINING.is_set():return self.reply(503,{'ok':False,'draining':True})
            try:
                registry=json.loads(REGISTRY.read_text());assert registry['entries']
                aodl=aodl_admission_state()
                if not aodl['aodl_admission_ready']:raise ValueError('AODL admission unavailable')
                return self.reply(200,{'ok':True,'authority_protocol_version':3,'supported_authority_protocol_versions':[2,3],**aodl,'scope':'dispatch ready; model availability checked on call','max_active':4,'socket_backlog':8,'queue_policy':'reject excess with 503'})
            except Exception:return self.reply(503,{'ok':False})
        self.reply(404,{'error':'not_found'})

    def do_POST(self):
        if not self.path.startswith('/v1/authority/') and self.path not in ('/v1/intelligence','/v1/worker','/v1/automatic','/v1/automatic/consumed'):return self.reply(404,{'error':'not_found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=(262144 if self.path.startswith('/v1/authority/') else 40000):return self.reply(413,{'error':'request_size'})
            args=json.loads(self.rfile.read(size))
            if self.path.startswith('/v1/authority/'):
                from .dispatch_authority import rpc
                return self.reply(200,rpc(self.path.removeprefix('/v1/authority/'),args))
            if self.path=='/v1/worker':
                from .worker_routing import dispatch_worker
                return self.reply(200,dispatch_worker(args))
            if self.path=='/v1/automatic/consumed':return self.reply(200,consume(args))
            started=time.monotonic()
            with METRIC_LOCK:
                METRICS['dispatch_active']+=1
                METRICS['dispatch_peak']=max(METRICS['dispatch_peak'],METRICS['dispatch_active'])
            try:
                result=(dispatch_event(args) if self.path=='/v1/automatic' else dispatch(args))
                metric('dispatch_replayed_total' if result.get('replayed') else 'dispatch_new_total')
                metric('dispatch_ok_total' if result.get('ok') else 'dispatch_failed_total')
            finally:
                metric('dispatch_active',-1)
                metric('dispatch_seconds_sum',time.monotonic()-started)
                metric('dispatch_finished_total')
            self.reply(200,result)
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
