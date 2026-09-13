/* Active only inside the new dashboard. No effect on the standalone dashboard. */
(() => {
  if (window.parent === window || new URLSearchParams(location.search).get('previewEmbed') !== '1') return;
  document.documentElement.classList.add('preview-embedded');
  if (new URLSearchParams(location.search).get('autoHeight') === '1') {
    document.documentElement.classList.add('preview-auto-height');
    window.addEventListener('load', () => {
      const content = document.querySelector('.inner');
      if (!content) return;
      let lastHeight = 0;
      const reportHeight = () => {
        // Measure content, not the viewport-sized document, so shorter views shrink too.
        const height = Math.ceil(content.getBoundingClientRect().height);
        if (height > 0 && height !== lastHeight) {
          lastHeight = height;
          window.parent.postMessage({type:'sab-preview-height', height}, location.origin);
        }
      };
      new ResizeObserver(reportHeight).observe(content);
      reportHeight();
    });
  }
  // The outer Sheet owns the viewport; do not open a second drawer in this frame.
  document.addEventListener('click', event => {
    const article = event.target?.closest?.('[data-item]');
    const id = Number(article?.dataset.item);
    if (!article || !Number.isSafeInteger(id) || id <= 0) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    window.parent.postMessage({type:'sab-preview-item', id}, location.origin);
  }, true);
  const applyTheme = value => {
    document.documentElement.classList.toggle('dark', value === 'dark');
    document.documentElement.style.colorScheme = value === 'dark' ? 'dark' : 'light';
  };
  applyTheme(new URLSearchParams(location.search).get('theme'));
  const style = document.createElement('link');
  const assetQuery = document.currentScript?.src ? new URL(document.currentScript.src).search : '';
  style.rel = 'stylesheet'; style.href = '/static/preview-bridge.css' + assetQuery; document.head.append(style);
  let fontLoaded = false;
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== window.parent || event.data?.type !== 'sab-preview-theme') return;
    applyTheme(event.data.theme);
    if (Number.isFinite(event.data.viewportHeight) && event.data.viewportHeight > 0) {
      document.documentElement.style.setProperty('--host-viewport-height', event.data.viewportHeight + 'px');
    }
    if (!fontLoaded && typeof event.data.fontURL === 'string') {
      try {
        const url = new URL(event.data.fontURL);
        if(url.origin === location.origin) {
          const font = new FontFace('SUIT Variable', `url("${url.href}")`, {weight:'100 900',display:'swap'});
          document.fonts.add(font); font.load().catch(()=>{}); fontLoaded = true;
        }
      } catch {}
    }
  });
  const sendRoute = () => window.parent.postMessage({type:'sab-preview-route', hash:location.hash}, location.origin);
  window.addEventListener('hashchange', sendRoute);
  window.addEventListener('load', sendRoute);
})();
