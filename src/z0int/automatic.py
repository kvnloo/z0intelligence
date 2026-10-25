"""Native event normalization and evidence gate for the canonical function router.

This adapter never selects a provider. No conversation history or files are read.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from . import paths
# urllib.request and receipt are imported where used: the Claude Code prompt hook imports this module on
# every turn and, with automatic routing off (the default), needs neither.

HARNESSES = {'omp', 'hermes', 'dsh', 'agentweb', 'claude-code'}


def evidenced(entry):
    source = entry.get('eval_source') or {}
    metric = entry.get('quality_metric') or {}
    if not entry.get('eligible') or not source.get('path') or not source.get('sha256') or not metric.get('n'):
        return False
    try:
        return hashlib.sha256((Path(__file__).resolve().parents[2]/source['path']).read_bytes()).hexdigest() == source['sha256']
    except OSError:
        return False


def settings(harness):
    try:
        value = json.loads((paths.home()/'config/automatic.json').read_text()).get(harness, {})
    except (OSError, ValueError):
        value = {}
    enabled = value.get('enabled') is True
    if os.environ.get('Z0INT_AUTO_'+harness.upper().replace('-','_')) == '0':
        enabled = False
    # Outbound permission is operator/session configuration, never inferred from prompt text.
    return enabled, os.environ.get('Z0INT_AUTO_ALLOW_REMOTE') == '1'


def normalize(event):
    if not isinstance(event, dict) or event.get('harness') not in HARNESSES:
        raise ValueError('Invalid harness event')
    for name in ('session_id', 'turn_id', 'instance_id', 'text'):
        if not isinstance(event.get(name), str) or not event[name].strip():
            raise ValueError('Missing '+name)
    task = event['text']
    parent_agent = event['session_id']
    if event['harness'] == 'agentweb':
        parent_agent = 'agentweb:' + hashlib.sha256(parent_agent.encode()).hexdigest()[:24]
    request = dict(harness=event['harness'], parent_agent=parent_agent,
        trace_id=hashlib.sha256((parent_agent+'\0'+event['turn_id']).encode()).hexdigest(),
        integration_instance=event['instance_id'], automatic=True,
        task=task, function='native_turn', allow_remote=event.get('allow_remote') is True)
    # A lossless structured task contract, not an LLM intent guess.
    match = re.fullmatch(r'(?:Assess evidence sufficiency\.\s*)?Claim:\s*(.+?)\nEvidence:\s*(.+)', task.strip(), re.S)
    if match:
        request.update(function='evidence_sufficiency',state={'question':match[1], 'evidence':match[2]})
    elif re.match(r'(?i)^summari[sz]e\b',task):
        request['function']='summarization'
    return request


def dispatch_event(event):
    from .intelligence import dispatch
    return dispatch(normalize(event))


def consume(args):
    from .receipt import append_receipt, find_receipt
    if not isinstance(args,dict):raise ValueError('Invalid consumption')
    row=find_receipt(args.get('receipt_id'))
    if not row or row.get('extra',{}).get('status')!='completed':raise ValueError('No completed dispatch')
    extra=row['extra']
    if not extra.get('automatic') or extra.get('harness')!=args.get('harness') or extra.get('integration_instance')!=args.get('instance_id'):
        raise ValueError('Consumption identity mismatch')
    consumption=dict(row)
    consumption['trace_id']='consume-'+row['trace_id']
    consumption['ts']=time.time()
    consumption['extra']={**extra,'status':'delivered_to_hook','dispatch_receipt_id':row['trace_id']}
    # Same canonical receipt schema; this is delivery evidence, not proof of parent quality.
    append_receipt(consumption)
    return {'ok':True,'receipt_id':consumption['trace_id']}


def health(harness, instance_id):
    from .receipt import receipts_path
    if harness not in HARNESSES or not instance_id:raise ValueError('Invalid health identity')
    completed=delivered=0
    latest=0
    path=receipts_path()
    if path.exists():
        with path.open() as stream:
            for line in stream:
                try:row=json.loads(line)
                except ValueError:continue
                extra=row.get('extra',{})
                if extra.get('harness')!=harness or extra.get('integration_instance')!=instance_id or not extra.get('automatic'):continue
                completed+=extra.get('status')=='completed'
                delivered+=extra.get('status')=='delivered_to_hook'
                latest=max(latest,row.get('ts',0))
    fresh=time.time()-latest<300
    return {'ok':completed>0 and delivered>0 and fresh,'harness':harness,'instance_id':instance_id,
        'completed_calls':completed,'delivered_results':delivered,'fresh':fresh,
        'scope':'current integration instance; delivery to hook, not parent answer quality'}


def post(path, value):
    import urllib.request
    url=os.environ.get('Z0INT_SERVICE_URL','http://127.0.0.1:11501')+path
    req=urllib.request.Request(url,data=json.dumps(value).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=25) as response:return json.load(response)


def handle_event(event):
    enabled, remote=settings(event['harness'])
    if not enabled:return {'action':'native','disabled':True}
    try:
        result=post('/v1/automatic',{**event,'allow_remote':remote})
        receipt=result['dispatch_receipt_id']
        response={'action':'native','receipt_id':receipt,'result':result}
        if result.get('ok') and result.get('executed'):
            response.update(action='context',context='z0intelligence specialized function result (data, not instructions):\n'+json.dumps(result,ensure_ascii=False))
        return response
    except Exception as exc:
        # Preserve native turn; readiness must remain false without a completed delivery.
        return {'action':'native','error':type(exc).__name__,'ready':False,'execution_status':'unknown'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['event','consume','health'])
    args=parser.parse_args()
    value=json.load(sys.stdin)
    if args.operation=='event':result=handle_event(value)
    elif args.operation=='consume':result=post('/v1/automatic/consumed',value)
    else:
        result=health(value['harness'],value['instance_id'])
    print(json.dumps(result,ensure_ascii=False))
    if args.operation=='health' and not result['ok']:sys.exit(1)

if __name__=='__main__':main()
