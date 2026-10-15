"""Remote-only, stateless bounded executor; canonical persistence belongs to authority."""
import argparse
import json
import os
import secrets
import sys
import urllib.request
import urllib.error
import time
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from .dispatch_authority import validate_remote
from .worker_routing import execute_plan


def request(operation, payload):
    base=os.environ.get('Z0INT_AUTHORITY_URL','http://127.0.0.1:11501')
    req=urllib.request.Request(base+'/v1/authority/'+operation,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    attempts=12 if operation in ('receipt','release','complete') else 1
    deadline=time.monotonic()+15
    for attempt in range(attempts):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Authority persistence deadline exceeded')
        try:
            with urllib.request.urlopen(req,timeout=min(3,remaining)) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            if operation=='claim' and error.code==400:
                raise ValueError('Authority rejected request identity or execution policy') from error
            if error.code not in (500,502,503,504) or attempt+1==attempts:raise
        except (OSError,json.JSONDecodeError):
            if attempt+1==attempts:raise
        # Spread concurrent completions across the bounded authority connection pool.
        time.sleep(min(max(0,deadline-time.monotonic()),random.uniform(.05,.15)*2**min(attempt,3)))


def execute(args):
    owner=secrets.token_hex(32)
    common={'request':args,'owner':owner,'protocol_version':3}
    claim=request('claim',common)
    if not claim['claimed']:return claim['result']
    admission_id=claim.get('aodl_admission_receipt_id')
    worker,policy,providers,plan=validate_remote(args)
    if args['provider'] not in os.environ.get('Z0INT_EXECUTOR_PROVIDERS','openrouter').split(','):
        raise ValueError('Provider not enabled on this executor')
    if not os.environ.get(providers[args['provider']]['api_key_env']):
        raise ValueError('Existing provider credential unavailable')
    class RemoteAdmission:
        def acquire(self,provider,call_id,context):
            return request('acquire',{**common,'call_id':call_id})
        def release(self,permit,status,latency_ms,retry_after=None,attempted=True):
            return request('release',{**common,'token':permit['token'],'http_status':status,'latency_ms':latency_ms,'retry_after':retry_after,'attempted':attempted})
    index=0
    def sink(row):
        nonlocal index
        value=row.to_dict() if hasattr(row,'to_dict') else row
        # A missing acknowledgement aborts execution. Never re-claim or retry the model.
        saved=request('receipt',{**common,'index':index,'receipt':value})
        index+=1
        return saved
    plan={**plan,'harness':args['harness'],'caller_trace_id':args['trace_id']}
    result=execute_plan(worker,policy,providers,plan,receipt_sink=sink,
        admission=RemoteAdmission(),receipt_location=os.environ.get('Z0INT_AUTHORITY_URL','http://127.0.0.1:11501')+'/canonical-receipts')
    result={**result,'trace_id':args['trace_id']}
    if admission_id:result['aodl_admission_receipt_id']=admission_id
    return request('complete',{**common,'result':result})


SLOTS=threading.BoundedSemaphore(4)
class Server(ThreadingHTTPServer):
    daemon_threads=False
    request_queue_size=8
    def process_request(self,request,address):
        if not SLOTS.acquire(False):
            try:request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nRetry-After: 1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally:self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except BaseException:SLOTS.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:SLOTS.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):super().setup();self.connection.settimeout(30)
    def log_message(self,*args):pass
    def reply(self,status,value):
        body=json.dumps(value).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/healthz':return self.reply(200,{'ok':True})
        if self.path!='/readyz':return self.reply(404,{'ok':False})
        try:
            from .worker_routing import configuration, free_required, free_route
            policy,providers=configuration()
            enabled=os.environ.get('Z0INT_EXECUTOR_PROVIDERS','openrouter').split(',')
            with urllib.request.urlopen(os.environ['Z0INT_AUTHORITY_URL']+'/v1/providers',timeout=2) as r:
                state=json.load(r)
            if state.get('authority_protocol_version')!=3 or state.get('aodl_admission_ready') is not True:
                raise ValueError('authority protocol/AODL admission mismatch')
            ready=any(os.environ.get(providers[p]['api_key_env']) and state['providers'][p]['available']
                      and (not free_required(policy) or free_route(policy,p,policy['defaults'].get(p)))
                      and (not state.get('free_only') or policy['defaults'].get(p) in state.get('validated_free_models',{}).get(p,[])) for p in enabled)
            self.reply(200 if ready else 503,{'ok':bool(ready),'scope':'credential and authority capacity; inference not asserted'})
        except Exception:self.reply(503,{'ok':False})
    def do_POST(self):
        if self.path!='/v1/execute':return self.reply(404,{'ok':False})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=131072:return self.reply(413,{'ok':False})
            self.reply(200,execute(json.loads(self.rfile.read(size))))
        except ValueError:self.reply(400,{'ok':False,'error':'invalid_remote_request'})
        except Exception:self.reply(503,{'ok':False,'execution_status':'uncertain','reason':'Reconcile the identical request; never mint a new trace to retry execution'})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['serve','once'])
    args=parser.parse_args()
    if args.mode=='once':print(json.dumps(execute(json.load(sys.stdin))));return
    server=Server((os.environ.get('POD_IP','127.0.0.1'),11503),Handler)
    import signal
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever()
    finally:server.server_close()

if __name__=='__main__':main()
