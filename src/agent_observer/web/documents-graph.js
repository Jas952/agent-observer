(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  let nextGraph = 0;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const labelSides = ['below', 'above', 'right', 'left'];

  class ObserverDocumentsGraph {
    constructor(host, options) {
      this.host = host;
      this.options = options || {};
      this.nodes = new Map();
      this.links = [];
      this.topology = null;
      this.saved = options.loadPositions ? (options.loadPositions() || {}) : {};
      this.width = host.clientWidth || 560;
      this.height = host.clientHeight || 480;
      this.cameraInitialized = false;
      this.autoFit = true;
      this.visible = false;
      this.frame = null;
      this.alpha = 0;
      this.selected = null;
      this.labelMetrics = null;
      this.labelRects = [];
      this.drag = null;
      this.suppressClick = null;
      this.camera = { x: 0, y: 0, scale: 1 };
      this.pan = null;
      this.motion = matchMedia('(prefers-reduced-motion: reduce)');
      this.activities = new Map();
      this.explicitPendingIds = new Set();
      this.animatingPendingIds = new Set();
      this.arrivedIds = new Set();
      this.latestFocusedIds = new Set();

      host.classList.add('document-graph');
      host.tabIndex = 0;

      this.scene = document.createElement('div');
      this.scene.className = 'doc-graph-scene';

      this.svg = document.createElementNS(NS, 'svg');
      this.svg.classList.add('doc-graph-links');
      this.svg.setAttribute('aria-hidden', 'true');

      this.layer = document.createElement('div');
      this.layer.className = 'doc-graph-nodes';

      this.empty = document.createElement('div');
      this.empty.className = 'doc-graph-empty';
      this.empty.textContent = 'No documents';

      this.tip = document.createElement('div');
      this.tip.className = 'doc-graph-tooltip';
      this.tip.id = `doc-graph-tooltip-${++nextGraph}`;
      this.tip.setAttribute('role', 'tooltip');
      this.tip.hidden = true;

      this.actions = document.createElement('div');
      this.actions.className = 'doc-graph-actions';
      this.actions.setAttribute('role', 'group');
      this.actions.setAttribute('aria-label', 'Selected document actions');
      this.actions.hidden = true;

      this.actionName = document.createElement('span');
      this.actions.appendChild(this.actionName);

      for (const [label, callback] of [['Open', 'onOpen'], ['Edit Markdown', 'onEdit'], ['Comments', 'onNotes']]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.onclick = () => { if (this.selected && options[callback]) options[callback](this.selected, button); };
        this.actions.appendChild(button);
      }

      this.scene.append(this.svg, this.layer);

      this.controls = document.createElement('div');
      this.controls.className = 'doc-graph-zoom';
      this.controls.setAttribute('role', 'group');
      this.controls.setAttribute('aria-label', 'Document map zoom');
      for (const [label, text, action] of [
        ['Zoom out document map', '−', () => this.zoomAt(this.camera.scale / 1.2)],
        ['Reset document map view', '100%', () => this.resetCamera()],
        ['Zoom in document map', '+', () => this.zoomAt(this.camera.scale * 1.2)]
      ]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('aria-label', label);
        button.title = label;
        button.textContent = text;
        button.onclick = action;
        this.controls.appendChild(button);
      }

      host.append(this.scene, this.empty, this.tip, this.actions, this.controls);

      this.stopOuter = event => event.stopPropagation();
      host.addEventListener('pointerdown', this.stopOuter);

      this.onWheel = event => {
        event.preventDefault();
        event.stopPropagation();
        if (this.drag || this.pan || event.target.closest('.doc-graph-actions,.doc-graph-tooltip,.doc-graph-zoom')) return;
        const point = this.localPoint(event);
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? this.height : 1;
        this.zoomAt(this.camera.scale * Math.exp(-event.deltaY * unit * (event.ctrlKey ? .01 : .0015)), point.x, point.y);
      };

      this.onPanStart = event => {
        if (event.button !== 0 || this.drag || this.pan || event.target.closest('button,.doc-graph-tooltip,.doc-graph-actions,.doc-graph-zoom')) return;
        event.preventDefault();
        host.focus({ preventScroll: true });
        this.stop();
        const point = this.localPoint(event);
        this.pan = { pointer: event.pointerId, x: point.x, y: point.y, cameraX: this.camera.x, cameraY: this.camera.y };
        this.hideTooltip();
        host.classList.add('is-panning');
        host.setPointerCapture(event.pointerId);
      };

      this.onPanMove = event => {
        if (!this.pan || this.pan.pointer !== event.pointerId) return;
        const point = this.localPoint(event);
        if (point.x !== this.pan.x || point.y !== this.pan.y) this.autoFit = false;
        this.camera.x = this.pan.cameraX + point.x - this.pan.x;
        this.camera.y = this.pan.cameraY + point.y - this.pan.y;
        this.options.onCameraInput?.();
        this.updateCamera();
      };

      this.onPanEnd = event => this.finishGesture(event);
      this.onBlur = () => this.finishGesture();

      this.onZoomKey = event => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === '+' || event.key === '=') this.zoomAt(this.camera.scale * 1.2);
        else if (event.key === '-') this.zoomAt(this.camera.scale / 1.2);
        else if (event.key === '0') this.resetCamera();
        else return;
        event.preventDefault();
        event.stopPropagation();
      };

      host.addEventListener('wheel', this.onWheel, { passive: false });
      host.addEventListener('pointerdown', this.onPanStart);
      host.addEventListener('pointermove', this.onPanMove);
      host.addEventListener('pointerup', this.onPanEnd);
      host.addEventListener('pointercancel', this.onPanEnd);
      host.addEventListener('lostpointercapture', this.onPanEnd);
      window.addEventListener('blur', this.onBlur);
      window.addEventListener('pointerup', this.onPanEnd, true);
      window.addEventListener('pointercancel', this.onPanEnd, true);
      host.addEventListener('keydown', this.onZoomKey);

      this.tip.onpointerenter = () => clearTimeout(this.tipTimer);
      this.tip.onpointerleave = () => this.hideTooltip();

      this.onMotion = () => { this.stop(); if (!this.motion.matches) this.wake(); };
      this.onVisibility = () => { if (document.hidden) { this.finishGesture(null, false); this.stop(); } else this.wake(false); };
      this.motion.addEventListener('change', this.onMotion);
      document.addEventListener('visibilitychange', this.onVisibility);

      this.observer = new ResizeObserver(() => this.resize());
      this.observer.observe(host);
      this.updateCamera();
    }

    localPoint(event) {
      const rect = this.host.getBoundingClientRect();
      const w = rect.width || this.width || 1;
      const h = rect.height || this.height || 1;
      return {
        x: (event.clientX - rect.left) * this.width / w,
        y: (event.clientY - rect.top) * this.height / h
      };
    }
    finishGesture(event = null, wake = true) {
      const drag = this.drag, gesture = drag || this.pan;
      if (!gesture || (event && gesture.pointer !== event.pointerId)) return;
      this.drag = this.pan = null;
      this.host.classList.remove('is-panning');
      if (drag?.moved) {
        this.suppressClick = drag.node.id;
        clearTimeout(this.clickTimer);
        this.clickTimer = setTimeout(() => { this.suppressClick = null; }, 0);
        this.updateLabelCollisions();
        this.persist();
      }
      const owner = drag ? drag.node.el : this.host;
      if (owner.hasPointerCapture(gesture.pointer)) owner.releasePointerCapture(gesture.pointer);
      if (wake) this.wake(Boolean(drag?.moved));
    }


    zoomAt(scale, x = this.width / 2, y = (this.height - 48) / 2) {
      if (this.drag || this.pan) return;
      this.autoFit = false;
      const next = clamp(scale, .25, 4), ratio = next / this.camera.scale;
      this.camera.x = x - (x - this.camera.x) * ratio;
      this.camera.y = y - (y - this.camera.y) * ratio;
      this.camera.scale = next;
      this.options.onCameraInput?.();
      this.updateCamera();
    }

    fitCameraToContent() {
      if (!this.width || !this.height || this.nodes.size === 0) return;
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const node of this.nodes.values()) {
        minX = Math.min(minX, node.x);
        maxX = Math.max(maxX, node.x);
        minY = Math.min(minY, node.y);
        maxY = Math.max(maxY, node.y);
      }
      const contentW = Math.max(100, maxX - minX + 80);
      const contentH = Math.max(100, maxY - minY + 80);
      const availW = Math.max(100, this.width - 32);
      const availH = Math.max(100, this.height - 56);
      const fitScale = clamp(Math.min(availW / contentW, availH / contentH), 0.25, 1.0);
      const centerX = (minX + maxX) / 2;
      const centerY = (minY + maxY) / 2;
      this.camera.scale = fitScale;
      this.camera.x = this.width / 2 - centerX * fitScale;
      this.camera.y = (this.height - 48) / 2 - centerY * fitScale;
      this.updateCamera();
      this.cameraInitialized = true;
      this.autoFit = true;
    }

    resetCamera() {
      if (this.drag || this.pan) return;
      this.options.onCameraInput?.();
      this.fitCameraToContent();
    }

    updateCamera() {
      const { x, y, scale } = this.camera;
      this.scene.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
      if (this.controls && this.controls.children.length >= 3) {
        this.controls.children[0].disabled = scale <= .25;
        this.controls.children[1].textContent = `${Math.round(scale * 100)}%`;
        this.controls.children[2].disabled = scale >= 4;
      }
      this.hideTooltip();
      this.updateLabelCollisions();
      if (this.visible && this.options.onLayout) this.options.onLayout();
    }

    ensureVisible(node) {
      const x = this.camera.x + node.x * this.camera.scale;
      const y = this.camera.y + node.y * this.camera.scale;
      const radius = 16 * this.camera.scale;
      if (x >= radius && x <= this.width - radius && y >= radius && y <= this.height - 48 - radius) return;
      this.autoFit = false;
      this.camera.x = this.width / 2 - node.x * this.camera.scale;
      this.camera.y = (this.height - 48) / 2 - node.y * this.camera.scale;
      this.updateCamera();
    }

    setData(documents = [], relationships = []) {
      this.labelMetrics = null;
      const retained = new Set();
      const baseW = Math.max(560, this.width || 800);
      const baseH = Math.max(480, (this.height ? this.height - 48 : 500));
      const padX = 40, padY = 40;
      const usableW = baseW - padX * 2;
      const usableH = baseH - padY * 2;

      documents.forEach((documentNode, index) => {
        const id = documentNode.id;
        retained.add(id);
        let node = this.nodes.get(id);
        if (!node) {
          const saved = this.saved[id];
          const valid = saved && Number.isFinite(saved.x) && Number.isFinite(saved.y);
          let initX, initY, isPinned = false;
          if (valid && saved.pinned) {
            isPinned = true;
            if (saved.space !== 'scene' && saved.x >= 0 && saved.x <= 1 && saved.y >= 0 && saved.y <= 1) {
              initX = padX + saved.x * usableW;
              initY = padY + saved.y * usableH;
            } else {
              initX = saved.x;
              initY = saved.y;
            }
          } else {
            initX = baseW * 0.5;
            initY = baseH * 0.5;
          }
          node = {
            id,
            index,
            x: initX,
            y: initY,
            vx: 0,
            vy: 0,
            targetX: initX,
            targetY: initY,
            degree: 0,
            pinned: isPinned
          };
          node.wrap = document.createElement('div');
          node.wrap.className = 'doc-graph-node';
          node.el = document.createElement('button');
          node.el.type = 'button';
          node.el.id = `document-bubble-${id}`;
          node.el.dataset.nodeId = id;
          node.el.className = 'doc-bubble';
          node.name = document.createElement('span');
          node.name.className = 'doc-graph-name';
          node.wrap.append(node.el, node.name);
          this.layer.appendChild(node.wrap);
          this.nodes.set(id, node);
          this.bindNode(node);
        }
        node.document = documentNode;
        node.index = index;
        node.el.dataset.usageCount = String(documentNode.usage_count ?? 0);
        node.name.textContent = String(documentNode.path || documentNode.label).split('/').at(-1).replace(/\.md$/i, '');
        node.el.setAttribute('aria-label', `Open document: ${documentNode.path || documentNode.label}`);
      });

      for (const [id, node] of this.nodes) {
        if (!retained.has(id)) {
          if (this.drag?.node === node) this.finishGesture(null, false);
          node.wrap.remove();
          this.nodes.delete(id);
        }
      }

      if (this.selected && !this.nodes.has(this.selected)) {
        this.selected = null;
        this.actions.hidden = true;
        this.hideTooltip();
      } else if (this.selected && this.nodes.has(this.selected)) {
        this.updateSelectedInfo(this.nodes.get(this.selected));
      }

      const seen = new Set();
      this.svg.replaceChildren();
      this.links = [];
      for (const relation of relationships || []) {
        if (!this.nodes.has(relation.from) || !this.nodes.has(relation.to) || relation.from === relation.to) continue;
        const key = [relation.from, relation.to].sort().join('\0');
        if (seen.has(key)) continue;
        seen.add(key);
        const line = document.createElementNS(NS, 'line');
        line.dataset.from = relation.from;
        line.dataset.to = relation.to;
        this.svg.appendChild(line);
        this.links.push({ from: this.nodes.get(relation.from), to: this.nodes.get(relation.to), line });
      }

      this.empty.hidden = this.nodes.size > 0;
      const topology = JSON.stringify([[...retained].sort(), [...seen].sort()]);
      if (topology !== this.topology) {
        this.topology = topology;
        this.clusterTargets();
        for (const node of this.nodes.values()) {
          if (!node.pinned && node !== this.drag?.node) {
            node.x = node.targetX;
            node.y = node.targetY;
            node.vx = node.vy = 0;
          }
        }
      }

      if (!this.cameraInitialized && this.width && this.height) {
        this.fitCameraToContent();
      }
      this.updateLabelCollisions();
      this.render();
      this.wake();
    }

    clusterTargets() {
      const neighbors = new Map([...this.nodes.values()].map(node => [node, []]));
      for (const { from, to } of this.links) {
        neighbors.get(from).push(to);
        neighbors.get(to).push(from);
      }
      const compare = (a, b) => neighbors.get(b).length - neighbors.get(a).length || a.id.localeCompare(b.id);
      const remaining = new Set(this.nodes.values()), components = [], isolated = [];
      for (const node of this.nodes.values()) {
        node.degree = neighbors.get(node).length;
        if (!remaining.delete(node)) continue;
        const members = [node];
        for (let i = 0; i < members.length; i++) {
          for (const neighbor of neighbors.get(members[i])) {
            if (remaining.delete(neighbor)) members.push(neighbor);
          }
        }
        members.sort(compare);
        if (members.length === 1) isolated.push(node); else components.push(members);
      }
      components.sort((a, b) => b.length - a.length || compare(a[0], b[0]));

      const baseW = Math.max(560, this.width || 800);
      const baseH = Math.max(480, (this.height ? this.height - 48 : 500));
      const padX = 40, padY = 40;
      const usableW = baseW - padX * 2;
      const usableH = baseH - padY * 2;
      const centerX = padX + usableW / 2;
      const centerY = padY + usableH / 2;

      const place = (node, normX, normY) => {
        node.targetX = centerX + normX * (usableW / 2);
        node.targetY = centerY + normY * (usableH / 2);
      };

      const numComp = components.length;
      if (numComp > 0) {
        components.forEach((members, index) => {
          const angle = index * Math.PI * 2 / numComp - Math.PI / 2;
          const compDist = numComp > 1 ? 0.44 : 0;
          const cx = Math.cos(angle) * compDist;
          const cy = Math.sin(angle) * compDist;
          const maxRadius = numComp > 1 ? Math.min(0.22, 0.45 / Math.sqrt(numComp)) : 0.42;

          members.forEach((node, rank) => {
            const a = rank * 2.399963229728653;
            const r = rank ? maxRadius * Math.sqrt(rank / (members.length - 1)) : 0;
            place(node, cx + Math.cos(a) * r, cy + Math.sin(a) * r);
          });
        });

        isolated.sort((a, b) => a.id.localeCompare(b.id));
        const twoRings = isolated.length > 18;
        isolated.forEach((node, index) => {
          const angle = index * Math.PI * 2 / isolated.length - Math.PI / 2;
          const radius = twoRings ? (index % 2 === 0 ? 0.94 : 0.80) : 0.90;
          place(node, Math.cos(angle) * radius, Math.sin(angle) * radius);
        });
      } else {
        isolated.sort((a, b) => a.id.localeCompare(b.id));
        if (isolated.length <= 16) {
          isolated.forEach((node, index) => {
            const angle = index * Math.PI * 2 / isolated.length - Math.PI / 2;
            place(node, Math.cos(angle) * 0.75, Math.sin(angle) * 0.75);
          });
        } else {
          isolated.forEach((node, index) => {
            const ring = index % 2 === 0 ? 0.88 : (index % 4 === 1 ? 0.60 : 0.35);
            const angle = index * Math.PI * 2 / isolated.length - Math.PI / 2;
            place(node, Math.cos(angle) * ring, Math.sin(angle) * ring);
          });
        }
      }

      // Fast deterministic relaxation pass to ensure minimum separation >= 36px
      const arr = [...this.nodes.values()];
      const minSeparation = 36;
      for (let iter = 0; iter < 25; iter++) {
        for (let i = 0; i < arr.length; i++) {
          for (let j = i + 1; j < arr.length; j++) {
            const a = arr[i], b = arr[j];
            const dx = a.targetX - b.targetX || 0.01;
            const dy = a.targetY - b.targetY || 0.01;
            const dist = Math.hypot(dx, dy);
            if (dist < minSeparation) {
              const delta = (minSeparation - dist) / (dist || 1) * 0.5;
              a.targetX += dx * delta;
              a.targetY += dy * delta;
              b.targetX -= dx * delta;
              b.targetY -= dy * delta;
            }
          }
        }
      }
    }

    updateLabelCollisions() {
      if (!this.visible || !this.nodes.size) return;
      const farZoom = this.camera.scale < .65;
      let changed = !this.labelMetrics || this.labelFarZoom !== farZoom;
      if (!this.labelMetrics) {
        const first = this.nodes.values().next().value;
        const width = first.name.offsetWidth, height = first.name.offsetHeight;
        if (!width || !height) return;
        this.labelMetrics = { width, height, radius: first.el.offsetWidth / 2 };
      }
      this.labelFarZoom = farZoom;
      for (const node of this.nodes.values()) {
        const priority = node.id === this.selected ? 0 :
          node.wrap.classList.contains('is-active') || node.el.classList.contains('sequence-focus') || node.el.dataset.status === 'active' ? 1 : 2;
        if (node.labelX !== node.x || node.labelY !== node.y || node.labelPriority !== priority) changed = true;
        node.labelX = node.x;
        node.labelY = node.y;
        node.labelPriority = priority;
      }
      if (!changed) return;

      const { width, height, radius } = this.labelMetrics;
      // Reserve the widest arrival glow (8px), plus a gap around every bubble.
      const gap = 4, clearance = radius + 8 + gap;
      const placed = this.labelRects;
      placed.length = 0;
      for (let priority = 0; priority < 3; priority++) for (const node of this.nodes.values()) {
        if (node.labelPriority !== priority) continue;
        let side = -1;
        if (!farZoom || priority < 2) {
          const preferred = node.labelSide ?? 0;
          for (let attempt = 0; attempt < 5; attempt++) {
            const candidate = attempt === 0 ? preferred : attempt - 1;
            if (attempt > 0 && candidate === preferred) continue;
            const x = candidate === 2 ? clearance : candidate === 3 ? -clearance - width : -width / 2;
            const y = candidate === 0 ? clearance : candidate === 1 ? -clearance - height : -height / 2;
            const left = node.x + x, top = node.y + y, right = left + width, bottom = top + height;
            let blocked = false;
            for (const other of this.nodes.values()) {
              if (other !== node && left < other.x + clearance && right > other.x - clearance &&
                  top < other.y + clearance && bottom > other.y - clearance) { blocked = true; break; }
            }
            if (!blocked) for (const rect of placed) {
              if (left < rect.right + gap && right > rect.left - gap && top < rect.bottom + gap && bottom > rect.top - gap) { blocked = true; break; }
            }
            if (blocked) continue;
            side = candidate;
            node.labelSide = side;
            if (node.labelOffsetX !== x) { node.name.style.left = `${x}px`; node.labelOffsetX = x; }
            if (node.labelOffsetY !== y) { node.name.style.top = `${y}px`; node.labelOffsetY = y; }
            const rect = node.labelRect || (node.labelRect = {});
            rect.left = left; rect.top = top; rect.right = right; rect.bottom = bottom;
            placed.push(rect);
            break;
          }
        }
        const position = side < 0 ? 'hidden' : labelSides[side];
        if (node.wrap.dataset.labelPos !== position) node.wrap.dataset.labelPos = position;
      }
    }

    bindNode(node) {
      node.el.addEventListener('pointerenter', () => { this.choose(node); this.showTooltip(node); });
      node.el.addEventListener('pointerleave', () => { this.tipTimer = setTimeout(() => this.hideTooltip(), 120); });
      node.el.addEventListener('focus', () => { if (!this.drag && !this.pan) this.ensureVisible(node); this.choose(node); if (!this.drag) this.showTooltip(node); });
      node.el.addEventListener('blur', () => this.hideTooltip());
      node.el.addEventListener('click', event => {
        if (this.suppressClick === node.id && event.detail !== 0) { this.suppressClick = null; return; }
        this.hideTooltip();
        this.choose(node);
        if (this.options.onOpen) this.options.onOpen(node.id);
      });
      node.el.addEventListener('pointerdown', event => {
        if (event.button !== 0 || this.drag || this.pan) return;
        event.preventDefault();
        event.stopPropagation();
        this.stop();
        this.choose(node);
        this.hideTooltip();
        this.drag = { node, pointer: event.pointerId, clientX: event.clientX, clientY: event.clientY, x: node.x, y: node.y, moved: false };
        node.el.focus({ preventScroll: true });
        node.el.setPointerCapture(event.pointerId);
      });
      node.el.addEventListener('pointermove', event => {
        const drag = this.drag;
        if (!drag || drag.node !== node || drag.pointer !== event.pointerId) return;
        const rect = this.host.getBoundingClientRect();
        const dx = event.clientX - drag.clientX, dy = event.clientY - drag.clientY;
        if (Math.hypot(dx, dy) > 3) drag.moved = true;
        if (!drag.moved) return;
        this.autoFit = false;
        const w = rect.width || this.width || 1;
        const h = rect.height || this.height || 1;
        node.x = drag.x + (dx * this.width / w) / this.camera.scale;
        node.y = drag.y + (dy * this.height / h) / this.camera.scale;
        node.pinned = true;
        node.vx = node.vy = 0;
        this.render();
      });
      node.el.addEventListener('pointerup', this.onPanEnd);
      node.el.addEventListener('pointercancel', this.onPanEnd);
      node.el.addEventListener('lostpointercapture', this.onPanEnd);
      node.el.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.stopPropagation(); this.hideTooltip(); return; }
        const movement = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
        if (!movement) return;
        event.preventDefault();
        event.stopPropagation();
        if (this.drag || this.pan) return;
        this.stop();
        this.autoFit = false;
        const distance = (event.shiftKey ? 20 : 5) / this.camera.scale;
        node.x += movement[0] * distance;
        node.y += movement[1] * distance;
        node.pinned = true;
        this.render();
        this.updateLabelCollisions();
        this.persist();
        this.showTooltip(node);
      });
    }

    updateSelectedInfo(node = this.nodes.get(this.selected)) {
      if (!node || !node.document) return;
      const count = Number(node.document.usage_count) || 0;
      this.actionName.dataset.usageCount = String(count);
      this.actions.dataset.usageCount = String(count);
      this.actionName.textContent = count > 0 ? `${node.name.textContent} (${count})` : node.name.textContent;
      this.actionName.title = `${node.document.path || node.document.label}${count > 0 ? ` · ${count} recorded call${count === 1 ? '' : 's'}` : ''}`;
    }

    choose(node) {
      this.selected = node.id;
      this.updateSelectedInfo(node);
      this.actions.hidden = false;
      for (const other of this.nodes.values()) {
        const isSel = other.id === node.id;
        other.wrap.classList.toggle('is-selected', isSel);
        other.el.classList.toggle('is-selected', isSel);
      }
      this.updateLabelCollisions();
    }

    showTooltip(node) {
      if (!this.visible || this.drag) return;
      clearTimeout(this.tipTimer);
      this.tip.replaceChildren();
      const path = document.createElement('strong');
      path.textContent = node.document.path || node.document.label;
      this.tip.append(path);
      if (node.document.description && node.document.description !== 'Project document') {
        const description = document.createElement('span');
        description.textContent = node.document.description;
        this.tip.append(description);
      }
      const count = Number(node.document?.usage_count) || 0;
      this.tip.dataset.usageCount = String(count);
      if (count > 0) {
        const usage = document.createElement('span');
        usage.textContent = `${count} recorded call${count === 1 ? '' : 's'}`;
        this.tip.append(usage);
      } else if (node.document && typeof node.document.usage_count === 'number') {
        const usage = document.createElement('span');
        usage.textContent = '0 recorded calls';
        this.tip.append(usage);
      }
      this.tip.hidden = false;
      for (const other of this.nodes.values()) other.el.removeAttribute('aria-describedby');
      node.el.setAttribute('aria-describedby', this.tip.id);
      const x = this.camera.x + node.x * this.camera.scale;
      const y = this.camera.y + node.y * this.camera.scale;
      this.tip.style.left = `${clamp(x + 18, 8, Math.max(8, this.width - this.tip.offsetWidth - 8))}px`;
      this.tip.style.top = `${clamp(y - this.tip.offsetHeight - 18, 8, Math.max(8, this.height - this.tip.offsetHeight - 42))}px`;
    }

    hideTooltip() {
      clearTimeout(this.tipTimer);
      this.tip.hidden = true;
      for (const node of this.nodes.values()) node.el.removeAttribute('aria-describedby');
    }

    updateState(documents, focusedIds) {
      this.latestFocusedIds = focusedIds || new Set();
      for (const documentNode of documents || []) {
        const node = this.nodes.get(documentNode.id);
        if (!node) continue;
        node.document = documentNode;
        node.el.dataset.usageCount = String(documentNode.usage_count ?? 0);
        this.applyNodeStatus(node);
        if (this.selected === node.id) {
          this.updateSelectedInfo(node);
        }
      }
      if (this.tip && !this.tip.hidden) {
        const activeTipNode = [...this.nodes.values()].find(n => n.el.getAttribute('aria-describedby') === this.tip.id);
        if (activeTipNode) this.showTooltip(activeTipNode);
      }
      this.updateLabelCollisions();
    }

    isPending(id) {
      return this.explicitPendingIds.has(id) || this.animatingPendingIds.has(id);
    }

    getNodeArrivalActivity(id) {
      let latest = null;
      for (const record of this.activities.values()) {
        if (record.id === id && record.direction !== 'out' && record.deliveredTransferKey) {
          latest = record;
        }
      }
      return latest;
    }

    applyNodeStatus(node) {
      if (!node || !node.document) return;
      const pending = this.isPending(node.id);
      if (pending) {
        node.el.classList.add('arrival-pending');
        node.el.dataset.status = 'idle';
        node.el.classList.remove('sequence-focus');
        node.el.style.removeProperty('border-color');
        node.el.style.removeProperty('--doc-call-color');
        node.el.style.removeProperty('--arrival-color');
        return;
      }
      node.el.classList.remove('arrival-pending');
      node.el.dataset.status = node.document.status || 'idle';
      node.el.classList.toggle('sequence-focus', Boolean(this.latestFocusedIds?.has(node.id)));
      const arrival = this.getNodeArrivalActivity(node.id);
      if (arrival && arrival.color) {
        node.el.style.borderColor = arrival.color;
        node.el.style.setProperty('--doc-call-color', arrival.color);
        node.el.style.setProperty('--arrival-color', arrival.color);
      }
    }

    isArrivalReady(id) {
      if (this.isPending(id)) return false;
      if (this.arrivedIds.has(id)) return true;
      if (this.explicitPendingIds.size > 0 || this.animatingPendingIds.size > 0) return false;
      return true;
    }

    setArrivalState(arrivedIds, pendingIds) {
      this.arrivedIds = new Set(arrivedIds || []);
      this.explicitPendingIds = new Set(pendingIds || []);
      for (const node of this.nodes.values()) {
        this.applyNodeStatus(node);
      }
      this.updateLabelCollisions();
    }

    syncActivity(items = []) {
      const list = Array.isArray(items) ? items : [];
      for (const item of list) {
        if (!item || !item.id) continue;
        const node = this.nodes.get(item.id);
        if (!node) continue;

        const direction = item.direction === 'out' ? 'out' : 'in';
        const key = item.key || `${item.id}:${direction}`;
        const transferKey = item.transferKey ? String(item.transferKey) : null;
        const color = item.color || 'var(--cyan)';

        let record = this.activities.get(key);
        if (!record) {
          record = {
            id: item.id,
            key,
            direction,
            transferKey,
            color,
            deliveredTransferKey: null,
            glowTimer: null
          };
          this.activities.set(key, record);
        } else {
          record.color = color;
          record.direction = direction;
          if (transferKey && transferKey !== record.transferKey) {
            record.transferKey = transferKey;
            record.deliveredTransferKey = null;
          }
        }

        const isNewTransfer = Boolean(transferKey && record.deliveredTransferKey !== transferKey);

        if (direction === 'in') {
          if (isNewTransfer) {
            record.deliveredTransferKey = transferKey;
            node.el.style.borderColor = color;
            node.el.style.setProperty('--doc-call-color', color);
            node.el.style.setProperty('--arrival-color', color);
            node.wrap.classList.add('is-active');

            this.animatingPendingIds.delete(item.id);
            this.explicitPendingIds.delete(item.id);
            this.arrivedIds.add(item.id);
            this.applyNodeStatus(node);

            if (item.animate && !this.motion.matches) {
              node.el.classList.remove('doc-bubble-arrived');
              void node.el.offsetWidth;
              node.el.classList.add('doc-bubble-arrived');
              clearTimeout(record.glowTimer);
              record.glowTimer = setTimeout(() => {
                node.el.classList.remove('doc-bubble-arrived');
                record.glowTimer = null;
              }, 800);
            }

            if (typeof item.onArrive === 'function') {
              item.onArrive();
            }
          }
        } else {
          // Outgoing transfer
          if (isNewTransfer) {
            record.deliveredTransferKey = transferKey;
            if (typeof item.onArrive === 'function') {
              item.onArrive();
            }
          }
        }
      }
      this.updateLabelCollisions();
    }

    clearActivity() {
      for (const record of this.activities.values()) {
        clearTimeout(record.glowTimer);
      }
      this.activities.clear();
      this.animatingPendingIds.clear();
      this.explicitPendingIds.clear();
      this.arrivedIds.clear();
      for (const node of this.nodes.values()) {
        node.el.style.removeProperty('border-color');
        node.el.style.removeProperty('--doc-call-color');
        node.el.style.removeProperty('--arrival-color');
        node.el.classList.remove('doc-bubble-arrived');
        node.wrap.classList.remove('is-active');
        this.applyNodeStatus(node);
      }
      this.updateLabelCollisions();
    }

    getElement(id) {
      return this.visible ? this.nodes.get(id)?.el || null : null;
    }

    getBubbleCenter(id) {
      const node = this.nodes.get(id);
      if (!node) return null;
      return {
        hostX: node.x * this.camera.scale + this.camera.x,
        hostY: node.y * this.camera.scale + this.camera.y,
        sceneX: node.x,
        sceneY: node.y
      };
    }

    toScenePoint(hostX, hostY) {
      return {
        x: (hostX - this.camera.x) / this.camera.scale,
        y: (hostY - this.camera.y) / this.camera.scale
      };
    }

    toHostPoint(sceneX, sceneY) {
      return {
        x: sceneX * this.camera.scale + this.camera.x,
        y: sceneY * this.camera.scale + this.camera.y
      };
    }

    resize() {
      const width = this.host.clientWidth, height = this.host.clientHeight;
      if (!width || !height || (width === this.width && height === this.height)) return;
      this.finishGesture(null, false);
      const prevWidth = this.width, prevHeight = this.height;
      this.width = width;
      this.height = height;
      this.labelMetrics = null;

      if (!this.cameraInitialized || this.autoFit) {
        this.fitCameraToContent();
      } else if (prevWidth && prevHeight) {
        this.camera.x += (width - prevWidth) / 2;
        this.camera.y += (height - prevHeight) / 2;
      }
      this.updateCamera();
      this.render();
      if (this.visible) this.wake(false);
    }

    setVisible(visible) {
      this.visible = visible;
      if (!visible) { this.finishGesture(null, false); this.stop(); this.hideTooltip(); return; }
      this.resize();
      this.render();
      this.wake(false);
    }

    wake(reset = true) {
      if (reset) this.alpha = 1;
      if (!this.visible || document.hidden || this.motion.matches || this.drag || this.pan || this.frame !== null || this.alpha < .006) return;
      this.frame = requestAnimationFrame(() => this.tick());
    }

    stop() {
      if (this.frame !== null) cancelAnimationFrame(this.frame);
      this.frame = null;
    }

    tick() {
      this.frame = null;
      if (!this.visible || this.motion.matches || document.hidden || this.drag || this.pan) return;

      const grid = new Map(), cellSize = 80;
      for (const node of this.nodes.values()) {
        const strength = node.degree ? .025 : .065;
        node.fx = (node.targetX - node.x) * strength;
        node.fy = (node.targetY - node.y) * strength;
        const key = `${Math.floor(node.x / cellSize)}:${Math.floor(node.y / cellSize)}`;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(node);
      }

      for (const node of this.nodes.values()) {
        const gx = Math.floor(node.x / cellSize), gy = Math.floor(node.y / cellSize);
        for (let x = gx - 1; x <= gx + 1; x++) for (let y = gy - 1; y <= gy + 1; y++) {
          for (const other of grid.get(`${x}:${y}`) || []) {
            if (other.index <= node.index) continue;
            const dx = node.x - other.x || .1, dy = node.y - other.y || .1;
            const distance = Math.hypot(dx, dy);
            if (distance < 36) {
              const force = (36 - distance) * 0.35;
              node.fx += (dx / distance) * force;
              node.fy += (dy / distance) * force;
              other.fx -= (dx / distance) * force;
              other.fy -= (dy / distance) * force;
            } else {
              const force = Math.min(2.5, 1200 / (distance * distance + 25));
              node.fx += (dx / distance) * force;
              node.fy += (dy / distance) * force;
              other.fx -= (dx / distance) * force;
              other.fy -= (dy / distance) * force;
            }
          }
        }
      }

      for (const link of this.links) {
        const dx = link.to.x - link.from.x, dy = link.to.y - link.from.y;
        const distance = Math.hypot(dx, dy) || 1;
        const rest = Math.hypot(link.to.targetX - link.from.targetX, link.to.targetY - link.from.targetY);
        const force = (distance - rest) * .025;
        link.from.fx += (dx / distance) * force;
        link.from.fy += (dy / distance) * force;
        link.to.fx -= (dx / distance) * force;
        link.to.fy -= (dy / distance) * force;
      }

      for (const node of this.nodes.values()) {
        if (!node.pinned) {
          node.vx = (node.vx + node.fx * this.alpha) * .78;
          node.vy = (node.vy + node.fy * this.alpha) * .78;
          node.x += clamp(node.vx, -6, 6);
          node.y += clamp(node.vy, -6, 6);
        }
      }

      this.alpha *= .955;
      this.render();
      if (this.alpha >= .006) {
        this.wake(false);
      } else {
        if (this.autoFit) this.fitCameraToContent();
        this.updateLabelCollisions();
        this.persist();
      }
    }

    render() {
      for (const node of this.nodes.values()) {
        node.wrap.style.transform = `translate(${node.x}px, ${node.y}px)`;
      }
      for (const link of this.links) {
        link.line.setAttribute('x1', link.from.x);
        link.line.setAttribute('y1', link.from.y);
        link.line.setAttribute('x2', link.to.x);
        link.line.setAttribute('y2', link.to.y);
      }
      this.updateLabelCollisions();
      if (this.visible && this.options.onLayout) this.options.onLayout();
    }

    persist() {
      const positions = {};
      for (const node of this.nodes.values()) {
        if (node.pinned) {
          positions[node.id] = {
            x: node.x,
            y: node.y,
            pinned: true,
            space: 'scene'
          };
        }
      }
      this.saved = positions;
      if (this.options.savePositions) this.options.savePositions(positions);
    }

    destroy() {
      this.finishGesture(null, false);
      this.stop();
      this.clearActivity();
      clearTimeout(this.tipTimer);
      clearTimeout(this.clickTimer);
      this.observer.disconnect();
      this.motion.removeEventListener('change', this.onMotion);
      document.removeEventListener('visibilitychange', this.onVisibility);
      this.host.removeEventListener('pointerdown', this.stopOuter);
      this.host.removeEventListener('wheel', this.onWheel);
      this.host.removeEventListener('pointerdown', this.onPanStart);
      this.host.removeEventListener('pointermove', this.onPanMove);
      this.host.removeEventListener('pointerup', this.onPanEnd);
      this.host.removeEventListener('pointercancel', this.onPanEnd);
      this.host.removeEventListener('lostpointercapture', this.onPanEnd);
      window.removeEventListener('blur', this.onBlur);
      window.removeEventListener('pointerup', this.onPanEnd, true);
      window.removeEventListener('pointercancel', this.onPanEnd, true);
      this.host.removeEventListener('keydown', this.onZoomKey);
      this.host.replaceChildren();
    }
  }

  window.ObserverDocumentsGraph = ObserverDocumentsGraph;
})();
