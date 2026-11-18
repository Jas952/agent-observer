(() => {
  'use strict';

  const MAX_TERMINAL_LINES = 2000;
  const SCROLL_THRESHOLD_PX = 36;

  const icons = {
    terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    play: '<polygon points="6 4 19 12 6 20 6 4" fill="currentColor" stroke="none"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
    refresh: '<path d="M21 2v6h-6M3 12a9 9 0 0 1 15.5-6.4L21 8M3 22v-6h6M21 12a9 9 0 0 1-15.5 6.4L3 16"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    arrowDown: '<path d="M12 5v14M19 12l-7 7-7-7"/>',
    error: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 3h.01"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>'
  };

  function svg(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || ''}</svg>`;
  }

  function make(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }

  function sanitizeLogText(text) {
    if (text == null) return '';
    return String(text)
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .replace(/\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)/g, '')
      .replace(/\x1B\[[0-9;?]*[ -/]*[@-~]/g, '')
      .replace(/\x1B[@-Z\\-_]/g, '')
      .replace(/[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]/g, '');
  }

  class ObserverServiceTerminal {
    constructor({ request, onUpdate, onOpen, onClose, project } = {}) {
      this.nativeRoot = project?.path || window.__BOOTSTRAP__?.config?.project_root || '';
      this.project = project || {path:this.nativeRoot,current:true,name:'Current project'};
      this.sessionId = '';
      this.scopeGeneration = 0;
      this.request = (endpoint,body) => {
        const native = this.project.path === this.nativeRoot;
        return request(native ? endpoint : `/api/project-settings?project=${encodeURIComponent(this.project.path)}&endpoint=${encodeURIComponent(endpoint)}`, body);
      };
      this.onUpdate = onUpdate;
      this.onOpen = onOpen;
      this.onClose = onClose;

      this.services = [];
      this.processes = [];
      this.processesAvailable = null;
      this.openTabs = [];
      this.activeId = null;
      this.tabsInitialized = false;
      this.seenRunning = new Set();

      this.serviceLogs = new Map();
      this.pendingAction = new Set();
      this.actionErrors = new Map();
      this.statusError = null;
      this.statusAvailable = null;

      this.visible = false;
      this.mounted = false;
      this.destroyed = false;

      this.refreshTimer = null;
      this.logsTimer = null;
      this.inFlightLogsFetch = false;
      this.refreshing = false;
      this.displayedId = null;

      this.visibilityHandler = () => {
        if (document.hidden) {
          clearInterval(this.logsTimer);
          this.logsTimer = null;
        } else {
          this.refresh().catch(() => {});
          if (this.visible) {
            this.startLogsPolling();
            if (this.activeId) this.fetchLogs(this.activeId).catch(() => {});
          }
        }
      };
      document.addEventListener('visibilitychange', this.visibilityHandler);
      this.languageHandler = () => this.renderServiceShortcuts();
      window.addEventListener('observer-language-changed', this.languageHandler);
    }

    mount(main) {
      if (this.mounted) return;


      const view = make('section', 'terminal-view');
      view.id = 'terminal-view';
      view.setAttribute('aria-label', 'Service log terminal');
      view.tabIndex = -1;
      this.context = make('div','terminal-context');
      this.contextName = make('strong');this.contextName.setAttribute('translate','no');
      this.context.append(this.contextName,make('small','','Live project processes & service logs · shared by this folder’s sessions'));
      view.append(this.context);this.renderContext();
      const close = make('button','icon-button terminal-close');
      close.id='terminal-close';close.type='button';close.dataset.tooltip='Back to graph';
      close.setAttribute('aria-label','Back to graph');close.innerHTML=svg('close');
      close.onclick=()=>this.onClose?.();view.append(close);

      const header = make('div', 'terminal-header');
      const tabsBar = make('div', 'terminal-tabs-bar');
      tabsBar.setAttribute('role', 'tablist');
      tabsBar.setAttribute('aria-label', 'Services');
      tabsBar.addEventListener('keydown', e => this.handleTablistKeyDown(e));
      this.tabsBar = tabsBar;
      const tabsRow = make('div', 'terminal-tabs-row');
      this.shortcuts = make('div', 'terminal-service-shortcuts');
      this.shortcuts.setAttribute('role', 'group');
      this.shortcuts.setAttribute('aria-label', 'Available services');
      tabsRow.append(tabsBar, this.shortcuts);

      const toolbar = make('div', 'terminal-service-toolbar');
      this.toolbar = toolbar;

      header.append(tabsRow, toolbar);
      const body = make('div', 'terminal-body');
      body.id = 'terminal-log-panel';
      body.setAttribute('role', 'tabpanel');
      body.tabIndex = 0;
      body.setAttribute('aria-label', 'Service log stream');

      const logsContainer = make('div', 'terminal-logs-container');
      this.logsContainer = logsContainer;

      const scrollBottomBtn = make('button', 'terminal-scroll-bottom', '↓ Follow logs');
      scrollBottomBtn.type = 'button';
      scrollBottomBtn.setAttribute('aria-label', 'Follow latest logs');
      scrollBottomBtn.hidden = true;
      scrollBottomBtn.onclick = () => {
        this.scrollToBottom(this.activeId, true);
      };
      this.scrollBottomBtn = scrollBottomBtn;

      body.append(logsContainer, scrollBottomBtn);
      body.addEventListener('scroll', () => this.handleBodyScroll(), { passive: true });
      this.terminalBody = body;

      const emptyView = make('div', 'terminal-empty-state');
      emptyView.hidden = true;
      this.emptyView = emptyView;

      view.append(header, body, emptyView);
      main.appendChild(view);
      this.terminalView = view;
      this.mounted = true;

      this.startServicePolling();
      void this.refresh();
    }

    setVisible(visible) {
      this.visible = Boolean(visible);
      if (this.terminalView) {
        this.terminalView.classList.toggle('active', this.visible);
        this.terminalView.style.display = this.visible ? 'flex' : 'none';
      }
      if (this.visible) {
        this.refresh().then(() => {
          if (this.activeId) {
            void this.fetchLogs(this.activeId);
          }
        }).catch(() => {});
        this.startLogsPolling();
      } else {
        window.ObserverFeedback.clear(this.terminalView);
        clearInterval(this.logsTimer);
        this.logsTimer = null;
      }
    }

    startServicePolling() {
      clearInterval(this.refreshTimer);
      this.refreshTimer = setInterval(() => {
        if (!document.hidden) {
          this.refresh().catch(() => {});
        }
      }, 4000);
    }

    startLogsPolling() {
      clearInterval(this.logsTimer);
      if (!this.visible || !this.activeId) return;
      this.logsTimer = setInterval(() => {
        if (this.visible && !document.hidden && this.activeId) {
          this.fetchLogs(this.activeId).catch(() => {});
        }
      }, 1500);
    }

    async setProject(project, sessionId='') {
      if (project.path===this.project.path && sessionId===this.sessionId) return this.services;
      this.scopeGeneration++;
      this.project={...project,current:project.path===this.nativeRoot};this.sessionId=sessionId;
      this.renderContext();
      this.services=[];this.processes=[];this.processesAvailable=null;this.openTabs=[];this.activeId=null;this.tabsInitialized=false;this.seenRunning=new Set();
      this.serviceLogs=new Map();this.pendingAction=new Set();this.actionErrors=new Map();
      this.statusAvailable=null;this.statusError=null;this.refreshing=false;this.inFlightLogsFetch=false;
      this.displayedId=null;this.tabsSignature=null;this.logsContainer?.replaceChildren();
      this.renderTabs();this.renderToolbar();
      this.onUpdate?.(this.services);
      return this.refresh();
    }

    renderContext(){
      if(!this.contextName)return;
      const session=(this.project.sessions||[]).find(entry=>entry.session_id===this.sessionId);
      this.contextName.textContent=[this.project.name||this.project.path,session?.display_name||session?.session_name||this.sessionId].filter(Boolean).join(' · ');
      this.contextName.title=this.project.path||'';
    }

    async refresh() {
      if (this.destroyed || this.refreshing) return this.services;
      this.refreshing = true;
      const generation=this.scopeGeneration;
      try {
        const res = await this.request('/api/local-services');
        if(generation!==this.scopeGeneration||this.destroyed)return this.services;
        if(res.project_root&&this.project.path&&res.project_root!==this.project.path)throw new Error('Settings belong to a different project.');
        const list = Array.isArray(res) ? res : (Array.isArray(res?.services) ? res.services : []);
        const previous = this.services;
        this.processes = Array.isArray(res?.processes) ? res.processes : [];
        this.processesAvailable = Array.isArray(res?.processes);
        this.processError = res?.process_error || 'Process status unavailable.';
        this.services = this.terminalEntries(list,previous);
        this.statusAvailable = true;
        this.statusError = null;


        for(const service of this.services){
          if(!service.connected&&service.status!=='running')continue;
          const key=JSON.stringify([service.id,service.identity]);
          if(this.seenRunning.has(key))continue;
          this.seenRunning.add(key);
          if(!this.openTabs.includes(service.id))this.openTabs.push(service.id);
          if(!this.activeId)this.activeId=service.id;
        }
        if (!this.tabsInitialized) {
          this.tabsInitialized = true;
          if (!this.openTabs.length && list.length) {this.openTabs.push(list[0].id);this.activeId=list[0].id}
        }

        if (this.activeId && !this.openTabs.includes(this.activeId)) {
          this.activeId = this.openTabs[0] || null;
        }

        this.renderTabs();
        this.renderToolbar();
        this.renderActiveLogsPane();

        if (typeof this.onUpdate === 'function') {
          this.onUpdate(this.services);
        }
      } catch (err) {
        if(generation!==this.scopeGeneration||this.destroyed)return this.services;
        this.statusAvailable = false;
        this.statusError = err.message || 'Service status unavailable';

        this.renderTabs();
        this.renderToolbar();

        if (typeof this.onUpdate === 'function') {
          this.onUpdate(this.services);
        }
      }
      finally {
        if(generation===this.scopeGeneration)this.refreshing = false;
      }
      return this.services;
    }

    terminalEntries(services,previous) {
      const entries=services.map(service=>({...service}));
      const byPid=new Map(this.processes.map(process=>[process.pid,process]));
      for(const process of this.processes){
        if(!process.ports?.length)continue;
        const ancestors=new Set();let current=process;
        while(current&&!ancestors.has(current.pid)){ancestors.add(current.pid);current=byPid.get(current.ppid)}
        const service=entries.find(item=>!item.processOnly&&(ancestors.has(item.pid)||item.connected===true&&(item.endpoints||[item.endpoint]).some(raw=>{
          try{const url=new URL(raw);return process.ports.some(port=>Number(port.port)===Number(url.port||(url.protocol==='https:'?443:80)))}catch{return false}
        })));
        if(service){
          service.process=process;
          if(this.openTabs.includes(process.id)){
            this.openTabs=[...new Set(this.openTabs.map(id=>id===process.id?service.id:id))];
            if(this.activeId===process.id)this.activeId=service.id;
            this.serviceLogs.get(process.id)?.pane?.remove();this.serviceLogs.delete(process.id);
          }
          continue;
        }
        const endpoints=process.urls||[],ports=[...new Set(process.ports.map(port=>port.port))].join(', ');
        entries.push({id:process.id,name:`${process.name} · ${ports}`,processOnly:true,process,
          endpoint:endpoints[0]||'',endpoints,status:'running',connected:true,owner:process.owner,
          identity:process.identity,cwd:process.cwd,can_start:false,can_stop:process.can_stop,log_available:false});
      }
      for(const entry of previous){
        if(entry.processOnly&&this.openTabs.includes(entry.id)&&!entries.some(item=>item.id===entry.id)){
          entries.push({...entry,status:this.processesAvailable?'stopped':'unknown',connected:false,can_stop:false});
        }
      }
      return entries;
    }

    processOrigin(process) {
      return ({graph:'Started by graph',graph_independent:'Graph launch · independent lifetime',external_terminal:`External terminal${process.tty?' · '+process.tty:''}`,external_process:'Outside graph · launcher not established'})[process.origin]||({graph:'Started by graph',independent:'Independent graph launch',external:'Started outside graph'})[process.owner]||'Origin not established';
    }

    async stopProcess(id) {
      const generation=this.scopeGeneration,process=this.processes.find(item=>item.id===id);
      if(!process?.can_stop||this.pendingAction.has(id))return;
      if(!window.confirm(`Stop "${process.name||'process'}" (PID ${process.pid}) and its verified project children?\nFolder: ${process.cwd}\nOrigin: ${this.processOrigin(process)}`))return;
      this.pendingAction.add(id);this.renderToolbar();
      try{
        await this.request(`/api/local-processes/${encodeURIComponent(id)}/stop`,{identity:process.identity});
        if(generation!==this.scopeGeneration)return;
        await this.refresh();
      }catch(error){if(generation===this.scopeGeneration)this.setError(id,`Process stop rejected: ${error.message}`)}
      finally{if(generation===this.scopeGeneration){this.pendingAction.delete(id);this.renderToolbar()}}
    }


    openService(id) {
      if (!id) return;
      if (!this.openTabs.includes(id)) {
        this.openTabs.push(id);
      }
      this.activeId = id;
      this.renderTabs();
      this.renderToolbar();
      this.renderActiveLogsPane();

      if (typeof this.onOpen === 'function') {
        this.onOpen();
      }

      void this.fetchLogs(id);
      if (this.visible) {
        this.startLogsPolling();
      }
    }

    getLogState(id) {
      let state = this.serviceLogs.get(id);
      if (!state) {
        state = {
          cursor: 0,
          userScrolledUp: false,
          linesCount: 0,
          truncated: false,
          scrollTop: 0,
          error: null,
          pane: null
        };
        this.serviceLogs.set(id, state);
      }
      return state;
    }

    getOrCreatePane(id) {
      const state = this.getLogState(id);
      if (!state.pane) {
        const pane = make('div', 'terminal-service-logs');
        pane.dataset.serviceId = id;
        pane.hidden = id !== this.activeId;
        this.logsContainer.appendChild(pane);
        state.pane = pane;
      }
      return state.pane;
    }

    renderTabs() {
      if (!this.tabsBar) return;
      this.renderServiceShortcuts();
      const signature = JSON.stringify([this.openTabs, this.activeId, this.statusAvailable, this.processesAvailable, this.processError, this.services.map(service => [service.id, service.name, service.status, service.can_start])]);
      if (signature === this.tabsSignature) return;
      this.tabsSignature = signature;

      const activeEl = document.activeElement;
      const focusedId = (activeEl && this.tabsBar.contains(activeEl)) ? activeEl.id : null;

      if (this.openTabs.length === 0) {
        this.tabsBar.replaceChildren();

        if (this.emptyView) {
          this.emptyView.hidden = false;
          this.emptyView.replaceChildren();
          const msg = make('p', '', this.statusAvailable === null ? 'Inspecting…' : this.statusAvailable === false ? 'Service status unavailable.' : this.services.length ? 'All service tabs are closed.' : this.processesAvailable === false ? this.processError : 'No running localhost or registered services found for this folder.');
          this.emptyView.appendChild(msg);

        }
        if (this.terminalBody) this.terminalBody.hidden = true;
        return;
      }

      if (this.emptyView) this.emptyView.hidden = true;
      if (this.terminalBody) this.terminalBody.hidden = false;

      const existingTabEls = new Map();
      for (const el of this.tabsBar.querySelectorAll('.terminal-subtab')) {
        existingTabEls.set(el.dataset.serviceId, el);
      }

      const fragment = document.createDocumentFragment();

      for (let i = 0; i < this.openTabs.length; i++) {
        const id = this.openTabs[i];
        const service = this.services.find(s => s.id === id);
        const isActive = id === this.activeId;
        const statusClass = this.statusAvailable === false ? 'unknown' : (service?.status || 'stopped');

        let tab = existingTabEls.get(id);
        if (!tab) {
          tab = make('div', 'terminal-subtab');
          tab.id = `terminal-subtab-${id}`;
          tab.dataset.serviceId = id;
          tab.setAttribute('role', 'tab');
          tab.setAttribute('aria-controls', 'terminal-log-panel');

          const statusDot = make('span', 'service-dot');
          statusDot.setAttribute('aria-hidden', 'true');

          const title = make('span', 'tab-title');

          const closeBtn = make('button', 'tab-close');
          closeBtn.type = 'button';
          closeBtn.innerHTML = svg('close');

          tab.append(statusDot, title, closeBtn);
          tab.onclick = () => {
            if (this.activeId !== id) {
              this.activeId = id;
              this.renderTabs();
              this.renderToolbar();
              this.renderActiveLogsPane();
              void this.fetchLogs(id);
              this.startLogsPolling();
            }
          };
        }

        tab.className = `terminal-subtab ${isActive ? 'active' : ''}`;
        tab.setAttribute('aria-selected', String(isActive));
        tab.tabIndex = isActive ? 0 : -1;
        if(isActive)this.terminalBody?.setAttribute('aria-labelledby',tab.id);

        const dot = tab.querySelector('.service-dot');
        if (dot) dot.className = `service-dot status-${statusClass}`;

        const title = tab.querySelector('.tab-title');
        if (title) { title.setAttribute('translate', 'no'); title.textContent = service?.name || id; }

        const closeBtn = tab.querySelector('.tab-close');
        if (closeBtn) {
          closeBtn.title = `Close ${service?.name || id} tab`;
          closeBtn.setAttribute('aria-label', `Close ${service?.name || id} tab`);
          closeBtn.onclick = e => {
            e.stopPropagation();
            this.closeTab(id);
          };
        }

        fragment.appendChild(tab);
      }

      this.tabsBar.replaceChildren(fragment);

      if (focusedId) {
        const toRefocus = document.getElementById(focusedId);
        if (toRefocus && toRefocus !== document.activeElement) {
          toRefocus.focus({ preventScroll: true });
        }
      }
    }

    renderServiceShortcuts() {
      if (!this.shortcuts) return;
      const t = text => window.ObserverI18n?.t(text) || text;
      const signature = JSON.stringify([this.statusAvailable, window.ObserverI18n?.language, this.openTabs, [...this.pendingAction],
        this.services.map(service => [service.id, service.name, service.description, service.endpoint, service.endpoints, service.launch_command, service.cwd, service.status, service.can_start])]);
      if (signature === this.shortcutsSignature) return;
      this.shortcutsSignature = signature;
      const focused = this.shortcuts.contains(document.activeElement) ? document.activeElement.dataset.serviceId : null;
      this.shortcuts.replaceChildren(...this.services.filter(service=>!service.processOnly||!this.openTabs.includes(service.id)).map(service => {
        const name = service.name || service.id;
        const button = make('button', 'terminal-service-shortcut', name);
        button.type = 'button';button.dataset.serviceId = service.id;button.setAttribute('translate', 'no');
        button.setAttribute('aria-label', t(`Open service: ${name}`));
        button.setAttribute('aria-busy', String(this.pendingAction.has(service.id)));
        const endpoints = service.endpoints?.length ? service.endpoints.join(', ') : service.endpoint;
        button.dataset.tooltip = [service.description,
          endpoints && t(`Host: ${endpoints}`),
          service.launch_command ? t(`Launch: ${service.launch_command}`) : t(Object.hasOwn(service, 'launch_command') ? 'No launch command configured.' : 'Launch command unavailable.'),
          service.cwd && t(`Folder: ${service.cwd}`)
        ].filter(Boolean).join('\n');
        button.onclick = () => { void this.openServiceShortcut(service.id); };
        return button;
      }));
      this.shortcuts.hidden = this.shortcuts.children.length === 0;
      if (focused) [...this.shortcuts.children].find(button => button.dataset.serviceId === focused)?.focus({preventScroll:true});
    }

    async openServiceShortcut(id) {
      const service = this.services.find(item => item.id === id);
      if (!service) return;
      this.openService(id);
      if (this.statusAvailable !== false && (service.connected === true || service.status === 'running')) {
        window.ObserverFeedback.show(this.terminalView, `${service.name || id} is already running.`);
      } else if (this.pendingAction.has(id) || this.statusAvailable !== false && service.status === 'starting') {
        window.ObserverFeedback.show(this.terminalView, `${service.name || id} is starting.`);
      } else if (this.statusAvailable !== false && service.can_start) {
        const generation = this.scopeGeneration;
        try {
          const updated = await this.startService(id);
          if (generation === this.scopeGeneration && updated) {
            const message = updated.connected === true || updated.status === 'running' ? 'is running.' : 'is starting.';
            window.ObserverFeedback.show(this.terminalView, `${updated.name || id} ${message}`);
          }
        } catch { /* startService displays its scoped error and retains the log tab. */ }
      }
    }

    handleTablistKeyDown(event) {
      if (['Enter', ' '].includes(event.key) && event.target.matches('.terminal-subtab')) {
        event.preventDefault();
        event.target.click();
        return;
      }
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();

      const tabs = Array.from(this.tabsBar.querySelectorAll('.terminal-subtab'));
      if (!tabs.length) return;

      const currentIndex = tabs.findIndex(tab => tab.classList.contains('active'));
      let nextIndex = 0;

      if (event.key === 'ArrowRight') {
        nextIndex = (currentIndex + 1) % tabs.length;
      } else if (event.key === 'ArrowLeft') {
        nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      } else if (event.key === 'Home') {
        nextIndex = 0;
      } else if (event.key === 'End') {
        nextIndex = tabs.length - 1;
      }

      const targetTab = tabs[nextIndex];
      targetTab?.focus();
      targetTab?.click();
    }

    closeTab(id) {
      const idx = this.openTabs.indexOf(id);
      if (idx < 0) return;

      this.openTabs.splice(idx, 1);

      if (this.activeId === id) {
        if (this.openTabs.length > 0) {
          const nextIdx = Math.min(idx, this.openTabs.length - 1);
          this.activeId = this.openTabs[nextIdx];
        } else {
          this.activeId = null;
        }
      }

      const state = this.serviceLogs.get(id);
      if (state?.pane) {
        state.pane.remove();
        state.pane = null;
      }

      this.renderTabs();
      this.renderToolbar();
      this.renderActiveLogsPane();

      if (this.activeId) {
        void this.fetchLogs(this.activeId);
        this.startLogsPolling();
      }
    }

    renderToolbar() {
      if (!this.toolbar) return;

      const activeEl = document.activeElement;
      const focusedAction = (activeEl && this.toolbar.contains(activeEl)) ? activeEl.dataset.action : null;

      const service = this.services.find(s => s.id === this.activeId);
      if (!service) {
        this.toolbar.replaceChildren();
        if (this.statusAvailable === false) {
          const infoWrap = make('div', 'terminal-service-info');
          const pill = make('span', 'terminal-status-pill status-unknown', 'status unavailable');
          infoWrap.append(pill);
          this.toolbar.append(infoWrap);
        }
        return;
      }
      this.toolbar.replaceChildren();

      const infoWrap = make('div', 'terminal-service-info');
      const title = make('strong', '', service.name || service.id);
      title.setAttribute('translate', 'no');

      const endpoint = service.endpoint || (Array.isArray(service.endpoints) ? service.endpoints[0] : '');
      const allEndpoints = Array.isArray(service.endpoints) && service.endpoints.length ? service.endpoints.join(', ') : endpoint;
      if (endpoint) {
        if(service.processOnly)infoWrap.append(make('code','',`PID ${service.process.pid}`));
        const epCode = make('code', '', endpoint);
        epCode.title = `Endpoints: ${allEndpoints}`;
        infoWrap.append(title, epCode);
      } else {
        infoWrap.append(title);
        if(service.processOnly)infoWrap.append(make('code','',`PID ${service.process.pid} · ${service.process.ports.map(port=>`${port.host}:${port.port}`).join(', ')}`));
      }

      const statusText = this.statusAvailable === false ? 'status unavailable' : ({running:'Running',stopped:'Stopped',starting:'Starting',stopping:'Stopping',error:'Error',occupied:'Occupied',unhealthy:'Unhealthy'}[service.status]||'Unknown');
      const statusClass = this.statusAvailable === false ? 'status-unknown' : `status-${service.status}`;
      const statusPill = make('span', `terminal-status-pill ${statusClass}`, statusText);
      infoWrap.append(statusPill);

      const ownerClass = service.owner === 'independent' ? 'independent' : service.owner === 'external' ? 'external' : (service.owner === 'graph' ? 'graph' : 'none');
      const ownerLabel = service.process ? this.processOrigin(service.process) : service.owner === 'independent' ? 'Independent graph launch' : service.owner === 'external' ? 'Started outside graph' : (service.owner === 'graph' ? 'Started by graph' : 'Not running');
      const ownerBadge = make('span', `terminal-owner-badge ${ownerClass}`, ownerLabel);
      ownerBadge.title = service.owner === 'independent' ? 'Keeps running after Observer exits.' : service.owner === 'graph' ? 'Observer shutdown stops this managed launch.' : 'Origin is reported independently of service health.';
      infoWrap.append(ownerBadge);

      const actionsWrap = make('div', 'terminal-service-actions');
      const isPending = this.pendingAction.has(service.id);

      // Authoritative can_start flag only
      const canStart = Boolean(service.can_start);
      const startBtn = make('button', 'terminal-action-btn start-btn');
      startBtn.type = 'button';
      startBtn.dataset.action = 'start';
      startBtn.innerHTML = `${svg('play')}<span>${isPending ? 'Starting…' : 'Start'}</span>`;
      startBtn.disabled = !canStart || isPending;
      startBtn.title = canStart ? `Start ${service.name}` : (service.note || `Cannot start ${service.name}`);
      if (canStart) {
        startBtn.onclick = () => this.startService(service.id).catch(() => {});
      }
      if(!service.processOnly)actionsWrap.appendChild(startBtn);
      if (Object.hasOwn(service,'can_start_independent')) {
        const independent = make('button','terminal-action-btn','Start independently');
        independent.type='button';independent.dataset.action='start-independent';
        independent.disabled=!service.can_start_independent||isPending;
        independent.title='Start a registered service that survives Observer shutdown.';
        independent.onclick=()=>this.startService(service.id,true).catch(()=>{});
        actionsWrap.append(independent);
      }

      // Authoritative can_stop flag only
      const canStop = Boolean(service.can_stop);
      const stopBtn = make('button', 'terminal-action-btn stop-btn');
      stopBtn.type = 'button';
      stopBtn.dataset.action = 'stop';
      stopBtn.innerHTML = `${svg('stop')}<span>${isPending ? 'Stopping…' : 'Stop'}</span>`;
      stopBtn.disabled = !canStop || isPending;
      stopBtn.title = canStop ? `Stop ${service.name}` : (service.note || `Cannot stop ${service.name}`);
      if (canStop) {
        stopBtn.onclick = () => service.processOnly ? void this.stopProcess(service.id) : this.stopService(service.id).catch(() => {});
      }
      actionsWrap.appendChild(stopBtn);

      const clearBtn = make('button', 'terminal-action-btn clear-btn');
      clearBtn.type = 'button';
      clearBtn.dataset.action = 'clear';
      clearBtn.innerHTML = `${svg('trash')}<span>Clear</span>`;
      clearBtn.title = 'Clear console buffer';
      clearBtn.onclick = () => this.clearLogs(service.id);
      actionsWrap.appendChild(clearBtn);

      const refreshBtn = make('button', 'terminal-action-btn refresh-btn');
      refreshBtn.type = 'button';
      refreshBtn.dataset.action = 'refresh';
      refreshBtn.innerHTML = `${svg('refresh')}`;
      refreshBtn.title = 'Refresh logs';
      refreshBtn.setAttribute('aria-label', 'Refresh logs');
      refreshBtn.onclick = () => {
        void this.fetchLogs(service.id);
        void this.refresh();
      };
      actionsWrap.appendChild(refreshBtn);

      this.toolbar.append(infoWrap, actionsWrap);

      if (focusedAction) {
        const toRefocus = this.toolbar.querySelector(`[data-action="${focusedAction}"]`);
        if (toRefocus && toRefocus !== document.activeElement) {
          toRefocus.focus({ preventScroll: true });
        }
      }
    }

    renderActiveLogsPane() {
      const changed = this.displayedId !== this.activeId;
      if (changed && this.displayedId && this.terminalBody) {
        this.getLogState(this.displayedId).scrollTop = this.terminalBody.scrollTop;
      }
      this.displayedId = this.activeId;
      for (const [id, state] of this.serviceLogs.entries()) {
        if (state.pane) {
          state.pane.hidden = id !== this.activeId;
        }
      }

      if (!this.activeId) return;

      const pane = this.getOrCreatePane(this.activeId);
      pane.hidden = false;

      const state = this.getLogState(this.activeId);
      const service=this.services.find(item=>item.id===this.activeId);
      let context=pane.querySelector('.terminal-process-context');
      if(service?.process){
        if(!context){context=make('div','terminal-process-context terminal-line stream-system');context.setAttribute('translate','no');pane.prepend(context)}
        const process=service.process,text=`PID ${process.pid} · ${process.cwd}\n$ ${process.command}\n`;
        if(context.textContent!==text)context.textContent=text;
      }else context?.remove();
      const unavailable=service?.processOnly||service?.owner==='external'&&!service.log_available;
      let notice=pane.querySelector('.terminal-log-notice');
      if(unavailable&&!notice){notice=make('p','terminal-log-notice','External terminal output is not attached. Logs appear here only when a log source is available.');pane.append(notice)}
      else if(!unavailable)notice?.remove();
      if (changed && state.userScrolledUp && this.terminalBody) this.terminalBody.scrollTop = state.scrollTop;
      if (!state.userScrolledUp) this.scrollToBottom(this.activeId, false);
    }

    handleBodyScroll() {
      if (!this.terminalBody || !this.activeId) return;
      const body = this.terminalBody;
      const state = this.getLogState(this.activeId);

      const isNearBottom = (body.scrollHeight - body.scrollTop - body.clientHeight) <= SCROLL_THRESHOLD_PX;
      state.userScrolledUp = !isNearBottom;
      state.scrollTop = body.scrollTop;
      this.scrollBottomBtn.hidden = isNearBottom;
    }

    scrollToBottom(id, smooth = false) {
      if (!this.terminalBody || id !== this.activeId) return;
      const body = this.terminalBody;
      const state = this.getLogState(id);

      if (smooth) {
        body.scrollTo({ top: body.scrollHeight, behavior: 'smooth' });
      } else {
        body.scrollTop = body.scrollHeight;
      }
      state.userScrolledUp = false;
      this.scrollBottomBtn.hidden = true;
    }

    async fetchLogs(id) {
      if (!id || this.destroyed || this.inFlightLogsFetch) return;
      if(this.services.find(service=>service.id===id)?.processOnly)return;
      const generation=this.scopeGeneration;
      this.inFlightLogsFetch = true;

      const state = this.getLogState(id);
      const cursor = state.cursor || 0;

      try {
        const data = await this.request(`/api/local-services/${encodeURIComponent(id)}/logs?after=${cursor}`);
        if (this.destroyed||generation!==this.scopeGeneration) return;
        if (!data || !Array.isArray(data.lines)) {
          throw new Error('Invalid service log response.');
        }
        if (state.error) {
          state.error = null;
          if (id === this.activeId) this.renderToolbar();
        }

        const pane = this.getOrCreatePane(id);
        const body = this.terminalBody;
        const wasNearBottom = body && (body.scrollHeight - body.scrollTop - body.clientHeight) <= SCROLL_THRESHOLD_PX;

        if (typeof data.next_cursor === 'number' && data.next_cursor < cursor) {
          pane.replaceChildren();
          state.linesCount = 0;
          state.truncated = false;
          const resetNotice = make('div', 'terminal-line stream-system', '[system] Process restarted or log buffer reset.');
          pane.appendChild(resetNotice);
          state.cursor = 0;
        }

        if (data.truncated && !state.truncated) {
          state.truncated = true;
          const truncEl = make('div', 'terminal-line stream-system', '[system] Earlier logs truncated by server buffer.');
          pane.appendChild(truncEl);
        }

        if (data.lines.length > 0) {
          const fragment = document.createDocumentFragment();

          for (const item of data.lines) {
            const lineEl = make('div', `terminal-line stream-${item.stream || 'stdout'}`);
            lineEl.textContent = sanitizeLogText(item.text);
            fragment.appendChild(lineEl);
            state.linesCount++;
          }

          pane.appendChild(fragment);

          while (pane.childNodes.length > MAX_TERMINAL_LINES) {
            pane.removeChild(pane.firstChild);
            state.linesCount--;
          }

          state.cursor = typeof data.next_cursor === 'number' ? data.next_cursor : cursor + data.lines.length;

          if (id === this.activeId && body) {
            if (wasNearBottom || !state.userScrolledUp) {
              body.scrollTop = body.scrollHeight;
              state.userScrolledUp = false;
              this.scrollBottomBtn.hidden = true;
            } else {
              state.userScrolledUp = true;
              this.scrollBottomBtn.hidden = false;
            }
          }
        }
      } catch (err) {
        if(generation!==this.scopeGeneration)return;
        state.error = err.message || 'Could not read service logs.';
        if (!this.destroyed && id === this.activeId) {
          window.ObserverFeedback.show(this.terminalView, state.error, { error: true });
        }
      } finally {
        if(generation===this.scopeGeneration)this.inFlightLogsFetch = false;
      }
    }

    clearLogs(id) {
      const state = this.getLogState(id);
      if (state.pane) {
        state.pane.replaceChildren();
      }
      state.linesCount = 0;
      state.userScrolledUp = false;
      this.scrollBottomBtn.hidden = true;
      this.appendSystemLog(id, 'Console buffer cleared.');
    }

    appendSystemLog(id, text) {
      const pane = this.getOrCreatePane(id);
      const lineEl = make('div', 'terminal-line stream-system', `[system] ${text}`);
      pane.appendChild(lineEl);

      const state = this.getLogState(id);
      state.linesCount++;

      if (id === this.activeId && this.terminalBody && !state.userScrolledUp) {
        this.terminalBody.scrollTop = this.terminalBody.scrollHeight;
      }
    }

    setError(id, message) {
      this.actionErrors.set(id, message);
      window.ObserverFeedback.show(this.terminalView, message, { error: true });
    }

    async startService(id, independent=false) {
      const generation=this.scopeGeneration;
      if (this.pendingAction.has(id)) return;
      const service = this.services.find(s => s.id === id);
      if(independent&&(!service?.can_start_independent||!window.confirm(`Start "${service.name}" independently? It will keep running after Observer exits. You can stop it from this project’s process controls.`)))return;
      this.pendingAction.add(id);
      this.actionErrors.delete(id);
      window.ObserverFeedback.clear(this.terminalView);
      this.renderToolbar();
      this.renderServiceShortcuts();

      try {
        const res = await this.request(`/api/local-services/${encodeURIComponent(id)}/start`, independent ? {mode:'independent'} : {});
        const updated = res?.service || res;
        if(generation!==this.scopeGeneration)return updated;
        this.updateSingleService(updated);
        this.openService(id);
        return updated;
      } catch (err) {
        if(generation!==this.scopeGeneration)return;
        const msg = `Failed to start ${service?.name || id}: ${err.message}`;
        this.setError(id, msg);
        this.appendSystemLog(id, `[system error] ${msg}`);
        throw err;
      } finally {
        if(generation===this.scopeGeneration){this.pendingAction.delete(id);this.renderToolbar();this.renderServiceShortcuts()}
      }
    }

    async stopService(id) {
      const generation=this.scopeGeneration;
      const service = this.services.find(s => s.id === id);
      if (!service) return;

      const endpoint = service.endpoint || (Array.isArray(service.endpoints) ? service.endpoints[0] : '');
      const addr = endpoint ? ` at ${endpoint}` : '';
      const confirmMsg = service.owner === 'external'
        ? `Stop external service "${service.name}"${addr}?`
        : `Stop service "${service.name}"${addr}?`;

      const confirmed = window.confirm(confirmMsg);
      if (!confirmed) return;

      this.pendingAction.add(id);
      this.actionErrors.delete(id);
      window.ObserverFeedback.clear(this.terminalView);
      this.renderToolbar();

      try {
        const res = await this.request(`/api/local-services/${encodeURIComponent(id)}/stop`, {
          identity: service.identity || null
        });
        const updated = res?.service || res;
        if(generation!==this.scopeGeneration)return updated;
        this.updateSingleService(updated);
        return updated;
      } catch (err) {
        if(generation!==this.scopeGeneration)return;
        const isConflict = err.message && (err.message.includes('409') || /identity|stale/i.test(err.message));
        const msg = isConflict
          ? 'Stop rejected: Process identity changed or service was already stopped.'
          : `Failed to stop ${service.name}: ${err.message}`;
        this.setError(id, msg);
        this.appendSystemLog(id, `[system error] ${msg}`);
        throw err;
      } finally {
        if(generation===this.scopeGeneration){this.pendingAction.delete(id);this.renderToolbar()}
      }
    }

    updateSingleService(updated) {
      if (!updated || !updated.id) return;
      const idx = this.services.findIndex(s => s.id === updated.id);
      if (idx >= 0) {
        this.services[idx] = updated;
      } else {
        this.services.push(updated);
      }
      this.statusAvailable = true;
      this.renderTabs();
      this.renderToolbar();
      if (typeof this.onUpdate === 'function') {
        this.onUpdate(this.services);
      }
    }

    destroy() {
      this.destroyed = true;
      clearInterval(this.refreshTimer);
      clearInterval(this.logsTimer);
      if (this.visibilityHandler) {
        document.removeEventListener('visibilitychange', this.visibilityHandler);
      }
      window.removeEventListener('observer-language-changed', this.languageHandler);
      this.terminalView?.remove();
    }
  }

  window.ObserverServiceTerminal = ObserverServiceTerminal;
})();
