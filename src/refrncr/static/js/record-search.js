/* Search is explicitly submitted; status preserves the last applied term. */
(() => {
  const form = document.querySelector('[data-record-status-form]');
  form?.querySelector('[data-filter-status]')?.addEventListener('change', () => {
    form.action = location.pathname + '#record-status';
    form.requestSubmit(form.querySelector('[data-filter-apply]'));
  });
  if (location.hash === '#record-status') {
    document.getElementById('record-status')?.focus();
  }
  const count = document.getElementById('record-result-count');
  const announcement = document.getElementById('record-result-announcement');
  if (count && announcement) {
    requestAnimationFrame(() => { announcement.textContent = count.textContent; });
  }
})();
