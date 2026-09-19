(() => {
  const root = document.querySelector('[data-one-time-secret]');
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = 'true';
  const secret = root.querySelector('#api-key-secret');
  const copy = root.querySelector('[data-copy-secret]');
  const status = root.querySelector('[data-copy-status]');
  copy.hidden = false;
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(secret.value);
      status.textContent = 'Secret copied. Store it securely.';
    } catch {
      secret.focus();
      secret.select();
      status.textContent = 'Copy was unavailable. Copy the selected secret manually.';
    }
  });
  window.addEventListener('pagehide', () => {
    secret.value = '';
    secret.textContent = '';
    copy.disabled = true;
    status.textContent = 'The secret is no longer available. Return to API keys.';
  });
})();
