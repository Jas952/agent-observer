/* Canonical English UI stays in the components; only presentation is localized. */
(() => {
  'use strict';
  const storageKey = 'agent-observer:language';
  const valid = value => value === 'en' || value === 'ru';
  let language = 'en';
  try { const requested = new URL(location.href).searchParams.get('lang'); const saved = localStorage.getItem(storageKey); language = valid(requested) ? requested : valid(saved) ? saved : 'en'; } catch {}
  document.documentElement.lang = language;
  const strings = new Map(), patterns = [], cache = new Map();
  const textState = new WeakMap(), attributeState = new WeakMap();
  const attributes = ['title', 'aria-label', 'placeholder', 'data-tooltip'];
  const protectedSelector = [
    '[translate="no"]:not(html):not(body)', '[data-i18n-ignore]', 'script', 'style', 'pre', 'code', 'kbd', 'samp', 'textarea',
    '[contenteditable="true"]', '.session-chat-label', '.session-project-name', '.connection-session-name',
    '#app-name', '.agent-title',
    '.doc-tree-label', '.doc-graph-name', '.category-name', '.result-file-name', '.result-detail-path',
    '.connection-session-app', '.memory-card-category-label', '.memory-taxonomy-label',
    '.memory-bank header strong', '.memory-store-engine', '.memory-type',
    '.result-preview-name', '.result-full-text', '.result-code-preview', '.terminal-line',
    '.md-preview', '.md-preview-pane', '.history-row-title', '.history-text', '.route-overview',
    '.notes-target', '.saved-note>div', '.activity-line:not(.activity-empty)', '.activity-path-text',
    '.activity-chronology-name', '.tool-command', '.tool-output', '#inspector-payload', '#inspector-reason',
    '.content:not(#prompt-content)', '#resource-subtitle', '.graph-settings-role-path', '.memory-taxonomy-path',
    '.memory-taxonomy-cell-path', '.memory-card-category-path', '.memory-taxonomy-description',
    '.memory-card-category-text', '.memory-taxonomy-item-text', '.block-vault-path',
    '.g-commands .card-title', '.g-tools .card-title', '.g-capabilities .card-title', '.g-memory .card-title',
    '.g-docs .card-title', '.g-files .card-title', '.edge-label', '.edge-step', '.edge-pin-label',
    '.result-dialog code', '.activity-detail-body pre'
  ].join(',');
  const escaped = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function translate(source) {
    if (language === 'en' || typeof source !== 'string' || !source) return source;
    if (cache.has(source)) return cache.get(source);
    const normalized = source.trim().replace(/\s+/g, ' ');
    let value = strings.get(normalized);
    if (value == null) for (const [pattern, replacement] of patterns) { const match = normalized.match(pattern); if (match) { value = replacement.replace(/\{(\d+)\}/g, (_all, index) => match[Number(index) + 1] ?? ''); break; } }
    if (value == null && normalized.includes(' · ')) { const parts = normalized.split(' · '); const translated = parts.map(part => translate(part)); if (parts.some((part, index) => part !== translated[index])) value = translated.join(' · '); }
    const result = value == null ? source : source.slice(0, source.indexOf(source.trimStart())) + value + (source.match(/\s*$/)?.[0] || '');
    if (cache.size > 2048) cache.clear(); cache.set(source, result); return result;
  }
  function register(dictionary) {
    for (const [source, target] of Object.entries(dictionary.strings || {})) {
      if (!source || typeof target !== 'string' || source === target) continue;
      if (/\{\d+\}/.test(source)) {
        const indices = []; let offset = 0, expression = '^';
        for (const match of source.matchAll(/\{(\d+)\}/g)) { expression += escaped(source.slice(offset, match.index)) + '([\\s\\S]*?)'; indices.push(Number(match[1])); offset = match.index + match[0].length; }
        expression += escaped(source.slice(offset)) + '$';
        const replacement = target.replace(/\{(\d+)\}/g, (_all, index) => '{' + indices.indexOf(Number(index)) + '}');
        patterns.push([new RegExp(expression), replacement]);
      } else strings.set(source, target);
    }
    patterns.sort((a, b) => b[0].source.replace(/\([^)]*\)/g, '').length - a[0].source.replace(/\([^)]*\)/g, '').length);
    cache.clear();
  }
  function excluded(element) { return !element || Boolean(element.closest(protectedSelector)); }
  function localizeText(node) {
    if (excluded(node.parentElement)) return;
    const current = node.data;
    let record = textState.get(node);
    if (!record || current !== record.output) record = {source: current, output: current};
    record.output = translate(record.source); textState.set(node, record);
    if (current !== record.output) node.data = record.output;
  }
  function localizeAttributes(element) {
    if (excluded(element)) return;
    let records = attributeState.get(element); if (!records) { records = new Map(); attributeState.set(element, records); }
    for (const name of attributes) {
      if (name === 'title' && element.matches('.card[data-node-id],.graph-settings-name:has(strong[translate="no"])')) continue;
      const current = element.getAttribute(name); if (current == null) { records.delete(name); continue; }
      let record = records.get(name); if (!record || current !== record.output) record = {source: current, output: current};
      if (name === 'title' && element.matches('.session-chat')) {
        const split = record.source.lastIndexOf(' · ');
        record.output = split < 0 ? record.source : record.source.slice(0, split + 3) + translate(record.source.slice(split + 3));
      } else record.output = translate(record.source);
      records.set(name, record);
      if (current !== record.output) element.setAttribute(name, record.output);
    }
  }
  function localize(root) {
    if (!root || !root.isConnected) return;
    if (root.nodeType === Node.TEXT_NODE) { localizeText(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE || excluded(root)) return;
    localizeAttributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {acceptNode(node) { return node.nodeType === Node.ELEMENT_NODE && excluded(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; }});
    while (walker.nextNode()) { const node = walker.currentNode; if (node.nodeType === Node.TEXT_NODE) localizeText(node); else localizeAttributes(node); }
  }
  let toggle = null;
  function setLanguage(next, persist = true) {
    if (!valid(next)) return;
    language = next; cache.clear(); document.documentElement.lang = language;
    if (persist) { try { localStorage.setItem(storageKey, next); const url = new URL(location.href); url.searchParams.set('lang', next); history.replaceState(null, '', url); } catch {} }
    if (toggle) { toggle.textContent = next.toUpperCase(); toggle.title = next === 'ru' ? 'Язык интерфейса: русский. Переключить на английский' : 'Interface language: English. Switch to Russian'; toggle.setAttribute('aria-label', toggle.title); }
    localize(document.body);
    window.dispatchEvent(new CustomEvent('observer-language-changed', {detail: {language: next}}));
  }
  function start() {
    const actions = document.querySelector('.status-navigation');
    if (actions) {
      toggle = document.createElement('button'); toggle.id = 'language-toggle'; toggle.type = 'button';
      toggle.className = 'icon-button language-toggle'; toggle.setAttribute('data-i18n-ignore', '');
      toggle.onclick = () => setLanguage(language === 'en' ? 'ru' : 'en');
      actions.insertBefore(toggle, document.getElementById('server-address'));
    }
    const emptyLog = document.querySelector('#activity-list>.activity-line'); if (emptyLog?.className === 'activity-line' && emptyLog.textContent === 'No events') emptyLog.classList.add('activity-empty');
    setLanguage(language, false);
    const options = {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: [...attributes, 'translate']};
    const observer = new MutationObserver(records => {
      const roots = new Set(), texts = new Set(), controls = new Set();
      for (const record of records) {
        if (record.type === 'characterData') texts.add(record.target);
        else if (record.type === 'attributes') (record.attributeName === 'translate' ? roots : controls).add(record.target);
        else for (const node of record.addedNodes) roots.add(node);
      }
      const covered = node => { for (let parent = node.parentNode; parent; parent = parent.parentNode) if (roots.has(parent)) return true; return false; };
      // Do not enqueue another observer pass for our own translated text/attributes.
      observer.disconnect();
      try {
        for (const root of roots) if (!covered(root)) localize(root);
        for (const node of texts) if (!roots.has(node) && !covered(node) && node.isConnected) localizeText(node);
        for (const element of controls) if (!roots.has(element) && !covered(element) && element.isConnected) localizeAttributes(element);
      } finally { observer.observe(document.body, options); }
    });
    observer.observe(document.body, options);
    window.addEventListener('storage', event => { if (event.key === storageKey && valid(event.newValue)) setLanguage(event.newValue, false); });
    const requested = new URL(location.href).searchParams.get('lang'); if (valid(requested)) try { localStorage.setItem(storageKey, requested); } catch {}
  }
  function sourceText(element) {
    if (!element) return '';
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let result = '', node;
    while ((node = walker.nextNode())) result += textState.get(node)?.source ?? node.data;
    return result;
  }
  function sourceAttribute(element, name) {
    return attributeState.get(element)?.get(name)?.source ?? element?.getAttribute(name) ?? '';
  }
  for (const name of ['confirm', 'alert', 'prompt']) {
    const nativeDialog = window[name].bind(window);
    window[name] = (message, ...args) => nativeDialog(translate(message), ...args);
  }
  window.ObserverI18n = {register, t: translate, setLanguage, localize, sourceText, sourceAttribute, get language() { return language; }};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
