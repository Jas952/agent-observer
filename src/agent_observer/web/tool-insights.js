(() => {
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;};
  const text=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
  const number=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
  function metrics(event){
    const raw=event?.token_metrics||event?.result?.details?.token_metrics;
    if(!raw||!number(raw.output_tokens))return null;
    return {output:raw.output_tokens,without:number(raw.without_tokens)?raw.without_tokens:null,estimated:raw.estimated!==false,source:raw.source||'Reported tool metrics'};
  }
  function summary(node,calls){
    let output=0,without=0,measured=0,baselines=0,estimated=false;const sources=new Set();
    for(const call of calls){const value=metrics(call.endEvent);if(!value)continue;output+=value.output;measured++;estimated||=value.estimated;sources.add(value.source);if(value.without!=null){without+=value.without;baselines++;}}
    const savingTool=measured>0||/(?:^|\b)(rtk|headroom|jcodemunch)(?:\b|$)/i.test(node.name||node.label||'');
    if(!savingTool)return null;
    // A text-size estimate is not a model tokenizer and never invents a baseline.
    if(!measured){for(const call of calls){const end=call.endEvent;if(end){const value=end.result??end.output;if(value!=null){output+=Math.ceil(text(value).length/4);measured++;}}}estimated=measured>0;sources.add('Output character estimate (~4 chars/token)');}
    return {output,without:baselines&&baselines===measured?without:null,measured,baselines,estimated,partial:baselines!==measured||measured!==calls.filter(call=>call.endEvent).length,sources:[...sources]};
  }
  class ObserverToolInsights{
    constructor(){this.opened=new Set();}
    retainExpansion(details,key){details.open=this.opened.has(key);details.addEventListener('toggle',()=>{if(details.isConnected){if(details.open)this.opened.add(key);else this.opened.delete(key);}});}
    updateCard(card,node,calls){
      if(!card)return;const value=summary(node,calls);let meter=card.querySelector('.tool-efficiency');if(!value){meter?.remove();return;}
      if(!meter){meter=el('div','tool-efficiency');card.querySelector('.card-title').after(meter);}
      const format=n=>Math.round(n).toLocaleString('en-US'),prefix=value.estimated?'~':'';
      meter.textContent=value.measured?`${prefix}${format(value.output)} out · ${value.without==null?'—':prefix+format(value.without)} without`:'Tokens · waiting for output';
      const parts=[`${value.measured} recorded output${value.measured===1?'':'s'}`];if(value.baselines)parts.push(`${value.baselines} reported baseline${value.baselines===1?'':'s'}`);if(value.partial)parts.push('Partial coverage');if(value.sources.length)parts.push(value.sources.join('; '));meter.title=parts.join(' · ');
      meter.setAttribute('aria-label',meter.title+' '+meter.textContent);
    }
    renderDetails(holder,node,calls){
      let section=holder.querySelector('.tool-executions');if(!section){section=el('section','tool-executions');holder.append(section);}
      const signature=JSON.stringify(calls.map(call=>[call.ev.call_id,call.endStep,call.endEvent?.token_metrics]));if(section.dataset.signature===signature)return;section.dataset.signature=signature;section.replaceChildren(el('h3','','Recorded executions'));
      if(!calls.length){section.append(el('p','tool-no-executions','No calls'));return;}
      const value=summary(node,calls);if(value){const line=el('p','tool-efficiency-summary');line.textContent=value.measured?`${value.estimated?'Estimated':'Reported'} output: ${Math.round(value.output).toLocaleString('en-US')} tokens · without tool: ${value.without==null?'not recorded':Math.round(value.without).toLocaleString('en-US')+' tokens'}${value.partial?' · partial coverage':''}`:'Waiting for token metrics';line.title=value.sources.join('; ');section.append(line);}
      for(const call of calls){
        const details=el('details','tool-execution'),heading=el('summary'),name=String(call.tool||call.ev.tool||call.ev.tool_name||'Tool');let input=call.ev.input??call.ev.args;try{if(typeof input==='string')input=JSON.parse(input);}catch{}
        heading.append(el('strong','',`#${call.ordinal} · ${name}`),el('span','',call.endEvent?(call.failed?'Failed':'Completed'):'In progress'));details.append(heading);
        this.retainExpansion(details,`${node.id}:${call.startedAt}:${call.ordinal}`);
        if(input?.command)details.append(el('pre','tool-command',input.command));else if(input!=null)details.append(el('pre','tool-command',text(input)));
        const nested=call.endEvent?.nested_calls||call.endEvent?.result?.details?.tool_calls;
        if(Array.isArray(nested)&&nested.length){for(const child of nested){const item=el('div','tool-nested');item.append(el('strong','',String(child.tool||child.command||child.name||'Recorded subcall')));if(child.input!=null)item.append(el('pre','',text(child.input)));if(child.result!=null||child.output!=null)item.append(el('pre','',text(child.result??child.output)));details.append(item);}}
        const output=call.endEvent?.result??call.endEvent?.output;if(output!=null)details.append(el('pre','tool-output',text(output)));else details.append(el('p','tool-no-executions','Output not recorded.'));section.append(details);
      }
      const final=[...calls].reverse().find(call=>call.endEvent&&(call.endEvent.result!=null||call.endEvent.output!=null));if(final){const tail=el('details','tool-final-output');tail.append(el('summary','',`Latest output · #${final.ordinal}`),el('pre','tool-output',text(final.endEvent.result??final.endEvent.output)));this.retainExpansion(tail,`${node.id}:final`);section.append(tail);}
    }
  }
  ObserverToolInsights.summary=summary;window.ObserverToolInsights=ObserverToolInsights;
})();
