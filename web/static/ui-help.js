(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.UIHelp = api; api.bind(document); }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function render(text) {
    return `<button type="button" class="help-icon" aria-label="도움말" data-help="${escape(text)}">ⓘ</button>`;
  }
  function position(anchor, size, viewport) {
    return {
      left:Math.max(8, Math.min(anchor.left, viewport.width - size.width - 8)),
      top:anchor.bottom + size.height + 8 < viewport.height ? anchor.bottom + 6 : Math.max(8, anchor.top - size.height - 6)
    };
  }
  function bind(doc) {
    const tip = doc.createElement('div');
    tip.id = 'ui-help-tooltip'; tip.className = 'help-tooltip';
    tip.setAttribute('role', 'tooltip'); tip.hidden = true;
    doc.body.append(tip);
    let active = null, pinned = false, openTimer = null, closeTimer = null, lastOpen = 0;
    function cancelTimers() {
      clearTimeout(openTimer); clearTimeout(closeTimer);
      openTimer = closeTimer = null;
    }
    function close() {
      cancelTimers();
      active?.removeAttribute('aria-describedby');
      active = null; pinned = false; tip.hidden = true;
    }
    function show(button) {
      cancelTimers();
      if (active !== button) close();
      if (!button.isConnected) return;
      lastOpen = Date.now();
      active = button; tip.textContent = button.dataset.help;
      button.setAttribute('aria-describedby', tip.id); tip.hidden = false;
      const r = button.getBoundingClientRect();
      const {left, top} = position(r, {width:tip.offsetWidth,height:tip.offsetHeight},
        {width:doc.documentElement.clientWidth,height:doc.documentElement.clientHeight});
      tip.style.left = left + 'px'; tip.style.top = top + 'px';
    }
    doc.addEventListener('mouseover', e => {
      const b = e.target.closest('[data-help]');
      if (tip.contains(e.target)) { clearTimeout(closeTimer); return; }
      if (!b || b.contains(e.relatedTarget)) return;
      cancelTimers();
      if (active || Date.now() - lastOpen < 600) show(b);
      else openTimer = setTimeout(() => show(b), 300);
    });
    doc.addEventListener('mouseout', e => {
      const b = e.target.closest('[data-help]');
      if (b && !b.contains(e.relatedTarget)) { clearTimeout(openTimer); openTimer = null; }
      if (active && !pinned && !active.contains(e.relatedTarget) && !tip.contains(e.relatedTarget) && doc.activeElement !== active) {
        clearTimeout(closeTimer); closeTimer = setTimeout(close, 140);
      }
    });
    doc.addEventListener('focusin', e => { const b = e.target.closest('[data-help]'); if (b) show(b); else close(); });
    doc.addEventListener('focusout', e => { if (e.target === active && !pinned) close(); });
    doc.addEventListener('click', e => {
      const b = e.target.closest('[data-help]');
      if (!b) { if (!tip.contains(e.target)) close(); return; }
      if (active === b && pinned) close();
      else { show(b); pinned = true; }
    });
    doc.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    doc.addEventListener('scroll', () => {
      if (active && doc.activeElement === active) show(active);
      else close();
    }, true);
    window.addEventListener('resize', close);
    window.addEventListener('hashchange', close);
  }
  return {render, bind, position};
});
