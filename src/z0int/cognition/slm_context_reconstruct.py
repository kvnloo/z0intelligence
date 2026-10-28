"""Read-only, branch-aware pre-call goal context from exact native OMP traces."""
from collections import deque
import json
from pathlib import Path
import re


def bounded(text, limit):
    return text.strip().encode('utf-8')[:limit].decode('utf-8','ignore') or None


def events(row):
    msg=row.get('message') or {}
    content=msg.get('content',[])
    if isinstance(content,str):
        content=[{'type':'text','text':content}]
    if msg.get('role')=='assistant':
        for item in content:
            if isinstance(item,dict) and item.get('type')=='toolCall':
                yield item.get('id'),item.get('name'),item.get('arguments')
    if row.get('type')=='custom' and row.get('customType')=='tool_execution_start':
        data=row.get('data') or {}
        yield data.get('toolCallId'),data.get('toolName'),data.get('args') or data.get('arguments')


def context_before(rows, target):
    user=None; assistant=None; history=deque(maxlen=5); seen=set()
    for row in rows:
        msg=row.get('message') or {}
        content=msg.get('content',[])
        if isinstance(content,str):
            content=[{'type':'text','text':content}]
        if msg.get('role')=='user':
            user=bounded('\n'.join(item.get('text','') for item in content if isinstance(item,dict) and item.get('type')=='text'),600)
        if msg.get('role')=='assistant':
            for item in content:
                if not isinstance(item,dict):
                    continue
                if item.get('type')=='toolCall' and item.get('id')==target:
                    break
                if item.get('type')=='text':
                    sentences=re.split(r'(?<=[.!?])\s+',item.get('text','').strip())
                    assistant=bounded(sentences[-1],300) if sentences else assistant
        for cid,tool,args in events(row):
            if cid==target:
                return {'latest_user_message':user,'last_assistant_sentence':assistant,
                        'previous_tools':list(history),'sources':{'latest_user_message':'native-session-jsonl-ancestor-chain','last_assistant_sentence':'native-session-jsonl-ancestor-chain'}}
            if not cid or not tool or cid in seen:
                continue
            seen.add(cid)
            intent=args.get('i') if isinstance(args,dict) else None
            history.append({'tool':bounded(tool,120),'intent':bounded(intent,160) if isinstance(intent,str) else None})
    return None


def reconstruct(root, targets):
    wanted=set(targets); sessions={s for s,c in wanted}; result={}
    for path in Path(root).rglob('*.jsonl'):
        with path.open() as stream:
            header=[]
            for _ in range(8):
                try:
                    header.append(json.loads(next(stream)))
                except (ValueError,StopIteration):
                    continue
        sid=next((r.get('id') for r in header if r.get('type')=='session'),None)
        if sid not in sessions:
            continue
        rows=[]
        with path.open() as stream:
            for line in stream:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        by_id={r['id']:r for r in rows if r.get('id')}
        linear=not any('parentId' in r for r in rows)
        for index,row in enumerate(rows):
            for cid,tool,args in events(row):
                key=(sid,cid)
                if key not in wanted or key in result:
                    continue
                chain=[row]; parent=row.get('parentId'); visited=set()
                while parent:
                    if parent in visited or parent not in by_id:
                        chain=[]; break
                    visited.add(parent); ancestor=by_id[parent]
                    chain.append(ancestor)
                    if 'parentId' not in ancestor and ancestor.get('type') != 'session':
                        chain=[]; break
                    parent=ancestor.get('parentId')
                # Only a wholly parentless file is a linear format.
                if 'parentId' not in row:
                    chain=rows[:index+1] if linear else []
                else:
                    chain.reverse()
                value=context_before(chain,cid)
                if value is not None:
                    result[key]=value
        if len(result)==len(wanted):
            break
    return result
