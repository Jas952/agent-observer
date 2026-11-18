(() => {
  'use strict';
  const titles = {command:'Commands',mcp:'MCP',skill:'Skills',integration:'CLI integrations',tool:'Tools & services',memory:'Memory systems',document:'Documents',file:'Working files',other:'Other operations'};
  function summarize(calls, targets, nodes, isCommand, events) {
    const rows = new Map();
    for (const call of calls) {
      const matched = new Set();
      for (const id of targets.get(call) || []) {
        const node = nodes[id]; if (!node) continue;
        const kind = isCommand(node) ? 'command' : node.kind;
        if (!titles[kind]) continue;
        const name = node.label || node.name || id, key = kind + '\0' + name;
        if (matched.has(key)) continue; matched.add(key);
        if (!rows.has(key)) rows.set(key,{kind,name,count:0,failed:0,pending:0});
        const row = rows.get(key); row.count++; row.failed += Number(Boolean(call.failed)); row.pending += Number(call.endStep == null);
      }
      if (!matched.size) {
        const name = call.tool, key = 'other\0' + name;
        if (!rows.has(key)) rows.set(key,{kind:'other',name,count:0,failed:0,pending:0});
        const row = rows.get(key); row.count++; row.failed += Number(Boolean(call.failed)); row.pending += Number(call.endStep == null);
      }
    }
    const first = Date.parse(events[0]?.timestamp), last = Date.parse(events.at(-1)?.timestamp);
    return {total:calls.length,failed:calls.filter(call=>call.failed).length,pending:calls.filter(call=>call.endStep==null).length,
      intervalMs:Number.isFinite(first)&&Number.isFinite(last)&&last>=first?last-first:null,
      rows:[...rows.values()].sort((a,b)=>b.count-a.count||a.name.localeCompare(b.name))};
  }
  const make = (tag, text, cls) => {const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
  const fact = (holder, label, value) => {if(holder.childNodes.length)holder.append(document.createTextNode(' · '));holder.append(make('span',label),document.createTextNode(' '),make('strong',value));};
  function render(report, files) {
    const section=make('section',null,'result-report');
    section.append(make('h3','Execution report'));
    if (!report) section.append(make('p','Recorded call details are unavailable.'));
    else {
      const summary=make('p');fact(summary,'Calls recorded:',report.total);fact(summary,'Failed:',report.failed);fact(summary,'No completion captured:',report.pending);section.append(summary);
      if(report.intervalMs!=null){const elapsed=make('p',null,'result-report-note');fact(elapsed,'Recorded interval:',(report.intervalMs/1000).toFixed(2)+' s');section.append(elapsed);}
      section.append(make('p','Counts reflect the selected route at the selected step. Categories can overlap.', 'result-report-note'));
      for(const [kind,title] of Object.entries(titles)){
        const rows=report.rows.filter(row=>row.kind===kind);if(!rows.length)continue;
        section.append(make('h4',title));const list=make('ul');
        for(const row of rows){const item=make('li');item.append(make('code',row.name),document.createTextNode(' — '));const counts=make('span');fact(counts,'Calls:',row.count);if(row.failed)fact(counts,'Failed:',row.failed);if(row.pending)fact(counts,'No completion captured:',row.pending);item.append(counts);list.append(item);}section.append(list);
      }
      if(!report.rows.some(row=>row.kind==='mcp'))section.append(make('p','No MCP calls captured.','result-report-note'));
    }
    section.append(make('h4','File changes'));
    if(!files?.length)section.append(make('p','No file changes captured.','result-report-note'));
    else {
      const list=make('ul');for(const file of files){const item=make('li');item.append(make('code',file.path||file.name),document.createTextNode(' — '),make('span',({added:'Added',deleted:'Deleted',modified:'Modified',output:'Output'})[file.change]||'Observed file'));
        if(Number.isInteger(file.added)&&Number.isInteger(file.removed))item.append(document.createTextNode(' '),make('strong','+'+file.added,'result-added'),document.createTextNode(' / '),make('strong','−'+file.removed,'result-removed'));
        else if(['code','text'].includes(file.kind))item.append(document.createTextNode(' · '),make('span','Line counts not captured.'));
        if(file.success===false)item.append(document.createTextNode(' · '),make('strong','Failed operation'));
        list.append(item);
      }section.append(list);
    }
    return section;
  }
  window.ObserverResultReport={summarize,render};
})();
