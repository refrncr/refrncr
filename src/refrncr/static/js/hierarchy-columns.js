(() => {
  const table = document.querySelector('.hierarchy-table');
  const scrollZone = table?.closest('.hierarchy-table-scroll');
  const updateScrollState = () => {
    if (!scrollZone) return;
    const maxScroll = scrollZone.scrollWidth - scrollZone.clientWidth;
    scrollZone.dataset.scrollStart = scrollZone.scrollLeft > 0 ? 'true' : 'false';
    scrollZone.dataset.scrollEnd = scrollZone.scrollLeft < maxScroll - 1 ? 'true' : 'false';
  };
  if (scrollZone) {
    scrollZone.addEventListener('scroll', updateScrollState, { passive: true });
    window.addEventListener('resize', updateScrollState);
    updateScrollState();
  }
  const chooser = document.querySelector('.hierarchy-columns');
  if (!chooser || !table) return;
  const datasetKey = table.closest('[data-hierarchy-dataset]')?.dataset.hierarchyDataset || location.pathname;
  const storageKey = `refrncr.hierarchy-columns.${datasetKey}`;
  const toggles = [...chooser.querySelectorAll('[data-column-toggle]')];
  const count = chooser.querySelector('[data-column-count]');
  const defaultKeys = toggles.filter((toggle) => toggle.checked || toggle.disabled).map((toggle) => toggle.value);
  const validKeys = new Set(toggles.map((toggle) => toggle.value));
  const key = toggles.find((toggle) => toggle.disabled)?.value;
  const cleanKeys = (keys) => {
    const cleaned = [...new Set(keys.filter((item) => validKeys.has(item)))];
    if (key && !cleaned.includes(key)) cleaned.unshift(key);
    return cleaned;
  };
  const save = (keys) => {
    try { window.localStorage.setItem(storageKey, JSON.stringify(keys)); } catch (_) {}
  };
  const apply = (keys) => {
    const visible = new Set(keys);
    table.querySelectorAll('[data-column]').forEach((cell) => {
      cell.hidden = !visible.has(cell.dataset.column);
    });
    toggles.forEach((toggle) => { toggle.checked = visible.has(toggle.value); });
    if (count) count.textContent = String(toggles.filter((toggle) => toggle.checked).length);
  };
  let saved = null;
  try { saved = JSON.parse(window.localStorage.getItem(storageKey)); } catch (_) {}
  const initialKeys = Array.isArray(saved) ? cleanKeys(saved) : defaultKeys;
  apply(initialKeys);
  if (Array.isArray(saved)) save(initialKeys);
  toggles.forEach((toggle) => toggle.addEventListener('change', () => {
    const keys = cleanKeys(toggles.filter((item) => item.checked || item.disabled).map((item) => item.value));
    apply(keys);
    save(keys);
  }));
  chooser.querySelector('[data-columns-reset]')?.addEventListener('click', () => {
    apply(defaultKeys);
    save(defaultKeys);
  });
})();
