# The blind verifier's P-2 reproduction (homes/C4a-hermes-capture-verify/p2probe/probe.py), copied verbatim; the last 3 lines (closed-drop count) were added.
import importlib.util, os, subprocess, sys, time, json
from pathlib import Path
W = sys.argv[1]; T = Path(sys.argv[2])
repo = T / 'repo'
if not repo.exists():
    repo.mkdir(parents=True)
    subprocess.run(['git','-C',str(repo),'init','-q'],check=True); (repo/'README.md').write_text('# r\n')
    subprocess.run(['git','-C',str(repo),'add','-A'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=t','-c','user.email=t@t','-c','commit.gpgsign=false','commit','-qm','i'],check=True)
spec = importlib.util.spec_from_file_location('p', W + '/harness-adapters/hermes-z0intelligence/__init__.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m._hermes_workspace_root = lambda t: str(repo); m._profile_config = lambda: {}
class C:
    hooks = {}
    def get_config(self, k, d=None): return {'mode': 'shadow', 'z0int_python': sys.executable}.get(k, d)
    def register_hook(self, n, cb): self.hooks[n] = cb
    def on_unload(self, cb): pass
c = C(); cap = m.register(c)
for i in range(3):
    c.hooks['pre_llm_call'](session_id='s', turn_id=str(i), user_message=f'fix thing {i}', platform='cli')
cap.flush(20); t0=time.monotonic(); cap.close(); print('close_s', round(time.monotonic()-t0,3))
opp = Path(os.environ['Z0INT_HOME'])/'state/hermes/opportunities.jsonl'
n = lambda: len(opp.read_text().splitlines()) if opp.exists() else 0
a = n(); time.sleep(8); print('opp rows at close', a, 'after 8s', n())
drops = Path(os.environ['Z0INT_HOME'])/'state/hermes/drops.jsonl'
print('closed opportunity drops', sum(json.loads(l)['count'] for l in (drops.read_text().splitlines() if drops.exists() else [])
      if json.loads(l)['kind'] == 'opportunity_record' and json.loads(l)['reason'] == 'closed'))
