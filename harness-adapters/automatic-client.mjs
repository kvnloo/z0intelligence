import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
export const instanceId = randomUUID();
export function call(operation, value) {
  return new Promise((resolve,reject)=>{
    const child=spawn(process.env.Z0INT_PYTHON || 'python3',['-m','z0int.automatic',operation],{
      env:{...process.env,PYTHONPATH:root+'src'+(process.env.PYTHONPATH?':'+process.env.PYTHONPATH:'')},stdio:['pipe','pipe','pipe']});
    let out='';
    const timer=setTimeout(()=>child.kill(),30000);
    child.stdout.on('data',chunk=>{out+=chunk;if(out.length>200000)child.kill();});
    child.stderr.resume();
    child.on('error',reject);
    child.stdin.on('error',()=>{});
    child.on('close',code=>{clearTimeout(timer);try{if(code)throw new Error('automatic adapter failed');resolve(JSON.parse(out));}catch(error){reject(error);}});
    child.stdin.end(JSON.stringify(value));
  });
}
export async function route(harness, sessionId, turnId, text) {
  try{return await call('event',{harness,session_id:sessionId,turn_id:turnId,instance_id:instanceId,text});}
  catch{return {action:'native',ready:false};}
}
export async function delivered(harness, result) {
  if(!result.receipt_id)return;
  try{await call('consume',{harness,instance_id:instanceId,receipt_id:result.receipt_id});}
  catch{/* Native behavior survives service failure; readiness does not turn green. */}
}
