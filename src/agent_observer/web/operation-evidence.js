(() => {
  'use strict';
  const kinds = new Set(['mcp', 'skill', 'integration']);
  const shortName = value => String(value || '').trim().replace(/^(?:functions|collaboration)\./, '').toLowerCase();
  const cleanPath = value => String(value || '').trim().replace(/:\d+(?:-\d+)?(?::\d+)?$/, '');
  function inputFor(event) {
    const value = event.input ?? event.args ?? event.arguments ?? event.parameters;
    if (typeof value !== 'string') return value;
    try { return JSON.parse(value); } catch { return value; }
  }
  function commandFor(event) {
    if (typeof event.command === 'string') return event.command;
    const input = inputFor(event);
    if (input && typeof input === 'object') return typeof input.command === 'string' ? input.command : typeof input.cmd === 'string' ? input.cmd : '';
    return /^(?:bash|shell|shell_command|exec_command|exec|run_command|terminal|zsh|sh)$/.test(shortName(event.tool || event.tool_name)) && typeof input === 'string' ? input : '';
  }
  function explicitPaths(event) {
    const values = [];
    const add = value => { if (typeof value === 'string') values.push(cleanPath(value)); else if (Array.isArray(value)) value.forEach(add); else if (value && typeof value === 'object') visit(value); };
    const visit = object => {
      if (!object || typeof object !== 'object') return;
      for (const key of ['target_id', 'path', 'file_path', 'filepath', 'filename', 'file', 'target_path', 'source_path', 'dest_path', 'destination']) add(object[key]);
      add(object.paths); add(object.files); add(object.file_paths);
      for (const key of ['changes', 'file_changes', 'edits']) if (Array.isArray(object[key])) object[key].forEach(visit);
      if (object.patch && typeof object.patch === 'object') visit(object.patch);
      if (object.edit && typeof object.edit === 'object') visit(object.edit);
      if (typeof object.kind?.move_path === 'string') add(object.kind.move_path);
    };
    visit(event); visit(inputFor(event));
    return values;
  }
  function context(config, snapshot, intrinsicNames = []) {
    const inventory = snapshot?.capability_inventory || config.capability_inventory || {};
    const nodes = new Map(), memories = new Map();
    for (const node of [...config.nodes || [], ...Object.values(snapshot?.nodes || {})]) {
      if (kinds.has(node.kind)) nodes.set(node.id, node);
      if (node.kind === 'memory') memories.set(node.id, node);
    }
    const mcpByTool = new Map(), mcpByName = new Map(), nativeNames = new Map();
    for (const node of nodes.values()) if (node.kind === 'mcp') {
      mcpByName.set(String(node.name || node.label), node);
      for (const tool of [...node.tool_names || [], ...node.match?.tool_names || []]) mcpByTool.set(String(tool).toLowerCase(), node);
    }
    for (const server of inventory.mcp || []) {
      const node = mcpByName.get(server.name);
      if (node) for (const tool of server.tool_names || []) mcpByTool.set(String(tool).toLowerCase(), node);
    }
    for (const descriptor of inventory.native_tools || config.native_tools || []) {
      const item = typeof descriptor === 'string' ? {name:descriptor} : descriptor;
      if (item?.name) nativeNames.set(shortName(item.name), item);
    }
    for (const node of Object.values(snapshot?.nodes || {})) if (node.kind === 'command' && !node.inventory_resource && node.label && !nativeNames.has(shortName(node.label))) nativeNames.set(shortName(node.label), {name:node.label,description:node.description||''});
    return {inventory, nodes:[...nodes.values()], memories:[...memories.values()], mcpByTool, mcpByName, nativeNames, intrinsic:new Set(intrinsicNames.map(shortName)), services:(config.nodes || []).filter(node => node.kind === 'tool'), skills:[...nodes.values()].filter(node => node.kind === 'skill').sort((a, b) => String(b.name || b.label).length - String(a.name || a.label).length)};
  }
  function operationKind(name, ctx) {
    const raw = String(name || '').trim(), short = shortName(raw);
    if (ctx.mcpByTool.has(raw.toLowerCase()) || /^mcp(?:__|[.:])/.test(raw.toLowerCase())) return 'mcp';
    if (ctx.intrinsic.has(short)) return 'command';
    if (ctx.services.some(node => shortName(node.label) === short || (node.match?.tool_names || []).some(alias => shortName(alias) === short))) return 'tool';
    return ctx.nativeNames.has(short) ? 'command' : 'tool';
  }
  function skillForPath(path, ctx) {
    const value = cleanPath(path);
    return ctx.skills.find(node => {
      const name = String(node.name || node.label), uri = 'skill://' + name;
      return value === uri || value.startsWith(uri + '/') || value === node.path || value === node.absolute_path;
    }) || null;
  }
  function mcpForCall(event, ctx) {
    const name = String(event.tool || event.tool_name || '').toLowerCase();
    if (event.mcp_server && ctx.mcpByName.has(event.mcp_server)) return ctx.mcpByName.get(event.mcp_server);
    if (ctx.mcpByTool.has(name)) return ctx.mcpByTool.get(name);
    // Reading xd:// describes a schema; writing to the device invokes it.
    if (/^(?:write|write_file)$/.test(shortName(name))) for (const path of explicitPaths(event)) {
      const match = /^xd:\/\/([^/?#:]+)/.exec(path);
      if (match && ctx.mcpByTool.has(match[1].toLowerCase())) return ctx.mcpByTool.get(match[1].toLowerCase());
    }
    return null;
  }
  function matchesCapability(node, event, ctx) {
    if (node.kind === 'mcp') {
      const names = node.tool_names || node.match?.tool_names || [];
      if (event.mcp_server === (node.name || node.label)) return true;
      if (names.some(name => String(name).toLowerCase() === String(event.tool || event.tool_name || '').toLowerCase())) return true;
      if (/^(?:write|write_file)$/.test(shortName(event.tool || event.tool_name))) return explicitPaths(event).some(path => { const match = /^xd:\/\/([^/?#:]+)/.exec(path); return match && names.includes(match[1]); });
      return mcpForCall(event, ctx)?.id === node.id;
    }
    if (node.kind === 'skill') {
      if (event.skill_name === (node.name || node.label)) return true;
      const paths = /^(?:read|read_file)$/.test(shortName(event.tool || event.tool_name))
        ? explicitPaths(event)
        : (Array.isArray(event.file_actions) ? event.file_actions : []).filter(action=>action?.action === 'read' && typeof action.path === 'string').map(action=>action.path);
      return paths.some(path => skillForPath(path, ctx)?.id === node.id);
    }
    if (node.kind === 'integration' && shortName(node.name || node.label) === 'rtk') return /^(?:bash|shell|shell_command|exec|exec_command|run_command|terminal)$/.test(shortName(event.tool || event.tool_name)) && rtkRanges(commandFor(event)).length > 0;
    return false;
  }

  function matchesMemory(node, event) {
    if (node.enabled === false) return false;
    if (!['recall','retain','reflect','learn'].includes(event.memory_operation)) return false;
    const identity = event.memory_system_id, endpoint = event.memory_endpoint, bank = event.memory_bank_id;
    if (identity && identity !== node.memory_system_id) return false;
    if (endpoint && String(endpoint).replace(/\/+$/, '') !== node.endpoint) return false;
    if (bank && node.bank_ids?.length && !node.bank_ids.includes(bank)) return false;
    const name = String(event.tool || event.tool_name || '').toLowerCase();
    const alias = (node.match?.tool_names || []).some(value => String(value).toLowerCase() === name)
      && /[._:/]/.test(name) && !['read','write','recall','retain','reflect','learn'].includes(shortName(name));
    return Boolean(identity || endpoint && bank || alias);
  }
  function memoryForCall(event, ctx) {
    const matches = ctx.memories.filter(node => matchesMemory(node, event));
    return matches.length === 1 ? matches[0] : null;
  }

  // A small lexical scan, not shell evaluation: quoted strings never introduce commands.
  function shellTokens(text) {
    const tokens = [];
    for (let i = 0; i < text.length;) {
      if (text[i] === '\n') { tokens.push({operator:true, value:'\n', start:i, end:++i}); continue; }
      if (/\s/.test(text[i])) { i++; continue; }
      if (text[i] === '#') { while (i < text.length && text[i] !== '\n') i++; continue; }
      if (/[;&|]/.test(text[i])) { const start = i++, char = text[start]; if (text[i] === char) i++; tokens.push({operator:true,value:text.slice(start,i),start,end:i}); continue; }
      const start = i;
      let value = '', quote = '';
      while (i < text.length) {
        const char = text[i];
        if (!quote && (/\s/.test(char) || /[;&|]/.test(char))) break;
        if (char === '\\' && quote !== "'" && i + 1 < text.length) { value += text[i + 1]; i += 2; continue; }
        if (char === "'" || char === '"') {
          if (!quote) { quote = char; i++; continue; }
          if (quote === char) { quote = ''; i++; continue; }
        }
        value += char; i++;
      }
      tokens.push({operator:false,value,start,end:i});
    }
    return tokens;
  }
  function executions(text) {
    const tokens = shellTokens(text), result = [];
    let segment = [];
    const flush = end => {
      let index = 0;
      while (index < segment.length && (/^[A-Za-z_]\w*=/.test(segment[index].value) || ['command','exec'].includes(segment[index].value))) index++;
      if (segment[index]?.value === 'env') {
        index++;
        while (index < segment.length) {
          const word = segment[index].value;
          if (/^[A-Za-z_]\w*=/.test(word) || word === '-i' || word === '--ignore-environment' || word === '--') { index++; continue; }
          if (word === '-u' || word === '--unset') { index += 2; continue; }
          break;
        }
      }
      if (segment[index]) result.push({tokens:segment.slice(index), end});
      segment = [];
    };
    for (const token of tokens) { if (token.operator) flush(token.start); else segment.push(token); }
    flush(text.length);
    return result;
  }
  function rtkRanges(text) {
    if (!text) return [];
    return executions(text).filter(segment => /(?:^|\/)rtk$/.test(segment.tokens[0].value)).map(segment => ({start:segment.tokens[0].start,end:segment.tokens[0].end}));
  }
  function commandCandidates(event) {
    const command = commandFor(event);
    if (!command) return [];
    return executions(command).map(segment => {
      let index = 0;
      if (/(?:^|\/)rtk$/.test(segment.tokens[index].value)) { index++; if (segment.tokens[index]?.value === 'proxy') index++; }
      if (!segment.tokens[index] || segment.tokens[index].value.startsWith('-')) return '';
      return command.slice(segment.tokens[index].start,segment.end).trim();
    }).filter(Boolean);
  }
  function colorText(text, ranges, escape) {
    let result = '', index = 0;
    for (const range of ranges) { result += escape(text.slice(index,range.start)) + '<span class="rtk-prefix">' + escape(text.slice(range.start,range.end)) + '</span>'; index = range.end; }
    return result + escape(text.slice(index));
  }
  function formatInput(event, raw, escape) {
    const text = typeof raw === 'string' ? raw : raw == null ? '' : JSON.stringify(raw,null,2), command = commandFor(event), ranges = rtkRanges(command);
    if (!ranges.length) return escape(text);
    if (text === command) return colorText(text,ranges,escape);
    const direct = text.indexOf(command);
    if (direct >= 0) return colorText(text,ranges.map(range=>({start:direct+range.start,end:direct+range.end})),escape);
    const encoded = JSON.stringify(command), at = text.indexOf(encoded);
    if (at < 0) return escape(text);
    const offset = index => JSON.stringify(command.slice(0,index)).length - 1;
    return colorText(text,ranges.map(range => ({start:at+offset(range.start),end:at+offset(range.end)})),escape);
  }
  function summarizeUsage(calls, resolveTargets, groupForTarget) {
    const counts = new Map(), groups = new Map();
    for (const call of calls) {
      const callGroups = new Set();
      for (const id of new Set(resolveTargets(call))) {
        counts.set(id, (counts.get(id) || 0) + 1);
        const group = groupForTarget(id);
        if (group) callGroups.add(group);
      }
      for (const group of callGroups) groups.set(group, (groups.get(group) || 0) + 1);
    }
    return { counts, groups };
  }
  window.ObserverOperationEvidence = {kinds, shortName, inputFor, commandFor, explicitPaths, context, operationKind, skillForPath, mcpForCall, matchesCapability, matchesMemory, memoryForCall, rtkRanges, commandCandidates, formatInput, summarizeUsage};
})();
