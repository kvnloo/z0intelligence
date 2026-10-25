"""Deterministic, identity-bound ChoiceLadder discovery and admitted execution.

Configuration: $Z0INT_HOME/config/choice-ladder.json. The timer calls ``tick``;
workers execute ``measure``. No inference runs in the timer process.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import socket
import struct
import subprocess
import sys
import time
import urllib.request

from .. import paths
from .compile import CompilerConfig, compile_policy, option_capacity_key, write_policy
from .findings import append_findings, canonical, digest, ledger_path, locked, read_findings, timestamp


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def gguf_metadata(path):
    """Read GGUF v2/v3 metadata only, without mapping model tensors."""
    with Path(path).open('rb') as stream:
        def unpack(fmt):
            size = struct.calcsize(fmt)
            data = stream.read(size)
            if len(data) != size:
                raise ValueError('Truncated GGUF metadata')
            return struct.unpack(fmt, data)[0]
        def string():
            size = unpack('<Q')
            if size > 64 * 1024 * 1024:
                raise ValueError('Oversized GGUF metadata string')
            value = stream.read(size)
            if len(value) != size:
                raise ValueError('Truncated GGUF string')
            return value.decode('utf-8')
        def value(kind):
            formats = {0:'<B', 1:'<b', 2:'<H', 3:'<h', 4:'<I', 5:'<i', 6:'<f', 7:'<?', 10:'<Q', 11:'<q', 12:'<d'}
            if kind in formats:
                return unpack(formats[kind])
            if kind == 8:
                return string()
            if kind == 9:
                subtype, count = unpack('<I'), unpack('<Q')
                if count > 10000000:
                    raise ValueError('Oversized GGUF array')
                for _ in range(count):
                    value(subtype)
                return None
            raise ValueError('Unsupported GGUF metadata type')
        if stream.read(4) != b'GGUF' or unpack('<I') not in (2, 3):
            raise ValueError('Expected GGUF v2/v3')
        unpack('<Q')
        count = unpack('<Q')
        if count > 100000:
            raise ValueError('Oversized GGUF metadata')
        result = {}
        for _ in range(count):
            key = string()
            result[key] = value(unpack('<I'))
        return result


QUANTS = {0:'F32', 1:'F16', 2:'Q4_0', 3:'Q4_1', 7:'Q8_0', 8:'Q5_0', 9:'Q5_1',
          10:'Q2_K', 11:'Q3_K_S', 12:'Q3_K_M', 13:'Q3_K_L', 14:'Q4_K_S',
          15:'Q4_K_M', 16:'Q5_K_S', 17:'Q5_K_M', 18:'Q6_K', 32:'BF16'}


def discover(config):
    """Aliases collapse by bytes; new weights always receive a new model scope."""
    candidates = set()
    registry = Path(config['registry']).expanduser()
    if registry.exists():
        for endpoint in json.loads(registry.read_text()).get('endpoints', []):
            if endpoint.get('model_path'):
                candidates.add(Path(endpoint['model_path']).expanduser().resolve())
            elif endpoint.get('served_model'):
                # The farm uses the exact served id as the GGUF basename.
                for directory in config['model_dirs']:
                    candidate = Path(directory).expanduser() / (endpoint['served_model'] + '.gguf')
                    if candidate.exists():
                        candidates.add(candidate.resolve())
    for directory in config['model_dirs']:
        candidates.update(p.resolve() for p in Path(directory).expanduser().glob('*.gguf'))
    models = {}
    for path in sorted(candidates):
        metadata = gguf_metadata(path)
        sha = file_sha(path)
        quant = QUANTS.get(metadata.get('general.file_type'))
        if quant is None:
            raise ValueError(f'Unsupported GGUF quantization: {path}')
        architecture = metadata.get('general.architecture')
        # Protocol-v2 templates are deliberately not guessed for other families.
        template = {'qwen2':'qwen25', 'qwen3':'qwen3'}.get(architecture)
        models[sha] = {'id':'sha256:' + sha, 'sha256':sha, 'path':str(path),
                       'quantization':quant, 'template':template, 'architecture':architecture}
    return list(models.values())


def scope_for(model, lane):
    return {'model':model['id'], 'quant':model['quantization'], 'engine':'llama.cpp',
            'backend':lane['backend'], 'device':lane['device']}


def due(model, lane, findings, now, refresh_hours):
    scope = scope_for(model, lane)
    expiry = {}
    for row in findings:
        if (row['subject']['family'] != 'choice.option_capacity' or
                row['metric']['name'] != 'choice_accuracy' or row['evidence_class'] != 'measured' or
                row.get('synthetic', False) or row['arm'].get('model_sha256') != model['sha256'] or
                any(row['arm'].get(k) != v for k, v in scope.items()) or timestamp(row['ts']) > now):
            continue
        task = row['subject'].get('task')
        # Match compiler's maximum evidence age, not just the producer's expiry.
        expires = min(timestamp(row['expires_at']), timestamp(row['ts']) + timedelta(days=14))
        expiry[task] = max(expiry.get(task, expires), expires)
    return any(expiry.get(task, now) <= now + timedelta(hours=refresh_hours) for task in ('T1','T2','T3','T4'))


def wilson(correct, n):
    p = correct / n
    z = 1.959963984540054
    denominator = 1 + z*z/n
    center = (p + z*z/(2*n))/denominator
    radius = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/denominator
    return center-radius, center+radius


def needs_more(correct, n, bar=.95, initial=100, cap=400):
    return n < initial or (n < cap and correct/n >= bar and wilson(correct,n)[0] < bar)


def tick(home, config, now=None, launch=subprocess.Popen):
    if not 100 <= config.get('cap',400) <= 400:
        raise ValueError('Adaptive cap must be in [100,400]')
    if not 0 < config.get('bar',.95) < 1:
        raise ValueError('Adaptive bar must be in (0,1)')
    now = now or datetime.now(timezone.utc)
    directory = home / 'policy' / 'choice-ladder'
    directory.mkdir(parents=True, exist_ok=True)
    events = []
    with locked(directory / 'dispatch.lock'):
        findings = read_findings(ledger_path(home))
        for model in discover(config):
            if model['template'] is None:
                events.append({'status':'unsupported_template', 'model':model})
                continue
            # One configured lane is selected deterministically, never an LLM.
            lane = config['lane']
            key = digest(scope_for(model,lane))
            state_path = directory / (key + '.json')
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            if not due(model,lane,findings,now,config.get('refresh_hours',48)):
                continue
            if state.get('unit') and subprocess.run(
                    ['systemctl','--user','is-active','--quiet',state['unit']],check=False).returncode == 0:
                continue
            if state.get('pid') and not state.get('unit'):
                try:
                    os.kill(state['pid'],0)
                    continue
                except ProcessLookupError:
                    pass
            retry = state.get('retry_after')
            if retry and timestamp(retry) > now:
                continue
            request = directory / (key + '.request.json')
            payload = {'model':model, 'lane':lane, 'config':config, 'home':str(home),
                       'scope':scope_for(model,lane), 'requested_at':now.isoformat()}
            request.write_bytes(canonical(payload) + b'\n')
            log_path = directory / (key + '.log')
            with log_path.open('ab') as log:
                command = [sys.executable,'-m','z0int.policy.autoeval','dispatch','--request',str(request)]
                unit = None
                if config.get('dispatcher','systemd') == 'systemd':
                    unit = 'z0int-choice-' + key
                    environment = ['--setenv=Z0INT_HOME='+str(home)]
                    if os.environ.get('PYTHONPATH'):
                        environment.append('--setenv=PYTHONPATH='+os.environ['PYTHONPATH'])
                    command = ['systemd-run','--user','--collect','--unit='+unit,
                               '--property=Nice=15',*environment,'--',*command]
                process = launch(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            state = {'pid':process.pid,'unit':unit,'status':'enqueued','request':str(request),
                     'retry_after':(now + timedelta(hours=config.get('retry_hours',6))).isoformat()}
            state_path.write_bytes(canonical(state) + b'\n')
            events.append({**state,'scope':payload['scope']})
    return events


def dispatch(request):
    payload = json.loads(request.read_text())
    config, lane = payload['config'],payload['lane']
    root = Path(config['toolkit']).expanduser().resolve()
    output = request.parent / (request.stem + '.result')
    output.mkdir(exist_ok=True)
    staged = output / 'request.json'
    staged.write_bytes(canonical(payload) + b'\n')
    if lane['backend'] == 'cuda':
        command = ['groot-admit','--need-mb',str(lane.get('need_mb',4096)),'--wait',str(lane.get('wait',3600)),
                   '--',sys.executable,'-m','z0int.policy.autoeval','measure','--request',str(staged),'--out',str(output)]
        subprocess.run(command,check=True)
    else:
        # Ship the installed package and toolkit, not an uncommitted repository.
        package = Path(__file__).resolve().parents[1]
        import shutil
        shutil.copytree(package,output/'z0int',dirs_exist_ok=True)
        (output/'toolkit').mkdir(exist_ok=True)
        for name in ('benchmark.py','items.jsonl'):
            shutil.copyfile(root/name,output/'toolkit'/name)
        shutil.copyfile(config['router_corpus'],output/'router-corpus.json')
        shutil.copyfile(config['router_harness'],output/'router-harness.py')
        remote = json.loads(staged.read_text())
        remote['config']['toolkit'] = 'toolkit'
        remote['config']['router_corpus'] = 'router-corpus.json'
        remote['config']['router_harness'] = 'router-harness.py'
        remote['model']['path'] = 'weights.gguf'
        staged.write_bytes(canonical(remote) + b'\n')
        command = ['fleet-build','--node','mbp','--class','bench','--mem',lane.get('mem','4G'),
                   '--cpus','4','--wait','-1','--timeout','14400','--src',str(output),
                   '--input',payload['model']['path']+':weights.gguf','--fetch','measurement.json','--out',str(output/'fetched'),
                   '--','python3','-m','z0int.policy.autoeval','measure','--request','request.json','--out','.']
        subprocess.run(command,check=True)
        shutil.copyfile(output/'fetched'/'measurement.json',output/'measurement.json')
    receipt = output/'measurement.json'
    measurement = json.loads(receipt.read_text())
    if measurement['scope'] != payload['scope'] or measurement['model_sha256'] != payload['model']['sha256']:
        raise ValueError('Worker result identity mismatch')
    expected = {(task,k) for task in ('T1','T2') for k in (2,3,5,8,12,16,24,32,64)}
    expected.update(('T3',k) for k in (2,3,5,8,12,16))
    expected.add(('T4',10))
    cells = measurement['cells']
    if len(cells)!=len(expected) or {(c['task'],c['k']) for c in cells} != expected:
        raise ValueError('Worker returned an incomplete or duplicate ladder')
    for cell in cells:
        if (type(cell['n']) is not int or not 100 <= cell['n'] <= config.get('cap',400) or
                type(cell['correct']) is not int or not 0 <= cell['correct'] <= cell['n']):
            raise ValueError('Invalid worker sample counts')
        cell['ci_low'],cell['ci_high'] = wilson(cell['correct'],cell['n'])
    home = Path(payload['home'])
    findings = []
    for cell in measurement['cells']:
        findings.append({'schema':'z0.finding.v1','ts':measurement['started_at'],
                        'expires_at':measurement['expires_at'],'source':{'kind':'z0eval_result','path':str(receipt.resolve()),'sha256':file_sha(receipt)},
                        'subject':{'family':'choice.option_capacity','job':'ChoiceLadder','task':cell['task']},
                        'arm':{'kind':'slm',**payload['scope'],'model_sha256':payload['model']['sha256']},
                        'metric':{'name':'choice_accuracy','value':cell['correct']/cell['n'],'n':cell['n'],
                                  'answered':cell['n'],'abstained':0,'denominator':cell['n'],
                                  'correct':cell['correct'],'option_count':cell['k'],'ci_low':cell['ci_low'],'ci_high':cell['ci_high'],
                                  'ci_method':'wilson95','higher_is_better':True,'scoring_version':2},
                        'evidence_class':'measured','baseline':{},'synthetic':False,
                        'scope_note':'Adaptive protocol-v2 choice benchmark; fixed label encoding, no thinking. Repeated looks use a fixed-n final Wilson interval, not a sequential coverage guarantee. Not live task success.'})
    append_findings(findings,ledger_path(home))
    rows = read_findings(ledger_path(home))
    previous_path = home/'policy'/'decision-policy.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    policy = compile_policy(rows,previous,datetime.now(timezone.utc),CompilerConfig(compiler_revision=config['compiler_revision']))
    write_policy(policy,home)
    print(json.dumps({'status':'compiled','scope':payload['scope'],'receipt':str(receipt),
                      'capacity':policy['option_capacity'].get(option_capacity_key(payload['scope']))}),flush=True)


def measure(request, out):
    payload = json.loads(request.read_text())
    config,model,lane = payload['config'],payload['model'],payload['lane']
    root = Path(config['toolkit']).expanduser().resolve()
    if file_sha(root/'benchmark.py') != config['benchmark_sha256']:
        raise ValueError('ChoiceLadder scorer revision mismatch')
    spec = importlib.util.spec_from_file_location('choice_ladder_benchmark',root/'benchmark.py')
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    if file_sha(model['path']) != model['sha256'] or file_sha(lane['server']) != lane['server_sha256']:
        raise ValueError('Worker model/runtime digest mismatch')
    if os.uname().nodename != lane['device']:
        raise ValueError('Worker device mismatch')
    out.mkdir(parents=True,exist_ok=True)
    started = datetime.now(timezone.utc)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    command = [lane['server'],'-m',model['path'],'-t','4','-tb','4','-b','128','-ub','128','-c','4096',
               '-ngl','99' if lane['backend']=='cuda' else '0','-lm','mmap','--host','127.0.0.1','--port',str(port),'--parallel','1','--no-webui']
    cells = []
    with (out/'server.log').open('w') as log:
        process = subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+180
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Model server exited')
                try:
                    with urllib.request.urlopen(base+'/health',timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic()>deadline:
                    raise TimeoutError('Model server readiness')
                time.sleep(.25)
            slots = {}
            for label in benchmark.SLOTS:
                tokens = benchmark.post(base,'/tokenize',{'content':label,'add_special':False})['tokens']
                if len(tokens)!=1:
                    raise ValueError('Label is not one token')
                slots[label] = tokens[0]
            items = [json.loads(line) for line in (root/'items.jsonl').read_text().splitlines()]
            groups = defaultdict(list)
            for item in items:
                groups[item['task'],item['k']].append(item)
            expected = {(task,k) for task in ('T1','T2') for k in benchmark.KS}
            expected.update(('T3',k) for k in benchmark.KS if k <= 16)
            expected.add(('T4',10))
            if set(groups) != expected or any(len(rows)!=100 for rows in groups.values()):
                raise ValueError('Expected the complete frozen 25-cell protocol-v2 corpus')
            supplements = router_supplements(benchmark,config,groups['T4',10])
            with (out/'rows.jsonl').open('w') as ledger:
                for (task,k), initial in sorted(groups.items()):
                    n = correct = 0
                    for item in adaptive_items(benchmark,task,k,initial,config.get('cap',400),supplements):
                        result = benchmark.score(base,item,model,slots)
                        ledger.write(json.dumps({**result,'id':item['id'],'task':task,'k':k,'answer':item['answer']})+'\n')
                        ledger.flush()
                        n += 1
                        correct += result['correct']
                        if not needs_more(correct,n,config.get('bar',.95),cap=config.get('cap',400)):
                            break
                    lower,upper = wilson(correct,n)
                    cells.append({'task':task,'k':k,'n':n,'correct':correct,'ci_low':lower,'ci_high':upper})
                    print(json.dumps(cells[-1]),flush=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    receipt = {'scope':payload['scope'],'model_sha256':model['sha256'],'runtime_sha256':lane['server_sha256'],
               'started_at':started.isoformat(),'expires_at':(started+timedelta(days=14)).isoformat(),
               'cells':cells,'rows_sha256':file_sha(out/'rows.jsonl'),'items_sha256':file_sha(root/'items.jsonl')}
    (out/'measurement.json').write_bytes(canonical(receipt)+b'\n')


def adaptive_items(benchmark,task,k,initial,cap,supplements=()):
    """Frozen n=100 prefix, followed by seeded additional benchmark items."""
    yield from initial
    if task == 'T4':
        yield from supplements[:max(0,cap-len(initial))]
        return
    rng = random.Random('choice-adaptive-v1:'+task+':'+str(k))
    words = sorted(set(sum(benchmark.CATEGORIES.values(),[])))
    for i in range(len(initial),cap):
        if task == 'T1':
            options = rng.sample(words,k)
            answer = rng.choice(options)
            question = 'Which option is exactly equal to the query string '+json.dumps(answer)+'?'
        elif task == 'T2':
            category = rng.choice(sorted(benchmark.CATEGORIES))
            answer = rng.choice(benchmark.CATEGORIES[category])
            options = [answer]+rng.sample(sorted(set(words)-set(benchmark.CATEGORIES[category])),k-1)
            question = 'Which single option is a '+category+'?'
        elif task == 'T3':
            answer = rng.choice(sorted(benchmark.INTENTS))
            options = [answer]+rng.sample([x for x in sorted(benchmark.INTENTS) if x!=answer],k-1)
            question = 'Classify the primary communicative intent of this short message: '+json.dumps(rng.choice(benchmark.INTENTS[answer]))
        else:
            raise ValueError('Unknown ladder task')
        rng.shuffle(options)
        yield {'id':f'adaptive-v1-{task}-k{k}-{i:03}','task':task,'k':k,'question':question,'options':options,'answer':options.index(answer)}


def router_supplements(benchmark,config,initial):
    """Use additional distinct frozen router nodes, never duplicate observations."""
    import ast
    corpus_path = Path(config['router_corpus']).expanduser()
    harness_path = Path(config['router_harness']).expanduser()
    if file_sha(corpus_path)!=config['router_corpus_sha256'] or file_sha(harness_path)!=config['router_harness_sha256']:
        raise ValueError('Router corpus/harness identity mismatch')
    tree = ast.parse(harness_path.read_text())
    oracle = next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='oracle')
    namespace = {}
    exec(compile(ast.Module(body=[oracle],type_ignores=[]),str(harness_path),'exec'),namespace)
    used = {row['source_node'] for row in initial}
    nodes = [node for node in json.loads(corpus_path.read_text())['nodes'] if node['id'] not in used]
    random.Random('choice-adaptive-T4-v1').shuffle(nodes)
    count = config.get('cap',400)-len(initial)
    if len(nodes)<count:
        raise ValueError('Not enough distinct router nodes for the configured adaptive cap')
    rows = []
    for i,node in enumerate(nodes[:count]):
        target,state = node['task_spec']['oracle'],node['snapshot']
        kind = target['kind']
        if kind=='refusal':
            label = 'UNKNOWN'
        elif namespace['oracle'](node['task_spec'],node['initial_snapshot'],state,None):
            label = 'DONE'
        else:
            label = {'calculator':'TYPE' if state.get('front_app')=='Calculator' else 'OPEN',
                     'volume':'KEY','volume_exact':'KEY','mute':'KEY','dark':'DARK_MODE',
                     'folder':'FOLDER','app':'OPEN','timer':'TIMER','screenshot':'SCREENSHOT','event':'EVENT'}[kind]
        options = list(benchmark.DESCRIPTIONS)
        random.Random('choice-adaptive-T4-options:'+str(node['id'])).shuffle(options)
        rows.append({'id':f'adaptive-v1-T4-k10-{i+100:03}','task':'T4','k':10,
                     'question':node['user']+'\nChoose the required next ACTION FAMILY (not its arguments).',
                     'options':options,'answer':options.index(benchmark.DESCRIPTIONS[benchmark.ACTIONS.index(label)]),
                     'source_node':node['id']})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['tick','dispatch','measure'])
    parser.add_argument('--config',type=Path)
    parser.add_argument('--request',type=Path)
    parser.add_argument('--out',type=Path)
    args = parser.parse_args(argv)
    if args.command == 'tick':
        home = paths.home()
        path = args.config or home/'config'/'choice-ladder.json'
        print(json.dumps(tick(home,json.loads(path.read_text()))) if path.exists() else '[]')
    elif args.command == 'dispatch':
        state_path = args.request.with_name(args.request.name.removesuffix('.request.json')+'.json')
        status = 'failed'
        try:
            dispatch(args.request)
            status = 'completed'
        finally:
            with locked(args.request.parent/'dispatch.lock'):
                if state_path.exists():
                    state = json.loads(state_path.read_text())
                    state.update(status=status,pid=None)
                    state_path.write_bytes(canonical(state)+b'\n')
    else:
        measure(args.request,args.out)


if __name__ == '__main__':
    main()
