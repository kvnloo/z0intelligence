// Invoke the real registered OMP z0int_route_worker tool against the live host service.
// No parent model call. The single synthetic provider call is initiated by the tool itself.
import {writeFileSync,mkdtempSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const omp=process.env.Z0INT_OMP_ROOT;
if(!omp)throw new Error('Z0INT_OMP_ROOT must identify the installed OMP package');
if(process.env.Z0INT_GOVERNED_REMOTE!=='1')throw new Error('Z0INT_GOVERNED_REMOTE=1 is required');

const {loadExtensions}=await import(omp+'/src/extensibility/extensions/loader.ts');
const {ExtensionRunner}=await import(omp+'/src/extensibility/extensions/runner.ts');
const {SessionManager}=await import(omp+'/src/session/session-manager.ts');
const {AuthStorage}=await import(omp+'/src/session/auth-storage.ts');
const {ModelRegistry}=await import(omp+'/src/config/model-registry.ts');

const dir=mkdtempSync('/tmp/z0int-omp-governed-');
const loaded=await loadExtensions([root+'omp-extensions/z0int-intelligence/index.ts'],root);
if(loaded.errors.length)throw new Error(JSON.stringify(loaded.errors));

const session=SessionManager.inMemory(root);
const auth=await AuthStorage.create(dir+'/auth.db');
const registry=new ModelRegistry(auth,dir+'/models.json',{ignoreLocalModelConfig:true,cacheDbPath:dir+'/cache.db'});
const runner=new ExtensionRunner(loaded.extensions,loaded.runtime,root,session,registry);

const registered=runner.getRegisteredTool('z0int_route_worker');
if(!registered)throw new Error('z0int_route_worker was not registered');

const params={
  task:'Public synthetic diagnostic. Return exactly CANONICAL_OK and nothing else.',
  context:'Public synthetic diagnostic only.',
  parent_agent:session.getSessionId(),
  trace_id:'remote-public-proof',
  function:'cheap_bounded_worker',
  allow_remote:true,
  max_tokens:16,
};

const result=await registered.definition.execute(
  'z0int-governed-canary',
  params,
  undefined,
  undefined,
  runner.createContext(),
);

const details=result?.details;
if(!details||typeof details!=='object')throw new Error('Missing structured tool result details');
if(details.ok!==true)throw new Error('Governed OMP tool failed: '+JSON.stringify(details));
if(details.trace_id!=='remote-public-proof')throw new Error('Root trace identity changed');
if(details.governed_remote!==true)throw new Error('Tool did not traverse governed remote lane');
if(String(details.output||'').trim()!=='CANONICAL_OK')throw new Error('Synthetic output mismatch');

const proof={
  runtime:'OMP ExtensionRunner.getRegisteredTool().definition.execute',
  tool:'z0int_route_worker',
  parent_model_called:false,
  root_trace_id:details.trace_id,
  governed_remote:true,
  output_verified_exact:true,
  output:'CANONICAL_OK',
  session_id:session.getSessionId(),
  aodl_admission_receipt_id:details.aodl_admission_receipt_id,
};

const out=process.env.Z0INT_PROOF_OUTPUT;
if(!out)throw new Error('Z0INT_PROOF_OUTPUT is required');
writeFileSync(out+'/omp-governed.json',JSON.stringify(proof,null,2)+'\n');
console.log(JSON.stringify(proof));
