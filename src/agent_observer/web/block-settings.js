(() => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const titles = {request:'Project prompt', commands:'Commands', tools:'Tools & services', documents:'Documents & references', files:'Working files', capabilities:'MCP, skills & integrations', memory:'Memory systems'};
  const purposes = {
    request: 'Prompt, specification, attachments, and refinements captured from agent requests.',
    commands: 'Choose which native commands appear on the graph.',
    tools: 'Configured and discovered tools and services.',
    documents: 'Project document folders and Obsidian vaults.',
    files: 'Working files recorded from agent operations.',
    capabilities: 'Plugins available to the selected agent.',
    memory: 'Configured memory systems and metadata.'
  };

  const closeIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>';
  const plusIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
  const activityIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h14v12H9l-4 4zM8 8h8M8 12h5"/></svg>';
  const refreshIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M19 8a8 8 0 1 0 1 7M19 4v5h-5"/></svg>';
  const showAllIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m4 7 2 2 4-4M12 7h8M4 13h16M4 19h16"/></svg>';
  const layoutIcons = {
    list:'<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    grid:'<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
    free:'<path d="M9 4H4v5M15 20h5v-5M4 4l6 6M20 20l-6-6M15 4h5v5M9 20H4v-5"/>'
  };
  function placeSettingsPopover(popup, target, below = false) {
    const gap = 10, edge = 12, rect = target.getBoundingClientRect(), box = popup.getBoundingClientRect();
    const limitX = Math.max(edge, innerWidth - edge - box.width), limitY = Math.max(edge, innerHeight - edge - box.height);
    let left, top;
    if (below) {
      left = rect.right - box.width;
      top = rect.bottom + gap + box.height <= innerHeight - edge ? rect.bottom + gap : rect.top - gap - box.height;
    } else if (rect.right + gap + box.width <= innerWidth - edge) {
      left = rect.right + gap; top = rect.top;
    } else if (rect.left - gap - box.width >= edge) {
      left = rect.left - gap - box.width; top = rect.top;
    } else {
      left = rect.left;
      top = rect.bottom + gap + box.height <= innerHeight - edge ? rect.bottom + gap : rect.top - gap - box.height;
    }
    popup.style.left = `${Math.max(edge, Math.min(limitX, left))}px`;
    popup.style.top = `${Math.max(edge, Math.min(limitY, top))}px`;
  }

  class ObserverSettingsGuide {
    constructor(owner, button, getSteps) {
      this.owner = owner; this.button = button; this.getSteps = getSteps;
      this.active = false; this.index = 0; this.target = null; this.frame = 0;
      this.details = new Map(); this.descriptions = [];
      this.popup = document.createElement('div');
      this.popup.className = 'settings-guide-popover';
      this.popup.setAttribute('popover', 'manual');
      this.popup.setAttribute('role', 'dialog');
      const identity = `settings-guide-${crypto.randomUUID().slice(0,8)}`;
      this.popup.id = identity;
      this.popup.innerHTML = `<div class="settings-guide-heading"><span class="settings-guide-count" aria-live="polite"></span><button type="button" class="settings-icon-button" aria-label="Close field guide">${closeIcon}</button></div><h3 id="${identity}-title"></h3><p id="${identity}-text"></p><div class="settings-guide-actions"><button type="button" class="settings-guide-back">Back</button><button type="button" class="settings-guide-next">Next</button></div>`;
      this.popup.setAttribute('aria-labelledby', `${identity}-title`);
      this.popup.setAttribute('aria-describedby', `${identity}-text`);
      this.title = this.popup.querySelector('h3'); this.text = this.popup.querySelector('p');
      this.count = this.popup.querySelector('.settings-guide-count');
      this.back = this.popup.querySelector('.settings-guide-back'); this.next = this.popup.querySelector('.settings-guide-next');
      this.popup.querySelector('[aria-label="Close field guide"]').onclick = () => this.stop();
      this.back.onclick = () => { this.show(this.index - 1); if (this.index === 0) this.next.focus({preventScroll:true}); };
      this.next.onclick = () => this.index + 1 < this.steps.length ? this.show(this.index + 1) : this.stop();
      this.onKey = event => { if (this.active && event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); this.stop(); } };
      this.onClose = () => this.stop(false);
      this.schedule = () => {
        if (this.frame || !this.active) return;
        this.frame = requestAnimationFrame(() => {
          this.frame = 0;
          if (!this.target?.isConnected || !this.owner.contains(this.target)) this.stop();
          else placeSettingsPopover(this.popup, this.target);
        });
      };
      this.resize = new ResizeObserver(this.schedule);
      this.removal = new MutationObserver(() => { if (this.active && !this.target?.isConnected) this.stop(); });
      owner.addEventListener('close', this.onClose);
    }
    toggle() { if (this.active) this.stop(); else this.start(); }
    start() {
      this.stop(false);
      this.steps = this.getSteps().filter(step => step.area.isConnected).sort((a,b) => a.area === b.area ? 0 : a.area.compareDocumentPosition(b.area) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
      if (!this.steps.length) return;
      this.active = true;
      this.owner.appendChild(this.popup);
      this.button.setAttribute('aria-pressed', 'true');
      this.button.setAttribute('aria-controls', this.popup.id);
      document.addEventListener('keydown', this.onKey, true);
      this.owner.addEventListener('scroll', this.schedule, true);
      window.addEventListener('resize', this.schedule);
      this.removal.observe(this.owner, {childList:true, subtree:true});
      this.show(0);
      this.next.focus({preventScroll:true});
    }
    clearTarget() {
      this.target?.classList.remove('settings-guide-target');
      for (const [control, previous] of this.descriptions) {
        if (previous === null) control.removeAttribute('aria-describedby');
        else control.setAttribute('aria-describedby', previous);
      }
      this.descriptions = [];
      this.resize.disconnect();
    }
    show(index) {
      if (!this.active || index < 0 || index >= this.steps.length) return;
      this.clearTarget();
      this.index = index;
      const step = this.steps[index];
      if (!step.area.isConnected) { this.stop(); return; }
      this.target = step.area;
      for (let parent = step.area.parentElement; parent && parent !== this.owner; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') {
          if (!this.details.has(parent)) this.details.set(parent, parent.open);
          parent.open = true;
        }
      }
      step.area.scrollIntoView({block:'nearest', inline:'nearest'});
      step.area.classList.add('settings-guide-target');
      this.title.textContent = step.title;
      this.text.textContent = step.text;
      this.count.textContent = `${index + 1} / ${this.steps.length}`;
      this.back.disabled = index === 0;
      this.next.textContent = index + 1 === this.steps.length ? 'Done' : 'Next';
      for (const control of [step.area, ...step.area.querySelectorAll('input,select,textarea,button')].filter(element => element.matches('input,select,textarea,button'))) {
        const previous = control.getAttribute('aria-describedby');
        this.descriptions.push([control, previous]);
        control.setAttribute('aria-describedby', [previous, this.text.id].filter(Boolean).join(' '));
      }
      if (!this.popup.matches(':popover-open')) this.popup.showPopover();
      placeSettingsPopover(this.popup, step.area);
      this.resize.observe(step.area); this.resize.observe(this.popup);
    }
    stop(restoreFocus = true) {
      const focusedInside = this.popup.contains(document.activeElement);
      this.active = false;
      this.clearTarget();
      this.target = null;
      if (this.frame) cancelAnimationFrame(this.frame);
      this.frame = 0;
      this.removal.disconnect();
      document.removeEventListener('keydown', this.onKey, true);
      this.owner.removeEventListener('scroll', this.schedule, true);
      window.removeEventListener('resize', this.schedule);
      for (const [detail, wasOpen] of this.details) if (detail.isConnected) detail.open = wasOpen;
      this.details.clear();
      if (this.popup.matches(':popover-open')) this.popup.hidePopover();
      this.popup.remove();
      this.button.setAttribute('aria-pressed', 'false');
      this.button.removeAttribute('aria-controls');
      if (restoreFocus && focusedInside && this.button.isConnected) this.button.focus({preventScroll:true});
    }
    destroy() { this.stop(false); this.owner.removeEventListener('close', this.onClose); }
  }
  window.ObserverSettingsGuide = ObserverSettingsGuide;

  class ObserverBlockSettings {
    constructor(api) {
      this.api = api;
      this.generation = 0;
      this.dirty = false;
      this.busy = false;
      this.disabledControls = new WeakMap();
      this.model = null;
      this.memoryErrors = new Set();
      this.helpAreas = [];
      this.persistedMemoryIds = new Set();
      this.dialog = document.createElement('dialog');
      this.dialog.id = 'block-settings-dialog';
      this.dialog.className = 'block-settings-dialog';
      this.dialog.setAttribute('aria-labelledby', 'block-settings-title');
      this.dialog.innerHTML = '<div class="block-settings-heading"><div><small>Project defaults</small><h2 id="block-settings-title"></h2><p id="block-settings-purpose" class="block-settings-purpose"></p></div><div class="block-settings-heading-actions"><button type="button" id="block-settings-help" class="settings-help-button" aria-label="Explain settings" title="Explain settings" aria-pressed="false" aria-controls="block-settings-body" hidden><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 16.5h.01"/></svg></button><button type="button" id="block-settings-close" class="block-settings-close" aria-label="Close block settings" title="Close block settings"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12 M18 6L6 18"/></svg></button></div></div><div class="block-settings-body" id="block-settings-body"></div><div class="block-settings-footer"><span id="block-settings-message" role="status" aria-live="polite"></span><button type="button" id="block-settings-reload">Reload settings</button><button type="button" id="block-settings-save" class="primary">Save defaults</button></div>';
      document.body.appendChild(this.dialog);
      this.body = this.dialog.querySelector('#block-settings-body');
      this.message = this.dialog.querySelector('#block-settings-message');
      this.saveButton = this.dialog.querySelector('#block-settings-save');
      this.reloadButton = this.dialog.querySelector('#block-settings-reload');
      this.closeButton = this.dialog.querySelector('#block-settings-close');
      this.closeButton.onclick = () => this.close();
      this.helpButton = this.dialog.querySelector('#block-settings-help');
      this.guide = new ObserverSettingsGuide(this.dialog, this.helpButton, () => this.helpAreas);
      this.helpButton.onclick = () => { const active = this.guide.active; this.dismissPopovers(); if (!active) this.guide.start(); };
      this.dialog.addEventListener('cancel', event => { event.preventDefault(); this.close(); });
      this.dialog.addEventListener('close', () => { if (!this.dialog.open) { this.dismissPopovers(); this.generation++; this.opener?.focus({ preventScroll: true }); } });
      this.reloadButton.onclick = () => { if (!this.dirty || window.confirm('Discard unsaved settings and reload the project defaults?')) this.load(); };
      this.saveButton.onclick = () => this.save();
      this.dialog.addEventListener('keydown', event => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); this.save(); }
      });
    }

    commandVisible(node) {
      const config = this.api.getConfig();
      return !(config.hidden_commands || config.commands?.hidden || []).includes(node.id);
    }


    async open(group, opener) {
      if (!titles[group]) return;
      if (this.dialog.open && !this.close()) return;
      this.group = group;
      this.opener = opener;
      this.dialog.querySelector('#block-settings-title').textContent = titles[group];
      this.dialog.querySelector('#block-settings-purpose').textContent = purposes[group];
      this.dialog.dataset.settingsGroup = group;
      this.helpButton.hidden = !['memory','capabilities','commands'].includes(group);
      this.dialog.querySelector('.block-layout-picker')?.remove();
      this.dialog.showModal();
      this.closeButton.focus();
      await this.load();
    }

    close() {
      if (this.busy) return false;
      if (this.dirty && !window.confirm('Discard unsaved block settings?')) return false;
      this.dismissPopovers();
      this.dialog.close();
      return true;
    }

    async load() {
      this.dismissPopovers();
      const generation = ++this.generation;
      this.model = null;
      this.dirty = false;
      this.memoryErrors.clear();
      this.busy = true;
      this.body.innerHTML = '<p class="block-settings-note">Loading settings…</p>';
      this.status('');
      this.updateButtons();
      try {
        const data = await this.api.request('/api/block-settings');
        if (generation !== this.generation || !this.dialog.open) return;
        this.model = structuredClone(data);
        this.model.documents ||= [];
        this.model.tools ||= [];
        this.model.hidden_commands ||= [];
        this.model.block_layouts ||= {};
        this.model.block_layouts.memory = 'list';
        this.model.capabilities ||= {mcp:[], skills:[]};
        this.model.memory_systems ||= [];
        this.persistedMemoryIds = new Set(this.model.memory_systems.map(system => system.id));
        if (this.group === 'capabilities') {
          const inventory = await this.api.request('/api/capabilities');
          if (generation !== this.generation || !this.dialog.open) return;
          this.capabilitySnapshot = inventory;
        }
        this.render();
      } catch (error) {
        if (generation !== this.generation) return;
        this.body.innerHTML = `<p class="block-settings-error">${escape(error.message)}</p>`;
        window.ObserverFeedback.show(this.dialog, error.message, { error: true });
      } finally {
        if (generation === this.generation) { this.busy = false; this.updateButtons(); }
      }
    }

    status(text, error = false) {
      if (error) {
        window.ObserverFeedback.show(this.dialog, text, { error: true });
        this.message.textContent = this.dirty ? 'Unsaved changes' : '';
        return;
      }
      if (text && text !== 'Unsaved changes' && text !== 'Unsaved project defaults') {
        window.ObserverFeedback.show(this.dialog, text);
        this.message.textContent = this.dirty ? 'Unsaved changes' : '';
        return;
      }
      this.message.textContent = text || (this.dirty ? 'Unsaved changes' : '');
    }

    markDirty() { this.dirty = true; this.status('Unsaved changes'); this.updateButtons(); }
    updateButtons() {
      this.saveButton.hidden = this.group === 'request';
      this.saveButton.disabled = this.busy || !this.model || !this.dirty || this.group === 'memory' && this.memoryErrors.size > 0;
      this.reloadButton.disabled = this.busy;
      this.closeButton.disabled = this.busy;
      this.helpButton.disabled = this.busy;
      for (const input of this.dialog.querySelectorAll('.block-settings-body :is(input,select,textarea,button),.block-layout-picker input')) {
        if (this.busy) {
          if (!this.disabledControls.has(input)) this.disabledControls.set(input, input.disabled);
          input.disabled = true;
        } else if (this.disabledControls.has(input)) {
          input.disabled = this.disabledControls.get(input);
          this.disabledControls.delete(input);
        }
      }
    }

    async save() {
      if (!this.model || !this.dirty || this.busy || this.group === 'memory' && this.memoryErrors.size > 0) return;
      const patch = {revision:this.model.revision};
      if (this.group === 'capabilities') patch.capabilities = this.model.capabilities;
      if (this.group === 'commands') patch.hidden_commands = this.model.hidden_commands;
      if (this.group === 'tools') patch.tools = this.model.tools;
      if (this.group === 'documents') { patch.documents = this.model.documents; patch.vault_path = this.model.vault_path || ''; }
      if (this.group === 'memory') { patch.memory_systems = this.model.memory_systems; patch.block_layouts = {memory:'list'}; }
      if (['commands', 'tools', 'capabilities', 'files'].includes(this.group)) patch.block_layouts = this.model.block_layouts;
      this.dismissPopovers();
      this.busy = true;
      this.updateButtons();
      try {
        const result = await this.api.request('/api/block-settings', patch);
        this.model = structuredClone(result);
        this.persistedMemoryIds = new Set(this.model.memory_systems.map(system => system.id));
        this.dirty = false;
        if (result.catalog) this.api.refreshCatalog(result.catalog, this.group);
        if (this.group === 'capabilities' && result.catalog) this.capabilitySnapshot = {...this.capabilitySnapshot, inventory:result.catalog.capability_inventory, manual:this.model.capabilities};
        this.render();
        this.status('');
        window.ObserverFeedback.show(this.dialog, 'Saved');
      } catch (error) { window.ObserverFeedback.show(this.dialog, error.message, { error: true }); }
      finally { this.busy = false; this.updateButtons(); }
    }

    dismissPopovers() {
      this.guide.stop(false);
      if (this.addMenu?.matches(':popover-open')) this.addMenu.hidePopover();
      this.menuEvents?.abort();
    }

    render() {
      this.dismissPopovers();
      const scrollTop = this.body.scrollTop;
      for (const detail of this.body.querySelectorAll('details[data-capability-group]')) (this.capabilityDisclosures ||= {})[detail.dataset.capabilityGroup] = detail.open;
      this.body.replaceChildren();
      this.dialog.querySelector('.block-layout-picker')?.remove();
      this.addMenu = null;
      this.helpAreas = [];
      ({request:() => this.renderPrompt(), commands:() => this.renderCommands(), tools:() => this.renderTools(), documents:() => this.renderDocuments(), files:() => this.renderFiles(), capabilities:() => this.renderCapabilities(), memory:() => this.renderMemory()})[this.group]();
      if (['commands','capabilities','tools','files'].includes(this.group)) this.renderFooterLayout();
      this.body.scrollTop = scrollTop;
      this.updateButtons();
    }

    explain(area, text, title = area.firstChild?.textContent || 'Memory system') {
      this.helpAreas.push({area, text, title});
    }


    renderFooterLayout() {
      const picker = document.createElement('fieldset');
      picker.className = 'block-layout-picker';
      picker.setAttribute('role', 'radiogroup'); picker.setAttribute('aria-label', 'Card layout');
      for (const [value,title] of [['list','List · up to 5 rows'],['grid','Grid · up to 5 × 5'],['free','Free · resize and reflow']]) {
        const label = document.createElement('label'); label.title = title;
        label.innerHTML = `<input type="radio" name="settings-card-layout" value="${value}" aria-label="${escape(title)}"><span><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${layoutIcons[value]}</svg></span>`;
        const input = label.querySelector('input');
        input.checked = (this.model.block_layouts[this.group] || 'list') === value;
        input.onchange = () => { this.model.block_layouts[this.group] = value; this.markDirty(); };
        picker.appendChild(label);
      }
      this.dialog.querySelector('.block-settings-footer').prepend(picker);
      this.explain(picker, 'Choose a scrolling list, a grid of up to 5 × 5 cards, or freely resized cards. Save to apply this arrangement to the graph.', 'Card layout');
    }

    sectionHeading(section) {
      const heading = document.createElement('div'); heading.className = 'settings-section-heading';
      heading.appendChild(section.querySelector('h3')); section.prepend(heading);
      const actions = document.createElement('div'); actions.className = 'settings-section-actions'; heading.appendChild(actions);
      return actions;
    }

    iconButton(parent, label, icon, handler = null) {
      const button = this.button(parent, label, handler, 'settings-icon-button');
      button.innerHTML = icon; button.setAttribute('aria-label', label); button.title = label;
      return button;
    }

    choiceMenu(parent, trigger, choices, id) {
      const menu = document.createElement('div'); menu.id = id; menu.className = 'settings-add-menu';
      menu.setAttribute('popover', 'auto'); menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', trigger.getAttribute('aria-label'));
      trigger.setAttribute('aria-haspopup', 'menu'); trigger.setAttribute('aria-controls', id); trigger.setAttribute('aria-expanded', 'false');
      parent.appendChild(menu); this.addMenu = menu;
      const options = choices.map(([label, action]) => {
        const option = this.button(menu, label, action); option.setAttribute('role','menuitem'); option.tabIndex = -1; return option;
      });
      const open = () => {
        this.guide.stop(false);
        if (menu.matches(':popover-open')) { menu.hidePopover(); return; }
        menu.showPopover(); placeSettingsPopover(menu, trigger, true); trigger.setAttribute('aria-expanded','true'); options[0].focus({preventScroll:true});
      };
      trigger.onclick = open;
      trigger.onkeydown = event => { if (event.key === 'ArrowDown') { event.preventDefault(); open(); } };
      menu.onkeydown = event => {
        const index = options.indexOf(document.activeElement);
        if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
          options[next].focus();
        } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); menu.hidePopover(); trigger.focus({preventScroll:true}); }
        else if (event.key === 'Tab') menu.hidePopover();
      };
      menu.addEventListener('toggle', () => {
        if (this.addMenu !== menu) return;
        const opened = menu.matches(':popover-open'); trigger.setAttribute('aria-expanded', String(opened)); this.menuEvents?.abort();
        if (!opened) return;
        this.menuEvents = new AbortController();
        const reposition = () => placeSettingsPopover(menu, trigger, true), signal = this.menuEvents.signal;
        window.addEventListener('resize', reposition, {signal}); this.body.addEventListener('scroll', reposition, {signal});
      });
    }

    renderMemory() {
      this.memoryErrors.clear();
      const section = this.section('Configured memory systems');
      const actions = this.sectionHeading(section);
      this.iconButton(actions, 'Recorded memory activity', activityIcon, () => { if (this.close()) this.api.openActivity('memory'); });
      const addButton = this.iconButton(actions, 'Add memory system', plusIcon);
      this.explain(actions, 'Add a Hindsight or manual connection with +. The activity icon opens recorded memory calls.', 'Memory connections');
      const list = document.createElement('div'); list.className = 'memory-settings-list'; section.appendChild(list);
      const empty = document.createElement('p'); empty.className = 'block-settings-note memory-settings-empty'; empty.textContent = 'No memory systems'; empty.hidden = this.model.memory_systems.length > 0; list.appendChild(empty);
      this.memoryEmpty = empty;
      for (const system of this.model.memory_systems) this.memoryEditor(list, system);
      const add = provider => {
        this.dismissPopovers();
        const system = {id:`${provider}-${crypto.randomUUID().slice(0,8)}`, name:provider === 'hindsight' ? 'Hindsight' : 'Memory system', provider, endpoint:provider === 'hindsight' ? 'http://127.0.0.1:8888' : '', experience_endpoint:'', token_file:'', bank_ids:[], categories:[], stores:[], match:{tool_names:[]}};
        this.model.memory_systems.push(system);
        empty.hidden = true;
        const entry = this.memoryEditor(list, system);
        this.markDirty(); entry.querySelector('input')?.focus();
      };
      this.choiceMenu(section, addButton, [['Hindsight', () => add('hindsight')], ['Manual system', () => add('custom')]], 'memory-system-add-menu');
    }

    memoryEditor(list, system) {
      const entry = document.createElement('fieldset'); entry.className = 'memory-settings-entry';
      const legend = document.createElement('legend'); legend.textContent = system.name; entry.appendChild(legend);
      const field = (label, key, help, {multiline = false, readOnly = false, value = system[key] || '', parent = entry} = {}) => {
        const wrap = document.createElement('label'); wrap.textContent = label;
        const input = document.createElement(multiline ? 'textarea' : 'input');
        if (multiline) input.rows = 1; else input.type = 'text';
        input.setAttribute('aria-label', `${label} · ${system.id}`); input.spellcheck = false;
        input.value = value;
        input.readOnly = readOnly;
        if (readOnly) wrap.classList.add('settings-field-automatic');
        else input.oninput = () => { system[key] = input.value; if (key === 'name') legend.textContent = input.value || 'Memory system'; this.markDirty(); };
        wrap.appendChild(input); parent.appendChild(wrap); this.explain(wrap, help); return input;
      };
      field('Name', 'name', 'The label shown on the graph, such as Hindsight · CAD. Renaming this label does not rename the memory service or its banks.');
      field('Stable system ID', 'id', 'Generated when this connection is added. Recorded memory events use this ID to reach the correct card. It is read-only so existing history keeps its destination.', {readOnly:true});
      const provider = document.createElement('p');
      provider.className = 'memory-provider-value memory-settings-wide settings-field-automatic';
      provider.textContent = `Provider · ${system.provider === 'hindsight' ? 'Hindsight' : 'Manual'}`;
      this.explain(provider, 'The provider was selected when this connection was added. It is fixed; add another connection to use a different provider. A manual system is described without API inspection.');
      entry.appendChild(provider);
      field(system.provider === 'hindsight' ? 'Memory API URL' : 'Service URL (optional)', 'endpoint',
        system.provider === 'hindsight' ? 'The base HTTP address of an existing local Hindsight API, for example http://127.0.0.1:8888. This is not the model router or Jupyter address. Saving it does not launch or reconfigure the server.' : 'Optional address of the service being described. Manual systems are not probed by the graph.');
      if (system.provider === 'hindsight') {
        const authConfigured = this.model.memory_auth_configured?.[system.id];
        if (authConfigured === true || authConfigured === undefined && this.persistedMemoryIds.has(system.id)) {
          field('Authorization', 'token_file', 'Managed privately by this project. The graph keeps the existing token-file setting without returning its path or token to the browser. Nothing needs to be entered here.', {readOnly:true, value:authConfigured ? 'Configured privately' : 'Not reported'});
        } else {
          field('Token file (optional)', 'token_file', 'Path to an existing file containing the Hindsight API token, for example memory/.token relative to the project. Enter a file path, never the token itself. Leave blank only if this local API does not require authentication.').placeholder = 'memory/.token';
        }
        field('CAD experience API (optional)', 'experience_endpoint', 'Optional separate CAD taxonomy and lesson-count API, normally http://127.0.0.1:8877. Leave blank for agents without this integration; it is not the Hindsight or model API.').placeholder = 'http://127.0.0.1:8877';
      }
      const bankInput = field('Bank IDs', 'bank_ids', 'Optional filter of existing memory banks. Enter one ID per line, up to 8, for example cad-modeling. Leave empty to discover banks automatically. This does not create, rename, or delete banks.', {multiline:true, value:(system.bank_ids || []).join('\n')});
      bankInput.classList.add('memory-bank-ids');
      bankInput.placeholder = 'Automatic discovery';
      const updateBankRows = () => { bankInput.rows = Math.min(4, Math.max(1, bankInput.value.split(/\r?\n/).length)); };
      updateBankRows();
      bankInput.oninput = () => { system.bank_ids = bankInput.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean); updateBankRows(); this.markDirty(); };
      const advanced = document.createElement('details');
      advanced.innerHTML = '<summary>Categories, storage & operation matching</summary>';
      entry.appendChild(advanced);
      const jsonField = (label, key, initial, help, assign) => {
        const input = field(label, key, help, {multiline:true, parent:advanced});
        input.className = 'memory-settings-json'; input.rows = 5; input.value = JSON.stringify(initial, null, 2);
        input.oninput = () => {
          try {
            const parsed = JSON.parse(input.value); if (!Array.isArray(parsed)) throw new Error();
            assign(parsed); this.memoryErrors.delete(input); input.setAttribute('aria-invalid', 'false'); this.markDirty();
          } catch {
            this.memoryErrors.add(input); input.setAttribute('aria-invalid', 'true'); this.markDirty(); window.ObserverFeedback.show(this.dialog, `${label}: enter a valid JSON array`, { error: true });
          }
        };
      };
      jsonField('Fallback categories (JSON)', 'categories', system.categories || [], 'Optional category descriptions used when no live taxonomy is available. For example [{"id":"geometry","label":"Geometry"}]. Leave [] if not needed. Live categories and experience counts are read automatically, not edited here.', value => { system.categories = value; });
      jsonField('Storage descriptors (JSON)', 'stores', system.stores || [], 'Optional descriptions of the databases behind this memory. For example [{"id":"reference","label":"References","engine":"sqlite","path":"data/reference.db"}]. These are graph labels, not SQL commands or database setup. Never include credentials.', value => { system.stores = value; });
      const aliases = field('Operation aliases', 'aliases', 'Optional exact tool names from recorded memory calls, one per line. These aliases associate existing events with this memory card; they do not run commands. Leave empty when events already identify the memory system.', {multiline:true, parent:advanced, value:(system.match?.tool_names || []).join('\n')});
      aliases.classList.add('memory-bank-ids');
      aliases.rows = Math.min(4, Math.max(1, aliases.value.split(/\r?\n/).length));
      aliases.oninput = () => { system.match = {tool_names:aliases.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean)}; aliases.rows = Math.min(4, Math.max(1, aliases.value.split(/\r?\n/).length)); this.markDirty(); };
      const remove = this.button(entry, 'Remove memory system', () => {
        this.dismissPopovers();
        this.model.memory_systems = this.model.memory_systems.filter(item => item.id !== system.id);
        for (const input of entry.querySelectorAll('textarea')) this.memoryErrors.delete(input);
        this.helpAreas = this.helpAreas.filter(({area}) => !entry.contains(area));
        entry.remove();
        this.memoryEmpty.hidden = this.model.memory_systems.length > 0;
        this.markDirty();
        (list.querySelector('input') || this.body.querySelector('[aria-label="Add memory system"]'))?.focus({preventScroll:true});
      }, 'settings-icon-button memory-settings-remove');
      remove.innerHTML = closeIcon; remove.setAttribute('aria-label', `Remove ${system.name}`); remove.title = 'Remove memory system';
      this.explain(remove, 'Remove this connection from the graph, then save. The memory service, its banks and stored data are kept.', 'Remove connection');
      list.appendChild(entry); return entry;
    }

    renderCapabilities() {
      const config = this.api.getConfig(), snapshot = this.capabilitySnapshot || {};
      const inventory = snapshot.discovery || snapshot.inventory || config.capability_inventory || {}, runtime = snapshot.runtime_inventory || {};
      const maps = {mcp:new Map(), skills:new Map(), integrations:new Map()};
      for (const source of [inventory,runtime]) for (const key of Object.keys(maps)) for (const item of source[key] || []) maps[key].set(item.name.toLowerCase(), item);
      const section = this.section('Tools'), actions = this.sectionHeading(section);
      this.iconButton(actions, 'Search MCP & skills again', refreshIcon, () => this.refreshCapabilities());
      this.iconButton(actions, 'Recorded tool activity', activityIcon, () => { if (this.close()) this.api.openActivity('capabilities'); });
      const addButton = this.iconButton(actions, 'Add tool registration', plusIcon);
      this.explain(actions, 'Refresh the selected agent’s tool catalog, open its recorded calls, or add a manual MCP or skill reference. These actions do not install or connect a tool.', 'Tool actions');
      for (const [key,title] of [['mcp','MCP servers'],['skills','Skills'],['integrations','CLI integrations']]) {
        const items = [...maps[key].values()].filter(item => item.source !== 'manual' || (runtime[key] || []).some(record => record.name.toLowerCase() === item.name.toLowerCase()));
        const group = document.createElement(items.length ? 'details' : 'div'); group.className = 'capability-settings-group'; group.dataset.capabilityGroup = key;
        const heading = document.createElement(items.length ? 'summary' : 'div'); heading.className = 'capability-settings-group-heading';
        heading.innerHTML = `<span>${title}</span><span class="block-settings-count">${items.length}</span>`; group.appendChild(heading);
        if (items.length) {
          group.open = this.capabilityDisclosures?.[key] ?? key !== 'skills';
          group.ontoggle = () => { if (group.isConnected) (this.capabilityDisclosures ||= {})[key] = group.open; };
        }
        for (const item of items) {
          const row = document.createElement('div'); row.className = 'block-inventory-row capability-settings-row';
          row.innerHTML = `<div><div class="capability-settings-row-heading"><strong title="${escape(item.path || item.name)}">${escape(item.name)}</strong><span class="capability-settings-state">${escape(item.connection || item.availability || 'Unknown')}</span></div><small title="${escape(item.description || item.path || '')}">${escape(item.description || item.path || '')}</small></div>`;
          group.appendChild(row);
        }
        section.appendChild(group);
        this.explain(heading, key === 'mcp' ? 'MCP servers found for the selected agent. Configured is not connected; runtime events report the connection state. Edit the agent’s own configuration to change these automatic entries.' : key === 'skills' ? 'Separately installed or runtime-provided agent skills. Project reference files in docs/skills belong in Docs, not here. Automatic entries are read-only.' : 'Command-line integrations available to this agent. RTK is a CLI wrapper, not an MCP server; availability alone does not mean it was used.', title);
      }
      this.capabilityManualSection = this.section('Manual entries');
      this.capabilityManualSection.hidden = !this.model.capabilities.mcp.length && !this.model.capabilities.skills.length;
      const manualActions = this.sectionHeading(this.capabilityManualSection);
      this.capabilityManualCount = document.createElement('span'); this.capabilityManualCount.className = 'block-settings-count'; manualActions.appendChild(this.capabilityManualCount);
      this.capabilityManualList = document.createElement('div'); this.capabilityManualList.className = 'capability-manual-list'; this.capabilityManualSection.appendChild(this.capabilityManualList);
      this.capabilitySerial = {mcp:0,skills:0};
      for (const key of ['mcp','skills']) for (const item of this.model.capabilities[key]) this.capabilityEditor(key,item);
      const add = key => {
        this.dismissPopovers();
        const item = key === 'mcp' ? {name:'',description:'',tool_names:[]} : {name:'',path:'',description:''};
        this.model.capabilities[key].push(item);
        this.capabilityManualSection.hidden = false;
        const entry = this.capabilityEditor(key,item);
        this.markDirty(); entry.querySelector('input').focus();
      };
      this.choiceMenu(section, addButton, [['MCP server', () => add('mcp')], ['Skill', () => add('skills')]], 'capability-add-menu');
      this.updateCapabilityManualCount();
    }

    updateCapabilityManualCount() {
      const count = this.model.capabilities.mcp.length + this.model.capabilities.skills.length;
      this.capabilityManualCount.textContent = count; this.capabilityManualSection.hidden = !count;
    }

    capabilityEditor(key,item) {
      const serial = ++this.capabilitySerial[key], type = key === 'mcp' ? 'MCP' : 'Skill';
      const row = document.createElement('fieldset'); row.className = 'block-manual-capability'; row.dataset.manualCapabilities = key;
      row.innerHTML = `<legend>${type === 'MCP' ? 'MCP server' : 'Skill'}</legend><label>Name<input type="text" aria-label="${type} name ${serial}" spellcheck="false"></label><label>${key === 'mcp' ? 'Tool names' : 'Path or URI'}<input type="text" aria-label="${key === 'mcp' ? 'MCP tool names' : 'Skill path'} ${serial}" spellcheck="false"></label><label class="capability-registration-description">Description<textarea rows="2" aria-label="Registration description ${key} ${serial}"></textarea></label>`;
      const [name,location] = row.querySelectorAll('input'), description = row.querySelector('textarea');
      name.value = item.name || ''; location.value = key === 'mcp' ? (item.tool_names || []).join(', ') : item.path || ''; description.value = item.description || '';
      const remove = this.iconButton(row, `Remove ${type} registration${item.name ? ': '+item.name : ''}`, closeIcon, () => {
        this.dismissPopovers();
        const entries = this.model.capabilities[key], index = entries.indexOf(item);
        if (index >= 0) entries.splice(index,1);
        this.helpAreas = this.helpAreas.filter(({area}) => !row.contains(area)); row.remove();
        this.updateCapabilityManualCount(); this.markDirty();
        (this.capabilityManualList.querySelector('input') || this.body.querySelector('[aria-label="Add tool registration"]'))?.focus({preventScroll:true});
      });
      remove.classList.add('capability-registration-remove');
      name.oninput = () => { item.name = name.value; remove.setAttribute('aria-label',`Remove ${type} registration${item.name ? ': '+item.name : ''}`); this.markDirty(); };
      location.oninput = () => { if (key === 'mcp') item.tool_names = location.value.split(',').map(value => value.trim()).filter(Boolean); else item.path = location.value; this.markDirty(); };
      description.oninput = () => { item.description = description.value; this.markDirty(); };
      this.explain(name.parentElement, 'The tool or skill label shown in the graph. This registers a catalog reference; it does not install or connect an agent plugin.', `${type} name`);
      this.explain(location.parentElement, key === 'mcp' ? 'Exact MCP operation names, separated by commas. They associate recorded calls with this server; no commands run here.' : 'Optional path or URI of an installed skill, such as .agents/skills/review/SKILL.md. Project reference documents should stay in Docs.', key === 'mcp' ? 'Tool names' : 'Skill path');
      this.explain(description.parentElement, 'A short purpose description for this graph. Saving it does not change the tool or its instructions.', 'Description');
      this.explain(remove, 'Remove this manual graph reference, then save. The actual MCP server, installed skill and recorded calls are kept.', 'Remove registration');
      this.capabilityManualList.appendChild(row); this.updateCapabilityManualCount();
      return row;
    }

    async refreshCapabilities() {
      this.dismissPopovers();
      this.busy=true;this.updateButtons();
      try {
        const result=await this.api.request('/api/capabilities/discover',{});
        if(result.config)this.api.refreshCatalog(result.config);
        this.capabilitySnapshot=await this.api.request('/api/capabilities');
        if(!this.dialog.open)return;
        this.render();
      } catch(error){window.ObserverFeedback.show(this.dialog, error.message, { error: true });}
      finally{this.busy=false;this.updateButtons()}
    }

    section(title, note = '') {
      const section = document.createElement('section');
      section.className = 'block-settings-section';
      section.innerHTML = `<h3>${escape(title)}</h3>${note ? `<p class="block-settings-note">${escape(note)}</p>` : ''}`;
      this.body.appendChild(section);
      return section;
    }

    button(parent, label, handler, className = '') {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.className = className;
      button.onclick = handler;
      parent.appendChild(button);
      return button;
    }

    activityButton(parent, group = this.group) {
      this.button(parent, 'View all recorded activity', () => {
        if (this.close()) this.api.openActivity(group);
      });
    }

    renderPrompt() {
      const section=this.section('Captured input');
      const state=this.api.getState(), blocks=state?.prompt_blocks || [];
      const items=[['Prompt',state?.prompt?'Captured from request':'Waiting for request'],['Specification',state?.project_spec||blocks.some(block=>block.kind==='spec')?'Captured from request':'Not recorded'],['Refinements',`${blocks.filter(block=>block.kind==='comment').length} recorded`],['Attachments',`${state?.attachments?.length || 0} recorded`]];
      const list=document.createElement('dl');list.className='block-files-guide';
      for(const [name,value] of items){const term=document.createElement('dt'),detail=document.createElement('dd');term.textContent=name;detail.textContent=value;list.append(term,detail)}
      section.appendChild(list);
    }

    renderCommands() {
      const section = this.section('Commands'), actions = this.sectionHeading(section);
      const commands = new Map();
      for (const command of [...this.api.getKnownCommands(), ...this.api.getCommands()]) commands.set(command.id,command);
      const count = document.createElement('span'); count.className = 'block-settings-count'; actions.appendChild(count);
      const updateCount = () => { const visible = [...commands.keys()].filter(id => !this.model.hidden_commands.includes(id)).length; count.textContent = `${visible}/${commands.size}`; count.title = 'Visible commands'; count.setAttribute('aria-label',`${visible} of ${commands.size} commands visible`); };
      this.iconButton(actions, 'Show all commands', showAllIcon, () => {
        this.model.hidden_commands = [];
        for (const input of list.querySelectorAll('input')) input.checked = true;
        updateCount(); this.markDirty();
      });
      this.iconButton(actions, 'Recorded command activity', activityIcon, () => { if (this.close()) this.api.openActivity('commands'); });
      this.explain(actions, 'Show every known command card, or open the complete recorded command history. Hidden cards do not remove calls or logs.', 'Command actions');
      const list = document.createElement('div'); list.className = 'block-command-options';
      for (const node of [...commands.values()].sort((a,b) => a.label.localeCompare(b.label))) {
        const label = document.createElement('label'); label.className = 'block-command-option';
        const observed = node.status && node.status !== 'idle';
        label.innerHTML = `<input type="checkbox" ${this.model.hidden_commands.includes(node.id) ? '' : 'checked'}><span><strong>${escape(node.label)}</strong><small>${escape(node.description || '')}</small></span>${observed ? '<span class="command-observed-indicator" role="img" aria-label="Recorded in this route" title="Recorded in this route"></span>' : ''}`;
        const input = label.querySelector('input'); input.setAttribute('aria-label',`Show command ${node.label}`);
        input.onchange = () => { this.model.hidden_commands = this.model.hidden_commands.filter(id => id !== node.id); if (!input.checked) this.model.hidden_commands.push(node.id); updateCount(); this.markDirty(); };
        list.appendChild(label);
      }
      if (!commands.size) list.innerHTML = '<p class="block-settings-note">No native commands reported.</p>';
      section.appendChild(list); updateCount();
      this.explain(list.firstElementChild || list, 'Checked commands appear as cards in the graph. Unchecking hides only the card; input, output and chronological call history stay available. The small dot identifies a command recorded in the selected route.', 'Command visibility');
    }

    renderTools() {
      const inventory = this.section('Registered tools & services');
      const list = document.createElement('div');
      list.className = 'block-settings-list';
      this.model.tools.forEach((tool, index) => {
        const row = document.createElement('div');
        row.className = 'block-inventory-row';
        row.innerHTML = `<div class="block-grow"><strong>${escape(tool.name)}</strong><small>${escape(tool.description || 'Configured tool')}</small></div>`;
        this.button(row, 'Remove', () => { this.model.tools.splice(index, 1); this.markDirty(); this.render(); });
        list.appendChild(row);
      });
      if (!this.model.tools.length) list.innerHTML = '<p class="block-settings-note">No registered tools.</p>';
      inventory.appendChild(list);
      const derived = (this.api.getConfig().nodes || []).filter(node => node.kind === 'tool' && node.immutable);
      if (derived.length) {
        const note = document.createElement('p');
        note.className = 'block-settings-note';
        note.textContent = 'Entries from project tool catalog:';
        inventory.appendChild(note);
        for (const tool of derived) {
          const row = document.createElement('div');
          row.className = 'block-inventory-row';
          row.innerHTML = `<div><strong>${escape(tool.label)}</strong><small>${escape(tool.description)}</small><small>Project catalog · ${escape(tool.source || this.model.tools_markdown || '')}</small></div>`;
          inventory.appendChild(row);
        }
        const original = (this.api.getConfig().nodes || []).find(node => node.path === this.model.tools_markdown && node.kind === 'document');
        if (original) this.button(inventory, 'Open tool catalog', () => { if (this.close()) this.api.openDocument(original); });
      }
      const actions = document.createElement('div');
      actions.className = 'block-row-actions';
      this.button(actions, 'Discover local tools', event => this.discoverTools(event.currentTarget));
      this.activityButton(actions);
      inventory.appendChild(actions);
      this.discoveryHolder = document.createElement('div');
      inventory.appendChild(this.discoveryHolder);
      const manual = this.section('Add a tool');
      manual.innerHTML += '<div class="block-source-row"><label class="block-grow">Tool / service name<input id="block-tool-name" type="text" placeholder="My build service"></label><label class="block-grow">Invocation name<input id="block-tool-call" type="text" placeholder="mcp__server__tool"></label></div><label>Description<input id="block-tool-description" type="text" placeholder="What this tool provides"></label>';
      this.button(manual, 'Add to defaults', () => {
        const name = manual.querySelector('#block-tool-name').value.trim(), invocation = manual.querySelector('#block-tool-call').value.trim();
        if (!name) { window.ObserverFeedback.show(this.dialog, 'Enter a tool name.', { error: true }); return; }
        if (this.model.tools.some(tool => tool.name.toLowerCase() === name.toLowerCase())) { window.ObserverFeedback.show(this.dialog, 'This tool is already registered.', { error: true }); return; }
        this.model.tools.push({name, description:manual.querySelector('#block-tool-description').value.trim(), match:{tool_names:[invocation || name]}});
        this.markDirty(); this.render();
      });
      const agent = this.section('Agent discovery prompt', 'Copy this prompt to run tool discovery in your agent.');
      const prompt = document.createElement('textarea');
      prompt.className = 'block-agent-request';
      prompt.setAttribute('aria-label', 'Agent tool discovery request');
      prompt.value = this.agentDiscoveryPrompt();
      agent.appendChild(prompt);
      this.button(agent, 'Copy discovery request', async () => { try { await navigator.clipboard.writeText(prompt.value); window.ObserverFeedback.show(this.dialog, 'Copied'); } catch { prompt.focus(); prompt.select(); window.ObserverFeedback.show(this.dialog, 'Clipboard unavailable. Copy manually.', { error: true }); } });
      const imported = this.section('Import tool inventory', 'Paste a JSON array of tool descriptors (name, description, optional match rules).');
      const input = document.createElement('textarea');
      input.setAttribute('aria-label', 'Agent tool inventory JSON');
      input.placeholder = '[{"name":"Build tool","description":"Project build","match":{"tool_names":["build"]}}]';
      imported.appendChild(input);
      this.button(imported, 'Review / add inventory', () => {
        try {
          const tools = JSON.parse(input.value);
          if (!Array.isArray(tools) || tools.some(tool => !tool || typeof tool !== 'object' || typeof tool.name !== 'string' || !tool.name.trim() || (tool.match !== undefined && (!tool.match || typeof tool.match !== 'object' || Array.isArray(tool.match))))) throw new Error('Expected an array of named tool descriptors.');
          let count = 0;
          for (const tool of tools) if (!this.model.tools.some(existing => existing.name.toLowerCase() === tool.name.trim().toLowerCase())) { this.model.tools.push({name:tool.name.trim(), description:String(tool.description || ''), ...(tool.match ? {match:tool.match} : {})}); count++; }
          if (count) { this.markDirty(); this.render(); }
          window.ObserverFeedback.show(this.dialog, `${count} tool${count === 1 ? '' : 's'} added`);
        } catch (error) { window.ObserverFeedback.show(this.dialog, error.message, { error: true }); }
      });
    }

    agentDiscoveryPrompt() {
      return `Discover the actual tools and services available to this project for its Agent Observer graph.\nProject: ${this.api.getConfig().project_root || this.model.project_root}\nInspect project tool catalogs, MCP definitions, dependency/build manifests and executable availability. Distinguish configured, installed, reachable and actually invoked evidence; do not claim availability solely from a filename. Do not reveal credentials or run destructive commands. Ask before probing external services if authorization is needed.\nReturn a concise evidence report and a JSON array of descriptors with name, description and optional match: {tool_names: ["actual invocation name"]}. Do not include keys, tokens, environment values or command arguments containing secrets.\nCurrently registered names: ${this.model.tools.map(tool => tool.name).join(', ') || 'None'}.`;
    }

    async discoverTools(button) {
      button.disabled = true;
      const generation = this.generation;
      try {
        const result = await this.api.request('/api/tools/discover');
        if (generation !== this.generation || !this.dialog.open) return;
        const tools = (result.tools || []).filter(tool => !tool.immutable && !this.model.tools.some(existing => existing.name.toLowerCase() === tool.name.toLowerCase()));
        const holder = this.discoveryHolder;
        holder.replaceChildren();
        const heading = document.createElement('p');
        heading.className = 'block-settings-note';
        heading.textContent = tools.length ? `${tools.length} candidate${tools.length === 1 ? '' : 's'} found:` : 'No additional candidates found.';
        holder.appendChild(heading);
        const selected = new Set();
        tools.forEach((tool, index) => {
          const row = document.createElement('label');
          row.className = 'block-command-option';
          row.innerHTML = `<input type="checkbox"><span><strong>${escape(tool.name)}</strong><small>${escape(tool.description || '')}</small><small>${escape(tool.source || '')}${tool.evidence ? ` · ${escape(tool.evidence)}` : ''}</small></span>`;
          row.querySelector('input').setAttribute('aria-label', `Register ${tool.name}`);
          row.querySelector('input').onchange = event => event.target.checked ? selected.add(index) : selected.delete(index);
          holder.appendChild(row);
        });
        if (tools.length) this.button(holder, 'Add selected candidates', () => {
          for (const index of selected) { const {source, evidence, ...descriptor} = tools[index]; if (!this.model.tools.some(existing => existing.name.toLowerCase() === descriptor.name.toLowerCase())) this.model.tools.push(descriptor); }
          if (selected.size) { this.markDirty(); this.render(); }
        });
      } catch (error) { if (generation === this.generation) window.ObserverFeedback.show(this.dialog, error.message, { error: true }); }
      finally { button.disabled = false; }
    }

    renderDocuments() {
      const vault = this.section('Obsidian vault', 'Vault folder path. Leave empty to disconnect.');
      const generation=this.generation;
      window.ObserverVaultSettings.mount(vault,{value:this.model.vault_path||'',chooseFolder:()=>this.api.request('/api/installations/choose-folder',{}),
        isCurrent:()=>generation===this.generation&&this.dialog.open&&this.group==='documents',
        onChange:value=>{this.model.vault_path=value;this.markDirty()}});
      const sources = this.section('Project document sources', 'Paths relative to the project root with include patterns.');
      this.model.documents.forEach((source, index) => {
        const row = document.createElement('div');
        row.className = 'block-source-row';
        row.innerHTML = `<label class="block-grow">Folder or file<input type="text" value="${escape(source.path)}" aria-label="Document source path ${index + 1}" spellcheck="false"></label><label class="block-grow">Include patterns<input type="text" value="${escape((source.include || ['**/*.md']).join(', '))}" aria-label="Document include patterns ${index + 1}" spellcheck="false"></label>`;
        const inputs = row.querySelectorAll('input');
        inputs[0].oninput = event => { source.path = event.target.value; this.markDirty(); };
        inputs[1].oninput = event => { source.include = event.target.value.split(',').map(value => value.trim()).filter(Boolean); this.markDirty(); };
        this.button(row, 'Remove', () => { this.model.documents.splice(index, 1); this.markDirty(); this.render(); });
        sources.appendChild(row);
      });
      this.button(sources, 'Add document source', () => { this.model.documents.push({path:'', include:['**/*.md']}); this.markDirty(); this.render(); });
      const section = this.section('Move a document', 'Move a registered file inside its current repository or vault.');
      const docs = (this.api.getConfig().nodes || []).filter(node => node.kind === 'document');
      if (!docs.length) { section.innerHTML += '<p class="block-settings-note">No registered documents to move.</p>'; return; }
      section.innerHTML += '<label>Registered document<select id="block-move-source" aria-label="Document to move"></select></label><label>Destination path, relative to the selected file’s repository root<input id="block-move-destination" type="text" spellcheck="false" placeholder="docs/category/document.md"></label><p class="block-settings-note" id="block-move-root"></p>';
      const select = section.querySelector('select'), destination = section.querySelector('input'), rootNote = section.querySelector('#block-move-root');
      for (const node of docs) { const option = document.createElement('option'); option.value = node.id; option.textContent = node.path || node.label; select.appendChild(option); }
      const update = () => { const node = docs.find(item => item.id === select.value), config = this.api.getConfig(); destination.value = node.source_relative || String(node.path || node.label).replace(/^@vault\//, ''); rootNote.textContent = 'Repository root: ' + (node.source_root === 'vault' ? config.vault_path : config.project_root); };
      select.onchange = update;
      update();
      this.button(section, 'Move file…', () => this.moveDocument(docs.find(node => node.id === select.value), destination.value.trim()));
      this.activityButton(section);
    }

    async moveDocument(node, destination) {
      if (this.dirty) { window.ObserverFeedback.show(this.dialog, 'Save or reload settings before moving a file.', { error: true }); return; }
      if (!destination) { window.ObserverFeedback.show(this.dialog, 'Enter a destination path.', { error: true }); return; }
      if (this.api.hasDocumentDraft(node)) { window.ObserverFeedback.show(this.dialog, 'Document has unsaved draft changes.', { error: true }); return; }
      if (!window.confirm(`Move ${node.path || node.label}\nto ${destination}\ninside its registered repository? Existing files will not be overwritten.`)) return;
      this.busy = true;
      this.updateButtons();
      try {
        const original = await this.api.request('/api/document?path=' + encodeURIComponent(node.path || node.label));
        const result = await this.api.request('/api/document/move', {path:node.path || node.label, destination, revision:original.revision});
        if (result.catalog) this.api.refreshCatalog(result.catalog);
        const settings = await this.api.request('/api/block-settings');
        this.model = structuredClone(settings);
        this.render();
        window.ObserverFeedback.show(this.dialog, `Moved to ${result.path}`);
      } catch (error) { window.ObserverFeedback.show(this.dialog, error.message, { error: true }); }
      finally { this.busy = false; this.updateButtons(); }
    }

    renderFiles() {
      const section = this.section('What belongs here');
      section.innerHTML += '<dl class="block-files-guide"><dt>Code & source files</dt><dd>Source paths passed to recorded read, write, edit or compile calls.</dd><dt>Images & previews</dt><dd>Explicit image paths passed to viewing or processing operations.</dd><dt>Models & exports</dt><dd>Input models and named output files, such as an OpenSCAD export.</dd><dt>Project data</dt><dd>Other explicit file arguments, including extensionless project files.</dd></dl>';
      const recorded = this.section('Files in selected route', 'Files accessed at the current replay step.');
      const files = Object.values(this.api.getState()?.nodes || {}).filter(node => node.kind === 'file');
      const list = document.createElement('div');
      list.className = 'block-settings-list';
      if (!files.length) list.innerHTML = '<p class="block-settings-note">No file access recorded at this step.</p>';
      for (const file of files) { const row = document.createElement('div'); row.className = 'block-inventory-row'; row.innerHTML = `<div><strong>${escape(file.label)}</strong><small>${escape(file.action || 'File argument')} · ${escape(file.reason || '')}</small></div>`; list.appendChild(row); }
      recorded.appendChild(list);
      this.activityButton(recorded);
    }

  }
  window.ObserverBlockSettings = ObserverBlockSettings;
})();
