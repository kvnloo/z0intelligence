// Real installed OMP runtime; public evidence only, no parent model call.
import {readFileSync,writeFileSync,mkdtempSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {buildIntelligenceRequest} from '../harness-adapters/governed-client.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const governedSelector=buildIntelligenceRequest({
  task:'Public canary text',
  context:'',
  parent_agent:'omp-canary',
  trace_id:'omp-governed-selector',
  function:'cheap_bounded_worker',
  allow_remote:true,
  max_tokens:64,
},{Z0INT_GOVERNED_REMOTE:'1'});
if(governedSelector.path!=='/v1/governed-worker'||governedSelector.body.harness!=='omp')throw new Error('Governed OMP selector mismatch');
const omp=process.env.Z0INT_OMP_ROOT;
if(!omp)throw new Error('Z0INT_OMP_ROOT must identify the installed OMP package');
const {loadExtensions}=await import(omp+'/src/extensibility/extensions/loader.ts');
const {ExtensionRunner}=await import(omp+'/src/extensibility/extensions/runner.ts');
const {SessionManager}=await import(omp+'/src/session/session-manager.ts');
const {AuthStorage}=await import(omp+'/src/session/auth-storage.ts');
const {ModelRegistry}=await import(omp+'/src/config/model-registry.ts');
const dir=mkdtempSync('/tmp/z0int-omp-canonical-');
const loaded=await loadExtensions([root+'omp-extensions/z0int-bridge/index.ts',root+'omp-extensions/z0int-intelligence/index.ts'],root);
if(loaded.errors.length)throw new Error(JSON.stringify(loaded.errors));
const session=SessionManager.inMemory(root);
const auth=await AuthStorage.create(dir+'/auth.db');
const registry=new ModelRegistry(auth,dir+'/models.json',{ignoreLocalModelConfig:true,cacheDbPath:dir+'/cache.db'});
const runner=new ExtensionRunner(loaded.extensions,loaded.runtime,root,session,registry);
const text='Claim: The fictional museum closes at five.\nEvidence: The fictional museum opens at nine and closes at five.';
const result=await runner.emitBeforeAgentStart(text,undefined,['Native OMP system prompt.']);
if(result===undefined)throw new Error('Canonical result was not returned to OMP');

// Prove multi-turn usage stays on one bridge trace and becomes authoritative
// only after terminal agent_end. Each turn carries provider-reported usage.
const turn0={role:'assistant',content:[{type:'text',text:'tool call'}],provider:'openai',model:'gpt-test',usage:{input:10,output:2}};
const turn1={role:'assistant',content:[{type:'text',text:'done'}],provider:'openai',model:'gpt-test',usage:{input:7,output:2}};
await runner.emit({type:'turn_end',turnIndex:0,timestamp:Date.now(),message:turn0,toolResults:[]});
await runner.emit({type:'turn_end',turnIndex:1,timestamp:Date.now(),message:turn1,toolResults:[]});
await runner.emit({type:'agent_end',messages:[turn0,turn1],willContinue:false});

const heartRows=readFileSync(process.env.Z0INT_HOME+'/stream/bridge_heart.jsonl','utf8').trim().split('\n').map(JSON.parse);
const usageHeart=[...heartRows].reverse().find(r=>r.omp_session_id===session.getSessionId()&&r.source==='bridge_agent_end');
if(!usageHeart)throw new Error('Missing terminal bridge usage heart');
if(usageHeart.measurement_state!=='complete')throw new Error('Expected complete provider usage, got '+JSON.stringify(usageHeart));
if(usageHeart.measured!==21)throw new Error('Expected 21 accumulated tokens, got '+JSON.stringify(usageHeart));
const rows=readFileSync(process.env.Z0INT_HOME+'/receipts/decisions.jsonl','utf8').trim().split('\n').map(JSON.parse).filter(r=>r.session_id===session.getSessionId()&&r.extra?.automatic);
const completed=rows.filter(r=>r.extra.status==='completed');
const delivered=rows.filter(r=>r.extra.status==='delivered_to_hook');
if(completed.length!==1||delivered.length!==1)throw new Error('Expected one canonical hook execution and consumption, got '+completed.length+'/'+delivered.length);
const decision=completed[0];
if(decision.extra.result.route.kind!=='JEV_FUNCTION'||!decision.extra.result.executed)throw new Error('Jev did not execute');
const check=spawnSync(process.env.Z0INT_PYTHON,['-m','z0int.automatic','health'],{input:JSON.stringify({harness:'omp',instance_id:decision.extra.integration_instance}),encoding:'utf8',env:process.env});
if(check.status!==0)throw new Error('Automatic integration health failed: '+check.stderr);
process.env.Z0INT_AUTO_OMP='0';
const native=await runner.emitBeforeAgentStart('Trivial native turn',undefined,['Native OMP system prompt.']);
if(native!==undefined)throw new Error('Kill switch did not preserve native behavior');
writeFileSync(process.env.Z0INT_PROOF_OUTPUT+'/omp.json',JSON.stringify({runtime:'OMP ExtensionRunner.emitBeforeAgentStart',parent_model_called:false,returned_to_parent:result,dispatch_receipt:decision.trace_id,consumed_receipt:delivered[0].trace_id,health:JSON.parse(check.stdout),rollback_native:true,duplicate_hook:false,governed_tool_selector:true,usage_measurement_state:usageHeart.measurement_state,usage_measured_tokens:usageHeart.measured},null,2)+'\n');
console.log(JSON.stringify({omp:true,dispatch_receipt:decision.trace_id,rollback_native:true}));
process.exit(0);
