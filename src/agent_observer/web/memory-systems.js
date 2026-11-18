(() => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const labels = {available:'API available', configured:'Configured', disabled:'Disabled', unauthorized:'Authentication required', unavailable:'Unavailable', not_found:'Not created', loading:'Inspecting…'};
  const count = value => (Number.isInteger(value) && value >= 0) ? value.toLocaleString() : 'Unknown';
  const countOrDash = value => (Number.isInteger(value) && value >= 0) ? value.toLocaleString() : '—';
  const status = value => labels[value] || 'Not inspected';
  const badge = value => `<span class="memory-availability" data-status="${escape(value || '')}">${escape(status(value))}</span>`;
  const metric = (label, value) => `<div class="memory-metric"><strong>${countOrDash(value)}</strong><span>${escape(label)}</span></div>`;

  function validateUrl(url, { allowEmpty = false, requireLoopback = false, requireHttps = false } = {}) {
    const trimmed = String(url || '').trim();
    if (!trimmed) {
      if (allowEmpty) return { valid: true, value: '' };
      return { valid: false, error: 'URL is required.' };
    }
    try {
      const parsed = new URL(trimmed);
      if (requireHttps) {
        if (parsed.protocol !== 'https:') return { valid: false, error: 'URL must use HTTPS (https://).' };
      } else {
        if (!['http:', 'https:'].includes(parsed.protocol)) return { valid: false, error: 'URL must use HTTP or HTTPS.' };
      }
      if (parsed.username || parsed.password) {
        return { valid: false, error: 'URL must not contain credentials.' };
      }
      if (parsed.search || parsed.hash) {
        return { valid: false, error: 'URL must not contain query parameters or fragments.' };
      }
      if (requireLoopback && !['127.0.0.1', 'localhost', '::1', '[::1]'].includes(parsed.hostname)) {
        return { valid: false, error: 'Automatic Hindsight inspection requires a local loopback URL (127.0.0.1).' };
      }
      return { valid: true, value: trimmed.replace(/\/+$/, '') };
    } catch {
      return { valid: false, error: 'Invalid URL format.' };
    }
  }

  class ObserverMemorySystems {
    constructor(api) {
      this.api = api;
      this.configured = new Map();
      this.snapshots = new Map();
      this.signature = '';
      this.generation = 0;
      this.loading = false;
      this.error = '';
      this.settingsDrafts = new Map();
      this.settingsGeneration = 0;
    }

    setCatalog(refresh = true) {
      const config = this.api.getConfig(), systems = config.memory_systems || [], signature = JSON.stringify([config.project_root, systems]);
      this.configured = new Map(systems.map(system => [system.id, system]));
      if (signature !== this.signature) {
        this.signature = signature;
        this.snapshots.clear();
        if (systems.length && refresh) this.refresh();
        else { this.generation++; this.loading = false; this.error = ''; }
      }
      this.updateCards();
    }

    async refresh() {
      const generation = ++this.generation;
      this.loading = true; this.error = ''; this.updateCards(); this.api.onUpdate?.();
      try {
        const runtime=[...this.configured.keys()].some(id=>id.startsWith('omp-'))?'?runtime=omp':'';
        const data = await this.api.request('/api/memory-systems'+runtime);
        if (generation !== this.generation) return;
        if (!Array.isArray(data.systems)) throw new Error('Unsupported memory metadata response.');
        this.snapshots = new Map(data.systems.map(system => [system.id, system]));
      } catch (error) {
        if (generation !== this.generation) return;
        this.error = error.message || 'Memory metadata could not be loaded.';
      } finally {
        if (generation === this.generation) { this.loading = false; this.updateCards(); this.api.onUpdate?.(); this.api.onLayout?.(); }
      }
    }

    buildCategoryTree(categories) {
      if (!Array.isArray(categories) || !categories.length) return [];

      const byId = new Map();
      for (const cat of categories) {
        if (!cat || (!cat.id && !cat.slug_path)) continue;
        const id = String(cat.id || cat.slug_path);
        if (byId.has(id)) continue;
        const parentId = cat.parent_id || cat.parent_slug_path ? String(cat.parent_id || cat.parent_slug_path) : null;
        byId.set(id, {
          id,
          label: String(cat.label || cat.name || id),
          description: String(cat.description || ''),
          parent_id: parentId,
          lessons_count: (cat.lessons_count !== null && cat.lessons_count !== undefined && !Number.isNaN(Number(cat.lessons_count))) ? Number(cat.lessons_count) : null,
          tags: Array.isArray(cat.tags) ? cat.tags : [],
          children: []
        });
      }

      const roots = [];
      for (const item of byId.values()) {
        let parentId = item.parent_id;
        if (!parentId && item.id.includes('/')) {
          const derived = item.id.slice(0, item.id.lastIndexOf('/'));
          if (byId.has(derived)) {
            parentId = derived;
          }
        }
        if (parentId && byId.has(parentId) && parentId !== item.id) {
          byId.get(parentId).children.push(item);
        } else {
          roots.push(item);
        }
      }

      const comparator = (a, b) => (a.id || a.label).localeCompare(b.id || b.label);

      function sortTree(nodes) {
        nodes.sort(comparator);
        for (const node of nodes) {
          if (node.children.length) sortTree(node.children);
        }
      }
      sortTree(roots);

      const ordered = [];
      function traverse(nodes, depth = 0, lineage = new Set()) {
        for (const node of nodes) {
          if (lineage.has(node.id)) continue;
          ordered.push({ ...node, depth });
          lineage.add(node.id);
          if (node.children.length) {
            traverse(node.children, depth + 1, lineage);
          }
          lineage.delete(node.id);
        }
      }
      traverse(roots, 0);

      if (ordered.length < byId.size) {
        const visited = new Set(ordered.map(o => o.id));
        const missing = [...byId.values()].filter(item => !visited.has(item.id)).sort(comparator);
        for (const item of missing) {
          ordered.push({ ...item, depth: 0 });
        }
      }

      return ordered;
    }

    updateCards() {
      const config = this.api.getConfig(), state = this.api.getState();
      for (const card of document.querySelectorAll('#memory-container > .card')) {
        const node = state?.nodes?.[card.id] || config.nodes?.find(item => item.id === card.id);
        if (!node) continue;
        const system = this.snapshots.get(node.memory_system_id), configured = this.configured.get(node.memory_system_id);
        let meta = card.querySelector('.memory-state');
        if (!meta) { meta = document.createElement('div'); meta.className = 'memory-state'; card.insertBefore(meta, card.querySelector('.card-meta')); }
        const disabled=(configured?.enabled??node.enabled)===false,availability=disabled?'disabled':system?.status||(this.loading?'loading':'');
        meta.dataset.status = availability;
        meta.replaceChildren();
        const states=disabled?['Disabled']:[(configured?.enabled??node.enabled)===true?'Enabled':'Configured',status(availability),'Activity',this.api.statusLabel(node)];
        states.forEach((text,index)=>{if(index)meta.append(index===3?': ':' · ');const part=document.createElement('span');part.textContent=text;meta.append(part)});
        const activity=card.querySelector('.card-status');if(activity){activity.textContent=disabled?'Disabled':system?.status==='available'?'Enabled · API available':(configured?.enabled??node.enabled)===true?'Enabled':'Configured';activity.title='Configuration and API reachability are independent of recorded use or successful retention.'}
        let endpointEl = card.querySelector('.memory-endpoint');
        if (!endpointEl) { endpointEl = document.createElement('div'); endpointEl.className = 'memory-endpoint'; endpointEl.innerHTML = '<span>API</span><code></code>'; meta.after(endpointEl); }
        const endpoint = configured?.endpoint || node.endpoint || '';
        endpointEl.hidden = !endpoint;
        endpointEl.querySelector('code').textContent = endpoint;
        endpointEl.title = `${node.label} · ${endpoint || 'configured memory API'}`;
        const banks = system?.banks?.map(bank => bank.id) || configured?.bank_ids || node.bank_ids || [];
        const categories = system?.categories || configured?.categories || [];
        const tree = this.buildCategoryTree(categories);
        const metaEl = card.querySelector('.card-meta');
        if (metaEl) {metaEl.hidden=disabled||!endpoint;metaEl.textContent = [banks.join(', ') || 'Bank scope not reported', categories.length?`${tree.length} configured categories`:'Category metadata not reported'].join(' · ');}
        card.title = [node.label, endpoint, `Metadata: ${status(availability)}`].filter(Boolean).join('\n');

        let taxonomyEl = card.querySelector('.memory-card-taxonomy');
        if (!taxonomyEl) {
          taxonomyEl = document.createElement('div');
          taxonomyEl.className = 'memory-card-taxonomy';
          const commentWidget = card.querySelector('.comment-widget');
          if (commentWidget) {
            card.insertBefore(taxonomyEl, commentWidget);
          } else {
            card.appendChild(taxonomyEl);
          }
        }

        if (!tree.length) {
          const facts=(system?.banks||[]).filter(bank=>bank.stats).map(bank=>`${bank.id}: ${count(bank.stats.total_nodes)} stored facts · ${count(bank.stats.pending_operations)} pending · ${count(bank.stats.failed_operations)} failed operations`);
          taxonomyEl.replaceChildren();const note=document.createElement('p');note.className='memory-card-empty';note.textContent=disabled?'This memory backend is not selected.':facts.length?facts.join('\n'):!endpoint?'Local capability; API metadata does not apply.':'No category metadata reported. This does not mean memory is empty or disabled.';taxonomyEl.append(note);
        } else {
          let listHtml = '<div class="memory-card-categories" role="list">';
          for (const item of tree) {
            const isUnknown = item.lessons_count === null || item.lessons_count === undefined;
            const lCount = isUnknown ? 'Unknown' : count(item.lessons_count);
            const countAttr = isUnknown ? 'unknown' : String(item.lessons_count);
            const countTitle = isUnknown ? 'Unknown lesson count (configuration fallback)' : `${lCount} lesson${item.lessons_count === 1 ? '' : 's'}`;
            const indentPx = item.depth * 14;
            listHtml += `
              <div class="memory-card-category depth-${item.depth}" data-category-id="${escape(item.id)}" data-depth="${item.depth}" ${item.parent_id ? `data-parent-id="${escape(item.parent_id)}"` : ''} role="listitem">
                <div class="memory-card-category-main" style="padding-left:${indentPx}px">
                  ${item.depth > 0 ? '<span class="memory-card-category-indicator" aria-hidden="true">└─</span>' : ''}
                  <div class="memory-card-category-text">
                    <span class="memory-card-category-label" title="${escape(item.id)}">${escape(item.label)}</span>
                    <code class="memory-card-category-path">${escape(item.id)}</code>
                  </div>
                </div>
                <span class="memory-card-category-count" data-count="${countAttr}" title="${countTitle}">${lCount}</span>
              </div>
            `;
          }
          listHtml += '</div>';
          taxonomyEl.innerHTML = listHtml;
        }
      }
    }

    renderInfo(holder, node) {
      const configured = this.configured.get(node.memory_system_id), snapshot = this.snapshots.get(node.memory_system_id);
      const system = snapshot || configured || {name:node.label, endpoint:node.endpoint, bank_ids:node.bank_ids || [], categories:[], stores:[]};
      holder.className = 'capability-details memory-details';
      const availability = snapshot?.status || (this.loading ? 'loading' : '');
      const githubUrl = system.github_url || (system.provider === 'hindsight' ? 'https://github.com/vectorize-io/hindsight' : '');

      let html = `<section class="memory-overview"><div><small>MEMORY API ENDPOINT</small><p><code>${escape(system.endpoint || 'Manually described memory')}</code></p></div>${badge(availability)}<button type="button" class="memory-refresh" ${this.loading ? 'disabled' : ''}>Refresh metadata</button></section>`;
      html += `<dl class="memory-facts"><dt>Provider</dt><dd>${escape(system.provider === 'hindsight' ? 'Hindsight' : 'Manual memory system')}</dd><dt>Recorded activity</dt><dd id="memory-recorded-use">${escape(this.api.statusLabel(node))}</dd><dt>Metadata snapshot</dt><dd>${escape(snapshot?.checked_at ? new Date(snapshot.checked_at).toLocaleString() : 'Not inspected')}</dd></dl>`;

      holder.innerHTML = html;
      holder.querySelector('.memory-refresh').onclick = () => this.refresh();

      if (githubUrl || system.provider === 'hindsight') {
        const links = document.createElement('p'); links.className = 'memory-reference-links';
        let linksHtml = '';
        if (githubUrl) linksHtml += `<a href="${escape(githubUrl)}" target="_blank" rel="noopener noreferrer">${system.provider === 'hindsight' ? 'Hindsight on GitHub' : 'GitHub repository'} ↗</a>`;
        if (system.provider === 'hindsight') linksHtml += '<a href="https://hindsight.vectorize.io" target="_blank" rel="noopener noreferrer">Documentation ↗</a>';
        links.innerHTML = linksHtml;
        holder.appendChild(links);
      }


      const error = snapshot?.error || this.error;
      if (error) { const message = document.createElement('p'); message.className = 'memory-error'; message.textContent = error; holder.appendChild(message); }
      if (!configured) { const note = document.createElement('p'); note.className = 'memory-boundary'; note.textContent = 'Resource not configured in active settings.'; holder.appendChild(note); }

      this.renderTaxonomyExperience(holder, snapshot, system);
      const modelInfo = snapshot?.model || configured?.model || system.model;
      this.renderModelInfo(holder, modelInfo);
      this.renderBanks(holder, snapshot, system);
      this.renderStores(holder, system.stores || []);
    }

    renderModelInfo(holder, model) {
      const section = this.section(holder, 'Model & routing configuration');
      const dl = document.createElement('dl'); dl.className = 'memory-facts memory-model-facts';
      if (!model || (!model.name && !model.base_url && !model.provider)) {
        dl.innerHTML = '<dt>Model descriptor</dt><dd class="memory-muted-val">Not reported by service</dd><dt>Routing base URL</dt><dd class="memory-muted-val">Not reported by service</dd>';
      } else {
        let content = '';
        content += `<dt>Model descriptor</dt><dd>${model.name ? `<span translate="no">${escape(model.name)}</span>` : '<span class="memory-muted-val">Not reported by service</span>'}</dd>`;
        content += `<dt>Routing base URL</dt><dd>${model.base_url ? `<code>${escape(model.base_url)}</code>` : '<span class="memory-muted-val">Not reported by service</span>'}</dd>`;
        if (model.provider) content += `<dt>Model provider</dt><dd translate="no">${escape(model.provider)}</dd>`;
        dl.innerHTML = content;
      }
      section.appendChild(dl);
    }

    section(holder, title, note) {
      const section = document.createElement('section'); section.className = 'memory-section';
      section.innerHTML = `<h4>${escape(title)}</h4>${note ? `<p class="memory-section-note">${escape(note)}</p>` : ''}`;
      holder.appendChild(section); return section;
    }

    renderTaxonomyExperience(holder, snapshot, system) {
      const categories = system.categories || [];
      const source = snapshot?.categories_source || 'configuration';
      const section = this.section(holder, source === 'configuration' ? 'Configured categories' : 'Experience & categories', system.experience_endpoint || '');
      if (snapshot?.categories_error) { const error = document.createElement('p'); error.className = 'memory-error'; error.textContent = snapshot.categories_error; section.appendChild(error); }

      const tree = this.buildCategoryTree(categories);
      const hero = document.createElement('div'); hero.className = 'memory-hero-stats';
      const uniqueCountVal = snapshot?.taxonomy_unique_lessons;
      const assignmentsTotalVal = snapshot?.taxonomy_assignments_total;
      const uniqueText = (uniqueCountVal !== null && uniqueCountVal !== undefined) ? count(uniqueCountVal) : 'Unknown';
      const assignmentsText = (assignmentsTotalVal !== null && assignmentsTotalVal !== undefined) ? count(assignmentsTotalVal) : 'Unknown';

      hero.innerHTML = `
        <div class="memory-hero-stat">
          <strong>${uniqueText}</strong>
          <span>Unique lessons</span>
        </div>
        <div class="memory-hero-stat">
          <strong>${assignmentsText}</strong>
          <span>Category lesson assignments</span>
        </div>
        <div class="memory-hero-stat">
          <strong>${count(Number.isInteger(snapshot?.taxonomy_categories_count) ? snapshot.taxonomy_categories_count : tree.length)}</strong>
          <span>Taxonomy categories</span>
        </div>
      `;
      section.appendChild(hero);

      const distinction = document.createElement('div'); distinction.className = 'memory-taxonomy-distinction';
      distinction.innerHTML = `
        <p><strong>Unique lessons:</strong> Distinct lessons from CAD experience API. <strong>Category assignments:</strong> Indexed category references.</p>
        ${(uniqueCountVal === null || uniqueCountVal === undefined) ? '<p class="memory-section-note">Showing category assignment counts (unique lesson count not reported by API).</p>' : ''}
      `;
      section.appendChild(distinction);

      if (!tree.length) {
        section.insertAdjacentHTML('beforeend', '<p class="memory-empty">No categories reported.</p>');
        return;
      }

      const table = document.createElement('table'); table.className = 'memory-taxonomy';
      table.innerHTML = '<thead><tr><th scope="col" class="memory-taxonomy-th-label">Category</th><th scope="col" class="memory-taxonomy-th-path">Path</th><th scope="col" class="memory-taxonomy-th-count">Lessons</th></tr></thead>';
      const body = document.createElement('tbody');
      for (const category of tree) {
        const row = document.createElement('tr');
        row.className = `memory-taxonomy-row depth-${category.depth}`;
        row.dataset.categoryId = category.id;
        row.dataset.depth = String(category.depth);
        if (category.parent_id) row.dataset.parentId = category.parent_id;

        const isUnknown = category.lessons_count === null || category.lessons_count === undefined;
        const lCount = isUnknown ? 'Unknown' : count(category.lessons_count);
        const countAttr = isUnknown ? 'unknown' : String(category.lessons_count);
        const countTitle = isUnknown ? 'Unknown lesson count (configuration fallback)' : `${lCount} lesson${category.lessons_count === 1 ? '' : 's'}`;
        const indentPx = category.depth * 18;

        row.innerHTML = `
          <td class="memory-taxonomy-cell-label">
            <div class="memory-taxonomy-item" style="padding-left:${indentPx}px">
              ${category.depth > 0 ? '<span class="memory-taxonomy-tree-branch" aria-hidden="true">└─</span>' : ''}
              <div class="memory-taxonomy-item-text">
                <strong class="memory-taxonomy-label">${escape(category.label)}</strong>
                ${category.description ? `<small class="memory-taxonomy-description">${escape(category.description)}</small>` : ''}
              </div>
            </div>
          </td>
          <td class="memory-taxonomy-cell-path"><code class="memory-taxonomy-path">${escape(category.id)}</code></td>
          <td class="memory-taxonomy-cell-count"><span class="memory-taxonomy-count" data-count="${countAttr}" title="${countTitle}">${lCount}</span></td>
        `;
        body.appendChild(row);
      }
      table.appendChild(body);
      section.appendChild(table);
    }

    renderBanks(holder, snapshot, system) {
      const section = this.section(holder, 'Memory banks');
      const banks = snapshot?.banks?.length ? snapshot.banks : (system.bank_ids || []).map(id => ({id, status:'configured'}));
      if (!banks.length) section.insertAdjacentHTML('beforeend', '<p class="memory-empty">No banks reported.</p>');
      for (const bank of banks) {
        const card = document.createElement('article'); card.className = 'memory-bank';
        card.innerHTML = `<header><div><strong>${escape(bank.name || bank.id)}</strong>${bank.name && bank.name !== bank.id ? `<code>${escape(bank.id)}</code>` : ''}</div>${badge(bank.status)}</header>`;
        if (bank.stats) {
          card.insertAdjacentHTML('beforeend', `<div class="memory-metrics">${metric('Memory units',bank.stats.total_nodes)}${metric('Documents',bank.stats.total_documents)}${metric('Links',bank.stats.total_links)}${metric('Pending operations',bank.stats.pending_operations)}${metric('Failed operations',bank.stats.failed_operations)}</div>`);
          const types = (bank.memory_types || []).map(type => `<span class="memory-type">${escape(type.id)} <strong>${countOrDash(type.count)}</strong></span>`).join('');
          if (types) card.insertAdjacentHTML('beforeend', `<div class="memory-types" aria-label="Actual memory types">${types}</div>`);
          const tags = (bank.tags || []).map(tag => `<span class="memory-tag">${escape(tag.tag)} <strong>${countOrDash(tag.count)}</strong></span>`).join('');
          if (tags) card.insertAdjacentHTML('beforeend', `<div class="memory-types" aria-label="Indexed memory tags">${tags}</div>`);
          if (bank.tags_truncated) card.insertAdjacentHTML('beforeend', `<p class="memory-section-note">Showing the first ${bank.tags.length} of ${countOrDash(bank.tags_total)} tags.</p>`);
        }
        if (bank.error) { const error = document.createElement('p'); error.className = 'memory-error'; error.textContent = bank.error; card.appendChild(error); }
        section.appendChild(card);
      }
      if (snapshot?.banks_truncated) section.insertAdjacentHTML('beforeend', '<p class="memory-section-note">Showing first 8 banks.</p>');
    }

    renderStores(holder, stores) {
      const section = this.section(holder, 'Storage & SQL');
      if (!stores.length) section.insertAdjacentHTML('beforeend', '<p class="memory-empty">No storage descriptors configured.</p>');
      for (const store of stores) {
        const card = document.createElement('article'); card.className = 'memory-store';
        const isLegacySqlite = store.role === 'legacy';
        const displayLabel = isLegacySqlite ? 'Retained reference database' : store.label;
        const engineLabel = store.engine === 'postgresql' ? 'PostgreSQL' : store.engine === 'sqlite' ? 'SQLite' : store.engine;
        const roleLabel = isLegacySqlite ? 'Reference source' : store.role === 'index' ? 'Memory index' : 'Primary storage';
        card.innerHTML = `<header><strong translate="${isLegacySqlite?'yes':'no'}">${escape(displayLabel)}</strong><span class="memory-store-engine">${escape(engineLabel)}</span></header><p class="memory-store-role">${escape(roleLabel)}</p>`;

        if (store.role === 'index') {
          card.insertAdjacentHTML('beforeend', `
            <div class="memory-taxonomy-distinction">
              <strong>Hindsight vector index:</strong> Shared semantic embedding and link index for Hindsight associative recall.
            </div>
          `);
        } else if (store.role === 'primary') {
          card.insertAdjacentHTML('beforeend', `
            <div class="memory-taxonomy-distinction">
              <strong>Experience storage:</strong> Configured storage for lessons and categories.
            </div>
          `);
        }

        const facts = [['File',store.path],['Host',store.host ? `${store.host}${store.port ? ':'+store.port : ''}` : ''],['Database',store.database],['Schema',store.schema]];
        card.insertAdjacentHTML('beforeend', `<dl class="memory-facts">${facts.filter(([,value])=>value).map(([key,value])=>`<dt>${escape(key)}</dt><dd><code>${escape(value)}</code></dd>`).join('')}</dl>`);
        if (store.tables?.length) card.insertAdjacentHTML('beforeend', `<div class="memory-types" aria-label="Configured SQL tables">${store.tables.map(name=>`<code class="memory-tag">${escape(name)}</code>`).join('')}</div>`);
        section.appendChild(card);
      }
    }

    async renderSettings(holder, node) {
      const generation=++this.settingsGeneration,id=node.memory_system_id;
      this.settingsGuide?.destroy();
      holder.className='capability-details memory-details memory-settings-container';
      holder.innerHTML='<p class="memory-empty">Loading settings…</p>';
      let draft=this.settingsDrafts.get(id);
      if(!draft){
        let current;try{current=await this.api.request('/api/block-settings')}catch(error){if(generation===this.settingsGeneration&&holder.dataset.resourceTab==='settings'){holder.replaceChildren();const message=document.createElement('p');message.className='memory-error';message.textContent=error.message;holder.appendChild(message)}return}
        if(generation!==this.settingsGeneration||holder.dataset.resourceTab&&holder.dataset.resourceTab!=='settings')return;
        const configured=current.memory_systems?.find(system=>system.id===id);
        if(!configured){holder.innerHTML='<p class="memory-empty">Memory system not configured.</p>';return}
        draft={current,system:structuredClone(configured),values:null};
      }
      if(generation!==this.settingsGeneration||holder.dataset.resourceTab&&holder.dataset.resourceTab!=='settings')return;
      const system=structuredClone(draft.system);

      holder.className = 'capability-details memory-details memory-settings-container';
      holder.innerHTML = `
        <div class="memory-settings-form">
          <div class="memory-settings-toolbar"><button type="button" id="mem-set-help" class="settings-help-button" aria-label="Explain settings" title="Explain settings" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 16.5h.01"/></svg></button></div>
          <div class="memory-settings-grid">
            <div class="memory-field">
              <label for="mem-set-name">System name</label>
              <input type="text" id="mem-set-name" value="${escape(system.name || '')}" spellcheck="false" />
            </div>

            <div class="memory-field settings-field-automatic">
              <label for="mem-set-provider">Provider</label>
              <input type="text" id="mem-set-provider" value="${system.provider === 'hindsight' ? 'Hindsight' : 'Manual'}" readonly />
            </div>

            <div class="memory-field memory-settings-wide">
              <label for="mem-set-endpoint">Memory API endpoint</label>
              <input type="text" id="mem-set-endpoint" value="${escape(system.endpoint || '')}" placeholder="http://127.0.0.1:8888" spellcheck="false" />
            </div>

            <div class="memory-field memory-settings-wide" id="mem-set-exp-wrap" style="${system.provider === 'hindsight' ? '' : 'display:none;'}">
              <label for="mem-set-exp">CAD experience API endpoint</label>
              <input type="text" id="mem-set-exp" value="${escape(system.experience_endpoint || '')}" placeholder="http://127.0.0.1:8877" spellcheck="false" />
            </div>

            <div class="memory-field memory-settings-wide">
              <label for="mem-set-github">GitHub repository URL</label>
              <input type="text" id="mem-set-github" value="${escape(system.github_url || (system.provider === 'hindsight' ? 'https://github.com/vectorize-io/hindsight' : ''))}" placeholder="https://github.com/vectorize-io/hindsight" spellcheck="false" />
            </div>

            <div class="memory-field memory-settings-wide">
              <label for="mem-set-banks">Bank IDs</label>
              <textarea id="mem-set-banks" class="memory-bank-ids" rows="1" spellcheck="false" placeholder="Automatic discovery">${escape((system.bank_ids || []).join('\n'))}</textarea>
            </div>

            <fieldset class="memory-settings-wide memory-field" style="border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin:0;">
              <legend style="padding:0 6px; font-size:11px; font-weight:600; color:var(--muted);">Model info (optional)</legend>
              <div class="memory-settings-grid" style="margin-top:4px;">
                <div class="memory-field">
                  <label for="mem-set-model-name">Model name</label>
                  <input type="text" id="mem-set-model-name" value="${escape(system.model?.name || '')}" placeholder="routing" spellcheck="false" />
                </div>
                <div class="memory-field">
                  <label for="mem-set-model-provider">Model provider</label>
                  <input type="text" id="mem-set-model-provider" value="${escape(system.model?.provider || '')}" placeholder="openai" spellcheck="false" />
                </div>
                <div class="memory-field memory-settings-wide">
                  <label for="mem-set-model-base">Routing base URL</label>
                  <input type="text" id="mem-set-model-base" value="${escape(system.model?.base_url || '')}" placeholder="http://127.0.0.1:20128/v1" spellcheck="false" />
                </div>
              </div>
            </fieldset>
          </div>


          <div class="memory-settings-actions">
            <button type="button" id="mem-set-save">Save settings</button>
            <button type="button" id="mem-set-reset">Reset</button>
          </div>
        </div>
      `;

      const providerInput = holder.querySelector('#mem-set-provider');
      const nameInput = holder.querySelector('#mem-set-name');
      const endpointInput = holder.querySelector('#mem-set-endpoint');
      const expInput = holder.querySelector('#mem-set-exp');
      const githubInput = holder.querySelector('#mem-set-github');
      const banksInput = holder.querySelector('#mem-set-banks');
      const modelNameInput = holder.querySelector('#mem-set-model-name');
      const modelProviderInput = holder.querySelector('#mem-set-model-provider');
      const modelBaseInput = holder.querySelector('#mem-set-model-base');
      const owner = holder.closest ? (holder.closest('dialog') || holder.closest('.resource-dialog') || holder) : holder;
      const showFeedback = (text, isErr = false) => window.ObserverFeedback.show(owner, text, { error: isErr });
      const clearFeedback = () => window.ObserverFeedback.clear(owner);
      const saveBtn = holder.querySelector('#mem-set-save');
      const resetBtn = holder.querySelector('#mem-set-reset');
      const fields={name:nameInput,endpoint:endpointInput,experience:expInput,github:githubInput,banks:banksInput,modelName:modelNameInput,modelProvider:modelProviderInput,modelBase:modelBaseInput};
      if(draft.values)for(const [key,input] of Object.entries(fields))input.value=draft.values[key];
      const updateBankRows = () => { banksInput.rows = Math.min(4, Math.max(1, banksInput.value.split(/\r?\n/).length)); };
      updateBankRows();
      banksInput.addEventListener('input', updateBankRows);
      const captureDraft=()=>{draft.values=Object.fromEntries(Object.entries(fields).map(([key,input])=>[key,input.value]));this.settingsDrafts.set(id,draft)};
      const form=holder.querySelector('.memory-settings-form');form.addEventListener('input',captureDraft);form.addEventListener('change',captureDraft);

      const helpButton = holder.querySelector('#mem-set-help');
      const guides = [
        [nameInput, 'A display label for this graph, such as Hindsight · CAD. It does not rename the service or its banks.'],
        [providerInput, 'Selected when this connection was added and read-only. Add another connection to use a different provider. A manual system is not inspected through an API.'],
        [endpointInput, system.provider === 'hindsight' ? 'The base HTTP URL of an existing local Hindsight API, for example http://127.0.0.1:8888. Not a model router or Jupyter address. Saving does not start or reconfigure the server.' : 'Optional address of the service being described. A manual system is not probed by the graph.'],
        [expInput, 'Optional separate CAD taxonomy and lesson-count API, normally http://127.0.0.1:8877. Leave blank for agents without this integration.'],
        [githubInput, 'An HTTPS GitHub repository link for reference. It is not a service endpoint, and saving the link does not install anything.'],
        [banksInput, 'Optional filter of existing bank IDs, one per line, up to 8. For example cad-modeling. Leave empty for automatic discovery. This does not create or modify banks.'],
        [modelNameInput, 'Optional model name shown in the memory information panel. This is descriptive metadata; it does not select or replace the model used by the memory service.'],
        [modelProviderInput, 'Optional provider label, for example openai. Display information only, not a provider connection or authentication setting.'],
        [modelBaseInput, 'Optional model/router base URL shown as information, for example http://127.0.0.1:20128/v1. Do not include credentials. This does not change actual request routing.']
      ].filter(([input]) => input !== expInput || system.provider === 'hindsight').map(([input, text]) => {
        const area = input.closest('.memory-field');
        return {area, text, title:area.querySelector('label').textContent};
      });
      this.settingsGuide = new window.ObserverSettingsGuide(owner, helpButton, () => guides);
      helpButton.onclick = () => this.settingsGuide.toggle();

      resetBtn.onclick = () => {
        clearFeedback();
        this.settingsDrafts.delete(id);
        this.renderSettings(holder, node);
      };

      saveBtn.onclick = async () => {
        this.settingsGuide?.stop(false);
        clearFeedback();

        const nameVal = nameInput.value.trim();
        if (!nameVal) {
          showFeedback('System name is required.', true);
          nameInput.focus();
          return;
        }

        const providerVal = system.provider;
        const endpointValidation = validateUrl(endpointInput.value, {
          allowEmpty: providerVal === 'custom',
          requireLoopback: providerVal === 'hindsight'
        });
        if (!endpointValidation.valid) {
          showFeedback(`Memory endpoint: ${endpointValidation.error}`, true);
          endpointInput.focus();
          return;
        }

        let expVal = '';
        if (providerVal === 'hindsight') {
          const expValidation = validateUrl(expInput.value, { allowEmpty: true, requireLoopback: true });
          if (!expValidation.valid) {
            showFeedback(`CAD experience endpoint: ${expValidation.error}`, true);
            expInput.focus();
            return;
          }
          expVal = expValidation.value;
        }

        let githubVal = '';
        const githubValidation = validateUrl(githubInput.value, { allowEmpty: true, requireHttps: true });
        if (!githubValidation.valid) {
          showFeedback(`GitHub URL: ${githubValidation.error}`, true);
          githubInput.focus();
          return;
        }
        githubVal = githubValidation.value;
        if(githubVal&&!['github.com','www.github.com','gist.github.com'].includes(new URL(githubVal).hostname)){
          showFeedback('Use an HTTPS GitHub or GitHub Gist repository URL.', true);
          githubInput.focus();
          return;
        }
        if (!githubVal && providerVal === 'hindsight') {
          githubVal = 'https://github.com/vectorize-io/hindsight';
        }

        let modelObj = null;
        const mName = modelNameInput.value.trim();
        const mProv = modelProviderInput.value.trim();
        const mBase = modelBaseInput.value.trim();
        if (mBase) {
          const baseValidation = validateUrl(mBase, { allowEmpty: false });
          if (!baseValidation.valid) {
            showFeedback(`Model routing base URL: ${baseValidation.error}`, true);
            modelBaseInput.focus();
            return;
          }
        }
        if (mName || mProv || mBase) {
          modelObj = {
            name: mName,
            provider: mProv,
            base_url: mBase ? mBase.replace(/\/+$/, '') : ''
          };
        }

        const bankIds = banksInput.value.split('\n').map(v => v.trim()).filter(Boolean);
        if (bankIds.length > 8) {
          showFeedback('A maximum of 8 bank IDs is supported.', true);
          banksInput.focus();
          return;
        }

        saveBtn.disabled = true;
        resetBtn.disabled = true;
        const submittedValues = draft.values;
        try {
          const current = draft.current;
          const revision = current.revision;
          const memorySystems = Array.isArray(current.memory_systems) ? current.memory_systems : [];

          const updatedSystems = memorySystems.map(sys => {
            if (sys.id === system.id) {
              return {
                ...sys,
                name: nameVal,
                provider: providerVal,
                endpoint: endpointValidation.value,
                experience_endpoint: expVal,
                github_url: githubVal,
                bank_ids: bankIds,
                model: modelObj
              };
            }
            return sys;
          });

          const patch = { revision, memory_systems: updatedSystems };
          const result = await this.api.request('/api/block-settings', patch);
          draft.current=result;draft.system=structuredClone(result.memory_systems.find(item=>item.id===id));
          // Input capture replaces this object; only discard the exact draft that was submitted.
          if(draft.values===submittedValues){draft.values=null;this.settingsDrafts.delete(id)}

          showFeedback('Settings saved');

          const refreshedConfig = result.catalog || result;
          if (this.api.onConfigSaved) {
            this.api.onConfigSaved(refreshedConfig);
          }

          this.setCatalog(false);
          await this.refresh();
        } catch (err) {
          showFeedback(err.message || 'Failed to save settings.', true);
        } finally {
          saveBtn.disabled = false;
          resetBtn.disabled = false;
        }
      };
    }
  }

  (typeof window !== 'undefined' ? window : globalThis).ObserverMemorySystems = ObserverMemorySystems;
})();
