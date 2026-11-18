/* Local CommonMark/GFM presentation. Captured source text is never changed. */
(() => {
  'use strict';
  const parser = window.markdownit({html: false, linkify: true, breaks: true});
  const escape = parser.utils.escapeHtml, rendered = new WeakMap();
  let sequence = 0;
  parser.renderer.rules.image = (tokens, index) => {
    const token = tokens[index], source = token.attrGet('src') || '', label = escape(token.content || source);
    return /^https?:\/\//i.test(source) && parser.validateLink(source)
      ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${label}</a>` : `<span>${label}</span>`;
  };
  const linkOpen = parser.renderer.rules.link_open || ((tokens, index, options, env, self) => self.renderToken(tokens, index, options));
  parser.renderer.rules.link_open = (tokens, index, options, env, self) => {
    const token = tokens[index], href = token.attrGet('href') || '';
    if (/^https?:\/\//i.test(href)) { token.attrSet('target', '_blank'); token.attrSet('rel', 'noopener noreferrer'); }
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^(https?:|mailto:)/i.test(href)) token.attrSet('href', '#');
    return linkOpen(tokens, index, options, env, self);
  };
  parser.core.ruler.after('inline', 'observer_message_structure', state => {
    const headings = new Map();
    state.tokens.forEach((token, index) => {
      if (token.type === 'heading_open') {
        const slug = (state.tokens[index + 1]?.content || '').toLowerCase().replace(/[^\p{L}\p{N}_-]+/gu, '-').replace(/^-|-$/g, '') || 'heading';
        const count = headings.get(slug) || 0; headings.set(slug, count + 1);
        token.attrSet('id', `${state.env.prefix}-${slug}${count ? '-'+count : ''}`);
        token.attrSet('data-message-anchor', slug);
      }
      if (token.type !== 'inline' || state.tokens[index - 2]?.type !== 'list_item_open') return;
      const first = token.children?.[0], check = first?.type === 'text' && first.content.match(/^\[([ xX])\]\s+/);
      if (!check) return;
      first.content = first.content.slice(check[0].length);
      const checkbox = new state.Token('html_inline', '', 0);
      checkbox.content = `<input type="checkbox" disabled${check[1] !== ' ' ? ' checked' : ''} aria-label="${escape(token.content.slice(check[0].length))}">`;
      token.children.unshift(checkbox);
    });
  });
  function render(host, text) {
    const source = String(text ?? '');
    if (rendered.get(host) === source && host.getAttribute('translate') === 'no') return host;
    host.classList.add('message-markdown'); host.setAttribute('translate', 'no');
    host.innerHTML = parser.render(source, {prefix: 'message-' + (++sequence)});
    rendered.set(host, source);
    host.onclick = event => {
      const link = event.target.closest('a'); if (!link || !host.contains(link)) return;
      const href = link.getAttribute('href') || '';
      if (href.startsWith('#')) {
        event.preventDefault(); event.stopPropagation();
        let anchor; try { anchor = decodeURIComponent(href.slice(1)); } catch { return; }
        const heading = [...host.querySelectorAll('[data-message-anchor]')].find(node => node.id === anchor || node.dataset.messageAnchor === anchor);
        heading?.scrollIntoView({block: 'nearest'});
      } else if (!/^(https?:|mailto:)/i.test(href)) {
        event.preventDefault(); event.stopPropagation();
        host.dispatchEvent(new CustomEvent('observer-message-reference', {bubbles: true, detail: {href}}));
      }
    };
    return host;
  }
  window.ObserverMarkdown = {render};
})();
