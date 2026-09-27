// Installed OMP loader regression; no service, model call, or credentials.
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const {loadExtensions}=await import(process.env.Z0INT_OMP_ROOT+'/src/extensibility/extensions/loader.ts');
const entries=[root+'omp-extensions/z0int-bridge/index.ts',root+'omp-extensions/z0int-intelligence/index.ts'];
for(let i=0;i<2;i++){
 const result=await loadExtensions(entries,root);
 if(result.errors.length)throw new Error(JSON.stringify(result.errors));
 const hooks=result.extensions.reduce((n,e)=>n+(e.handlers.get('before_agent_start')?.length||0),0);
 const tools=result.extensions.reduce((n,e)=>n+Number(e.tools.has('z0int_route_worker')),0);
 if(hooks!==2||tools!==1)throw new Error(JSON.stringify({runtime:i,hooks,tools}));
}
console.log(JSON.stringify({runtimes:2,intelligence_hooks_per_runtime:1,tools_per_runtime:1,physical_calls:0}));

process.exit(0);
