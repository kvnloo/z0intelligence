'use strict';
const $ = id => document.getElementById(id);
const components = [
  {name:'Bend', sub:'Laws & statecharts', status:'Implemented slices · bounded proof scope',copy:'Write the legal state transitions and authority boundary as a small, explicit program. The published memory slice pairs a pure Bend model with laws, a real proof gate, finite parity checks, and a negative mutation control.',limit:'A proof of an emitted book is not compiler correctness or universal runtime enforcement. Bend is not executed in this page.',source:'https://github.com/kvnloo/z0intelligence/pull/202'},
  {name:'z0intelligence',sub:'Typed decisions',status:'Existing project · ongoing integration',copy:'Separate legal-action construction from candidate selection. Models can propose a bounded decision without becoming the source of permission. Memory retains evidence and provenance rather than issuing authority.',limit:'The memory continuity roadmap contains branch-only and draft integrations. A plan is not a deployed capability.',source:'https://github.com/kvnloo/z0intelligence/issues/22'},
  {name:'Autoresearch',sub:'Propose · measure · compare',status:'Research direction · integration under study',copy:'Use a repeatable loop to propose changes, collect evidence, compare outcomes, and admit only supported improvements. Bend laws could constrain the search space before a candidate is promoted.',limit:'This diagram does not assert an end-to-end Bend-driven training or self-improvement result.',source:'https://github.com/kvnloo/z0intelligence/issues/199'},
  {name:'SLM SFT',sub:'Shadow network',status:'Research direction · no gain asserted',copy:'The intended shadow network learns bounded tasks from supervised examples while remaining separate from the authority boundary. Small language models are candidates for narrow decisions, not a blanket replacement for reasoning.',limit:'No training run, corpus size, or improvement number is claimed here. Bend-generated SFT data is a direction, not a reported result on this page.',source:'https://github.com/kvnloo/z0intelligence'},
  {name:'z0evals',sub:'Measure before promotion',status:'Published experiments · separate from proof',copy:'Evaluate the actual decision family and keep raw receipts. The published Phase 1b experiment reports 1,102 local receipts and distinguishes compiler-first arms from unfiltered controls.',limit:'The experiment is not evidence of trained shadow-network gains or a universal safety guarantee. Evaluation and formal proof answer different questions.',source:'https://kvnloo.github.io/z0evals/'}
];
let demo='work', cursor=0, playing=false, timer=null, refusals=false;
const memory = {
  title:'Memory law', kind:'Interactive projection · not Bend execution', nodes:['Absent','Admitted','Retrieved','Replayed'],
  boundary:'Browser projection of MODEL.bend at the published PR head. Input cases are demonstration choices, not recorded production events. Memory never grants authority.',
  steps:[
    {event:'No memory, no grants',state:'Absent',accepted:false,reason:'The bounded capability remains false.',note:'There is no retained evidence. Neither trusted grant is present.',facts:{explicit:false,policy:false,evidence:false,bounded:false,instruction:false}},
    {event:'Admit instruction-like memory',state:'Admitted',accepted:false,reason:'Memory is evidence, not permission.',note:'An authority claim inside remembered data cannot create a current trusted grant.',facts:{explicit:false,policy:false,evidence:true,bounded:false,instruction:false}},
    {event:'Retrieve with caller grant only',state:'Retrieved',accepted:false,reason:'Both trusted grants are required.',note:'The caller grant changes, but policy still denies the capability. Retrieved data does not fill the missing grant.',facts:{explicit:true,policy:false,evidence:true,bounded:false,instruction:false}},
    {event:'Replay with both trusted grants',state:'Replayed',accepted:true,reason:'The pure projection is bounded by both grants.',note:'The conjunction becomes true because both trusted inputs are true, not because memory was replayed. This is still not an execution token.',facts:{explicit:true,policy:true,evidence:true,bounded:true,instruction:false}}
  ]
};
let work = {title:'Work lifecycle',kind:'Precomputed Bend trace',nodes:[],steps:[],boundary:''};
const dataset=()=>demo==='memory'?memory:work;
function stop(){playing=false;clearTimeout(timer);timer=null;}
function schedule(){clearTimeout(timer);if(!playing)return;timer=setTimeout(()=>{cursor++;if(cursor>=dataset().steps.length)stop();render();schedule();},Number($('speed').value));}
function seek(n){stop();cursor=Math.max(0,Math.min(dataset().steps.length,n));render();}
function render(){
  const d=dataset(), current=d.steps[cursor-1];
  $('scene-title').textContent=d.title;$('trace-kind').textContent=d.kind;
  $('counter').textContent=String(cursor).padStart(2,'0')+' / '+String(d.steps.length).padStart(2,'0');
  $('position').textContent=cursor;$('timeline').max=d.steps.length;$('timeline').value=cursor;
  $('play').textContent=playing?'Ⅱ Pause':cursor===d.steps.length?'▶ Replay':'▶ Play';
  $('play').setAttribute('aria-label',playing?'Pause replay':cursor===d.steps.length?'Replay from start':'Play replay');
  $('step').disabled=cursor===d.steps.length;
  $('state-diagram').replaceChildren();
  d.nodes.forEach((name,i)=>{if(i){const arrow=document.createElement('span');arrow.className='node-arrow';arrow.textContent='→';arrow.setAttribute('aria-hidden','true');$('state-diagram').append(arrow);}const node=document.createElement('div');node.className='state-node'+(name===(current?.state??d.nodes[0])?' active':'');const index=document.createElement('span');index.className='node-number';index.textContent='STATE '+String(i).padStart(2,'0');const label=document.createElement('strong');label.textContent=name;node.append(index,label);$('state-diagram').append(node);});
  $('event-title').textContent=current?.event??'Ready to replay';
  $('event-note').textContent=current?.note??'Play the trace, advance one event, or seek a retained position. Nothing here changes a live system.';
  const verdict = current?.verdict ?? (current?.accepted ? 'accepted' : 'refused');
  $('verdict').textContent=current?`${verdict.toUpperCase()} · ${current.reason}`:'No event selected';
  $('verdict').className='verdict'+(current?' '+verdict:'');
  $('trace-boundary').textContent=d.boundary;
  $('facts').replaceChildren();Object.entries(current?.facts??{boundary:'read-only',execution:'none'}).forEach(([key,value])=>{const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=String(value);row.append(dt,dd);$('facts').append(row);});
  $('history').replaceChildren();let count=0;d.steps.slice(0,cursor).forEach((item,i)=>{const result=item.verdict??(item.accepted?'accepted':'refused');if(refusals&&result!=='refused')return;count++;const li=document.createElement('li'),button=document.createElement('button'),text=document.createElement('span'),outcome=document.createElement('span');button.className=i===cursor-1?'current':'';text.textContent=String(i+1).padStart(2,'0')+' · '+item.event;outcome.className='outcome '+(item.accepted?'ok':result==='held'?'held':'no');outcome.textContent=result.toUpperCase();button.append(text,outcome);button.addEventListener('click',()=>seek(i+1));li.append(button);$('history').append(li);});
  if(!count){const empty=document.createElement('li');empty.className='empty';empty.textContent=refusals?'No refusals at this position.':'The trace begins when you press Play or Step.';$('history').append(empty);}
}
$('play').addEventListener('click',()=>{if(playing){stop();render();return;}if(cursor===dataset().steps.length)cursor=0;playing=true;render();schedule();});
$('step').addEventListener('click',()=>seek(cursor+1));$('reset').addEventListener('click',()=>seek(0));
$('timeline').addEventListener('input',e=>seek(Number(e.target.value)));
$('speed').addEventListener('change',()=>schedule());
$('filter').addEventListener('click',()=>{refusals=!refusals;$('filter').setAttribute('aria-pressed',String(refusals));$('filter').textContent=refusals?'Show all events':'Refusals only';render();});
document.querySelectorAll('[data-demo]').forEach(button=>button.addEventListener('click',()=>{stop();demo=button.dataset.demo;cursor=0;document.querySelectorAll('[data-demo]').forEach(other=>other.setAttribute('aria-pressed',String(other===button)));render();}));
function selectComponent(index){const item=components[index];document.querySelectorAll('[data-component]').forEach((b,i)=>b.setAttribute('aria-pressed',String(i===index)));$('component-status').textContent=item.status;$('component-title').textContent=item.name;$('component-copy').textContent=item.copy;$('component-limit').textContent=item.limit;$('component-source').href=item.source;}
components.forEach((item,i)=>{const b=document.createElement('button');b.dataset.component=String(i);b.setAttribute('aria-pressed',String(i===0));const n=document.createElement('span'),title=document.createElement('strong'),sub=document.createElement('small');n.className='mono';n.textContent=String(i+1).padStart(2,'0')+' /';title.textContent=item.name;sub.textContent=item.sub;b.append(n,title,sub);b.addEventListener('click',()=>selectComponent(i));$('pipeline').append(b);});
selectComponent(0);
document.addEventListener('visibilitychange',()=>{if(document.hidden){stop();render();}});
fetch('trace.json').then(response=>{if(!response.ok)throw Error('Replay artifact unavailable');return response.json();}).then(data=>{work=data;render();}).catch(()=>{$('event-title').textContent='Replay artifact unavailable';$('event-note').textContent='The recorded trace could not load. The memory projection remains available.';$('play').disabled=true;$('step').disabled=true;});
