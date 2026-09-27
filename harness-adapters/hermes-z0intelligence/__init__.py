"""Hermes native pre_llm_call adapter; no provider or routing policy here."""
import json
import os
import subprocess
import uuid
INSTANCE_ID=uuid.uuid4().hex

def invoke(operation,value):
    env={**os.environ,'PYTHONPATH':'/mnt/zer0models/workspace/zer0/oss/.work/z0intelligence/src'}
    result=subprocess.run(['/home/kvn/tmp/openjev/.venv/bin/python','-m','z0int.automatic',operation],
        input=json.dumps(value),text=True,capture_output=True,timeout=30,env=env,check=True)
    return json.loads(result.stdout)

def before_turn(session_id='',turn_id=None,user_message='',**kwargs):
    if not isinstance(user_message,str) or not user_message.strip():return None
    try:
        result=invoke('event',dict(harness='hermes',session_id=session_id or 'hermes',
            turn_id=str(turn_id) if turn_id is not None else uuid.uuid4().hex,instance_id=INSTANCE_ID,text=user_message))
        context={'context':result['context']} if result.get('action')=='context' else None
        if result.get('receipt_id'):
            invoke('consume',dict(harness='hermes',instance_id=INSTANCE_ID,receipt_id=result['receipt_id']))
        return context
    except Exception:
        return None

def register(ctx):
    ctx.register_hook('pre_llm_call',before_turn)
