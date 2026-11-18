import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import observer from '../../../integrations/omp/observer.mjs';

async function fixture(run) {
  const root=mkdtempSync(join(tmpdir(),'observer-omp-inventory-')),config=join(root,'observer.json');
  writeFileSync(config,JSON.stringify({project:{root},port:1,storage_profile:'project'}));
  const oldConfig=process.env.AGENT_OBSERVER_CONFIG,oldToken=process.env.AGENT_OBSERVER_TOKEN,originalFetch=global.fetch;
  process.env.AGENT_OBSERVER_CONFIG=config;process.env.AGENT_OBSERVER_TOKEN='fixture-token';
  const events=[],hooks=new Map();
  global.fetch=async(_url,options)=>{events.push(JSON.parse(options.body));return {ok:true,status:200};};
  try { await run({root,events,hooks}); }
  finally {
    global.fetch=originalFetch;
    if(oldConfig===undefined)delete process.env.AGENT_OBSERVER_CONFIG;else process.env.AGENT_OBSERVER_CONFIG=oldConfig;
    if(oldToken===undefined)delete process.env.AGENT_OBSERVER_TOKEN;else process.env.AGENT_OBSERVER_TOKEN=oldToken;
    rmSync(root,{recursive:true,force:true});
  }
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('OMP inventory distinguishes inactive tools, selected memory and command references from calls',async()=>{
  await fixture(async({root,events,hooks})=>{
    await observer({on:(name,fn)=>hooks.set(name,fn),exec:async()=>({code:1}),getActiveTools:()=>['read'],
      getAllTools:()=>[{name:'read',sourceInfo:{source:'builtin'}},{name:'goal',sourceInfo:{source:'builtin'}}],
      getCommands:()=>[{name:'custom',source:'extension',description:'Useful command'},{name:'skill:example',source:'skill',description:'Skill'}]});
    const ctx={cwd:root,sessionManager:{getSessionId:()=> 'main'},memory:{status:async()=>({backend:'hindsight',active:true})}};
    await hooks.get('session_start')({},ctx);await flush();
    const inventory=events.find(event=>event.type==='RUNTIME_INVENTORY').inventory;
    assert.equal(inventory.native_tools.find(tool=>tool.name==='read').availability,'available');
    assert.equal(inventory.native_tools.find(tool=>tool.name==='goal').availability,'unavailable');
    assert.deepEqual(inventory.memory_systems.slice(0,4).filter(system=>system.enabled).map(system=>system.id),['omp-hindsight']);
    assert.equal(inventory.commands.find(command=>command.name==='/custom').availability,'provided');
    assert.ok(inventory.commands.some(command=>command.name==='/memory'&&command.availability==='configured'));
    assert.ok(!inventory.commands.some(command=>command.name==='/skill:example'));
    hooks.get('before_agent_start')({reason:'revive',prompt:''},ctx);await flush();
    assert.ok(!events.some(event=>event.type==='PROMPT_START'));
    hooks.get('tool_execution_start')({toolName:'read',toolCallId:'one',args:{path:'README.md'}});await flush();
    assert.equal(events.find(event=>event.type==='TOOL_CALL_START').cwd,root);
    assert.equal(events.find(event=>event.type==='TOOL_CALL_START').application,'OMP');
    hooks.get('tool_execution_start')({toolName:'write',toolCallId:'memory',args:{path:'xd://recall',content:'{\"query\":\"project\"}'}});await flush();
    const memoryCall=events.find(event=>event.call_id==='memory');
    assert.equal(memoryCall.memory_operation,'recall');
    assert.equal(memoryCall.memory_system_id,'omp-hindsight');
    hooks.get('session_shutdown')();
  });
});

test('Delayed OMP inventory cannot acquire the next session identity',async()=>{
  await fixture(async({root,events,hooks})=>{
    await observer({on:(name,fn)=>hooks.set(name,fn),exec:async()=>({code:1}),getAllTools:()=>[],getCommands:()=>[]});
    let release,arrived;
    const gate=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>arrived=resolve);
    const alpha={cwd:root,sessionManager:{getSessionId:()=> 'alpha'},getAllTools:()=>[{name:'alpha_read',sourceInfo:{source:'builtin'}}],memory:{status:async()=>{arrived();await gate;return{backend:'off'}}}};
    const beta={cwd:root,sessionManager:{getSessionId:()=> 'beta'},getAllTools:()=>[{name:'beta_read',sourceInfo:{source:'builtin'}}],memory:{status:async()=>({backend:'off'})}};
    const old=hooks.get('session_start')({},alpha);await entered;
    hooks.get('tool_execution_start')({toolName:'read',toolCallId:'old-call',args:{path:'alpha.txt'}});
    await hooks.get('session_switch')({},beta);release();await old;await flush();
    hooks.get('tool_execution_end')({toolName:'read',toolCallId:'old-call',result:'alpha result'});await flush();
    assert.ok(!events.some(event=>event.type==='TOOL_CALL_END'));
    const inventories=events.filter(event=>event.type==='RUNTIME_INVENTORY');
    assert.equal(inventories.length,1);
    assert.equal(inventories[0].session_id,'beta');
    assert.deepEqual(inventories[0].inventory.native_tools.map(tool=>tool.name),['beta_read']);
    hooks.get('session_shutdown')();
  });
});

test('OMP read-only telemetry reports context and roster without inventing a queue or crossing sessions',async()=>{
  await fixture(async({root,events,hooks})=>{
    const timers=[],cleared=[],state={tokens:930};
    await observer({on:(name,fn)=>hooks.set(name,fn),exec:async()=>({code:1}),getAllTools:()=>[],getCommands:()=>[],
      getSessionName:()=> 'Observed session',getThinkingLevel:()=> 'high'});
    const context=id=>({cwd:root,agent:{kind:'main'},sessionManager:{getSessionId:()=>id},
      model:{id:'test',provider:'provider',contextWindow:1000},
      getContextUsage:()=>({tokens:state.tokens,contextWindow:1000,percent:state.tokens/10}),
      getAsyncJobSnapshot:()=>({jobs:[{id:'job-1',name:'Build',status:'running',credentials:'PRIVATE'}]}),
      hasPendingMessages:()=>true,sessionModes:{getState:()=>({plan:'active',goal:'inactive'})},
      setInterval:fn=>{timers.push(fn);return timers.length},clearTimer:id=>cleared.push(id),
      memory:{status:async()=>({backend:'off'})}});
    const alpha=context('alpha');await hooks.get('session_start')({},alpha);await flush();
    const first=events.find(event=>event.type==='SESSION_OBSERVATION');
    assert.equal(first.observation.context.percent,93);assert.equal(first.observation.context.estimated,false);
    assert.deepEqual(first.observation.queue,{available:false,pending:true});
    assert.equal(first.observation.jobs.items[0].id,'job-1');assert.ok(!JSON.stringify(first).includes('PRIVATE'));
    const count=events.filter(e=>e.type==='SESSION_OBSERVATION').length;
    timers[0]();await flush();assert.equal(events.filter(e=>e.type==='SESSION_OBSERVATION').length,count);
    state.tokens=100;timers[0]();await flush();
    assert.equal(events.filter(e=>e.type==='SESSION_OBSERVATION').at(-1).observation.context.percent,10);
    await hooks.get('session_switch')({},context('beta'));await flush();
    const before=events.length;timers[0]();await flush();assert.equal(events.length,before);
    hooks.get('session_shutdown')();assert.ok(cleared.length>=2);
  });
});
