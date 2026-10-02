import socket
import threading
import urllib.request
import urllib.error
import time
from z0int.intelligence_service import Service, Handler


def test_health_and_bounded_admission():
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    address=server.server_address
    sockets=[]
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{address[1]}/readyz') as r:assert r.status==200
        for _ in range(4):
            s=socket.create_connection(address);s.sendall(b'POST /v1/intelligence HTTP/1.0\r\nContent-Length: 1\r\n\r\n');sockets.append(s)
        time.sleep(.1)
        with socket.create_connection(address) as s:
            s.sendall(b'GET /healthz HTTP/1.0\r\n\r\n')
            assert b'503 Service Unavailable' in s.recv(1000)
    finally:
        for s in sockets:s.close()
        server.shutdown();server.server_close();thread.join()


def test_metrics_track_real_dispatch_and_replay(tmp_path,monkeypatch):
    import json
    from z0int.intelligence_service import METRICS, METRIC_LOCK, metrics_text
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    with METRIC_LOCK:
        before=METRICS['dispatch_finished_total']
        replays=METRICS['dispatch_replayed_total']
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_address[1]}'
        data=json.dumps({'harness':'test','trace_id':'metrics-test','parent_agent':'parent','function':'cheap_bounded_worker','task':'2+2','expected_parent_tokens':10,'expected_parent_ms':100}).encode()
        for _ in range(2):
            with urllib.request.urlopen(urllib.request.Request(url+'/v1/intelligence',data=data,headers={'Content-Type':'application/json'})) as response:
                result=json.load(response)
                assert result['ok'] and not result['executed']
        with urllib.request.urlopen(url+'/metrics') as response:text=response.read().decode()
        assert f'z0intelligence_dispatch_finished_total {before+2}\n' in text
        assert f'z0intelligence_dispatch_replayed_total {replays+1}\n' in text
        assert 'z0intelligence_dispatch_active 0\n' in text
        assert '# TYPE z0intelligence_dispatch_active gauge' in text
    finally:server.shutdown();server.server_close();thread.join()

def test_readyz_requires_aodl_canon_v1(monkeypatch):
    import json
    import z0int.intelligence_service as svc
    monkeypatch.setattr(svc,'aodl_admission_state',lambda:{'aodl_admission_ready':False,'aodl_canon_version':None})
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_address[1]}/readyz'
        try:
            urllib.request.urlopen(url)
            assert False,'readyz unexpectedly succeeded without AODL'
        except urllib.error.HTTPError as exc:
            assert exc.code==503
    finally:server.shutdown();server.server_close();thread.join()


def test_readyz_reports_protocol_v3_with_aodl(monkeypatch):
    import json
    import z0int.intelligence_service as svc
    monkeypatch.setattr(svc,'aodl_admission_state',lambda:{'aodl_admission_ready':True,'aodl_canon_version':'aodl-canon-1'})
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}/readyz') as response:
            body=json.load(response)
        assert body['authority_protocol_version']==3
        assert body['supported_authority_protocol_versions']==[2,3]
        assert body['aodl_admission_ready'] is True
        assert body['aodl_canon_version']=='aodl-canon-1'
    finally:server.shutdown();server.server_close();thread.join()

def test_readyz_fails_when_enabled_governed_contract_is_unavailable(monkeypatch):
    import z0int.intelligence_service as svc
    monkeypatch.setattr(svc,'aodl_admission_state',lambda:{'aodl_admission_ready':True,'aodl_canon_version':'aodl-canon-1'})
    monkeypatch.setattr(svc,'governed_worker_state',lambda:{'governed_remote_enabled':True,'governed_remote_ready':False,'governed_remote_error':'ValueError'})
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}/readyz')
            assert False,'readyz unexpectedly succeeded with broken governed contract'
        except urllib.error.HTTPError as exc:
            assert exc.code==503
    finally:server.shutdown();server.server_close();thread.join()


def test_readyz_reports_governed_contract_identity(monkeypatch):
    import json
    import z0int.intelligence_service as svc
    monkeypatch.setattr(svc,'aodl_admission_state',lambda:{'aodl_admission_ready':True,'aodl_canon_version':'aodl-canon-1'})
    monkeypatch.setattr(svc,'governed_worker_state',lambda:{
        'governed_remote_enabled':True,
        'governed_remote_ready':True,
        'governed_contract_graph_id':'z0int-governed-worker-canary',
        'governed_contract_revision':1,
        'governed_contract_fingerprint':'aodl-canon-1:'+'a'*64,
        'governed_contract_source_hash':'0'*64,
    })
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}/readyz') as response:
            body=json.load(response)
        assert body['governed_remote_enabled'] is True
        assert body['governed_remote_ready'] is True
        assert body['governed_contract_graph_id']=='z0int-governed-worker-canary'
        assert body['governed_contract_revision']==1
    finally:server.shutdown();server.server_close();thread.join()


def test_governed_worker_endpoint_delegates_to_host_owned_adapter(monkeypatch):
    import json
    import z0int.governed_worker as governed
    captured={}
    def fake_execute(args):
        captured.update(args)
        return {'ok':True,'trace_id':args['trace_id'],'governed_remote':True}
    monkeypatch.setattr(governed,'execute',fake_execute)
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        payload={'harness':'omp','trace_id':'service-canary','parent_agent':'session','task':'public text','max_tokens':64,'allow_remote':True}
        req=urllib.request.Request(
            f'http://127.0.0.1:{server.server_address[1]}/v1/governed-worker',
            data=json.dumps(payload).encode(),
            headers={'Content-Type':'application/json'},
        )
        with urllib.request.urlopen(req) as response:body=json.load(response)
        assert body=={'ok':True,'trace_id':'service-canary','governed_remote':True}
        assert captured==payload
    finally:server.shutdown();server.server_close();thread.join()



def test_agentweb_bridge_endpoint_is_explicit_and_separate(monkeypatch):
    import json
    from z0int import agentweb_bridge

    monkeypatch.setattr(
        agentweb_bridge,
        "handle_bridge_request",
        lambda args: {
            "ok": True,
            "protocol_version": "agentweb.z0.bridge.v1",
            "mode": "shadow",
            "executed": False,
        },
    )
    server=Service(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_address[1]}'
        data=json.dumps({"protocol_version":"fixture"}).encode()
        request=urllib.request.Request(
            url+'/v1/agentweb',
            data=data,
            headers={'Content-Type':'application/json'},
        )
        with urllib.request.urlopen(request) as response:
            result=json.load(response)
            assert response.status==200
            assert result["mode"]=="shadow"
            assert result["executed"] is False
    finally:
        server.shutdown();server.server_close();thread.join()
