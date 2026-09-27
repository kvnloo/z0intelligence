import {route,delivered} from '../automatic-client.mjs';
import {createHash} from 'node:crypto';
export const name='z0intelligence-automatic';
export function apply(ctx) {
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
