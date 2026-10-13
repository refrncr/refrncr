(() => {
  const chooser = document.querySelector('.hierarchy-columns');
  const table = document.querySelector('.hierarchy-table');
  if (!chooser || !table) return;
  const datasetKey = table.closest('[data-hierarchy-dataset]')?.dataset.hierarchyDataset || location.pathname;
  const storageKey = `refrncr.hierarchy-columns.${datasetKey}`;
  const toggles = [...chooser.querySelectorAll('[data-column-toggle]')];
  const count = chooser.querySelector('[data-column-count]');
  const apply = (keys) => {
    const visible = new Set(keys);
    table.querySelectorAll('[data-column]').forEach((cell) => {
      cell.hidden = !visible.has(cell.dataset.column);
    });
    toggles.forEach((toggle) => { toggle.checked = visible.has(toggle.value); });
    if (count) count.textContent = String(visible.size);
  };
  const defaults = () => toggles.filter((toggle) => toggle.checked).map((toggle) => toggle.value);
  let saved = null;
  try { saved = JSON.parse(window.localStorage.getItem(storageKey)); } catch (_) {}
  apply(Array.isArray(saved) ? saved : defaults());
  toggles.forEach((toggle) => toggle.addEventListener('change', () => {
    const keys = toggles.filter((item) => item.checked || item.disabled).map((item) => item.value);
    apply(keys);
    try { window.localStorage.setItem(storageKey, JSON.stringify(keys)); } catch (_) {}
  }));
  chooser.querySelector('[data-columns-reset]')?.addEventListener('click', () => {
    const keys = defaults().slice(0, 6);
    apply(keys);
    try { window.localStorage.setItem(storageKey, JSON.stringify(keys)); } catch (_) {}
  });
})();
