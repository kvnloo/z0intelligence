import {route,delivered} from '../automatic-client.mjs';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {captureEnabled,registerCapture,z0Home} from './capture.mjs';
import {registerMemory} from './memory.mjs';
export {counters} from './capture.mjs';
export {canonicalTurnKey} from './lineage.mjs';
export {sampleHash} from './shadow.mjs';
export const name='z0intelligence-automatic';

// Governed routing stays off until z0intelligence#95 closes: both the profile config and automatic.json must opt in.
function routerEnabled(config, env) {
  if(config.router!==true || env.Z0INT_AUTO_DSH==='0')return false;
  try{return JSON.parse(readFileSync(join(z0Home(env),'config','automatic.json'),'utf8'))?.dsh?.enabled===true;}
  catch{return false;}
}

function registerRouter(ctx) {
  ctx.on('llm/stream',async function*(options,next){
    if(options.purpose){yield* next();return;}
    const last=options.messages?.at(-1);
    // Tool continuation is native: do not classify tool output as a fresh user task.
    if(last?.role!=='user' || !last.content?.every(b=>b.type==='text') || last.source?.kind==='tool'){
      yield* next();return;
    }
    const text=last.content.map(b=>b.text).join('\n');
    const turnId=String(last.id || createHash('sha256').update(JSON.stringify(options.messages)).digest('hex'));
    const result=await route('dsh',String(options.sessionId||'dsh'),turnId,text);
    if(result.action!=='context') {await delivered('dsh',result);yield* next();return;}
    // Only the exact standalone evidence contract can replace a native response.
    const output=JSON.stringify(result.result.output);
    yield {type:'block-start',index:0,blockType:'text'};
    yield {type:'text-delta',index:0,text:output};
    yield {type:'block-end',index:0,block:{type:'text',text:output}};
    await delivered('dsh',result);
    yield {type:'finish',reason:'stop'};
  });
}

// cordis calls apply(ctx, config); `deps` (env, spawn, fetch) exists for tests only.
export function apply(ctx, config, deps={}) {
  config=config ?? {};
  const env=deps.env ?? process.env;
  if(config.capture!==false && captureEnabled(env))registerCapture(ctx,config,{...deps,env});
  registerMemory(ctx,config,{...deps,env}); // C8 memory seam: memory_inject off|shadow|canary|on (default shadow)
  if(routerEnabled(config,env))registerRouter(ctx);
}
