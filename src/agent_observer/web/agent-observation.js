(() => {
  'use strict';
  const make = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls; if (text != null) el.textContent = String(text); return el; };
  const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const amount = value => number(value) ? new Intl.NumberFormat('en', {notation: 'compact', maximumFractionDigits: 1}).format(value) : '—';
  const printable = value => value == null ? 'Not reported' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  class ObserverAgentObservation {
    constructor(card) {
      this.card = card; this.active = 'log'; this.session = ''; this.signature = '';
      this.tabs = make('div', 'agent-observation-tabs'); this.tabs.setAttribute('role', 'tablist'); this.tabs.setAttribute('aria-label', 'Live agent information');
      this.panels = new Map(); this.buttons = new Map();
      for (const [id, title] of [['log', 'Log'], ['commands', '/commands'], ['model', '/model'], ['session', '/session']]) {
        const tab = make('button', '', title); tab.type = 'button'; tab.id = 'agent-tab-' + id;
        tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'agent-panel-' + id);
        tab.addEventListener('click', () => this.select(id));
        tab.addEventListener('keydown', event => {
          const ids = [...this.buttons.keys()], index = ids.indexOf(id);
          const next = event.key === 'ArrowRight' ? ids[(index + 1) % ids.length] : event.key === 'ArrowLeft' ? ids[(index + ids.length - 1) % ids.length] : event.key === 'Home' ? ids[0] : event.key === 'End' ? ids.at(-1) : null;
          if (next) { event.preventDefault(); event.stopPropagation(); this.select(next); this.buttons.get(next).focus(); }
        });
        this.buttons.set(id, tab); this.tabs.append(tab);
        const panel = id === 'log' ? card.querySelector('#activity-list') : make('div', 'agent-observation-panel');
        panel.id = id === 'log' ? 'activity-list' : 'agent-panel-' + id;
        tab.setAttribute('aria-controls', panel.id);
        panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id); panel.tabIndex = 0;
        this.panels.set(id, panel);
      }
      card.querySelector('.card-title').after(this.tabs);
      for (const [id, panel] of this.panels) if (id !== 'log') card.append(panel);
      this.ribbon = make('section', 'agent-context'); this.ribbon.setAttribute('aria-label', 'Session context');
      const line = make('div', 'agent-context-heading'); this.contextLabel = make('span', '', 'Context'); this.contextValue = make('strong'); line.append(this.contextLabel, this.contextValue);
      this.track = make('div', 'agent-context-track'); this.track.setAttribute('role', 'progressbar'); this.track.setAttribute('aria-label', 'Occupied context'); this.track.setAttribute('aria-valuemin', '0'); this.track.setAttribute('aria-valuemax', '100');
      this.fill = make('div', 'agent-context-fill'); this.track.append(this.fill);
      this.contextNote = make('div', 'agent-context-note'); this.contextNote.setAttribute('role', 'status');
      this.ribbon.append(line, this.track, this.contextNote); document.getElementById('agent-phase').after(this.ribbon);
      this.select('log');
    }
    select(id) {
      this.active = id;
      for (const [key, button] of this.buttons) { button.setAttribute('aria-selected', String(key === id)); button.tabIndex = key === id ? 0 : -1; this.panels.get(key).hidden = key !== id; }
    }
    fields(target, values) {
      const list = make('dl', 'agent-observation-fields');
      for (const [name, value] of values) { list.append(make('dt', '', name), make('dd', '', printable(value))); }
      target.append(list);
    }
    command(target, slash, name, status, content) {
      const details = make('details', 'agent-command'); details.open = true;
      const summary = make('summary'); summary.append(make('code', '', slash), make('strong', '', name));
      if (status) summary.append(make('span', 'agent-command-status', status));
      const body = make('div', 'agent-command-content');
      if (typeof content === 'function') content(body); else body.append(make('p', '', content));
      details.append(summary, body); target.append(details);
    }
    render(snapshot, events, config) {
      const obs = snapshot.session_observation || {}, session = obs.session || {}, model = obs.model || {}, context = obs.context || {}, compact = obs.compaction || {};
      const sid = session.id || snapshot.context?.session_id || config.session_id || '';
      if (sid !== this.session) { this.session = sid; this.signature = ''; this.commandsSignature = ''; this.select('log'); for (const panel of this.panels.values()) panel.scrollTop = 0; }
      const signature = JSON.stringify([obs, snapshot.context, snapshot.orchestrator_phase, events.length, events.at(-1)?.event, config.project_root]);
      if (signature === this.signature) return; this.signature = signature;
      this.renderContext(context, model, compact);
      const modelPanel = this.panels.get('model'); modelPanel.replaceChildren();
      const modelTitle = make('div', 'agent-observation-heading'); modelTitle.append(make('code', '', '/model'), make('strong', '', 'Model & execution profile')); modelPanel.append(modelTitle);
      this.fields(modelPanel, [['Model', model.id || snapshot.context?.model], ['Provider', model.provider], ['Effort', model.effort], ['Service tier', model.service_tier], ['Context window', context.window || model.context_window]]);
      modelPanel.append(make('p', 'agent-observation-muted', 'Selected/reported model metadata. Private reasoning is never displayed.'));
      const sessionPanel = this.panels.get('session'); sessionPanel.replaceChildren();
      const sessionTitle = make('div', 'agent-observation-heading'); sessionTitle.append(make('code', '', '/session'), make('strong', '', 'Current session')); sessionPanel.append(sessionTitle);
      this.fields(sessionPanel, [['Agent', session.application || snapshot.context?.application || config.agent_label], ['Session', sid || null], ['Name', session.name || snapshot.context?.session_name], ['Working directory', session.cwd || snapshot.context?.cwd], ['Observed project', config.project_root], ['Additional directories', session.additional_directories], ['Phase', snapshot.orchestrator_phase], ['Measurement source', context.source], ['Measured at', context.observed_at]]);
      const commands = this.panels.get('commands');
      const disclosures = new Map([...commands.querySelectorAll('details')].map(el => [el.querySelector('code')?.textContent, el.open]));
      const commandsSignature = JSON.stringify([obs.queue, obs.todos, obs.mode, obs.jobs, obs.approval, obs.compaction, obs.retry, obs.memory]);
      if (commandsSignature === this.commandsSignature) return;
      this.commandsSignature = commandsSignature;
      commands.replaceChildren(make('p', 'agent-observation-muted', 'Observation only · slash labels describe state; they do not execute commands.'));
      const queue = obs.queue || {};
      this.command(commands, '/queue', 'Pending messages', queue.available ? String((queue.steering?.length || 0) + (queue.follow_up?.length || 0)) : queue.pending ? 'Pending' : 'Not exposed', body => {
        if (!queue.available) { body.append(make('p', '', queue.pending ? 'OMP reports pending messages. Their text and count are not exposed by this connection.' : 'This source does not expose queue snapshots. This is not an empty queue.')); return; }
        for (const [title, items] of [['Steering · current turn', queue.steering || []], ['Follow-up · after this turn', queue.follow_up || []]]) {
          body.append(make('h4', '', title)); const list = make('ol'); for (const text of items) list.append(make('li', '', text)); body.append(items.length ? list : make('p', 'agent-observation-muted', 'No messages'));
        }
      });
      const phases = obs.todos?.phases;
      this.command(commands, '/todo', 'Plan & progress', null, body => {
        if (!Array.isArray(phases)) { body.append(make('p', '', 'No plan snapshot reported yet.')); return; }
        for (const phase of phases) { body.append(make('h4', '', phase.name || phase.phase || 'Tasks')); const list = make('ul', 'agent-todo-list'); for (const task of phase.tasks || phase.items || []) { const row = make('li', '', typeof task === 'string' ? task : task.content || task.task || task.name); row.dataset.status = task.status || 'pending'; list.append(row); } body.append(list); }
      });
      this.command(commands, '/plan', 'Session modes', null, body => this.fields(body, Object.entries(obs.mode || {}).length ? Object.entries(obs.mode) : [['Mode', null]]));
      const jobs = obs.jobs;
      this.command(commands, '/jobs', 'Background work & subagents', jobs?.available ? String(jobs.items?.length || 0) : null, body => {
        if (!jobs?.available) { body.append(make('p', '', 'Live job roster is not exposed by this connection. Recorded task/wait calls remain in Log.')); return; }
        if (!jobs.items?.length) body.append(make('p', '', 'No tracked jobs'));
        for (const job of jobs.items || []) { const item = make('div', 'agent-job'); item.append(make('strong', '', job.name || job.id || 'Job'), make('span', '', job.status || job.state || 'Reported')); if (job.description) item.append(make('p', '', job.description)); body.append(item); }
      });
      const approval = obs.approval || {};
      this.command(commands, '/ask', 'User input & approvals', approval.status || null, approval.title || 'No approval state reported. This does not assert that none is pending.');
      this.command(commands, '/compact', 'Context maintenance · /handoff', compact.status || null, body => {
        this.fields(body, [['Status', compact.status], ['Method', compact.method], ['Before', compact.tokens_before], ['After', compact.tokens_after], ['Recorded at', compact.at]]);
        body.append(make('p', 'agent-observation-muted', 'Manual and automatic compaction are observed. Execution from the graph is disabled.'));
      });
      this.command(commands, '/retry', 'Recovery', obs.retry?.status || null, body => this.fields(body, [['Status', obs.retry?.status], ['Attempt', obs.retry?.attempt], ['Delay (ms)', obs.retry?.delay_ms], ['Reason', obs.retry?.reason]]));
      const memory = obs.memory || {};
      this.command(commands, '/memory', 'Memory activity', memory.status || null, memory.operation ? printable(memory.operation) : 'Direct memory calls are recorded in Log. Automatic retention is not inferred from enabled settings.');
      for (const details of commands.querySelectorAll('details')) { const saved = disclosures.get(details.querySelector('code')?.textContent); if (saved != null) details.open = saved; }
    }
    renderContext(context, model, compact) {
      const window = number(context.window) && context.window > 0 ? context.window : model.context_window;
      const tokens = context.tokens, percent = number(context.percent) ? Math.min(100, context.percent) : number(tokens) && number(window) && window > 0 ? Math.min(100, tokens / window * 100) : null;
      const tier = percent == null ? 'unknown' : percent >= 95 ? 'critical' : percent >= 85 ? 'warning' : 'normal';
      this.ribbon.dataset.level = tier; this.fill.style.width = (percent ?? 0) + '%';
      if (percent == null) { this.track.removeAttribute('aria-valuenow'); this.track.setAttribute('aria-valuetext', 'Context usage not reported'); }
      else { this.track.setAttribute('aria-valuenow', String(Math.round(percent))); this.track.setAttribute('aria-valuetext', `${Math.round(percent)} percent occupied${context.estimated ? ', estimated' : ''}`); }
      this.contextValue.textContent = percent == null ? number(window) ? '— / ' + amount(window) : 'Not reported' : `${context.estimated ? '~' : ''}${Math.round(percent)}% · ${amount(tokens)} / ${amount(window)}`;
      const note = context.source === 'awaiting_post_compaction_usage' ? 'Compacted · awaiting a new context measurement' : tier === 'critical' ? 'Context nearly full · automatic compaction may follow' : tier === 'warning' ? 'Context is getting full · automatic compaction is monitored' : percent == null ? 'Waiting for context metadata from this session' : context.estimated ? 'Latest recorded provider load · estimated current context' : 'Live session context';
      if (this.contextNote.textContent !== note) this.contextNote.textContent = note;
      this.ribbon.title = `Context: ${printable(tokens)} / ${printable(window)} tokens\nSource: ${context.source || 'not reported'}${context.observed_at ? '\nMeasured: ' + context.observed_at : ''}${compact.at ? '\nLast compaction: ' + compact.at : ''}`;
    }
  }
  window.ObserverAgentObservation = ObserverAgentObservation;
})();
