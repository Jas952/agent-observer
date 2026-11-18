(() => {
  'use strict';
  const groups = ['commands', 'tools', 'capabilities', 'files', 'memory'];
  const modes = new Set(['list', 'grid', 'free']);
  class ObserverResourceLayouts {
    constructor(api) {
      this.api = api;
      this.meterResize = new ResizeObserver(entries => {
        for (const { target } of entries) this.updateUsageMeters(target);
      });
      for (const key of groups) if (key !== 'files') {
        const body = document.getElementById(`${key}-container`);
        if (body) this.meterResize.observe(body);
      }
    }
    modeFor(key) {
      if (key === 'memory') return 'list';
      const mode = this.api.getConfig()?.block_layouts?.[key];
      return modes.has(mode) ? mode : 'list';
    }
    getColumns(key) {
      const group = document.querySelector(`.group[data-group="${key}"]`);
      return Number(group?.dataset.resourceCols || 1);
    }
    updateUsageMeters(body) {
      if (!body.dataset.resourceLayout) return;
      const style = getComputedStyle(body), cell = parseFloat(style.getPropertyValue('--usage-cell')), gap = parseFloat(style.getPropertyValue('--usage-gap'));
      for (const card of body.querySelectorAll('.card[data-usage-count]')) {
        const count = Number(card.dataset.usageCount);
        let meter = card.querySelector(':scope > .resource-usage-meter');
        if (!count) { meter?.remove(); continue; }
        if (!meter) {
          meter = document.createElement('span'); meter.className = 'resource-usage-meter';
          meter.setAttribute('role', 'img');
          meter.innerHTML = '<span class="resource-usage-fill" aria-hidden="true"></span><span class="resource-usage-label" aria-hidden="true"></span>';
          card.appendChild(meter);
        }
        let left = 10, right = card.clientWidth - 10;
        for (const control of card.querySelectorAll(':scope > .comment-widget,:scope > .execution-details-button,:scope > .capability-pin-button')) {
          if (!control.offsetWidth) continue;
          if (control.offsetLeft < card.clientWidth / 2) left = Math.max(left, control.offsetLeft + control.offsetWidth + 6);
          else right = Math.min(right, control.offsetLeft - 6);
        }
        const width = Math.max(0, right - left), capacity = Math.min(50, Math.max(0, Math.floor((width + gap) / (cell + gap)))), overflow = count > capacity;
        meter.style.left = `${left}px`; meter.style.width = `${width}px`;
        meter.style.setProperty('--usage-units', String(Math.min(count, capacity)));
        meter.classList.toggle('is-overflow', overflow);
        const label = `${count} recorded ${count === 1 ? 'call' : 'calls'}`;
        meter.setAttribute('aria-label', label); meter.title = label;
        meter.querySelector('.resource-usage-label').textContent = overflow ? `${count}+ calls` : '';
      }
    }
    update() {
      let changed = false;
      for (const key of groups) {
        const group = document.querySelector(`.group[data-group="${key}"]`), body = document.getElementById(`${key}-container`);
        if (!group || !body) continue;
        const mode = this.modeFor(key), previous = group.dataset.resourceLayout;
        const columns = mode === 'list' ? 1 : Math.max(1, Math.min(mode === 'grid' ? 5 : Infinity, Math.floor((group.clientWidth - 24 + 8) / 148)));
        const modeChanged = previous !== mode, pinned = group.classList.contains('has-edge-docks');
        const docks = modeChanged && pinned ? [...group.querySelectorAll(':scope > .edge-pins')].map(el => [el, el.hidden]) : [];
        // Measure the new row budget without stale shelves or a previous Free height.
        if (modeChanged && pinned) { group.classList.remove('has-edge-docks'); for (const [el] of docks) el.hidden = true; }
        if (modeChanged || group.dataset.resourceCols !== String(columns)) changed = true;
        group.dataset.resourceLayout = body.dataset.resourceLayout = mode;
        group.dataset.resourceCols = body.dataset.resourceCols = String(columns);
        group.style.setProperty('--resource-grid-cols', String(columns));
        const size = this.api.getSavedSize(key);
        const packedSize = this.api.getPackedSize(key);
        if (Array.isArray(packedSize) && packedSize.length === 2 && packedSize.every(Number.isFinite)) {
          const target = `${Math.max(140, Math.round(packedSize[1]))}px`;
          if (group.style.height !== target) { group.style.height = target; changed = true; }
          group.style.setProperty('--pin-group-height', target);
          group.classList.add('packed-sized');
        } else if (mode === 'free' || key === 'files') {
          group.classList.remove('packed-sized');
          const height = Array.isArray(size) && Number.isFinite(size[1]) ? Math.max(160, size[1]) : 520;
          const target = `${height}px`;
          if (group.style.height !== target) { group.style.height = target; changed = true; }
          if (pinned) group.style.setProperty('--pin-group-height', target);
        } else if (group.style.height) {
          group.classList.remove('packed-sized');
          group.style.height = ''; changed = true;
        } else {
          group.classList.remove('packed-sized');
        }
        if (modeChanged && pinned) {
          const pinHeight = Array.isArray(packedSize) && packedSize.length === 2 ? `${Math.round(packedSize[1])}px` : `${group.offsetHeight}px`;
          group.style.setProperty('--pin-group-height', pinHeight);
          group.classList.add('has-edge-docks');
          for (const [el, hidden] of docks) el.hidden = hidden;
        }
        if (key !== 'files') this.updateUsageMeters(body);
      }
      return changed;
    }
  }
  window.ObserverResourceLayouts = ObserverResourceLayouts;
})();
