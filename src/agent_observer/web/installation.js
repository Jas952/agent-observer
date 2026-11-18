(() => {
  'use strict';
  const paths = {
    plug: '<path d="M8 3v5m8-5v5M5 8h14v3a7 7 0 0 1-14 0zM12 18v4"/>',
    folder: '<path d="M3 7h6l2-3h8a2 2 0 0 1 2 2v13H3z"/>',
    pin: '<path d="m9 3 6 0-1 6 4 4H6l4-4zM12 13v8"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    chevron: '<path d="m6 15 6-6 6 6"/>',
    terminal: '<path d="m5 6 6 6-6 6m8 0h6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H3V3h12v2"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    github: '<path d="M9 19c-4.3 1.3-4.3-2.5-6-3m12 6v-3.9a3.4 3.4 0 0 0-.9-2.6c3.1-.4 6.4-1.5 6.4-7A5.4 5.4 0 0 0 19 4.8 5.1 5.1 0 0 0 18.9 1S17.7.7 15 2.5a13.4 13.4 0 0 0-7 0C5.3.7 4.1 1 4.1 1A5.1 5.1 0 0 0 4 4.8a5.4 5.4 0 0 0-1.5 3.8c0 5.4 3.3 6.6 6.4 7A3.4 3.4 0 0 0 8 18.2V22"/>',
    plus: '<path d="M12 5v14M5 12h14"/>'
  };
  function icon(name) {
    const wrapper = document.createElement('span');
    wrapper.className = 'connection-icon';
    wrapper.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
    return wrapper;
  }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function button(label, symbol, action, {compact = false, primary = false} = {}) {
    const node = el('button', `connection-button${compact ? ' icon-only' : ''}${primary ? ' primary' : ''}`);
    node.type = 'button'; node.title = label; node.setAttribute('aria-label', label);
    if (symbol) node.append(icon(symbol));
    if (!compact) node.append(el('span', '', label));
    node.addEventListener('click', action);
    return node;
  }

  class ObserverConnections {
    constructor({request, onOpenGraph, onConfigChanged} = {}) {
      if (typeof request !== 'function') throw new TypeError('ObserverConnections requires request');
      this.request = request; this.onOpenGraph = onOpenGraph; this.onConfigChanged = onConfigChanged;
      this.dialog = null; this.toolbarButton = null; this.data = null; this.workspaces = [];
      this.selectedPath = ''; this.busy = false; this.generation = 0;
      this.draftRole = ''; this.analyses = new Map();
      this.draftModule = 'codex';
    }
    mount(toolbar) {
      if (this.toolbarButton) return;
      this.toolbarButton = button('Agent connections', 'folder', () => this.open(), {compact: true});
      this.toolbarButton.classList.add('observer-connections-action');
      this.toolbarButton.id = 'current-project';
      this.toolbarButton.setAttribute('aria-haspopup', 'dialog');
      toolbar.append(this.toolbarButton);
    }
    build() {
      this.dialog = el('dialog', 'observer-connections-dialog');
      this.dialog.setAttribute('aria-labelledby', 'observer-connections-title');
      const head = el('header', 'connections-header');
      const title = el('h2', '', 'Agent connections'); title.id = 'observer-connections-title';
      head.append(icon('plug'), title, button('Close connections', 'close', () => this.close(), {compact: true}));
      const body = el('div', 'connections-body');
      this.sidebar = el('nav', 'connections-sidebar'); this.sidebar.setAttribute('aria-label', 'Project folders');
      this.content = el('section', 'connections-content');
      this.cardObserver = new ResizeObserver(() => this.equalizeCards()); this.cardObserver.observe(this.content);
      const label = el('label', 'connection-path-label', 'Project folder');
      this.pathInput = el('input', 'connection-path'); this.pathInput.type = 'text';
      this.pathInput.id = 'connection-project-path'; this.pathInput.placeholder = '/path/to/project';
      this.pathInput.setAttribute('aria-label', 'Project folder path'); label.htmlFor = this.pathInput.id;
      this.pathInput.addEventListener('input', () => {
        this.selectedPath = this.pathInput.value; this.data = null; this.invalidateAnalysis(); this.generation++;
        this.cards.replaceChildren();
        this.useFolder.disabled = !this.selectedPath.trim() || this.busy;
      });
      this.pathInput.addEventListener('keydown', event => {
        if (event.key === 'Enter') {event.preventDefault(); this.choosePath();}
      });
      const inputRow = el('div', 'connection-path-row');
      this.useFolder = button('Use folder', 'check', () => this.choosePath());
      inputRow.append(this.pathInput,
        button('Browse for project folder', 'folder', () => this.browse(), {compact: true}), this.useFolder);
      this.cards = el('div', 'connection-modules');
      this.content.append(label, inputRow, this.cards);
      body.append(this.sidebar, this.content); this.dialog.append(head, body);
      this.dialog.addEventListener('close', () => this.onClose());
      this.dialog.addEventListener('click', event => { if (event.target === this.dialog) this.close(); });
      document.body.append(this.dialog);
    }
    async open() {
      if (!this.dialog) this.build();
      this.opener = document.activeElement;
      if (!this.dialog.open) this.dialog.showModal();
      const generation = ++this.generation;
      this.message('');
      this.invalidateAnalysis(); this.data = null; this.cards.replaceChildren();
      try {
        const data = await this.request('/api/connections');
        if (generation !== this.generation || !this.dialog.open) return;
        this.workspaces = data.workspaces || [];
        this.selectedPath = data.current_project;
        this.pathInput.value = this.selectedPath; this.data = data;
        this.draftRole = data.workspace.repository_mode || (data.workspace.module === 'codex' ? 'working_repository' : 'agent_system');
        this.draftModule = data.workspace.module === 'omp' ? 'omp' : 'codex';
        this.invalidateAnalysis(); this.message(''); this.render();
      } catch (error) {if (generation === this.generation) this.message(error.message, true);}
    }
    close() {this.dialog?.close();}
    onClose() {
      this.generation++;
      window.ObserverFeedback.clear(this.dialog);
      if (this.opener?.isConnected) this.opener.focus({preventScroll: true});
    }
    message(text, error = false) {
      if (text) window.ObserverFeedback.show(this.dialog, text, {error});
      else window.ObserverFeedback.clear(this.dialog);
    }
    async browse() {
      await this.act(async () => {
        const value = await this.request('/api/connections/choose-folder', {});
        if (value?.path) {
          this.selectedPath = value.path; this.pathInput.value = value.path;
          await this.loadPath(true);
        }
      });
    }
    async choosePath() {
      this.selectedPath = this.pathInput.value.trim();
      if (this.selectedPath) await this.act(() => this.loadPath(true));
    }
    async loadPath(save = false) {
      const path = this.selectedPath, generation = ++this.generation;
      this.invalidateAnalysis();
      if (save) await this.request('/api/connections/workspaces', {project: path});
      const [data, summary] = await Promise.all([
        this.request('/api/connections/inspect', {project: path}),
        this.request('/api/connections')
      ]);
      if (generation !== this.generation || !this.dialog.open) return;
      this.data = data; this.selectedPath = data.workspace.path; this.pathInput.value = this.selectedPath;
      this.draftRole = data.workspace.repository_mode || (data.workspace.module === 'codex' ? 'working_repository' : 'agent_system');
      this.draftModule = data.workspace.module === 'omp' ? 'omp' : 'codex';
      this.workspaces = summary.workspaces || []; this.message(''); this.render();
    }
    async act(action) {
      if (this.busy) return;
      this.busy = true; this.content.setAttribute('aria-busy', 'true'); this.disableActions(true);
      this.message('');
      try {await action();} catch (error) {this.message(error.message || 'Connection failed.', true);}
      finally {
        this.busy = false; this.content.removeAttribute('aria-busy'); this.disableActions(false);
        this.useFolder.disabled = !this.pathInput.value.trim();
      }
    }
    disableActions(disabled) {
      for (const node of this.dialog.querySelectorAll('button:not(.connections-header button)')) node.disabled = disabled || node.dataset.unavailable === 'true';
      this.pathInput.readOnly = disabled;
      this.dialog.querySelectorAll('input[type="radio"]').forEach(input => { input.disabled = disabled; });
    }
    renderSidebar() {
      this.sidebar.replaceChildren(el('h3', '', 'Projects'));
      for (const workspace of this.workspaces) {
        const row = el('div', `connection-workspace${workspace.path === this.selectedPath ? ' selected' : ''}`);
        const select = button(workspace.name, 'folder', () => this.act(async () => {
          this.selectedPath = workspace.path; this.pathInput.value = workspace.path; await this.loadPath();
        }));
        select.setAttribute('translate', 'no');
        select.title = workspace.path; select.setAttribute('aria-current', workspace.path === this.selectedPath ? 'true' : 'false');
        const pin = button(`${workspace.pinned ? 'Unpin' : 'Pin'} ${workspace.name}`, 'pin', () => this.act(async () => {
          await this.request('/api/connections/workspaces', {project: workspace.path, pinned: !workspace.pinned});
          this.workspaces = (await this.request('/api/connections')).workspaces || []; this.renderSidebar();
        }), {compact: true});
        pin.setAttribute('aria-pressed', String(workspace.pinned)); row.append(select, pin); this.sidebar.append(row);
      }
      this.sidebar.append(button('Add project folder', 'plus', () => {
        this.selectedPath = ''; this.pathInput.value = ''; this.pathInput.dispatchEvent(new Event('input'));
        this.pathInput.focus();
      }));
    }
    invalidateAnalysis() {
      this.analyses.clear();
    }
    selectRole(role) {
      if (this.busy || !['agent_system', 'working_repository'].includes(role) || role === this.draftRole) return;
      this.draftRole = role; this.invalidateAnalysis(); this.generation++; this.render();
    }
    verified() {
      const report = this.analyses.get(this.draftRole === 'agent_system' ? 'project' : this.draftModule)?.report;
      return report?.ready === true && Boolean(report.analysis_id && report.revision) &&
        report.repository_mode === this.draftRole && report.workspace?.path === this.data?.workspace.path &&
        (this.draftRole !== 'working_repository' || report.module === this.draftModule);
    }
    equalizeCards() {
      if (!this.dialog?.open) return;
      const cards = [...this.cards.querySelectorAll('.connection-agent-list .connection-module')];
      for (const card of cards) card.style.minHeight = '';
      const height = Math.ceil(Math.max(0, ...cards.map(card => card.getBoundingClientRect().height)));
      for (const card of cards) card.style.minHeight = height + 'px';
    }
    animateAnalysis(panel, expanded, from = panel.getBoundingClientRect().height) {
      const opacity = from ? Number(getComputedStyle(panel).opacity) : 0;
      for (const animation of panel.getAnimations()) { animation.onfinish = null; animation.cancel(); }
      panel.hidden = false; panel.inert = !expanded;
      const card = panel.previousElementSibling;
      card.classList.add('analysis-open');
      const finish = () => { panel.hidden = !expanded; card.classList.toggle('analysis-open', expanded); };
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) { finish(); return; }
      const animation = panel.animate([
        { height: from + 'px', opacity },
        { height: (expanded ? panel.scrollHeight : 0) + 'px', opacity: expanded ? 1 : 0 }
      ], { duration: 260, easing: 'cubic-bezier(.2,.7,.2,1)', fill: 'both' });
      animation.onfinish = () => { finish(); animation.cancel(); };
    }
    render() {
      const oldHeights = new Map([...this.cards.querySelectorAll('.connection-analysis-disclosure')].map(panel => [panel.dataset.module, panel.getBoundingClientRect().height]));
      for (const panel of this.cards.querySelectorAll('.connection-analysis-disclosure')) for (const animation of panel.getAnimations()) { animation.onfinish = null; animation.cancel(); }
      const reveals = [];
      this.renderSidebar(); this.cards.replaceChildren();
      if (!this.data) return;
      const role = this.draftRole, moduleId = role === 'agent_system' ? 'project' : this.draftModule;
      const options = el('fieldset', 'connection-role-options');
      options.append(el('legend', '', 'Folder role'));
      for (const [value, name, description] of [
        ['agent_system', 'Agent system', 'This folder contains the agent and its request-handling runtime.'],
        ['working_repository', 'Working repository', 'Codex and OMP can work on this folder as a development workspace.']
      ]) {
        const label = el('label', 'connection-role-option');
        const input = el('input'); input.type = 'radio'; input.name = 'connection-repository-role'; input.value = value; input.checked = role === value;
        input.onchange = () => this.selectRole(value);
        const text = el('span'); text.append(el('strong', '', name), el('small', '', description));
        label.append(input, text); options.append(label);
      }
      this.cards.append(options);
      if (role === 'working_repository') this.cards.append(el('p', 'connection-analysis-hint', 'One graph observes both Codex and OMP sessions for this folder. Analyze either agent to generate it; analyzing the other is optional.'));
      const agents = el('div', 'connection-agent-list');
      for (const id of role === 'working_repository' ? ['codex', 'omp'] : ['project']) {
        const selected = id === moduleId;
        const record = this.analyses.get(id);
        const agent = el('div', 'connection-agent');
        const module = (this.data.modules || []).find(item => item.id === id);
        const card = el('article', `connection-module${selected ? ' selected' : ''}`); card.dataset.module = id;
        const heading = el('div', 'connection-module-top');
        heading.append(icon(id === 'project' ? 'plug' : 'terminal'),
          el('h3', '', id === 'omp' ? 'OMP — Oh My Pi' : module?.name || (id === 'codex' ? 'Codex Desktop / CLI' : 'Project agent')));
        const badge = el('span', 'connection-state', record?.pending ? 'Analyzing…' : record?.report ? (selected && this.verified() ? 'Ready to generate' : 'Action required') : 'Not analyzed');
        heading.append(badge); card.append(heading);
        card.append(el('p', 'connection-description', id === 'codex'
          ? 'Read-only inspection of Codex storage and sessions associated with this exact folder.'
          : id === 'omp' ? 'Read-only inspection of OMP journals matching this folder or an explicitly added workspace. Only new records are captured.'
          : 'Inspect the embedded agent integration and the resources its graph can observe.'));
        const actions = el('div', 'connection-module-actions');
        const analyze = button(record?.report || record?.error ? 'Analyze again' : 'Analyze folder', 'search', () => this.act(() => {
          if (id !== this.draftModule && role === 'working_repository') {
            this.draftModule = id; this.generation++;
          }
          return this.analyze();
        }));
        analyze.classList.add('connection-analyze');
        const generate = button('Generate graph', 'check', () => this.act(() => this.generate()), {primary: true});
        generate.classList.add('connection-generate');
        generate.dataset.unavailable = String(!selected || !this.verified());
        generate.disabled = generate.dataset.unavailable === 'true';
        const repository = el(id === 'omp' ? 'a' : 'span', 'connection-button icon-only connection-repository');
        repository.title = id === 'omp' ? 'Oh My Pi on GitHub' : 'No GitHub repository link';
        repository.setAttribute('aria-label', repository.title);
        if (id === 'omp') {
          repository.href = 'https://github.com/can1357/oh-my-pi';
          repository.target = '_blank'; repository.rel = 'noopener noreferrer';
        } else {
          repository.setAttribute('role', 'img'); repository.setAttribute('aria-disabled', 'true');
        }
        repository.append(icon('github'));
        const graphActions = el('div', 'connection-graph-actions'); graphActions.append(generate, repository);
        actions.append(analyze, graphActions); card.append(actions); agent.append(card);
        if (record) {
          const panel = el('div', 'connection-analysis-disclosure'); panel.dataset.module = id; panel.id = 'connection-analysis-' + id;
          const body = el('div', 'connection-analysis-body');
          if (record.pending) {
            const status = el('p', 'connection-analysis-hint', 'Analyzing folder components and session records…');
            status.setAttribute('role', 'status'); body.append(status);
          } else if (record.error) {
            const error = el('p', 'connection-analysis-error', record.error); error.setAttribute('role', 'alert'); body.append(error);
          } else if (record.report) this.renderAnalysis(record.report, body);
          panel.append(body); panel.hidden = !record.expanded; panel.inert = !record.expanded;
          card.classList.toggle('analysis-open', record.expanded);
          const toggle = button(record.expanded ? 'Hide analysis' : 'Show analysis', 'chevron', () => {
            record.expanded = !record.expanded;
            toggle.setAttribute('aria-expanded', String(record.expanded));
            toggle.title = record.expanded ? 'Hide analysis' : 'Show analysis'; toggle.setAttribute('aria-label', toggle.title);
            this.animateAnalysis(panel, record.expanded);
          }, {compact: true});
          toggle.classList.add('connection-analysis-toggle'); toggle.setAttribute('aria-controls', panel.id);
          toggle.setAttribute('aria-expanded', String(record.expanded)); heading.append(toggle);
          agent.append(panel);
          if (record.animate) { reveals.push([panel, record.expanded, oldHeights.get(id) || 0]); record.animate = false; }
        }
        agents.append(agent);
      }
      this.cards.append(agents);
      if (!this.analyses.size) this.cards.append(el('p', 'connection-analysis-hint', 'Analyze this folder and role before generating its graph. Analysis does not connect or start an agent.'));
      this.disableActions(this.busy);
      this.equalizeCards();
      for (const args of reveals) this.animateAnalysis(...args);
    }
    renderAnalysis(report, target) {
      const section = el('section', 'connection-analysis');
      section.append(el('h4', '', 'Component checks'));
      const table = el('table', 'connection-checks');
      const thead = el('thead'), titles = el('tr');
      for (const title of ['Component', 'Status', 'Evidence']) titles.append(el('th', '', title));
      thead.append(titles); table.append(thead);
      const tbody = el('tbody');
      for (const check of report.checks || []) {
        const row = el('tr'); row.dataset.status = check.status;
        row.append(el('th', '', check.name || check.id),
          el('td', 'connection-check-status', ({pass:'Found',warning:'Notice',error:'Blocked',waiting:'Waiting'})[check.status] || check.status),
          el('td', '', check.detail || ''));
        tbody.append(row);
      }
      table.append(tbody); section.append(table);
      target.append(section);

      const logs = el('details', 'connection-analysis-log'); logs.open = true;
      logs.append(el('summary', '', 'Analysis log'));
      const list = el('ol');
      for (const item of report.logs || []) {
        const row = el('li', `log-${item.level || 'info'}`, item.message); list.append(row);
      }
      logs.append(list); target.append(logs);

      const sessions = report.sessions || [];
      const sessionsSection = el('section', 'connection-sessions-section');
      sessionsSection.append(el('h4', '', `Folder sessions · ${sessions.length}`));
      if (!sessions.length) sessionsSection.append(el('p', 'connection-sessions-empty', 'No matching sessions were found. The graph will wait for new recorded activity.'));
      else {
        const list = el('div', 'connection-sessions-list');
        for (const session of sessions) {
          const item = el('div', 'connection-session-item');
          const name = el('span', 'connection-session-name', session.session_name || session.session_id);
          name.title = session.session_id;
          item.append(name, el('span', 'connection-session-app', session.application || 'Agent'),
            el('span', 'connection-session-badge', ({running:'Active turn',idle:'Idle',completed:'Completed',failed:'Failed',waiting:'Waiting'})[session.status] || 'Unknown'));
          list.append(item);
        }
        sessionsSection.append(list);
      }
      target.append(sessionsSection);
      if (this.draftRole === 'agent_system' && !report.ready && this.data.integration_prompt) {
        const instructions = el('details', 'connection-manual');
        instructions.append(el('summary', '', 'Integration instructions'), el('code', '', this.data.integration_prompt));
        instructions.append(button('Copy integration prompt', 'copy', () => this.act(async () => {
          await navigator.clipboard.writeText(this.data.integration_prompt); this.message('Copied');
        })));
        target.append(instructions);
      }
    }
    async analyze() {
      const workspace = this.data?.workspace, role = this.draftRole, generation = this.generation;
      const id = role === 'agent_system' ? 'project' : this.draftModule;
      if (!workspace || workspace.path !== this.selectedPath) return;
      const record = {report: null, error: '', pending: true, expanded: true, animate: true};
      this.analyses.set(id, record); this.render();
      try {
        const report = await this.request('/api/connections/analyze', {project: workspace.path, repository_mode: role, revision: workspace.revision, ...(id === 'omp' ? {module:'omp'} : {})});
        if (generation !== this.generation || !this.dialog.open || role !== this.draftRole || this.analyses.get(id) !== record) return;
        record.report = report;
        if (report.workspace) this.data.workspace = report.workspace;
      } catch (error) {
        if (generation === this.generation) record.error = error.message || 'Folder analysis failed.';
      } finally {
        if (generation === this.generation && this.dialog.open) {
          record.pending = false; record.animate = true; this.render();
        }
      }
    }
    async generate() {
      if (!this.verified()) return;
      const id = this.draftRole === 'agent_system' ? 'project' : this.draftModule;
      const report = this.analyses.get(id).report, generation = this.generation;
      this.cards.querySelector(`[data-module="${this.draftRole === 'agent_system' ? 'project' : this.draftModule}"] .connection-state`).textContent = 'Generating graph…';
      try {
        const result = await this.request('/api/connections/generate', {
          project: this.data.workspace.path, repository_mode: this.draftRole,
          revision: report.revision, analysis_id: report.analysis_id,
          ...(this.draftModule === 'omp' && this.draftRole === 'working_repository' ? {module:'omp'} : {})
        });
        if (generation !== this.generation || !this.dialog.open) return;
        if (result.current && result.config) this.onConfigChanged?.(result.config, result.state, result.history);
        this.close();
        if (!result.current) this.onOpenGraph?.(result.url);
      } catch (error) {
        if (generation !== this.generation || !this.dialog.open) return;
        const record = this.analyses.get(id);
        record.report = null; record.error = error.message || 'Reanalyze the folder before generating its graph.'; record.expanded = true; record.animate = true;
        if (generation === this.generation && this.dialog.open) this.render();
      }
    }
    destroy() {this.cardObserver?.disconnect(); this.close(); this.dialog?.remove(); this.toolbarButton?.remove();}
  }
  window.ObserverConnections = ObserverConnections;
})();
