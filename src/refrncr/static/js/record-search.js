/* Search is explicitly submitted; status toggles preserve the last applied term. */
(() => {
  // Search, status and clear reload the page. navigation.js restores the scroll position
  // before the first paint; here focus returns to where the keyboard user was.
  const key = 'refrncr.criteria';
  const toggles = document.querySelector('[data-record-status-form]');
  const search = document.getElementById('record-search-form');
  const input = document.getElementById('record-search');
  // From 768px the main column scrolls instead of the window.
  const scroller = () => {
    const main = document.getElementById('content');
    return main && main.scrollHeight > main.clientHeight ? main : document.scrollingElement;
  };
  const remember = focus => {
    try {
      sessionStorage.setItem(key, JSON.stringify({path: location.pathname, y: scroller().scrollTop, focus}));
    } catch (_) {}
  };
  let keyboard = false;
  toggles?.addEventListener('click', event => { keyboard = event.detail === 0; });
  toggles?.addEventListener('submit', () => remember(keyboard ? 'toggle' : null));
  search?.addEventListener('submit', () => remember(document.activeElement === input ? 'search' : null));
  document.querySelector('a[data-search-clear][data-search-applied]')
    ?.addEventListener('click', event => remember(event.detail === 0 ? 'search' : null));
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(key)); } catch (_) {}
  if (saved?.path === location.pathname) {
    const target = saved.focus === 'toggle' ? toggles?.querySelector('[aria-pressed="true"]')
      : saved.focus === 'search' ? input : null;
    target?.focus({preventScroll: true});
  }
  const count = document.getElementById('record-result-count');
  const announcement = document.getElementById('record-result-announcement');
  if (count && announcement) {
    requestAnimationFrame(() => { announcement.textContent = count.textContent; });
  }
})();
