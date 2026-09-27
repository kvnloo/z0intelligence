import importlib.util,json,pathlib,sys,unittest
from unittest.mock import patch
ROOT=pathlib.Path('/mnt/zer0models/workspace/zer0/oss/.work/z0intelligence')
sys.path.insert(0,str(ROOT/'src'))
from z0int import worker_routing as r
class RoutingTests(unittest.TestCase):
 def setUp(self):
  self.policy,self.providers=r.configuration();self.policy['free_only']=False
 def plan(self,task):
  with patch.object(r,'available',return_value=True): return r.plan_route(task,self.policy,self.providers)
 def test_task_changes_provider(self):
  expected={'Write a Python function':'cerebras','Extract JSON':'cerebras','Summarize a paragraph':'cerebras','Critique this plan':'cerebras','Compare two ideas':'cerebras','Classify sentiment':'cerebras','Rewrite politely':'cerebras','Suggest two names':'cerebras'}
  for task,provider in expected.items():self.assertEqual(self.plan(task)['primary_provider'],provider)
 def test_local_only_never_falls_back_remotely(self):
  self.assertEqual([x['provider'] for x in self.plan('local-only: answer')['candidates']],['local'])
 def test_fallback_deduplicated_bounded(self):
  candidates=self.plan('Extract JSON')['candidates'];self.assertEqual(len(candidates),3);self.assertEqual(len({x['provider'] for x in candidates}),3)
 def test_missing_credentials_skipped(self):
  with patch.object(r,'available',side_effect=lambda p,c:p=='deepseek'):
   plan=r.plan_route('Extract JSON',self.policy,self.providers)
  self.assertEqual([x['provider'] for x in plan['candidates']],['deepseek']);self.assertTrue(plan['skipped'])
 def test_auto_rejects_manual_route(self):
  with self.assertRaises(ValueError):r.validate_request({'task':'x','parent_agent':'p','provider':'groq'})
 def test_budget_rejects_bool_negative_oversize(self):
  for b in [True,0,-1,2049]:
   with self.assertRaises(ValueError):r.validate_request({'task':'x','parent_agent':'p','max_tokens':b})
 def test_usage_missing_is_not_zero(self):self.assertIsNone(r.token({},'prompt_tokens'))
 def test_usage_bool_is_not_measurement(self):self.assertIsNone(r.token({'prompt_tokens':True},'prompt_tokens'))
 def test_estimator_declared_and_unicode(self):
  self.assertEqual(r.estimate('abcde'),2);self.assertEqual(r.estimate('你好'),2)
 def test_models_are_registry_valid(self):
  for p,m in self.policy['defaults'].items():self.assertIn(m,[x['id'] for x in self.providers[p]['models']])
 def test_unmetered_failed_attempt_does_not_break_successful_fallback(self):
  plan={'candidates':[{'provider':'groq','model':'a'},{'provider':'cerebras','model':'b'}]}
  failed={'ok':False,'output':'','receipt':{'provider':'groq','model':'a','extra':{'status':'failed'}}}
  completed={'ok':True,'output':'42','receipt':{'provider':'cerebras','model':'b','input_tokens':10,'output_tokens':1,'estimated_frontier_tokens_avoided':15,'extra':{'status':'completed'}}}
  with patch.object(r,'execute_attempt',side_effect=[failed,completed]),patch.object(r,'receipts_path',return_value=pathlib.Path('/tmp/unused')):
   result=r.execute_plan({'parent_agent':'p'},self.policy,self.providers,plan,receipt_sink=r.authority_receipt_sink)
  self.assertTrue(result['ok']);self.assertEqual(result['output'],'42');self.assertEqual(result['unmetered_attempts'],1);self.assertEqual(result['input_tokens'],10);self.assertEqual(len(result['attempts']),2)
if __name__=='__main__':unittest.main()
