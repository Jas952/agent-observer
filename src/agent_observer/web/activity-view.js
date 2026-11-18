(() => {
  'use strict';

  function escapeHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatMs(ms) {
    if (ms < 1000) return `${Math.round(ms)} ms`;
    return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
  }

  function calcDuration(call) {
    if (typeof call.duration === 'number') return formatMs(call.duration);
    if (call.startedAt && call.endedAt) {
      const t0 = Date.parse(call.startedAt);
      const t1 = Date.parse(call.endedAt);
      if (Number.isFinite(t0) && Number.isFinite(t1) && t1 >= t0) {
        return formatMs(t1 - t0);
      }
    }
    return '';
  }

  function safeJsonParse(value) {
    if (typeof value !== 'string') return value;
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }

  function normalizeToolName(tool) {
    return String(tool || '').trim().replace(/^(?:functions|collaboration)\./, '').replace(/^default_api:/, '').toLowerCase();
  }

  function extractDestinations(call) {
    const dests = [];
    const ev = call.ev || {};
    const end = call.endEvent || {};

    const checkFields = (obj) => {
      if (!obj || typeof obj !== 'object') return;
      for (const key of ['output_path', 'output_file', 'output_dir', 'output_target', 'destination', 'dest_path', 'target_path', 'created_file', 'modified_file']) {
        if (typeof obj[key] === 'string' && obj[key].trim()) dests.push(obj[key].trim());
      }
      for (const key of ['destinations', 'created_files', 'modified_files', 'output_files']) {
        if (Array.isArray(obj[key])) {
          for (const item of obj[key]) {
            if (typeof item === 'string' && item.trim()) dests.push(item.trim());
          }
        }
      }
    };

    checkFields(end);
    checkFields(ev);

    const input = safeJsonParse(ev.input ?? ev.args ?? ev.parameters);
    if (input && typeof input === 'object') {
      checkFields(input);
    }

    const normTool = normalizeToolName(ev.tool || ev.tool_name || call.tool || '');
    const isWriteTool = /^(?:write|write_file|create_file|edit|edit_file|apply_patch|put|save)$/.test(normTool);
    if (isWriteTool) {
      if (typeof ev.path === 'string' && ev.path.trim()) dests.push(ev.path.trim());
      if (typeof ev.file_path === 'string' && ev.file_path.trim()) dests.push(ev.file_path.trim());
      if (input && typeof input === 'object') {
        if (typeof input.path === 'string' && input.path.trim()) dests.push(input.path.trim());
        if (typeof input.file_path === 'string' && input.file_path.trim()) dests.push(input.file_path.trim());
      }
    }

    return [...new Set(dests)];
  }

  function extractReferencedFiles(call, explicitDests = []) {
    const refs = [];
    const destSet = new Set(explicitDests.map(d => d.toLowerCase()));
    const seen = new Set();

    const add = (candidate) => {
      if (!candidate || typeof candidate !== 'string') return;
      const clean = candidate.trim().replace(/^["']|["']$/g, '').replace(/:\d+(?:-\d+)?(?::\d+)?$/, '');
      if (!clean || destSet.has(clean.toLowerCase()) || seen.has(clean.toLowerCase())) return;
      seen.add(clean.toLowerCase());
      refs.push(clean);
    };

    if (window.ObserverOperationEvidence?.explicitPaths) {
      const paths = window.ObserverOperationEvidence.explicitPaths(call.ev);
      if (Array.isArray(paths)) paths.forEach(add);
    }

    const ev = call.ev || {};
    for (const key of ['path', 'file_path', 'filepath', 'filename', 'file']) {
      if (typeof ev[key] === 'string') add(ev[key]);
    }
    const input = safeJsonParse(ev.input ?? ev.args ?? ev.parameters);
    if (input && typeof input === 'object') {
      for (const key of ['path', 'file_path', 'filepath', 'filename', 'file']) {
        if (typeof input[key] === 'string') add(input[key]);
      }
    }

    return refs;
  }

  function findDocumentNode(nodes, path) {
    if (!nodes || !path) return null;
    const clean = path.replace(/^["']|["']$/g, '').toLowerCase();
    const values = Array.isArray(nodes) ? nodes : Object.values(nodes);
    return values.find(n => {
      if (n.kind !== 'document') return false;
      const label = String(n.label || '').toLowerCase();
      const nodePath = String(n.path || '').toLowerCase();
      const abs = String(n.absolute_path || '').toLowerCase();
      return label === clean || nodePath === clean || abs === clean ||
        clean.endsWith('/' + nodePath) || clean.endsWith('/' + label);
    }) || null;
  }

  function renderFormattedInput(model, event, rawInput) {
    if (typeof model.formatInput === 'function') {
      return model.formatInput(event, rawInput, escapeHtml);
    }
    const text = typeof rawInput === 'object' && rawInput !== null ? JSON.stringify(rawInput, null, 2) : String(rawInput ?? '');
    return escapeHtml(text);
  }

  function getStatusClass(status) {
    const s = String(status || '').toLowerCase();
    if (s.includes('succeed') || s.includes('complete') || s === 'hit') return 'succeeded';
    if (s.includes('fail') || s.includes('error')) return 'failed';
    if (s.includes('progress') || s.includes('running') || s === 'active') return 'in-progress';
    return 'not-recorded';
  }

  class ObserverActivityView {
    static render(holder, model) {
      if (!holder) return null;
      let view = holder._observerActivityView;
      if (!view || !(view instanceof ObserverActivityView)) {
        view = new ObserverActivityView();
        holder._observerActivityView = view;
      }
      return view.render(holder, model);
    }


    constructor() {
      this.selectedStep = null;
      this.listScrollTop = 0;
      this.detailScrollTop = 0;
      this.singleScrollTop = 0;
      this.rawDetailsOpen = false;
      this.currentScope = null;
      this.currentResource = null;
      this.currentResourceId = null;
      this.currentModel = null;
    }

    render(holder, model) {
      if (!holder) return;

      const scope = (model.scope === 'single') ? 'single' : 'group';
      const calls = Array.isArray(model.calls) ? model.calls : [];
      const resource = model.resource || null;
      const resourceId = resource ? (resource.id ?? resource.name ?? resource.label ?? null) : null;
      const isSameResource = Boolean(
        resource &&
        this.currentResource &&
        (this.currentResource === resource || (resourceId != null && this.currentResourceId === resourceId))
      );

      if (!isSameResource || this.currentScope !== scope) {
        this.currentModel = null;
        this.currentResource = null;
        this.currentResourceId = null;
        this.currentScope = null;
        this.selectedStep = null;
        this.listScrollTop = 0;
        this.detailScrollTop = 0;
        this.singleScrollTop = 0;
        this.rawDetailsOpen = false;
      }

      const dialog = holder.closest ? (holder.closest('dialog') || holder.closest('.resource-dialog')) : document.getElementById('resource-dialog');
      if (dialog) {
        dialog.dataset.view = 'activity';
        dialog.dataset.scope = scope;
      }

      // Determine selected step
      let targetStep = null;
      if (model.selectedStep != null && calls.some(c => c.step === model.selectedStep)) {
        targetStep = model.selectedStep;
      } else if (this.selectedStep == null || !calls.some(c => c.step === this.selectedStep)) {
        targetStep = calls.length ? calls[calls.length - 1].step : null;
      } else {
        targetStep = this.selectedStep;
      }


      // Preserve scroll & collapse positions
      const listEl = holder.querySelector('.activity-call-list');
      const preserveState = isSameResource && this.currentScope === scope;
      const restoreCallFocus = preserveState && Boolean(listEl?.contains(document.activeElement));
      if (preserveState && listEl) this.listScrollTop = listEl.scrollTop;
      const detailEl = holder.querySelector('.activity-detail-pane');
      if (preserveState && detailEl) this.detailScrollTop = detailEl.scrollTop;
      const singleEl = holder.querySelector('.activity-single-layout');
      if (preserveState && singleEl) this.singleScrollTop = singleEl.scrollTop;
      const rawDetails = holder.querySelector('.activity-raw-details');
      if (preserveState && rawDetails) this.rawDetailsOpen = rawDetails.open;

      this.currentScope = scope;
      this.currentResource = resource;
      this.currentResourceId = resourceId;
      this.currentModel = model;
      this.selectedStep = targetStep;

      const selectedIndex = calls.findIndex(c => c.step === this.selectedStep);
      const selectedCall = selectedIndex >= 0 ? calls[selectedIndex] : null;
      const nextCall = selectedIndex >= 0 && selectedIndex + 1 < calls.length ? calls[selectedIndex + 1] : null;

      holder.className = 'activity-view-holder';
      holder.innerHTML = '';

      const root = document.createElement('div');
      root.className = 'activity-view-container';
      root.dataset.scope = scope;

      // Summary Bar
      const summaryBar = this.buildSummaryBar(model, calls);
      if (summaryBar) root.appendChild(summaryBar);

      // Main Content Area
      if (!calls.length) {
        root.appendChild(this.buildEmptyState());
      } else if (scope === 'single' && calls.length === 1) {
        const targetCall = selectedCall || calls[calls.length - 1];
        root.appendChild(this.buildSingleLayout(holder, model, targetCall, nextCall));
      } else {
        root.appendChild(this.buildGroupLayout(holder, model, calls, selectedCall, nextCall));
      }

      holder.appendChild(root);

      // Restore scroll and state
      const newListEl = holder.querySelector('.activity-call-list');
      if (newListEl && this.listScrollTop > 0) newListEl.scrollTop = this.listScrollTop;
      if (restoreCallFocus) newListEl?.querySelector(`[data-step="${this.selectedStep}"]`)?.focus({ preventScroll: true });
      const newDetailEl = holder.querySelector('.activity-detail-pane');
      if (newDetailEl && this.detailScrollTop > 0) newDetailEl.scrollTop = this.detailScrollTop;
      const newSingleEl = holder.querySelector('.activity-single-layout');
      if (newSingleEl && this.singleScrollTop > 0) newSingleEl.scrollTop = this.singleScrollTop;
      const newRawDetails = holder.querySelector('.activity-raw-details');
      if (newRawDetails && this.rawDetailsOpen) newRawDetails.open = true;

      return this;
    }
    selectCall(holder, model, step) {
      if (this.currentModel !== model) return;
      this.selectedStep = step;
      model.onSelect?.(step);
      if (this.currentModel !== model) return;
      const list = holder.querySelector('.activity-call-list');
      const detail = holder.querySelector('.activity-detail-pane');
      if (list && detail) {
        return this.updateGroupSelection(holder, model, model.calls, step, list, detail);
      }
      return this.render(holder, { ...model, selectedStep: step });
    }


    updateGroupSelection(holder, model, calls, targetStep, listEl, detailPane) {
      this.selectedStep = targetStep;
      this.currentModel = model;

      const selectedIndex = calls.findIndex(c => c.step === targetStep);
      const selectedCall = selectedIndex >= 0 ? calls[selectedIndex] : null;
      const nextCall = selectedIndex >= 0 && selectedIndex + 1 < calls.length ? calls[selectedIndex + 1] : null;

      // Update ARIA, tabindex, and selected class on call list items
      const listHadFocus = Boolean(listEl.contains(document.activeElement));
      const targetStepStr = String(targetStep);
      const prevSelectedItems = listEl.querySelectorAll('.activity-call-item.selected');
      for (const el of prevSelectedItems) {
        if (el.dataset.step !== targetStepStr) {
          el.classList.remove('selected');
          el.setAttribute('aria-selected', 'false');
          el.setAttribute('tabindex', '-1');
        }
      }

      const safeStep = (typeof CSS !== 'undefined' && typeof CSS.escape === 'function')
        ? CSS.escape(targetStepStr)
        : targetStepStr.replace(/["\\]/g, '\\$&');
      const nextSelectedBtn = listEl.querySelector(`[data-step="${safeStep}"]`);
      if (nextSelectedBtn) {
        nextSelectedBtn.classList.add('selected');
        nextSelectedBtn.setAttribute('aria-selected', 'true');
        nextSelectedBtn.setAttribute('tabindex', '0');
        if (listHadFocus) {
          nextSelectedBtn.focus({ preventScroll: true });
        }
      }

      // Update Detail Pane while preserving raw collapse & scroll
      const rawDetails = detailPane.querySelector('.activity-raw-details');
      if (rawDetails) this.rawDetailsOpen = rawDetails.open;
      this.detailScrollTop = detailPane.scrollTop;

      detailPane.innerHTML = '';
      if (selectedCall) {
        detailPane.appendChild(this.buildCallDetail(holder, model, selectedCall, nextCall));
      } else {
        detailPane.appendChild(this.buildEmptyState());
      }

      if (this.detailScrollTop > 0) detailPane.scrollTop = this.detailScrollTop;
      const newRawDetails = detailPane.querySelector('.activity-raw-details');
      if (newRawDetails && this.rawDetailsOpen) newRawDetails.open = true;

      return this;
    }


    buildSummaryBar(model, calls) {
      const resource = model.resource;
      if (!resource) return null;

      const bar = document.createElement('div');
      bar.className = 'activity-summary-bar';

      const rowTop = document.createElement('div');
      rowTop.className = 'activity-summary-row';

      const titleGroup = document.createElement('div');
      titleGroup.className = 'activity-title-group';

      const title = document.createElement('h3');
      title.className = 'activity-title';
      title.textContent = resource.label || resource.name || 'Resource Activity';
      if (resource.kind !== 'category' && (resource.label || resource.name)) title.setAttribute('translate', 'no');
      titleGroup.appendChild(title);

      if (resource.kind) {
        const kindBadge = document.createElement('span');
        kindBadge.className = `activity-kind-badge kind-${escapeHtml(resource.kind)}`;
        kindBadge.textContent = resource.kind.toUpperCase();
        titleGroup.appendChild(kindBadge);
      }
      rowTop.appendChild(titleGroup);

      const stats = document.createElement('div');
      stats.className = 'activity-stats';
      const succeededCount = calls.filter(c => !c.failed && c.endStep != null).length;
      const failedCount = calls.filter(c => c.failed).length;
      const withoutResultCount = calls.filter(c => c.endStep == null).length;
      stats.textContent = calls.length
        ? `${calls.length} calls (${succeededCount} succeeded, ${failedCount} failed${withoutResultCount ? `, ${withoutResultCount} without recorded result` : ''})`
        : '0 calls';
      rowTop.appendChild(stats);

      bar.appendChild(rowTop);

      // Assessment decision
      if (resource.resource_decision) {
        const dec = resource.resource_decision;
        const decNote = document.createElement('div');
        decNote.className = 'activity-decision-note';
        decNote.dataset.decision = dec.decision || '';
        decNote.innerHTML = `<strong>Decision: ${escapeHtml(dec.decision || 'recorded')}</strong> — ${escapeHtml(dec.reason || '')}`;
        bar.appendChild(decNote);
      }

      return bar;
    }

    buildEmptyState() {
      const empty = document.createElement('div');
      empty.className = 'activity-empty-state';

      const icon = document.createElement('div');
      icon.className = 'activity-empty-icon';
      icon.innerHTML = '<svg viewBox="0 0 24 24" width="36" height="36" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M12 8v4 M12 16h.01"/></svg>';
      empty.appendChild(icon);

      const h3 = document.createElement('h3');
      h3.textContent = 'No calls';
      empty.appendChild(h3);

      return empty;
    }

    buildSingleLayout(holder, model, call, nextCall) {
      const container = document.createElement('div');
      container.className = 'activity-single-layout';
      container.setAttribute('role', 'region');
      container.setAttribute('aria-label', 'Call details');

      if (!call) {
        container.appendChild(this.buildEmptyState());
        return container;
      }

      container.appendChild(this.buildCallDetail(holder, model, call, nextCall));
      return container;
    }

    buildGroupLayout(holder, model, calls, selectedCall, nextCall) {
      const layout = document.createElement('div');
      layout.className = 'activity-group-layout';

      // Left Column: Chronological Call List
      const list = document.createElement('div');
      list.className = 'activity-call-list';
      list.setAttribute('role', 'tablist');
      list.setAttribute('aria-label', 'Recorded calls');

      calls.forEach((call, idx) => {
        const isSelected = selectedCall && call.step === selectedCall.step;
        const op = model.describeOperation(call);
        const timing = model.callTiming(call);
        const statusClass = getStatusClass(op.status);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `activity-call-item${isSelected ? ' selected' : ''}${call.failed ? ' failed' : ''}`;
        btn.dataset.step = String(call.step);
        btn.setAttribute('role', 'tab');
        btn.setAttribute('aria-selected', String(isSelected));
        btn.setAttribute('tabindex', isSelected ? '0' : '-1');

        btn.innerHTML = `
          <div class="activity-call-item-top">
            <span class="activity-call-item-num">#${call.ordinal}</span>
            <span class="activity-call-item-tool">${escapeHtml(call.tool)}</span>
            <span class="activity-status-pill status-${statusClass}">${escapeHtml(op.status)}</span>
          </div>
          <div class="activity-call-item-meta">
            <span class="activity-call-item-timing">${escapeHtml(timing.text)}</span>
            <span class="activity-call-item-step">event #${call.step + 1}</span>
          </div>
          ${call.detail ? `<div class="activity-call-item-detail" title="${escapeHtml(call.detail)}">${escapeHtml(call.detail)}</div>` : ''}
        `;

        btn.addEventListener('click', (e) => {
          e.preventDefault();
          this.selectCall(holder, model, call.step);
        });

        // Keyboard arrow navigation
        btn.addEventListener('keydown', (e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            const nextBtn = list.children[idx + 1];
            if (nextBtn) {
              nextBtn.focus();
              nextBtn.click();
            }
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            const prevBtn = list.children[idx - 1];
            if (prevBtn) {
              prevBtn.focus();
              prevBtn.click();
            }
          }
        });

        list.appendChild(btn);
      });

      layout.appendChild(list);

      // Right Column: Call Details
      const detailPane = document.createElement('div');
      detailPane.className = 'activity-detail-pane';
      detailPane.setAttribute('role', 'region');
      detailPane.setAttribute('aria-label', 'Selected call details');

      if (selectedCall) {
        detailPane.appendChild(this.buildCallDetail(holder, model, selectedCall, nextCall));
      } else {
        detailPane.appendChild(this.buildEmptyState());
      }

      layout.appendChild(detailPane);
      return layout;
    }

    buildCallDetail(holder, model, call, nextCall) {
      const detail = document.createElement('div');
      detail.className = 'activity-detail-body';

      const op = model.describeOperation(call);
      const desc = model.describeCall(call);
      const timing = model.callTiming(call);
      const statusClass = getStatusClass(op.status);

      const explicitDests = extractDestinations(call);
      const refFiles = extractReferencedFiles(call, explicitDests);

      // Detail Header
      const header = document.createElement('div');
      header.className = 'activity-detail-header';

      const headerTop = document.createElement('div');
      headerTop.className = 'activity-detail-header-top';

      const title = document.createElement('h4');
      title.className = 'activity-detail-title';
      title.textContent = `Call #${call.ordinal} · ${call.tool}`;
      headerTop.appendChild(title);

      const statusBadge = document.createElement('span');
      statusBadge.className = `activity-status-pill status-${statusClass}`;
      statusBadge.textContent = op.status;
      headerTop.appendChild(statusBadge);

      header.appendChild(headerTop);

      const routeLine = document.createElement('div');
      routeLine.className = 'activity-route-line';
      routeLine.textContent = desc.route || `Agent → ${call.tool}`;
      header.appendChild(routeLine);

      if (op.purpose) {
        const purpose = document.createElement('p');
        purpose.className = 'activity-purpose';
        purpose.textContent = op.purpose;
        header.appendChild(purpose);
      }

      detail.appendChild(header);

      // Section 1: Input (Panel: .activity-panel-input)
      detail.appendChild(this.buildInputSection(model, call, op, refFiles));

      // Section 2: Execution (Panel: .activity-panel-execution)
      detail.appendChild(this.buildExecutionSection(call, op, desc, timing));

      // Section 3: Output (Panel: .activity-panel-output)
      detail.appendChild(this.buildOutputSection(holder, model, call, op, explicitDests, nextCall));

      // Section 4: Expandable Raw Event Data
      detail.appendChild(this.buildRawJsonSection(call, op));

      return detail;
    }

    buildInputSection(model, call, op, refFiles) {
      const section = document.createElement('div');
      section.className = 'activity-section activity-input-section activity-panel-input';

      const secHeader = document.createElement('div');
      secHeader.className = 'activity-section-header';
      secHeader.innerHTML = '<h5>Input</h5><span class="activity-section-badge">Exact Call Parameters</span>';
      section.appendChild(secHeader);

      const rawInput = op.rawInput;
      const hasInput = rawInput != null && rawInput !== '' && rawInput !== '{}';

      if (hasInput) {
        const formatted = renderFormattedInput(model, call.ev, rawInput);
        const pre = document.createElement('pre');
        pre.className = 'activity-code activity-input-code';
        pre.innerHTML = formatted;
        section.appendChild(pre);
      } else {
        const missing = document.createElement('div');
        missing.className = 'activity-not-captured';
        missing.textContent = 'Input arguments not captured';
        section.appendChild(missing);
      }

      // Referenced Files
      if (refFiles.length) {
        const refBlock = document.createElement('div');
        refBlock.className = 'activity-sub-block';
        refBlock.innerHTML = '<span class="activity-sub-title">Referenced Files</span>';

        const list = document.createElement('ul');
        list.className = 'activity-item-list activity-ref-list';

        refFiles.forEach(path => {
          const li = document.createElement('li');
          const span = document.createElement('span');
          span.className = 'activity-path-text';
          span.textContent = path;
          li.appendChild(span);

          const doc = findDocumentNode(model.nodes, path);
          if (doc) {
            const openBtn = document.createElement('button');
            openBtn.type = 'button';
            openBtn.className = 'activity-doc-btn';
            openBtn.dataset.docId = escapeHtml(doc.id);
            openBtn.textContent = `Open ${doc.label.split('/').at(-1)}`;
            openBtn.addEventListener('click', (e) => {
              e.preventDefault();
              const activeModel = this.currentModel || model;
              if (typeof activeModel.onOpenDocument === 'function') {
                activeModel.onOpenDocument(doc.id);
              } else if (typeof window.openResourceDetails === 'function') {
                window.openResourceDetails(doc.id);
              }
            });
            li.appendChild(openBtn);
          }
          list.appendChild(li);
        });

        refBlock.appendChild(list);
        section.appendChild(refBlock);
      }

      return section;
    }

    buildExecutionSection(call, op, desc, timing) {
      const section = document.createElement('div');
      section.className = 'activity-section activity-execution-section activity-panel-execution';

      const secHeader = document.createElement('div');
      secHeader.className = 'activity-section-header';
      secHeader.innerHTML = '<h5>Execution</h5><span class="activity-section-badge">Observed Runtime Status</span>';
      section.appendChild(secHeader);

      const statusClass = getStatusClass(op.status);

      // Metadata Grid
      const grid = document.createElement('div');
      grid.className = 'activity-meta-grid';

      const items = [
        { label: 'Status', html: `<span class="activity-status-pill status-${statusClass}">${escapeHtml(op.status)}</span>` },
        { label: 'Timing', val: timing.text || timing.elapsed || 'Time not recorded' },
        { label: 'Duration', val: Number.isFinite(timing.duration) ? `${timing.duration} ms` : calcDuration(call) || 'Not recorded' },
        { label: 'Tool / Route', val: desc.route || call.tool }
      ];

      items.forEach(it => {
        const box = document.createElement('div');
        box.className = 'activity-meta-box';
        box.innerHTML = `<span class="activity-meta-label">${escapeHtml(it.label)}</span>` +
          (it.html ? it.html : `<span class="activity-meta-val">${escapeHtml(it.val)}</span>`);
        grid.appendChild(box);
      });

      section.appendChild(grid);


      // Captured Execution Logs / Progress Events only (Never duplicate op.result here)
      const ev = call.ev || {};
      const end = call.endEvent || op.end || {};
      const logs = end.logs ?? end.stdout ?? end.stderr ?? ev.logs ?? '';

      const logsBlock = document.createElement('div');
      logsBlock.className = 'activity-sub-block';
      logsBlock.innerHTML = '<span class="activity-sub-title">Captured Execution Logs</span>';

      if (logs !== '' && logs !== null && logs !== undefined) {
        const pre = document.createElement('pre');
        pre.className = 'activity-code activity-logs-code';
        pre.textContent = typeof logs === 'object' ? JSON.stringify(logs, null, 2) : String(logs).replace(/\x1b\[[0-9;]*m/g, '').trim();
        logsBlock.appendChild(pre);
      } else {
        const missing = document.createElement('div');
        missing.className = 'activity-not-captured';
        missing.textContent = 'No intermediate logs captured';
        logsBlock.appendChild(missing);
      }
      section.appendChild(logsBlock);

      return section;
    }

    buildOutputSection(holder, model, call, op, explicitDests, nextCall) {
      const section = document.createElement('div');
      section.className = 'activity-section activity-output-section activity-panel-output';

      const secHeader = document.createElement('div');
      secHeader.className = 'activity-section-header';
      secHeader.innerHTML = '<h5>Output</h5><span class="activity-section-badge">Captured Return &amp; Destinations</span>';
      section.appendChild(secHeader);

      // Data Recipient: Agent (tool response)
      const flowIndicator = document.createElement('div');
      flowIndicator.className = 'activity-flow-indicator';
      flowIndicator.innerHTML = `
        <span class="activity-sub-title">Data Recipient</span>
        <div class="activity-recipient-badge">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M6 6a8.5 8.5 0 0 0 0 12 M18 6a8.5 8.5 0 0 1 0 12"/></svg>
          <strong>Agent (tool response)</strong>
        </div>
      `;
      section.appendChild(flowIndicator);

      // Captured Returned Result (Empty string is valid captured response, not missing)
      const resultBlock = document.createElement('div');
      resultBlock.className = 'activity-sub-block';
      resultBlock.innerHTML = '<span class="activity-sub-title">Captured Result</span>';

      const hasResult = op.result !== undefined && op.result !== null;
      if (hasResult) {
        let resultText = '';
        if (typeof op.result === 'object') {
          resultText = JSON.stringify(op.result, null, 2);
        } else {
          resultText = String(op.result);
        }
        const pre = document.createElement('pre');
        pre.className = 'activity-code activity-result-code';
        pre.textContent = resultText;
        resultBlock.appendChild(pre);
      } else {
        const missing = document.createElement('div');
        missing.className = 'activity-not-captured';
        missing.textContent = 'Result not captured';
        resultBlock.appendChild(missing);
      }
      section.appendChild(resultBlock);

      // Recorded Output Path(s)
      const destBlock = document.createElement('div');
      destBlock.className = 'activity-sub-block';
      destBlock.innerHTML = '<span class="activity-sub-title">Recorded Output Path</span>';

      if (explicitDests.length) {
        const list = document.createElement('ul');
        list.className = 'activity-item-list activity-dest-list';

        explicitDests.forEach(path => {
          const li = document.createElement('li');
          const span = document.createElement('span');
          span.className = 'activity-path-text';
          span.textContent = path;
          li.appendChild(span);

          const doc = findDocumentNode(model.nodes, path);
          if (doc) {
            const openBtn = document.createElement('button');
            openBtn.type = 'button';
            openBtn.className = 'activity-doc-btn';
            openBtn.dataset.docId = escapeHtml(doc.id);
            openBtn.textContent = `Open ${doc.label.split('/').at(-1)}`;
            openBtn.addEventListener('click', (e) => {
              e.preventDefault();
              const activeModel = this.currentModel || model;
              if (typeof activeModel.onOpenDocument === 'function') {
                activeModel.onOpenDocument(doc.id);
              } else if (typeof window.openResourceDetails === 'function') {
                window.openResourceDetails(doc.id);
              }
            });
            li.appendChild(openBtn);
          }
          list.appendChild(li);
        });
        destBlock.appendChild(list);
      } else {
        const none = document.createElement('div');
        none.className = 'activity-not-captured';
        none.textContent = 'No output paths recorded';
        destBlock.appendChild(none);
      }
      section.appendChild(destBlock);

      // Chronological Next Call (Separately and explicitly labelled)
      if (nextCall) {
        const nextTiming = model.callTiming(nextCall);
        const chronoCard = document.createElement('div');
        chronoCard.className = 'activity-chronology-card';

        const chronoHeader = document.createElement('div');
        chronoHeader.className = 'activity-chronology-header';
        chronoHeader.innerHTML = '<span class="activity-chronology-pill">Next call</span><span class="activity-chronology-sub">Recorded in sequence</span>';
        chronoCard.appendChild(chronoHeader);

        const chronoContent = document.createElement('div');
        chronoContent.className = 'activity-chronology-content';

        const chronoName = document.createElement('span');
        chronoName.className = 'activity-chronology-name';
        chronoName.innerHTML = `#${nextCall.ordinal} · <strong>${escapeHtml(nextCall.tool)}</strong> <small>(${escapeHtml(nextTiming.text)})</small>`;
        chronoContent.appendChild(chronoName);

        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'activity-btn-step';
        viewBtn.dataset.step = String(nextCall.step);
        viewBtn.textContent = `View call #${nextCall.ordinal}`;
        viewBtn.addEventListener('click', (e) => {
          e.preventDefault();
          this.selectCall(holder, model, nextCall.step);
        });
        chronoContent.appendChild(viewBtn);
        chronoCard.appendChild(chronoContent);
        section.appendChild(chronoCard);
      }

      return section;
    }

    buildRawJsonSection(call, op) {
      const details = document.createElement('details');
      details.className = 'activity-raw-details';

      const summary = document.createElement('summary');
      summary.textContent = 'Exact event data (raw JSON)';
      details.appendChild(summary);

      const body = document.createElement('div');
      body.className = 'activity-raw-body';

      // Start Event
      const startBox = document.createElement('div');
      startBox.className = 'activity-raw-box';
      startBox.innerHTML = '<span class="activity-raw-label">Start Event</span>';
      const startPre = document.createElement('pre');
      startPre.className = 'activity-code activity-raw-json';
      startPre.textContent = JSON.stringify(call.ev || {}, null, 2);
      startBox.appendChild(startPre);
      body.appendChild(startBox);

      // End Event
      const endBox = document.createElement('div');
      endBox.className = 'activity-raw-box';
      endBox.innerHTML = '<span class="activity-raw-label">Result Event</span>';
      const endEvent = call.endEvent || op.end;
      if (endEvent) {
        const endPre = document.createElement('pre');
        endPre.className = 'activity-code activity-raw-json';
        endPre.textContent = JSON.stringify(endEvent, null, 2);
        endBox.appendChild(endPre);
      } else {
        const missing = document.createElement('div');
        missing.className = 'activity-not-captured';
        missing.textContent = 'Result event not captured';
        endBox.appendChild(missing);
      }
      body.appendChild(endBox);

      details.appendChild(body);
      return details;
    }
  }

  window.ObserverActivityView = ObserverActivityView;
})();
