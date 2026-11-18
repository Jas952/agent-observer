(() => {
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };

  const icons = {
    settings: '<path d="m9 3-.5 3-2 .9L4 5.5 2 9l2.3 1.8v2.4L2 15l2 3.5 2.5-1.4 2 .9.5 3h4l.5-3 2-.9 2.5 1.4 2-3.5-2.3-1.8v-2.4L20 9l-2-3.5-2.5 1.4-2-.9L13 3z"/><circle cx="11" cy="12" r="3"/>',
    search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    folder: '<path d="M3 7h6l2-3h8a2 2 0 0 1 2 2v13H3z"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    plug: '<path d="M8 3v5m8-5v5M5 8h14v3a7 7 0 0 1-14 0zM12 18v4"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    error: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 3h.01"/>',
    play: '<polygon points="6 4 19 12 6 20 6 4" fill="currentColor" stroke="none"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
    terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    eyeOff: '<path d="M3 3l18 18M10 5c7-1 12 7 12 7a22 22 0 0 1-4 4M7 7c-3 2-5 5-5 5s4 7 10 7c2 0 4-.7 5-1.5"/><path d="M10 10a3 3 0 0 0 4 4"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>'
  };

  const svg = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || ''}</svg>`;

  function button(icon, title, action) {
    const el = make('button', 'icon-button graph-settings-action');
    el.type = 'button';
    el.innerHTML = svg(icon);
    el.title = title;
    el.setAttribute('aria-label', title);
    if (typeof action === 'function') {
      el.onclick = action;
    }
    return el;
  }

  class ObserverGraphSettings {
    constructor({ request, visible, onConfig, getConfig, getGraphResources, resolveCommand, serviceTerminal }) {
      this.requestProject = (project,endpoint,body) => request(project && !project.current
        ? `/api/project-settings?project=${encodeURIComponent(project.path)}&endpoint=${encodeURIComponent(endpoint)}`
        : endpoint, body);
      this.request = (endpoint,body) => this.requestProject(this.project,endpoint,body);
      this.visible = visible;
      this.onConfig = onConfig;
      this.getConfig = getConfig;
      this.serviceTerminal = serviceTerminal;
      this.getGraphResources = getGraphResources;
      this.resolveCommand = resolveCommand;
      this.chooseFolder = () => request('/api/installations/choose-folder', {});
      this.disclosures = new Map();
      this.generation = 0;
      this.statusAvailable = null;
    }

    setServiceTerminal(serviceTerminal) {
      this.serviceTerminal = serviceTerminal;
    }

    projectButton(project) {
      const control = button('settings', `Project settings: ${project.name || project.path}`, event => {
        event.preventDefault();
        event.stopPropagation();
        this.open(project, control);
      });
      control.classList.add('session-project-settings');
      control.dataset.project = project.path;
      control.setAttribute('aria-haspopup', 'dialog');
      control.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') event.stopPropagation(); };
      return control;
    }

    open(project = {path: this.getConfig().project_root, name: this.getConfig().project_title, current: true}, opener = document.activeElement) {
      this.project = {...project};
      if (!this.dialog) {
        const dialog = make('dialog', 'graph-settings-dialog');
        this.dialog = dialog;
        dialog.setAttribute('aria-labelledby', 'graph-settings-title');

        const head = make('div', 'graph-settings-heading');
        const title = make('h2', '', 'Project settings');
        this.heading = title;
        title.tabIndex = -1;
        title.id = 'graph-settings-title';
        head.append(title, button('close', 'Close settings', () => dialog.close()));

        const controls = make('div', 'graph-settings-controls');
        this.filter = make('select');
        this.filter.setAttribute('aria-label', 'Feature type');
        for (const [value, label] of [
          ['', 'All features'],
          ['block', 'Graph blocks'],
          ['command', 'Commands'],
          ['document', 'Documents'],
          ['file', 'Working files'],
          ['mcp', 'MCP'],
          ['skill', 'Skills'],
          ['memory', 'Memory'],
          ['tool', 'Tools'],
          ['integration', 'CLI integrations']
        ]) {
          const option = make('option', '', label);
          option.value = value;
          this.filter.append(option);
        }
        this.filter.onchange = () => this.render();

        this.search = make('input');
        this.search.type = 'search';
        this.search.placeholder = 'Find feature…';
        this.search.setAttribute('aria-label', 'Find graph feature');
        this.search.oninput = () => this.render();

        this.refresh = button('search', 'Scan features', () => this.load(true));
        controls.append(
          this.filter,
          this.search,
          this.refresh,
          button('folder', 'Open project folder', () => this.reveal('installation'))
        );
        this.roleSection = make('div', 'graph-settings-role-section');
        this.list = make('div', 'graph-settings-list');
        this.headerResize=new ResizeObserver(()=>{const height=this.list.querySelector('.graph-settings-columns')?.offsetHeight;if(height)this.list.style.setProperty('--settings-columns-height',height+'px')});
        this.headerResize.observe(this.list);
        dialog.append(head, controls, this.roleSection, this.list);
        document.body.append(dialog);
        const settle=()=>{if(this.renderPending&&!this.pointerActive&&!this.keyActive)requestAnimationFrame(()=>{if(dialog.open&&this.renderPending)this.render()})};
        dialog.addEventListener('pointerdown',()=>{this.pointerActive=true});
        document.addEventListener('pointerup',()=>{this.pointerActive=false;settle()});
        document.addEventListener('pointercancel',()=>{this.pointerActive=false;settle()});
        dialog.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.closest('button,input[type=checkbox],summary'))this.keyActive=true});
        document.addEventListener('keyup',event=>{if(['Enter',' '].includes(event.key)){this.keyActive=false;settle()}});
        window.addEventListener('blur',()=>{this.pointerActive=false;this.keyActive=false;settle()});

        dialog.addEventListener('close', () => {
          this.generation++;
          this.pointerActive=false;this.keyActive=false;this.renderPending=false;
          for (const control of document.querySelectorAll('.session-project-settings')) {
            if (control.dataset.project === this.project?.path) { this.opener = control; break; }
          }
          window.ObserverFeedback.clear(this.dialog);
          this.opener?.isConnected && this.opener.focus({ preventScroll: true });
        });
      }
      this.opener = opener;
      this.heading.title = project.path;
      this.dialog.setAttribute('aria-description', project.path);
      this.data = null; this.connectionStatus = null; this.sessionsData = null;
      this.vaultDraft = null;
      this.list.replaceChildren(); this.roleSection.replaceChildren();

      if (!this.dialog.open) {
        this.opener = opener;
        this.dialog.showModal();
        this.heading.focus({preventScroll: true});
      }
      void this.load(false);
    }

    async load(rescan) {
      const generation = ++this.generation;
      this.refresh.disabled = true;
      window.ObserverFeedback.clear(this.dialog);
      this.list.setAttribute('aria-busy', 'true');
      try {
        const [data, connStatus, sessionsData] = await Promise.all([
          this.request('/api/graph-settings', rescan ? { discover: true } : undefined),
          this.request('/api/connections/status').catch(() => null),
          this.request('/api/sessions').catch(() => null)
        ]);
        if (generation !== this.generation) return;
        if (data.project_root !== this.project.path || (connStatus?.workspace && connStatus.workspace.path !== this.project.path)) throw new Error('Settings belong to a different project.');
        this.data = data;
        this.connectionStatus = connStatus;
        this.sessionsData = sessionsData;
        this.statusAvailable = true;
        if (data.config && this.project.current) this.onConfig(data.config);
        if (data.local_services && this.serviceTerminal?.project?.path === this.project.path) {
          this.serviceTerminal.services = data.local_services;
          this.serviceTerminal.updateActivityIndicator?.();
          this.serviceTerminal.onUpdate?.(data.local_services);
        }
        this.render();
      } catch (error) {
        if (generation !== this.generation) return;
        this.statusAvailable = false;
        this.error(error);
        this.render();
      } finally {
        if (generation === this.generation) {
          this.refresh.disabled = false;
          this.list.removeAttribute('aria-busy');
        }
      }
    }

    error(error) {
      window.ObserverFeedback.show(this.dialog, error.message || String(error), { error: true });
    }

    async reveal(id) {
      try {
        await this.request('/api/graph-settings/reveal', { id });
      } catch (error) {
        this.error(error);
      }
    }

    getServices() {
      if (this.serviceTerminal?.project?.path === this.project?.path && this.serviceTerminal?.services && this.serviceTerminal.services.length) {
        return this.serviceTerminal.services;
      }
      return this.data?.local_services || [];
    }

    findServiceForItem(item) {
      const services = this.getServices();
      if (!services || !services.length) return null;
      const nodeId = item.node_id;

      let match = services.find(s => s.id === item.id || (nodeId && s.id === nodeId) || (item.name && s.name === item.name));
      if (match) return match;

      if (nodeId) {
        match = services.find(s => Array.isArray(s.node_ids) && s.node_ids.includes(nodeId));
        if (match) return match;
      }

      const config = this.project?.current ? this.getConfig() : this.data?.config || {};
      const configNodes = config?.nodes || [];
      const memNode = configNodes.find(n => (n.id === nodeId || n.id === item.id) && n.kind === 'memory');
      const memSystemId = memNode?.memory_system_id;
      if (memSystemId) {
        match = services.find(s => Array.isArray(s.memory_system_ids) && s.memory_system_ids.includes(memSystemId));
        if (match) return match;
      }

      const configSystems = config?.memory_systems || [];
      const memSys = configSystems.find(s => s.id === item.id || (nodeId && s.id === nodeId) || (memSystemId && s.id === memSystemId));
      if (memSys) {
        match = services.find(s => Array.isArray(s.memory_system_ids) && s.memory_system_ids.includes(memSys.id));
        if (match) return match;
      }

      const ep = memNode?.endpoint || memSys?.endpoint;
      if (ep) {
        const cleanEp = ep.replace(/\/+$/, '');
        match = services.find(s => {
          if (s.endpoint && s.endpoint.replace(/\/+$/, '') === cleanEp) return true;
          if (Array.isArray(s.endpoints) && s.endpoints.some(e => e.replace(/\/+$/, '') === cleanEp)) return true;
          return false;
        });
        if (match) return match;
      }

      return null;
    }

    async startService(id) {
      window.ObserverFeedback.clear(this.dialog);
      if (this.serviceTerminal?.project?.path === this.project.path) {
        try {
          await this.serviceTerminal.startService(id);
          this.dialog?.close();
        } catch (err) {
          this.error(err);
          this.render();
        }
      } else {
        try {
          await this.request(`/api/local-services/${encodeURIComponent(id)}/start`, {});
          await this.load(false);
        } catch (err) {
          this.error(err);
          this.render();
        }
      }
    }

    async stopService(id) {
      window.ObserverFeedback.clear(this.dialog);
      if (this.serviceTerminal?.project?.path === this.project.path) {
        try {
          await this.serviceTerminal.stopService(id);
          this.render();
        } catch (err) {
          this.error(err);
          this.render();
        }
      } else {
        const service = this.findServiceForItem({ id });
        const endpoint = service?.endpoint || (Array.isArray(service?.endpoints) ? service.endpoints[0] : '');
        const addr = endpoint ? ` at ${endpoint}` : '';
        const confirmMsg = service?.owner === 'external'
          ? `Stop external service "${service?.name || id}"${addr}?`
          : `Stop service "${service?.name || id}"${addr}?`;

        if (!window.confirm(confirmMsg)) return;

        try {
          await this.request(`/api/local-services/${encodeURIComponent(id)}/stop`, {
            identity: service?.identity || null
          });
          await this.load(false);
        } catch (err) {
          this.error(err);
          this.render();
        }
      }
    }

    async setRepositoryRole(role) {
      const workspace = this.connectionStatus?.workspace;
      if (!workspace) return;
      this.dialog.close();
      window.dispatchEvent(new CustomEvent('observer-setup-requested', {
        detail: {project: workspace.path, repository_mode: role}
      }));
    }

    static resourceKey(node, config={}) {
      if(!node?.id||['request_prompt','agent-hub','run_result'].includes(node.id)||!['command','tool','document','file','memory','mcp','skill','integration'].includes(node.kind))return null;
      if(!['document','file'].includes(node.kind))return 'node:'+node.id;
      const canonical=(config.nodes||[]).find(item=>item.id===node.id&&item.kind===node.kind)||node;
      let path=String(canonical.absolute_path||canonical.path||canonical.label||'').replaceAll('\\','/');
      if(!path)return null;
      if(path.startsWith('@vault/'))path=(config.vault_path||'')+'/'+path.slice(7);
      else if(!path.startsWith('/')&&!/^[A-Za-z]:\//.test(path))path=(config.project_root||'')+'/'+path;
      const absolute=path.startsWith('/'),parts=[];
      for(const part of path.split('/')){if(part==='..')parts.pop();else if(part&&part!=='.')parts.push(part)}
      return node.kind+':'+(absolute?'/':'')+parts.join('/');
    }

    resourceFor(item) {
      const config=this.project.current?this.getConfig():this.data?.config||{project_root:this.project.path,nodes:[]};
      const descriptor={...item.resource,id:item.node_id||item.resource?.id,kind:item.kind,label:item.name};
      const canonicalCommand=item.kind==='command'&&/^command_(?:template|observed)_/.test(descriptor.id||'');
      const node=item.kind==='command'?(canonicalCommand?descriptor:this.resolveCommand?.(item.name,config)):
        (config.nodes||[]).find(node=>node.id===item.node_id);
      return {config,node:node||descriptor};
    }

    resourceHidden(item) {
      const {config,node}=this.resourceFor(item);
      if(item.kind==='command')return (config.hidden_commands||this.data.hidden_commands||[]).some(id=>[node.id,node.label,...(node.aliases||[])].includes(id));
      const key=ObserverGraphSettings.resourceKey(node,config);
      return Boolean(key&&(config.hidden_resources||this.data.hidden_resources||[]).includes(key));
    }

    async toggleResource(item) {
      if(this.savingResource||this.savingVisibility)return;
      const project=this.project,{config,node}=this.resourceFor(item),key=ObserverGraphSettings.resourceKey(node,config);
      if(!key)return;
      const show=this.resourceHidden(item),request=(endpoint,body)=>this.requestProject(project,endpoint,body);
      this.savingResource=true;this.render();
      try{
        const fresh=await request('/api/block-settings'),updates={revision:fresh.revision};
        if(item.kind==='command'){
          const aliases=new Set([node.id,node.label,item.node_id,...(node.aliases||[])]);
          updates.hidden_commands=(fresh.hidden_commands||[]).filter(id=>!aliases.has(id));
          if(!show)updates.hidden_commands.push(node.id);
        }else{
          const hidden=new Set(fresh.hidden_resources||[]);show?hidden.delete(key):hidden.add(key);updates.hidden_resources=[...hidden];
        }
        const saved=await request('/api/block-settings',updates);
        const metadata={hidden_resources:saved.hidden_resources,hidden_commands:saved.hidden_commands};
        if(this.getConfig()?.project_root===project.path)this.onConfig({...this.getConfig(),...metadata});
        if(this.project!==project||!this.dialog.open||!this.data)return;
        Object.assign(this.data,{revision:saved.revision,...metadata});
        if(!project.current)this.data.config={...this.data.config,...metadata};
      }catch(error){if(this.project===project&&this.dialog.open)this.error(error)}
      finally{this.savingResource=false;if(this.dialog.open)this.render()}
    }

    disclosureState() {
      const key=this.project.path;
      if(!this.disclosures.has(key))this.disclosures.set(key,{role:false,groups:{},candidates:{}});
      return this.disclosures.get(key);
    }

    inventoryRows() {
      const source=this.data.rows, members=this.project.current&&this.getGraphResources?.();
      if(!members)return source;
      const used=new Set(), rows=source.filter(row=>row.kind==='block');
      for(const [group,nodes] of Object.entries(members))for(const node of nodes){
        const existing=source.find(row=>row.kind!=='block'&&row.node_id===node.id)||source.find(row=>row.kind!=='block'&&row.group===group&&row.name===(node.label||node.name));
        if(existing)used.add(existing.id);
        rows.push({...existing,id:existing?.id||node.id,name:node.label||node.name||node.id,kind:node.kind,
          group,node_id:node.id,resource:node,in_graph:true,detected:existing?.detected??['prompt','spec','comment','agent','result'].includes(node.kind)});
      }
      for(const row of source)if(row.kind!=='block'&&!used.has(row.id))rows.push({...row,in_graph:this.resourceHidden(row)});
      return rows;
    }

    async toggleVaultEditor() {
      if(this.vaultDraft){this.vaultDraft=null;this.render();return}
      const draft={project:this.project.path,loading:true,value:''};
      this.vaultDraft=draft;this.disclosureState().groups.documents=true;this.render();
      try{
        const saved=await this.request('/api/block-settings');
        if(this.vaultDraft!==draft||this.project.path!==draft.project)return;
        Object.assign(draft,{loading:false,value:saved.vault_path||'',revision:saved.revision});this.render();
      }catch(error){if(this.vaultDraft===draft){draft.loading=false;draft.error=error.message;this.render()}}
    }

    renderVaultEditor(host) {
      const draft=this.vaultDraft;
      if(!draft||draft.project!==this.project.path)return;
      if(draft.form){
        for(const control of draft.form.querySelectorAll('input,button'))control.disabled=Boolean(draft.saving)||control.getAttribute('aria-busy')==='true';
        host.append(draft.form);return;
      }
      const form=make('form','graph-settings-vault');
      form.append(make('strong','','Obsidian vault'),make('small','','Vault folder path. Leave empty to disconnect.'));
      if(draft.loading){form.append(make('p','','Loading settings…'));host.append(form);return}
      if(!draft.revision){form.append(make('p','block-settings-error',draft.error));host.append(form);return}
      const field=window.ObserverVaultSettings.mount(form,{id:'project-vault-path',value:draft.value,chooseFolder:this.chooseFolder,
        isCurrent:()=>this.vaultDraft===draft&&this.project.path===draft.project&&this.dialog.open,
        onChange:value=>{draft.value=value}});
      const actions=make('div','graph-settings-vault-actions'),save=make('button','','Save defaults'),cancel=make('button','','Cancel');
      save.type='submit';cancel.type='button';cancel.onclick=()=>{this.vaultDraft=null;this.render()};
      actions.append(save,cancel);form.append(actions);host.append(form);
      draft.form=form;
      for(const control of form.querySelectorAll('input,button'))control.disabled=Boolean(draft.saving);
      form.onsubmit=async event=>{
        event.preventDefault();if(draft.saving)return;
        draft.value=field.input.value;draft.saving=true;this.render();
        try{
          const saved=await this.request('/api/block-settings',{revision:draft.revision,vault_path:draft.value});
          if(this.vaultDraft!==draft||this.project.path!==draft.project)return;
          if(saved.catalog&&this.project.current)this.onConfig(saved.catalog);
          this.vaultDraft=null;await this.load(false);
        }catch(error){if(this.vaultDraft===draft){draft.saving=false;this.render();this.error(error)}}
      };
    }

    renderRoleSection() {
      if (!this.roleSection) return;
      const focused=this.roleSection.contains(document.activeElement)?document.activeElement.id:null;
      this.roleSection.replaceChildren();
      const workspace = this.connectionStatus?.workspace;
      if (!workspace) {
        this.roleSection.style.display = 'none';
        return;
      }
      this.roleSection.style.display = '';

      const currentRole = workspace.repository_mode || (workspace.module === 'codex' ? 'working_repository' : 'agent_system');

      const expanded=this.disclosureState().role;
      this.roleSection.dataset.expanded=String(expanded);
      const header = make('button', 'graph-settings-role-header graph-settings-disclosure');
      header.type='button';header.innerHTML = `${svg('chevron')} <span>Repository role</span>`;
      header.id='project-role-disclosure';
      header.append(make('span','graph-settings-role-selected',currentRole==='agent_system'?'Agent system':'Working repository'));
      header.setAttribute('aria-expanded',String(expanded));header.setAttribute('aria-controls','graph-settings-role-content');

      const options = make('div', 'graph-settings-role-options');

      const cardAgent = make('button', `graph-settings-role-card${currentRole === 'agent_system' ? ' selected' : ''}`);
      cardAgent.type = 'button';
      cardAgent.setAttribute('aria-pressed', String(currentRole === 'agent_system'));
      cardAgent.id='project-role-agent';
      const agentTitle = make('strong', '', 'Agent system');
      if (currentRole === 'agent_system') agentTitle.innerHTML += ` ${svg('check')}`;
      cardAgent.append(agentTitle);
      cardAgent.append(make('small', '', 'Built-in request handling · Project agent telemetry'));
      cardAgent.onclick = () => this.setRepositoryRole('agent_system');

      const cardCodex = make('button', `graph-settings-role-card${currentRole === 'working_repository' ? ' selected' : ''}`);
      cardCodex.type = 'button';
      cardCodex.setAttribute('aria-pressed', String(currentRole === 'working_repository'));
      cardCodex.id='project-role-codex';
      const codexTitle = make('strong', '', 'Working repository');
      if (currentRole === 'working_repository') codexTitle.innerHTML += ` ${svg('check')}`;
      cardCodex.append(codexTitle);
      cardCodex.append(make('small', '', 'External Codex works on folder · Passive session journals'));
      cardCodex.onclick = () => this.setRepositoryRole('working_repository');

      options.append(cardAgent, cardCodex);

      const footer = make('div', 'graph-settings-role-footer');
      const content=make('div','graph-settings-role-content');content.id='graph-settings-role-content';content.hidden=!expanded;content.append(options,footer);
      header.onclick=()=>{const open=!this.disclosureState().role;this.disclosureState().role=open;content.hidden=!open;header.setAttribute('aria-expanded',String(open));this.roleSection.dataset.expanded=String(open)};
      this.roleSection.append(header,content);
      if(focused)this.roleSection.querySelector('#'+focused)?.focus({preventScroll:true});
      this.updateSessionActivity(this.sessionsData);
    }

    updateSessionActivity(data) {
      if (this.project && !this.project.current && data !== this.sessionsData) return;
      this.sessionsData = data;
      const footer = this.roleSection?.querySelector('.graph-settings-role-footer');
      if (!footer) return;
      footer.replaceChildren();
      const projectPath = this.connectionStatus?.workspace?.path;
      const sessions = (data?.sessions || []).filter(session =>
        (session.project_path || session.project_root || session.cwd) === projectPath);
      const activeSessions = sessions.filter(s => s.status === 'running');
      const unknownSessions = sessions.filter(session => !['running', 'idle', 'completed', 'failed', 'waiting'].includes(session.status));

      const activity = make('span', `graph-settings-role-activity${activeSessions.length > 0 ? ' active' : ''}`);
      const dot = make('span', `activity-dot ${activeSessions.length > 0 ? 'active' : (sessions.length > 0 ? 'idle' : 'none')}`);
      activity.append(dot);
      let sessionNote = data ? 'No captured sessions for this folder' : 'Session discovery unavailable';
      if (activeSessions.length > 0) {
        sessionNote = `${activeSessions.length} active turn${activeSessions.length > 1 ? 's' : ''} in progress`;
      } else if (sessions.length > 0) {
        sessionNote = `${sessions.length} discovered session${sessions.length > 1 ? 's' : ''} · ${unknownSessions.length ? `${unknownSessions.length} with unknown activity` : 'no captured active turns'}`;
      }
      activity.append(document.createTextNode(` ${sessionNote}`));
      footer.append(activity);

    }

    async openServiceLogs(service) {
      const project = this.project;
      try {
        await this.serviceTerminal.setProject(project);
        if (this.project !== project || this.serviceTerminal.project.path !== project.path) return;
        this.serviceTerminal.openService(service.id);
        this.dialog.close();
      } catch (error) { this.error(error); }
    }

    async setBlockVisibility(group, visible) {
      if (['request', 'agent', 'result'].includes(group) || this.savingVisibility) return;
      const generation = this.generation;
      const hidden = new Set(this.data.hidden_blocks || []);
      visible ? hidden.delete(group) : hidden.add(group);
      this.savingVisibility = true;
      this.render();
      try {
        const saved = await this.request('/api/block-settings', {revision:this.data.revision, hidden_blocks:[...hidden]});
        if (generation !== this.generation) return;
        this.data.hidden_blocks = saved.hidden_blocks;
        this.data.revision = saved.revision;
        if (this.data.config) this.data.config.hidden_blocks = saved.hidden_blocks;
        if (this.project.current) this.onConfig({...this.getConfig(), hidden_blocks:saved.hidden_blocks});
      } catch (error) {
        if (generation === this.generation) this.error(error);
      } finally {
        this.savingVisibility = false;
        if (generation === this.generation) this.render();
      }
    }

    render() {
      if(this.pointerActive||this.keyActive){this.renderPending=true;return}
      this.renderPending=false;
      this.renderRoleSection();
      if (!this.data) return;
      const scroll = this.list.scrollTop;

      const activeEl = document.activeElement;
      const focusedFeatureId = activeEl?.closest?.('.graph-settings-row')?.dataset?.featureId;
      const focusedAction = activeEl?.dataset?.action;
      const focusedId=this.list.contains(activeEl)?activeEl.id:null, selection=activeEl instanceof HTMLInputElement?[activeEl.selectionStart,activeEl.selectionEnd]:null;

      this.list.replaceChildren();

      const heading = make('div', 'graph-settings-row graph-settings-columns');
      heading.append(make('span', '', 'Feature'));
      heading.append(make('span', '', 'Connection / Address'));
      for (const title of ['Found locally', 'Visible in graph', 'Runtime connection']) {
        heading.append(make('span', '', title));
      }
      heading.append(make('span', '', ''));
      this.list.append(heading);

      const query = this.search.value.trim().toLowerCase();
      const config = this.project?.current ? this.getConfig() : this.data.config || {nodes: []};
      const endpoints = new Map((config.nodes || []).filter(node => node.kind === 'memory').map(node => [node.id, node.endpoint]));
      let shown = 0;
      const sections = new Map();
      const hidden = new Set(config.hidden_blocks || this.data.hidden_blocks || []);
      const rows=this.inventoryRows(),ui=this.disclosureState(),bodies=new Map(),extras=new Map();
      const searching=Boolean(query||this.filter.value&&this.filter.value!=='block'),searchKey=query+'|'+this.filter.value;
      if(ui.searchKey!==searchKey){ui.searchKey=searchKey;ui.searchGroups={};ui.searchCandidates={}}
      for (const block of rows.filter(item=>item.kind==='block')) {
        const group=block.group||block.id.slice(6),section=make('section','graph-settings-group'),header=make('div','graph-settings-group-heading');
        section.dataset.group=group;
        const nodes=rows.filter(item=>item.kind!=='block'&&(item.group||'tools')===group),registered=nodes.filter(item=>item.in_graph!==false);
        const title=make('strong','',block.name),disclose=make('button','graph-settings-disclosure');
        disclose.type='button';disclose.id='group-disclosure-'+group;disclose.innerHTML=svg('chevron');
        const visibleCount=hidden.has(group)?0:registered.filter(item=>!this.resourceHidden(item)).length;
        const count=make('span','graph-settings-group-count',visibleCount===registered.length?String(visibleCount):`${visibleCount} / ${registered.length}`);count.title='Visible / total resources';
        disclose.append(title,count);
        const body=make('div','graph-settings-group-body');body.id='graph-settings-group-'+group;
        const open=searching?(ui.searchGroups[group]??true):Boolean(ui.groups[group]);
        body.hidden=!open;disclose.setAttribute('aria-expanded',String(open));disclose.setAttribute('aria-controls',body.id);
        disclose.onclick=()=>{const expanded=body.hidden;body.hidden=!expanded;disclose.setAttribute('aria-expanded',String(expanded));(searching?ui.searchGroups:ui.groups)[group]=expanded};
        const toggle=make('input');toggle.type='checkbox';toggle.id='group-visibility-'+group;toggle.checked=!hidden.has(group);
        toggle.disabled=['request','agent','result'].includes(group)||this.savingVisibility||this.savingResource;toggle.setAttribute('aria-label',`Show block: ${block.name}`);
        toggle.onchange=()=>void this.setBlockVisibility(group,toggle.checked);
        header.append(toggle,disclose);
        if(['request','agent','result'].includes(group))header.append(make('small','','Required'));
        if(group==='documents'){const vault=button('folder','Obsidian vault',()=>void this.toggleVaultEditor());vault.classList.add('graph-settings-vault-button');vault.append(make('span','','Obsidian'));header.append(vault);this.renderVaultEditor(body)}
        const extra=make('details','graph-settings-candidates');extra.open=searching?(ui.searchCandidates[group]??true):Boolean(ui.candidates[group]);
        const summary=make('summary');summary.append(make('span','','Not shown in this graph'),make('span','graph-settings-group-count',String(nodes.length-registered.length)));extra.append(summary);
        extra.ontoggle=()=>{if(extra.isConnected)(searching?ui.searchCandidates:ui.candidates)[group]=extra.open};
        body.append(extra);extra.hidden=true;extras.set(group,extra);bodies.set(group,body);
        section.append(header,body);sections.set(group,section);this.list.append(section);
      }

      const isUnavailable = this.statusAvailable === false || (this.serviceTerminal?.project?.path === this.project.path && this.serviceTerminal.statusAvailable === false);

      for (const item of rows) {
        if (item.kind === 'block') continue;
        const endpoint = endpoints.get(item.node_id) || '';
        const service = this.findServiceForItem(item);
        const interfaceLabel=item.kind==='block'||item.node_id==='request_prompt'&&item.name==='Project prompt'||item.node_id==='run_result'&&item.name==='Agent result'||item.node_id==='agent-hub'&&item.name==='Agent';
        const kindLabel=({prompt:'Project prompt',agent:'Agent',result:'Result',spec:'Project specification',comment:'Refinement comment'}[item.kind]||item.kind);
        const localizedName=interfaceLabel?(window.ObserverI18n?.t(item.name)||item.name):'';
        const localizedKind=window.ObserverI18n?.t(kindLabel)||kindLabel;
        const searchCorpus = `${item.name} ${localizedName} ${kindLabel} ${localizedKind} ${item.description || ''} ${endpoint} ${service?.name || ''} ${service?.endpoint || ''} ${service?.status || ''} ${service?.owner || ''}`.toLowerCase();

        if (this.filter.value && item.kind !== this.filter.value || query && !searchCorpus.includes(query)) {
          continue;
        }
        shown++;

        const row = make('div', 'graph-settings-row');
        row.dataset.featureId = item.id;

        const name = make('div', 'graph-settings-name');
        const label = make('strong', '', item.name);
        if (!interfaceLabel) label.setAttribute('translate', 'no');
        name.append(label, make('small', '', kindLabel));
        name.title = item.description || item.name;
        row.append(name);

        const connCol = make('div', 'graph-settings-connection');
        if (service) {
          const ep = service.endpoint || (Array.isArray(service.endpoints) ? service.endpoints[0] : '');
          const allEndpoints = Array.isArray(service.endpoints) && service.endpoints.length ? service.endpoints.join(', ') : ep;
          if (ep) {
            const address = make('code', 'graph-settings-endpoint', ep);
            address.title = `Endpoints: ${allEndpoints}`;
            connCol.append(address);
          }
          if (isUnavailable) {
            const pill = make('span', 'service-pill status-unavailable', 'unavailable');
            pill.title = 'Service status could not be confirmed';
            connCol.append(pill);
          } else {
            let statusText = service.status;
            let statusClass = `status-${service.status}`;
            if (service.status === 'running') {
              statusText = service.connected ? `connected · ${service.owner}` : `running · ${service.owner}`;
              statusClass = `status-running owner-${service.owner}`;
            } else if (service.status === 'starting') {
              statusText = 'starting…';
            } else if (service.status === 'stopping') {
              statusText = 'stopping…';
            } else if (service.status === 'stopped') {
              statusText = 'stopped';
            }
            const pill = make('span', `service-pill ${statusClass}`, statusText);
            if (service.owner === 'external') {
              pill.title = 'External process';
            }
            connCol.append(pill);
          }
        } else {
          if (endpoint) {
            const address = make('code', 'graph-settings-endpoint', endpoint);
            address.title = `Configured memory API endpoint: ${endpoint}`;
            connCol.append(address);
            const meta = make('span', 'graph-settings-meta-label', 'Configured endpoint');
            connCol.append(meta);
          } else {
            let metaText = '—';
            if (item.kind === 'mcp') metaText = item.connected ? 'MCP client · connected' : 'MCP client';
            else if (item.kind === 'skill') metaText = 'In-process skill';
            else if (item.kind === 'integration') metaText = 'CLI integration';
            else if (item.kind === 'tool') metaText = 'Native tool';
            else if (item.kind === 'block') metaText = 'Graph block';
            const meta = make('span', 'graph-settings-meta-label', metaText);
            connCol.append(meta);
          }
        }
        row.append(connCol);

        let runtimeConnected;
        if (service) {
          runtimeConnected = isUnavailable ? null : service.connected;
        } else {
          runtimeConnected = isUnavailable ? null : item.connected;
        }

        const connTitle = isUnavailable
          ? 'Status unavailable'
          : (runtimeConnected === true
              ? 'Connected'
              : (runtimeConnected === false
                  ? 'Not connected'
                  : 'Not reported'));

        const shownHere = this.project?.current ? this.visible(item.node_id) : null;
        const statusItems = [
          ['check', item.detected, item.detected ? 'Detected locally' : 'Not detected'],
          ['eye', shownHere, shownHere === null ? 'Visibility is available in this project’s graph' : shownHere ? 'Visible in graph' : 'Hidden from graph'],
          ['plug', runtimeConnected, connTitle]
        ];

        for (const [icon, state, title] of statusItems) {
          if(icon==='eye'){
            const {config:settings,node}=this.resourceFor(item),key=ObserverGraphSettings.resourceKey(node,settings),suppressed=this.resourceHidden(item);
            const blockHidden=(settings.hidden_blocks||this.data.hidden_blocks||[]).includes(item.group);
            const required=['request','agent','result'].includes(item.group);
            const canToggle=Boolean(key&&item.in_graph!==false&&!blockHidden);
            const shown=!suppressed&&!blockHidden&&item.in_graph!==false;
            const hint=required?'Required':blockHidden?'Enable the block first':!canToggle?'Not registered in graph':suppressed?'Show in graph':'Hide from graph';
            const eye=button(shown?'eye':'eyeOff',hint,()=>void this.toggleResource(item));
            eye.classList.add('graph-settings-status','graph-settings-eye');eye.dataset.action='visibility';eye.dataset.state=shown?'yes':'no';
            eye.setAttribute('aria-pressed',String(shown));eye.disabled=!canToggle||this.savingResource||this.savingVisibility;
            row.append(eye);continue;
          }
          const status = make('span', 'graph-settings-status');
          status.dataset.state = state === true ? 'yes' : (state === false ? 'no' : 'unknown');
          status.innerHTML = svg(icon);
          status.title = title;
          status.setAttribute('role', 'img');
          status.setAttribute('aria-label', title);
          row.append(status);
        }

        const actions = make('div', 'graph-settings-row-actions');

        if (service) {
          // Authoritative capability flags only
          const canStart = Boolean(service.can_start);
          const startTitle = canStart
            ? `Start ${service.name}`
            : (service.note || `Cannot start ${service.name}`);
          const startBtn = button('play', startTitle, canStart ? () => this.startService(service.id) : null);
          startBtn.dataset.action = 'start';
          startBtn.disabled = !canStart;
          actions.append(startBtn);

          const canStop = Boolean(service.can_stop);
          const stopTitle = canStop
            ? `Stop ${service.name}`
            : (service.note || `Cannot stop ${service.name}`);
          const stopBtn = button('stop', stopTitle, canStop ? () => this.stopService(service.id) : null);
          stopBtn.dataset.action = 'stop';
          stopBtn.disabled = !canStop;
          actions.append(stopBtn);

          const logsBtn = button('terminal', `Logs: ${service.name}`, () => {
            void this.openServiceLogs(service);
          });
          logsBtn.dataset.action = 'terminal';
          actions.append(logsBtn);
        }

        const findBtn = button('search', `Rescan ${item.name}`, () => this.load(true));
        findBtn.dataset.action = 'search';
        actions.append(findBtn);

        const reveal = button('folder', `Open folder: ${item.name}`, () => this.reveal(item.id));
        reveal.dataset.action = 'folder';
        reveal.disabled = !item.root_available;
        actions.append(reveal);

        row.append(actions);
        const group=item.group||({command:'commands',document:'documents',file:'files',memory:'memory',mcp:'capabilities',skill:'capabilities',integration:'capabilities'}[item.kind]||'tools');
        const extra=item.in_graph===false&&extras.get(group);if(extra){extra.hidden=false;extra.append(row)}else{const body=bodies.get(group);if(body)body.insertBefore(row,extras.get(group));else this.list.append(row)}
      }

      for(const section of sections.values())section.hidden=Boolean((query||this.filter.value&&this.filter.value!=='block')&&!section.querySelector('.graph-settings-row'));
      if (!shown&&this.filter.value!=='block') this.list.append(make('p', 'graph-settings-empty', 'No matching features.'));
      this.list.scrollTop = scroll;

      if (focusedFeatureId && focusedAction) {
        const toRefocus = this.list.querySelector(`.graph-settings-row[data-feature-id="${focusedFeatureId}"] [data-action="${focusedAction}"]`);
        if (toRefocus && toRefocus !== document.activeElement) {
          toRefocus.focus({ preventScroll: true });
        }
      }
      if(focusedId){const control=document.getElementById(focusedId);if(control&&this.list.contains(control)){control.focus({preventScroll:true});if(selection&&control instanceof HTMLInputElement&&selection[0]!==null)control.setSelectionRange(...selection)}}
    }
  }

  window.ObserverGraphSettings = ObserverGraphSettings;
})();
