import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import observer from '../../../integrations/omp/observer.mjs';

test('Queued OMP edits preserve pre-mutation file text and publish final text without thinking', {timeout:5000}, async()=>{
  const root=mkdtempSync(join(tmpdir(),'observer-omp-results-')),file=join(root,'source.py'),config=join(root,'observer.project.json');
  writeFileSync(config,JSON.stringify({project:{root},port:1,storage_profile:'project'}));writeFileSync(file,'VALUE = 1\n');
  const oldConfig=process.env.AGENT_OBSERVER_CONFIG,oldToken=process.env.AGENT_OBSERVER_TOKEN,nativeFetch=global.fetch;
  process.env.AGENT_OBSERVER_CONFIG=config;process.env.AGENT_OBSERVER_TOKEN='fixture-token';
  const events=[],hooks=new Map();let release,complete;
  const gate=new Promise(resolve=>release=resolve),delivered=new Promise(resolve=>complete=resolve);
  global.fetch=async(_url,options)=>{await gate;events.push(JSON.parse(options.body));if(events.some(e=>e.type==='GENERATION_END')&&events.some(e=>e.type==='RUNTIME_INVENTORY'))complete();return {ok:true,status:200};};
  try{
    await observer({on:(name,callback)=>hooks.set(name,callback),getAllTools:()=>[{name:'read',sourceInfo:{source:'builtin'}},{name:'edit',sourceInfo:{source:'builtin'}}],getCommands:()=>[],exec:async()=>({code:1})});
    const context={cwd:root,sessionManager:{getSessionId:()=> 'isolated-session'}};
    hooks.get('before_agent_start')({prompt:'Update the source.'},context);
    hooks.get('tool_execution_start')({toolCallId:'edit-one',toolName:'edit',args:{path:'source.py'}});
    writeFileSync(file,'VALUE = 2\n');
    hooks.get('tool_execution_end')({toolCallId:'edit-one',toolName:'edit',result:'Updated source.',isError:false});
    hooks.get('agent_end')({messages:[{role:'assistant',content:[{type:'thinking',thinking:'private thought'},{type:'text',text:'Published answer.'}]}]},context);
    release();await delivered;
    const before=events.find(e=>e.type==='TOOL_CALL_START').file_snapshots[0],after=events.find(e=>e.type==='TOOL_CALL_END').file_snapshots[0];
    assert.equal(before.content,'VALUE = 1\n');assert.equal(after.content,'VALUE = 2\n');
    assert.equal(events.find(e=>e.type==='GENERATION_END').result,'Published answer.');assert.ok(!JSON.stringify(events).includes('private thought'));
    const inventories=events.filter(e=>e.type==='RUNTIME_INVENTORY');assert.equal(inventories.length,1);assert.deepEqual(inventories[0].inventory.native_tools.map(tool=>tool.name),['read','edit']);
    hooks.get('session_shutdown')();
  }finally{
    release();global.fetch=nativeFetch;if(oldConfig===undefined)delete process.env.AGENT_OBSERVER_CONFIG;else process.env.AGENT_OBSERVER_CONFIG=oldConfig;if(oldToken===undefined)delete process.env.AGENT_OBSERVER_TOKEN;else process.env.AGENT_OBSERVER_TOKEN=oldToken;rmSync(root,{recursive:true,force:true});
  }
});
