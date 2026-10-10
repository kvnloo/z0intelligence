import importlib.util
from pathlib import Path
import pytest
from z0int import quota_budget as q
CFG={'quota_budgets':{'groq':{'model':'openai/gpt-oss-20b','limits':{'rpm':30,'rpd':1000,'tpm':8000,'tpd':200000},'kerdoios_root':__import__('os').environ.get('Z0INT_KERDOIOS_ROOT')}}}
MODEL='openai/gpt-oss-20b'
def call(ts,tokens=100,headers=None):
 return {'trace_id':'c'+str(ts),'provider':'groq','model':MODEL,'execution':'live','ts':ts,'input_tokens':tokens,'output_tokens':0,'extra':{'status':'completed','physical_started_at':ts,'physical_finished_at':ts,'quota_headers':headers or {}}}
def project(events,needed=2000,now=100000):return q.project('groq',MODEL,CFG,needed,events,now)
def test_empty_authority_capacity():assert project([])['allowed']
def test_unknown_contract_fails_closed():assert not q.project('groq',None,CFG,None,[],100000)['allowed']
def test_unknown_model_fails_closed():assert not q.project('groq','other',CFG,1,[],100000)['allowed']
def test_no_quota_rule_does_not_change_other_providers():assert q.project('jev','jev-1.13.0',CFG,10,[],100000) is None
def test_daily_tokens_bind_even_with_empty_minute():
 r=project([call(90000,199000)])
 assert not r['allowed'];assert r['quota']['dimensions']['tpm']['remaining']==8000
 assert r['next_eligible_at']==176400
def test_minute_tokens_bind():assert not project([call(99999,7000)])['allowed']
def test_minute_requests_bind():assert not project([call(99900+n*2,0) for n in range(30,60)])['allowed']
def test_latest_physical_row_not_double_counted():
 a=call(99990,500);b={**a,'input_tokens':1000}
 assert project([a,b])['tokens_24h']==1000
def _stub_header_parser(headers,*,provider,model,now):
 # Host-independent stand-in for kerdoios.quota.parse.quota_state_from_headers,
 # keeping the legacy mapping project() normalizes around: unsuffixed
 # "-requests" is per-minute, "-requests-day" is per-day.
 from types import SimpleNamespace
 dims={}
 for name,dim in (('requests','rpm'),('requests-day','rpd'),('tokens','tpm'),('tokens-day','tpd')):
  remaining=headers.get('x-ratelimit-remaining-'+name)
  if remaining is None:continue
  reset=headers.get('x-ratelimit-reset-'+name)
  dims[dim]=SimpleNamespace(limit=int(headers.get('x-ratelimit-limit-'+name,0)),remaining=int(remaining),
   reset_at=now+float(reset[:-1])*{'s':1,'m':60,'h':3600}[reset[-1]] if reset else None)
 return SimpleNamespace(dimensions=dims)
def test_exact_capacity_request_fits():
 assert project([],needed=8000)['allowed']
 assert not project([call(99990,1)],needed=8000)['allowed']
def test_groq_requests_header_is_daily(monkeypatch):
 import sys,types
 parse=types.ModuleType('kerdoios.quota.parse');parse.quota_state_from_headers=_stub_header_parser
 for name in ('kerdoios','kerdoios.quota'):
  package=types.ModuleType(name);package.__path__=[];monkeypatch.setitem(sys.modules,name,package)
 monkeypatch.setitem(sys.modules,'kerdoios.quota.parse',parse)
 r=project([call(99990,1,{'x-ratelimit-remaining-requests':'0','x-ratelimit-limit-requests':'1000','x-ratelimit-reset-requests':'30m'})])
 assert not r['allowed'];assert r['quota']['dimensions']['rpd']['remaining']==0
 assert r['quota']['dimensions']['rpm']['remaining']==29
 assert r['next_eligible_at']==101790
def test_expired_header_does_not_restore_daily_local_budget():
 assert not project([call(90000,199000,{'x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'1s'})])['allowed']
def test_unknown_usage_reserved_until_window_passes():
 row={'trace_id':'a','provider':'groq','capability_id':'intelligence.provider_admission','ts':99990,'extra':{'status':'acquired','quota_model':MODEL,'quota_reserved_tokens':7500}}
 assert not project([row])['allowed']
 assert project([row],now=100100)['allowed']
def test_rollover_not_midnight():
 assert not project([call(86399,199000)],now=86401)['allowed']
def test_more_than_capacity_request_fails():assert not project([],needed=8001)['allowed']

def test_optional_import_does_not_shadow_repository_tests():
 import sys
 before=list(sys.path)
 project([])
 assert sys.path==before
