(() => {
  'use strict';

  // Comprehensive entity escaper escaping &, <, >, ", and '
  const esc = (text) => String(text ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const statusLabel = (status) => {
    switch (status) {
      case 'hit':
        return 'Used';
      case 'observed':
        return 'Called';
      case 'active':
        return 'Active';
      case 'waiting':
        return 'Waiting';
      case 'error':
        return 'Error';
      default:
        return 'Not observed';
    }
  };

  const statusClass = (status) => {
    switch (status) {
      case 'hit':
      case 'observed':
        return 'hit';
      case 'active':
        return 'active';
      case 'waiting':
        return 'waiting';
      case 'error':
        return 'error';
      default:
        return status ? String(status) : 'idle';
    }
  };

  const noteIcon = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  const editIcon = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15Z"/></svg>';
  const folderIcon = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>';
  const docIcon = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';

  class ObserverDocumentsFiles {
    constructor(host, options = {}) {
      this.host = host;
      this.options = options;
      this.documents = [];
      this.documentsMap = new Map();
      this.docFolderMap = new Map();
      this.tree = { name: 'Root', path: '', folders: new Map(), files: [] };
      this.visible = !host.hidden;
      this.searchQuery = '';
      this.focusedIds = new Set();
      this.statuses = new Map(); // id -> status string
      this.expandedPaths = new Set();

      this.host.classList.add('observer-documents-files');
      this.buildChrome();

      this.resizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const width = entry.contentRect.width;
          this.host.classList.toggle('is-compact', width > 0 && width <= 360);
        }
        this.options.onLayout?.();
      });
      this.resizeObserver.observe(this.host);
    }

    get location() {
      return 'Documents';
    }

    buildChrome() {
      this.host.replaceChildren();

      // Top Toolbar: Search + Header + Expand/Collapse All
      this.toolbar = document.createElement('div');
      this.toolbar.className = 'doc-files-toolbar';
      this.toolbar.setAttribute('role', 'toolbar');
      this.toolbar.setAttribute('aria-label', 'Documents tree controls');

      const titleGroup = document.createElement('div');
      titleGroup.className = 'doc-tree-title-group';
      this.titleLabel = document.createElement('span');
      this.titleLabel.className = 'doc-tree-header-title';
      this.titleLabel.textContent = 'Documents';
      this.countBadge = document.createElement('span');
      this.countBadge.className = 'doc-tree-header-count';
      this.countBadge.textContent = '0';
      titleGroup.append(this.titleLabel, this.countBadge);

      this.searchWrap = document.createElement('div');
      this.searchWrap.className = 'doc-files-search-wrap';
      this.searchInput = document.createElement('input');
      this.searchInput.type = 'search';
      this.searchInput.className = 'doc-files-search';
      this.searchInput.placeholder = 'Filter docs…';
      this.searchInput.setAttribute('aria-label', 'Filter documents in tree');
      this.searchInput.oninput = (e) => {
        this.searchQuery = e.target.value.trim().toLowerCase();
        this.applySearchFilter();
      };
      this.searchWrap.appendChild(this.searchInput);

      const actionsGroup = document.createElement('div');
      actionsGroup.className = 'doc-tree-toolbar-actions';

      const expandAllBtn = document.createElement('button');
      expandAllBtn.type = 'button';
      expandAllBtn.className = 'doc-tree-action-btn';
      expandAllBtn.title = 'Expand all folders';
      expandAllBtn.setAttribute('aria-label', 'Expand all folders');
      expandAllBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/></svg>';
      expandAllBtn.onclick = () => this.expandAll();

      const collapseAllBtn = document.createElement('button');
      collapseAllBtn.type = 'button';
      collapseAllBtn.className = 'doc-tree-action-btn';
      collapseAllBtn.title = 'Collapse all folders';
      collapseAllBtn.setAttribute('aria-label', 'Collapse all folders');
      collapseAllBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 11 12 6 17 11"/><polyline points="7 18 12 13 17 18"/></svg>';
      collapseAllBtn.onclick = () => this.collapseAll();

      actionsGroup.append(expandAllBtn, collapseAllBtn);

      this.toolbar.append(titleGroup, this.searchWrap, actionsGroup);

      // Main Tree Container: ONE scrollable tree
      this.treeContainer = document.createElement('div');
      this.treeContainer.className = 'doc-tree-container';
      this.treeContainer.setAttribute('role', 'tree');
      this.treeContainer.setAttribute('aria-label', 'Project documents');

      // Scroll listener on internal viewport: reroute edges without resizing outer container
      this.treeContainer.addEventListener('scroll', () => {
        this.options.onLayout?.();
      }, { passive: true });

      // WAI-ARIA Keyboard navigation for tree items
      this.treeContainer.addEventListener('keydown', (e) => this.handleKeyDown(e));

      this.host.append(this.toolbar, this.treeContainer);
    }

    initExpansionState() {
      let saved = null;
      if (typeof this.options.loadExpanded === 'function') {
        try {
          const val = this.options.loadExpanded();
          if (Array.isArray(val)) {
            saved = new Set(val);
          } else if (val instanceof Set) {
            saved = new Set(val);
          } else if (val && typeof val === 'object') {
            saved = new Set(Object.keys(val).filter(k => Boolean(val[k])));
          }
        } catch {
          saved = null;
        }
      }

      if (saved !== null) {
        this.expandedPaths = saved;
      } else {
        // Sensible shallow default expansion: top-level folders expanded
        this.expandedPaths = new Set();
        for (const folder of this.tree.folders.values()) {
          this.expandedPaths.add(folder.path);
        }
        this.options.saveExpanded?.([...this.expandedPaths]);
      }
    }

    setData(documents = []) {
      this.documents = Array.isArray(documents) ? documents.map(doc => ({ ...doc })) : [];
      this.documentsMap.clear();
      this.docFolderMap.clear();

      // Build unified file tree based on actual filesystem paths
      this.tree = { name: 'Root', path: '', folders: new Map(), files: [] };

      for (const doc of this.documents) {
        this.documentsMap.set(doc.id, doc);
        const rawPath = String(doc.path || doc.label || '').replaceAll('\\', '/').replace(/^\/+/, '');
        const parts = rawPath.split('/').filter(Boolean);

        let branch = this.tree;
        for (let i = 0; i < parts.length - 1; i++) {
          const folderName = parts[i];
          if (!branch.folders.has(folderName)) {
            const folderPath = branch.path ? `${branch.path}/${folderName}` : folderName;
            branch.folders.set(folderName, {
              name: folderName,
              path: folderPath,
              parentPath: branch.path,
              folders: new Map(),
              files: []
            });
          }
          branch = branch.folders.get(folderName);
        }

        const folderPath = branch.path;
        this.docFolderMap.set(doc.id, folderPath);
        branch.files.push(doc);
      }

      this.initExpansionState();
      this.render();
      this.options.onLayout?.();
    }

    findFolder(folderPath) {
      if (!folderPath) return this.tree;
      const parts = folderPath.split('/').filter(Boolean);
      let branch = this.tree;
      for (const part of parts) {
        if (!branch.folders.has(part)) return null;
        branch = branch.folders.get(part);
      }
      return branch;
    }

    collectDescendantDocs(folder) {
      if (!folder) return [];
      const docs = [...folder.files];
      for (const sub of folder.folders.values()) {
        docs.push(...this.collectDescendantDocs(sub));
      }
      return docs;
    }

    folderStatus(folder) {
      const docs = this.collectDescendantDocs(folder);
      const getStatus = d => this.statuses.get(d.id) || d.status || 'idle';
      if (docs.some(d => getStatus(d) === 'active')) return 'active';
      if (docs.some(d => getStatus(d) === 'error')) return 'error';
      if (docs.some(d => getStatus(d) === 'waiting')) return 'waiting';
      if (docs.some(d => ['hit', 'observed'].includes(getStatus(d)))) return 'hit';
      return 'idle';
    }

    folderStats(folder) {
      const docs = this.collectDescendantDocs(folder);
      const getStatus = d => this.statuses.get(d.id) || d.status || 'idle';
      let used = 0;
      let active = 0;
      let error = 0;
      let waiting = 0;
      for (const d of docs) {
        const st = getStatus(d);
        if (st === 'active') active++;
        else if (st === 'error') error++;
        else if (st === 'waiting') waiting++;
        else if (st === 'hit' || st === 'observed') used++;
      }
      return {
        total: docs.length,
        used,
        active,
        error,
        waiting,
        hasActivity: (used + active + error + waiting) > 0
      };
    }

    render() {
      const active = document.activeElement?.closest('[role="treeitem"]');
      const hadFocus = Boolean(active && this.treeContainer.contains(active));
      const previous = hadFocus ? active : this.treeContainer.querySelector('[role="treeitem"][tabindex="0"]');
      if (this.countBadge) {
        this.countBadge.textContent = String(this.documents.length);
      }
      this.treeContainer.replaceChildren();

      const fragment = document.createDocumentFragment();
      this.renderBranch(this.tree, fragment, 1);
      this.treeContainer.appendChild(fragment);

      if (this.searchQuery) this.applySearchFilter();
      const preferred = previous && Array.from(this.treeContainer.querySelectorAll('[role="treeitem"]')).find(item =>
        previous.dataset.nodeId ? item.dataset.nodeId === previous.dataset.nodeId : item.dataset.folderPath === previous.dataset.folderPath);
      const target = this.ensureInitialTabindex(preferred);
      if (hadFocus) target?.focus({ preventScroll: true });
    }

    renderBranch(branch, container, level) {
      // 1. Folders sorted alphabetically
      const sortedFolders = [...branch.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
      for (const folder of sortedFolders) {
        const isExpanded = this.isFolderExpanded(folder.path);
        const st = this.folderStatus(folder);
        const stats = this.folderStats(folder);

        const folderRow = document.createElement('div');
        folderRow.className = `doc-tree-row doc-tree-folder ${statusClass(st)}${!isExpanded && stats.hasActivity ? ' has-hidden-activity' : ''}`;
        folderRow.setAttribute('role', 'treeitem');
        folderRow.setAttribute('tabindex', '-1');
        folderRow.setAttribute('aria-expanded', String(isExpanded));
        folderRow.setAttribute('aria-level', String(level));
        folderRow.dataset.folderPath = folder.path;
        folderRow.dataset.category = folder.path;
        folderRow.dataset.status = st;
        folderRow.style.paddingLeft = `${(level - 1) * 16 + 8}px`;

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'doc-tree-toggle';
        toggleBtn.setAttribute('tabindex', '-1');
        toggleBtn.setAttribute('aria-label', `Toggle folder ${folder.name}`);
        toggleBtn.innerHTML = '<svg class="doc-tree-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
        toggleBtn.onclick = (e) => {
          e.stopPropagation();
          this.toggleFolder(folder.path);
        };

        const icon = document.createElement('span');
        icon.className = 'doc-tree-icon doc-folder-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = folderIcon;

        const label = document.createElement('span');
        label.className = 'doc-tree-label category-name';
        label.textContent = folder.name;

        const statusDot = document.createElement('span');
        statusDot.className = `doc-tree-marker doc-folder-status ${statusClass(st)}`;
        statusDot.title = statusLabel(st);
        statusDot.setAttribute('aria-label', `Status: ${statusLabel(st)}`);
        statusDot.setAttribute('role', 'status');
        const badge = document.createElement('span');
        badge.className = `doc-tree-badge${stats.hasActivity ? ' used-badge' : ''}`;
        if (stats.hasActivity) {
          const activeCount = stats.used + stats.active + stats.error + stats.waiting;
          badge.textContent = `${activeCount}/${stats.total}`;
          badge.title = `${activeCount} active/used of ${stats.total} documents`;
        } else {
          badge.textContent = String(stats.total);
          badge.title = `${stats.total} documents`;
        }

        const statusPill = document.createElement('span');
        statusPill.className = 'card-status';
        statusPill.textContent = st !== 'idle' ? statusLabel(st) : '';

        folderRow.append(toggleBtn, statusDot, icon, label, badge, statusPill);

        folderRow.onclick = (e) => {
          if (e.target.closest('.doc-tree-toggle')) return;
          this.toggleFolder(folder.path);
        };

        // Children group (hidden when collapsed)
        const group = document.createElement('div');
        group.className = 'doc-tree-group';
        group.setAttribute('role', 'group');
        group.dataset.groupPath = folder.path;
        group.hidden = !isExpanded;

        this.renderBranch(folder, group, level + 1);

        container.append(folderRow, group);
      }

      // 2. Files sorted alphabetically by full filename
      const sortedFiles = [...branch.files].sort((a, b) => {
        const nameA = String(a.path || a.label).split('/').filter(Boolean).at(-1) || '';
        const nameB = String(b.path || b.label).split('/').filter(Boolean).at(-1) || '';
        return nameA.localeCompare(nameB);
      });

      for (const doc of sortedFiles) {
        const fileCard = this.createDocumentCard(doc, level);
        container.appendChild(fileCard);
      }
    }

    createDocumentCard(doc, level = 1) {
      const card = document.createElement('article');
      card.id = doc.id;
      card.dataset.nodeId = doc.id;
      const count = Number(doc.usage_count) || 0;
      card.dataset.usageCount = String(count);
      const status = this.statuses.get(doc.id) || doc.status || 'idle';
      const sCls = statusClass(status);
      const sLbl = this.options.statusLabel?.(doc) || statusLabel(status);
      card.className = `card doc-tree-row doc-tree-file ${sCls}`;
      card.dataset.status = status;
      card.dataset.decision = status === 'idle' ? doc.resource_decision?.decision || '' : '';
      card.setAttribute('role', 'treeitem');
      card.setAttribute('tabindex', '-1');
      card.setAttribute('aria-level', String(level));
      card.classList.toggle('sequence-focus', Boolean(this.focusedIds.has(doc.id)));
      card.style.paddingLeft = `${(level - 1) * 16 + 32}px`;

      // Full filename including extension (.md, etc.)
      const rawPath = String(doc.path || doc.label || '');
      const fileName = rawPath.split('/').filter(Boolean).at(-1) || doc.label || doc.id;
      const metaText = doc.description || doc.path || doc.label;
      card.title = `${fileName} (${doc.path || doc.label})`;

      // Leading status marker (Finder-like tag)
      const marker = document.createElement('span');
      marker.className = `doc-tree-marker doc-file-status ${sCls}`;
      marker.title = sLbl;
      marker.setAttribute('aria-label', `Status: ${sLbl}`);
      marker.setAttribute('role', 'status');

      const statusEl = document.createElement('span');
      statusEl.className = 'card-status';
      statusEl.textContent = sLbl;

      const main = document.createElement('div');
      main.className = 'doc-card-main';

      const icon = document.createElement('span');
      icon.className = 'doc-file-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.innerHTML = docIcon;

      const title = document.createElement('div');
      title.className = 'card-title';
      title.textContent = fileName;
      const countEl = document.createElement('span');
      countEl.className = 'doc-file-count';
      if (count > 0) {
        countEl.textContent = String(count);
        countEl.title = `${count} recorded call${count === 1 ? '' : 's'}`;
      } else {
        countEl.hidden = true;
      }

      main.append(icon, title, countEl);

      const meta = document.createElement('div');
      meta.className = 'card-meta';
      meta.textContent = metaText;

      const callMeta = document.createElement('div');
      callMeta.className = 'call-meta';

      card.append(marker, main, statusEl, meta, callMeta);

      card.onclick = () => this.options.onOpen?.(doc.id);

      // Edit action for editable formats
      const isEditable = !/\.(png|jpe?g|gif|webp|svg|ico|pdf|zip|gz|tar|stl|step|stp|obj|woff2?|ttf|eot)$/i.test(fileName);
      if (isEditable && typeof this.options.onEdit === 'function') {
        const isMd = /\.(md|markdown)$/i.test(fileName);
        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.className = 'document-edit-button';
        editBtn.title = isMd ? `Edit Markdown file: ${fileName}` : `Edit file: ${fileName}`;
        editBtn.setAttribute('aria-label', isMd ? `Edit Markdown file: ${fileName}` : `Edit file: ${fileName}`);
        editBtn.innerHTML = editIcon;
        editBtn.onpointerdown = (e) => e.stopPropagation();
        editBtn.onclick = (e) => {
          e.stopPropagation();
          this.options.onEdit?.(doc.id);
        };
        card.appendChild(editBtn);
      }

      // Comments / Notes action
      if (typeof this.options.onNotes === 'function') {
        const commentWrap = document.createElement('span');
        commentWrap.className = 'comment-widget';
        commentWrap.dataset.commentKey = `node:${doc.id}`;
        commentWrap.dataset.commentLabel = `Document: ${fileName}`;

        const commentBtn = document.createElement('button');
        commentBtn.type = 'button';
        commentBtn.className = 'comment-action';
        commentBtn.innerHTML = noteIcon;
        commentBtn.title = `Comments · ${fileName}`;
        commentBtn.setAttribute('aria-label', `Comments: ${fileName}`);
        commentBtn.onpointerdown = (e) => e.stopPropagation();
        commentBtn.onclick = (e) => {
          e.stopPropagation();
          this.options.onNotes?.(doc.id, commentBtn);
        };
        commentWrap.appendChild(commentBtn);
        card.appendChild(commentWrap);
      }

      return card;
    }

    isFolderExpanded(folderPath) {
      return this.expandedPaths.has(folderPath);
    }

    toggleFolder(folderPath) {
      const isExpanded = this.isFolderExpanded(folderPath);
      this.setFolderExpanded(folderPath, !isExpanded);
    }

    setFolderExpanded(folderPath, expanded) {
      if (expanded) {
        this.expandedPaths.add(folderPath);
      } else {
        this.expandedPaths.delete(folderPath);
      }
      this.options.saveExpanded?.([...this.expandedPaths]);

      const folderRow = this.treeContainer?.querySelector(`[data-folder-path="${folderPath}"]`);
      const group = this.treeContainer?.querySelector(`[data-group-path="${folderPath}"]`);

      if (folderRow) {
        folderRow.setAttribute('aria-expanded', String(expanded));
        const folder = this.findFolder(folderPath);
        if (folder) {
          const stats = this.folderStats(folder);
          folderRow.classList.toggle('has-hidden-activity', !expanded && stats.hasActivity);
        }
      }
      if (group) {
        group.hidden = !expanded;
      }
      this.ensureInitialTabindex();

      this.options.onLayout?.();
    }

    expandAll() {
      const addAll = (branch) => {
        for (const folder of branch.folders.values()) {
          this.expandedPaths.add(folder.path);
          addAll(folder);
        }
      };
      addAll(this.tree);
      this.options.saveExpanded?.([...this.expandedPaths]);
      this.syncExpansionDOM();
      this.options.onLayout?.();
    }

    collapseAll() {
      this.expandedPaths.clear();
      this.options.saveExpanded?.([]);
      this.syncExpansionDOM();
      this.options.onLayout?.();
    }

    syncExpansionDOM() {
      for (const group of this.treeContainer.querySelectorAll('.doc-tree-group')) {
        const fPath = group.dataset.groupPath;
        const isExp = this.expandedPaths.has(fPath);
        group.hidden = !isExp;
        const folderRow = this.treeContainer.querySelector(`[data-folder-path="${fPath}"]`);
        if (folderRow) {
          folderRow.setAttribute('aria-expanded', String(isExp));
          const folder = this.findFolder(fPath);
          if (folder) {
            const stats = this.folderStats(folder);
            folderRow.classList.toggle('has-hidden-activity', !isExp && stats.hasActivity);
          }
        }
      }
      this.ensureInitialTabindex();
    }

    applySearchFilter() {
      const query = this.searchQuery.toLowerCase().trim();
      const emptyEl = this.treeContainer.querySelector('.doc-tree-empty');

      if (!query) {
        if (emptyEl) emptyEl.remove();
        // Restore all rows and previous user expansion state without losing what was expanded
        for (const row of this.treeContainer.querySelectorAll('.doc-tree-row')) {
          row.style.display = '';
        }
        this.syncExpansionDOM();
        this.options.onLayout?.();
        return;
      }

      const matchingDocIds = new Set();
      const matchingFolderPaths = new Set();

      for (const doc of this.documents) {
        const name = String(doc.path || doc.label || '').toLowerCase();
        const desc = String(doc.description || '').toLowerCase();
        if (name.includes(query) || desc.includes(query)) {
          matchingDocIds.add(doc.id);
          const fPath = this.docFolderMap.get(doc.id) || '';
          if (fPath) {
            const parts = fPath.split('/').filter(Boolean);
            let acc = '';
            for (const part of parts) {
              acc = acc ? `${acc}/${part}` : part;
              matchingFolderPaths.add(acc);
            }
          }
        }
      }

      // Show matching folders and expand them; hide non-matching
      for (const group of this.treeContainer.querySelectorAll('.doc-tree-group')) {
        const fPath = group.dataset.groupPath;
        const folderRow = this.treeContainer.querySelector(`[data-folder-path="${fPath}"]`);
        if (matchingFolderPaths.has(fPath)) {
          if (folderRow) {
            folderRow.style.display = '';
            folderRow.setAttribute('aria-expanded', 'true');
            folderRow.classList.remove('has-hidden-activity');
          }
          group.hidden = false;
        } else {
          if (folderRow) folderRow.style.display = 'none';
          group.hidden = true;
        }
      }

      // Show matching files, hide non-matching
      for (const fileRow of this.treeContainer.querySelectorAll('.doc-tree-file')) {
        const id = fileRow.dataset.nodeId;
        fileRow.style.display = matchingDocIds.has(id) ? '' : 'none';
      }

      if (matchingDocIds.size === 0) {
        if (!emptyEl) {
          const empty = document.createElement('div');
          empty.className = 'doc-tree-empty';
          empty.textContent = `No documents matching "${this.searchQuery}"`;
          this.treeContainer.appendChild(empty);
        } else {
          emptyEl.textContent = `No documents matching "${this.searchQuery}"`;
        }
      } else if (emptyEl) {
        emptyEl.remove();
      }

      this.ensureInitialTabindex();
      this.options.onLayout?.();
    }

    updateState(documents = [], focusedIds = new Set()) {
      this.focusedIds = focusedIds || new Set();
      for (const doc of documents) {
        if (doc && doc.id) {
          const st = doc.status || 'idle';
          this.statuses.set(doc.id, st);
          const existing = this.documentsMap.get(doc.id);
          if (existing) {
            Object.assign(existing, doc);
            existing.resource_decision = doc.resource_decision;
            existing.usage_count = doc.usage_count ?? 0;
          }
        }
      }

      // Update file cards in DOM
      for (const card of this.treeContainer.querySelectorAll('.card[data-node-id]')) {
        const id = card.dataset.nodeId;
        const status = this.statuses.get(id) || 'idle';
        const sCls = statusClass(status);
        const doc = this.documentsMap.get(id);
        const sLbl = this.options.statusLabel?.(doc || { status }) || statusLabel(status);
        card.className = `card doc-tree-row doc-tree-file ${sCls}`;
        card.dataset.status = status;
        card.dataset.decision = status === 'idle' ? doc?.resource_decision?.decision || '' : '';
        const statusEl = card.querySelector('.card-status');
        if (statusEl) {
          statusEl.textContent = sLbl;
        }
        const marker = card.querySelector('.doc-tree-marker, .doc-file-status');
        if (marker) {
          marker.className = `doc-tree-marker doc-file-status ${sCls}`;
          marker.title = sLbl;
          marker.setAttribute('aria-label', `Status: ${sLbl}`);
        }
        card.classList.toggle('sequence-focus', Boolean(this.focusedIds.has(id)));
        const count = Number(doc?.usage_count) || 0;
        card.dataset.usageCount = String(count);
        let countEl = card.querySelector('.doc-file-count');
        if (!countEl && count > 0) {
          countEl = document.createElement('span');
          countEl.className = 'doc-file-count';
          const main = card.querySelector('.doc-card-main');
          if (main) main.appendChild(countEl);
          else card.appendChild(countEl);
        }
        if (countEl) {
          if (count > 0) {
            countEl.textContent = String(count);
            countEl.title = `${count} recorded call${count === 1 ? '' : 's'}`;
            countEl.hidden = false;
          } else {
            countEl.textContent = '';
            countEl.hidden = true;
          }
        }
      }
      // Update folder statuses in DOM (and ancestor highlighting)
      this.updateFolderStatuses();
      this.options.onLayout?.();
    }

    setStatuses(statuses) {
      if (statuses instanceof Map) {
        for (const [id, st] of statuses) {
          this.statuses.set(id, st);
        }
      } else if (statuses && typeof statuses === 'object') {
        for (const [id, st] of Object.entries(statuses)) {
          this.statuses.set(id, st);
        }
      }
      this.updateState([], this.focusedIds);
    }

    setFocusedIds(focusedIds = new Set()) {
      this.focusedIds = focusedIds instanceof Set ? focusedIds : new Set(focusedIds || []);
      for (const card of this.treeContainer.querySelectorAll('.card[data-node-id]')) {
        card.classList.toggle('sequence-focus', this.focusedIds.has(card.dataset.nodeId));
      }
      this.options.onLayout?.();
    }

    updateFolderStatuses() {
      for (const folderRow of this.treeContainer.querySelectorAll('.doc-tree-folder')) {
        const folderPath = folderRow.dataset.folderPath;
        const folder = this.findFolder(folderPath);
        if (!folder) continue;

        const st = this.folderStatus(folder);
        const stats = this.folderStats(folder);
        const isExpanded = this.isFolderExpanded(folderPath);

        const sCls = statusClass(st);
        const sLbl = statusLabel(st);
        folderRow.dataset.status = st;
        folderRow.className = `doc-tree-row doc-tree-folder ${sCls}${!isExpanded && stats.hasActivity ? ' has-hidden-activity' : ''}`;

        const statusDot = folderRow.querySelector('.doc-folder-status, .doc-tree-marker');
        if (statusDot) {
          statusDot.className = `doc-tree-marker doc-folder-status ${sCls}`;
          statusDot.title = sLbl;
          statusDot.setAttribute('aria-label', `Status: ${sLbl}`);
        }
        const badge = folderRow.querySelector('.doc-tree-badge');
        if (badge) {
          if (stats.hasActivity) {
            badge.className = 'doc-tree-badge used-badge';
            const activeCount = stats.used + stats.active + stats.error + stats.waiting;
            badge.textContent = `${activeCount}/${stats.total}`;
            badge.title = `${activeCount} active/used of ${stats.total} documents`;
          } else {
            badge.className = 'doc-tree-badge';
            badge.textContent = String(stats.total);
            badge.title = `${stats.total} documents`;
          }
        }

        const statusPill = folderRow.querySelector('.card-status');
        if (statusPill) {
          statusPill.textContent = st !== 'idle' ? statusLabel(st) : '';
        }
      }
    }

    getVisibleItems() {
      if (!this.treeContainer) return [];
      return Array.from(this.treeContainer.querySelectorAll('[role="treeitem"]')).filter(el => {
        return el.offsetWidth > 0 && el.offsetHeight > 0 && !el.closest('[hidden]:not([role="treeitem"])');
      });
    }

    ensureInitialTabindex(preferred = null) {
      const visible = this.getVisibleItems();
      const zeros = this.treeContainer.querySelectorAll('[role="treeitem"][tabindex="0"]');
      const target = visible.includes(preferred) ? preferred : visible.find(item => item.tabIndex === 0) || visible[0];
      for (const item of zeros) if (item !== target) item.tabIndex = -1;
      if (target) target.tabIndex = 0;
      return target;
    }

    focusItem(item) {
      if (!item) return;
      for (const el of this.treeContainer.querySelectorAll('[role="treeitem"][tabindex="0"]')) {
        el.setAttribute('tabindex', '-1');
      }
      item.setAttribute('tabindex', '0');
      item.focus({ preventScroll: true });
      item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    handleKeyDown(e) {
      if (e.target === this.searchInput) return;

      const currentItem = e.target.closest('[role="treeitem"]');
      if (!currentItem) return;

      const visibleItems = this.getVisibleItems();
      const currentIndex = visibleItems.indexOf(currentItem);

      switch (e.key) {
        case 'ArrowDown': {
          e.preventDefault();
          if (currentIndex >= 0 && currentIndex < visibleItems.length - 1) {
            this.focusItem(visibleItems[currentIndex + 1]);
          }
          break;
        }
        case 'ArrowUp': {
          e.preventDefault();
          if (currentIndex > 0) {
            this.focusItem(visibleItems[currentIndex - 1]);
          }
          break;
        }
        case 'ArrowRight': {
          e.preventDefault();
          if (currentItem.classList.contains('doc-tree-folder')) {
            const fPath = currentItem.dataset.folderPath;
            if (!this.isFolderExpanded(fPath)) {
              this.setFolderExpanded(fPath, true);
            } else if (currentIndex >= 0 && currentIndex < visibleItems.length - 1) {
              const next = visibleItems[currentIndex + 1];
              const nextLevel = Number(next.getAttribute('aria-level') || 1);
              const currentLevel = Number(currentItem.getAttribute('aria-level') || 1);
              if (nextLevel > currentLevel) {
                this.focusItem(next);
              }
            }
          }
          break;
        }
        case 'ArrowLeft': {
          e.preventDefault();
          if (currentItem.classList.contains('doc-tree-folder') && this.isFolderExpanded(currentItem.dataset.folderPath)) {
            this.setFolderExpanded(currentItem.dataset.folderPath, false);
          } else {
            const group = currentItem.closest('.doc-tree-group');
            if (group) {
              const parentRow = group.previousElementSibling;
              if (parentRow && parentRow.getAttribute('role') === 'treeitem') {
                this.focusItem(parentRow);
              }
            }
          }
          break;
        }
        case 'Home': {
          e.preventDefault();
          if (visibleItems.length > 0) {
            this.focusItem(visibleItems[0]);
          }
          break;
        }
        case 'End': {
          e.preventDefault();
          if (visibleItems.length > 0) {
            this.focusItem(visibleItems[visibleItems.length - 1]);
          }
          break;
        }
        case 'Enter':
        case ' ': {
          if (e.target.closest('button')) return;
          e.preventDefault();
          if (currentItem.classList.contains('doc-tree-folder')) {
            this.toggleFolder(currentItem.dataset.folderPath);
          } else if (currentItem.dataset.nodeId) {
            this.options.onOpen?.(currentItem.dataset.nodeId);
          }
          break;
        }
      }
    }

    getElement(id) {
      if (!this.visible && this.host.hidden) return null;

      const fileRow = this.treeContainer?.querySelector(`[data-node-id="${id}"]`);
      if (fileRow && fileRow.offsetWidth > 0 && fileRow.offsetHeight > 0 && !fileRow.closest('[hidden]')) {
        return fileRow;
      }

      const doc = this.documentsMap.get(id);
      if (!doc) return this.toolbar || this.host;

      const targetFolder = this.docFolderMap.get(id) || '';
      if (targetFolder) {
        const parts = targetFolder.split('/').filter(Boolean);
        let acc = '';
        const folderPaths = [];
        for (const part of parts) {
          acc = acc ? `${acc}/${part}` : part;
          folderPaths.push(acc);
        }

        for (let i = folderPaths.length - 1; i >= 0; i--) {
          const fPath = folderPaths[i];
          const folderEl = this.treeContainer?.querySelector(`[data-folder-path="${fPath}"]`);
          if (folderEl && folderEl.offsetWidth > 0 && folderEl.offsetHeight > 0 && !folderEl.closest('[hidden]')) {
            return folderEl;
          }
        }
      }

      return this.toolbar || this.host;
    }

    getSectionElement(id) {
      if (!this.visible && this.host.hidden) return null;

      const isVisibleInContainer = (el, container) => {
        if (!el || !container) return false;
        const elRect = el.getBoundingClientRect();
        const conRect = container.getBoundingClientRect();
        return (
          elRect.width > 0 &&
          elRect.height > 0 &&
          elRect.bottom >= conRect.top &&
          elRect.top <= conRect.bottom
        );
      };

      const doc = this.documentsMap.get(id);
      if (!doc) {
        return this.toolbar || this.host;
      }

      // 1. If file row itself is rendered and displayed
      const fileRow = this.treeContainer?.querySelector(`[data-node-id="${id}"]`);
      const isRowDisplayed = fileRow && fileRow.offsetWidth > 0 && fileRow.offsetHeight > 0 && !fileRow.closest('[hidden]');

      if (isRowDisplayed) {
        if (isVisibleInContainer(fileRow, this.treeContainer)) {
          return fileRow;
        }
        // When scrolled out of viewport, return stable visible tree boundary anchor
        return this.toolbar || this.host;
      }

      // 2. Resolve to nearest collapsed ancestor folder that is visible in DOM
      const targetFolder = this.docFolderMap.get(id) || '';
      if (targetFolder) {
        const parts = targetFolder.split('/').filter(Boolean);
        let acc = '';
        const folderPaths = [];
        for (const part of parts) {
          acc = acc ? `${acc}/${part}` : part;
          folderPaths.push(acc);
        }

        for (let i = folderPaths.length - 1; i >= 0; i--) {
          const fPath = folderPaths[i];
          const folderEl = this.treeContainer?.querySelector(`[data-folder-path="${fPath}"]`);
          if (folderEl && folderEl.offsetWidth > 0 && folderEl.offsetHeight > 0 && !folderEl.closest('[hidden]')) {
            if (isVisibleInContainer(folderEl, this.treeContainer)) {
              return folderEl;
            }
            return this.toolbar || this.host;
          }
        }
      }

      return this.toolbar || this.host;
    }

    reveal(id) {
      const doc = this.documentsMap.get(id);
      if (!doc) return null;

      if (this.searchQuery) {
        this.searchQuery = '';
        if (this.searchInput) this.searchInput.value = '';
        this.applySearchFilter();
      }

      const targetFolder = this.docFolderMap.get(id) || '';
      if (targetFolder) {
        const parts = targetFolder.split('/').filter(Boolean);
        let acc = '';
        let changed = false;
        for (const part of parts) {
          acc = acc ? `${acc}/${part}` : part;
          if (!this.expandedPaths.has(acc)) {
            this.expandedPaths.add(acc);
            changed = true;
          }
        }
        if (changed) {
          this.options.saveExpanded?.([...this.expandedPaths]);
          this.syncExpansionDOM();
        }
      }

      const card = this.treeContainer?.querySelector(`[data-node-id="${id}"]`);
      if (card) {
        this.focusItem(card);
        card.classList.add('reveal-pulse');
        setTimeout(() => card.classList.remove('reveal-pulse'), 1200);
        this.options.onLayout?.();
        return card;
      }

      return this.getElement(id);
    }

    navigate(folderPath = '') {
      if (!folderPath) {
        if (this.treeContainer) {
          this.treeContainer.scrollTo({ top: 0, behavior: 'smooth' });
        }
        const first = this.getVisibleItems()[0];
        if (first) this.focusItem(first);
        this.options.onLayout?.();
        return;
      }

      const parts = folderPath.split('/').filter(Boolean);
      let acc = '';
      let changed = false;
      for (const part of parts) {
        acc = acc ? `${acc}/${part}` : part;
        if (!this.expandedPaths.has(acc)) {
          this.expandedPaths.add(acc);
          changed = true;
        }
      }
      if (changed) {
        this.options.saveExpanded?.([...this.expandedPaths]);
        this.syncExpansionDOM();
      }

      const folderEl = this.treeContainer?.querySelector(`[data-folder-path="${folderPath}"]`);
      if (folderEl) {
        this.focusItem(folderEl);
      }
      this.options.onLayout?.();
    }

    setVisible(visible) {
      this.visible = Boolean(visible);
      this.host.hidden = !this.visible;
      if (this.visible) {
        this.ensureInitialTabindex();
        this.options.onLayout?.();
      }
    }

    destroy() {
      if (this.resizeObserver) {
        this.resizeObserver.disconnect();
      }
      this.host.replaceChildren();
    }
  }

  window.ObserverDocumentsFiles = ObserverDocumentsFiles;
})();
