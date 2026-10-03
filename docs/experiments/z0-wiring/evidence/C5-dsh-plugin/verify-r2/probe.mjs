const p = await import(process.argv[2] + '/harness-adapters/dsh-z0intelligence/index.mjs')
const calls = []; const hs = {}
const spawn = (cmd, args, opts) => { let i=''; return {unref(){}, on(){}, stdin:{on(){}, end(s){ calls.push({ev: args.at(-1), payload: JSON.parse(s)}) }}} }
p.apply({on:(e,f)=>{(hs[e]??=[]).push(f)}}, {capture:true}, {spawn, env:{Z0INT_HOME: process.env.Z0INT_HOME, Z0INT_PYTHON:'x'}})
const log = {1:{type:'session/header'}, 2:{type:'user/message', data:{role:'user', content:[{type:'text', text:'probe prompt'}]}}}
const agent = {id:'session-7', frozenMessages: new WeakSet(), session:{id:'session-7', header:{id:'session-7', createdAt:1, cwd:'/x'},
  surface:{nodes:[1,2]}, eventAt:(s)=>log[s]}}
await hs['agent/request'][0]({agent, turn:1, step:1}, async()=>({model:'m'}))
await hs['agent/turn-stopping'][0]({agent, turn:1})
console.log(JSON.stringify(calls))
