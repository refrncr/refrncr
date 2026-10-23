/* Reorder Dataset Fields by dragging or with arrow keys on the handle.
   Each change saves the complete new order; the server rejects it when the order
   changed elsewhere in the meantime. */
(() => {
  const table = document.querySelector('table[data-field-order]');
  if (!table) return;
  const scope = table.closest('[data-field-order-scope]') || table;
  const body = table.tBodies[0];
  const status = document.getElementById('field-order-status');
  const csrf = table.querySelector('input[name="csrf_token"]')?.value;
  if (!body || !csrf) return;
  const rows = () => [...scope.querySelectorAll('tr[data-field-row]')];
  const fieldId = row => row.querySelector('input[name="field_id"]')?.value;
  const order = () => rows().map(fieldId);
  let saved = order();
  let saving = Promise.resolve();

  const announce = message => { if (status) status.textContent = message; };
  const position = row => `${rows().indexOf(row) + 1} of ${rows().length}`;
  const save = () => {
    const proposed = order();
    if (proposed.join() === saved.join()) return saving;
    const expected = saved;
    saved = proposed;
    const data = new URLSearchParams({csrf_token: csrf});
    expected.forEach(id => data.append('expected_field_id', id));
    proposed.forEach(id => data.append('ordered_field_id', id));
    saving = saving.then(async () => {
      table.classList.add('is-saving');
      try {
        const response = await fetch(table.dataset.orderUrl, {
          method: 'POST', body: data, credentials: 'same-origin', redirect: 'manual',
        });
        if (response.status !== 204) throw new Error(String(response.status));
      } catch (_) {
        announce('The field order could not be saved. The page is reloaded with the latest order.');
        location.reload();
      } finally {
        table.classList.remove('is-saving');
      }
    });
    return saving;
  };

  const move = (row, target) => {
    if (!target || target === row || target.parentElement !== row.parentElement) return false;
    const list = rows();
    if (list.indexOf(target) < list.indexOf(row)) target.before(row);
    else target.after(row);
    return true;
  };

  rows().forEach(row => {
    const handle = row.querySelector('.field-drag-handle');
    if (!handle) return;
    handle.hidden = false;

    handle.addEventListener('keydown', event => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      event.preventDefault();
      const target = event.key === 'ArrowUp' ? row.previousElementSibling : row.nextElementSibling;
      if (!move(row, target)) return;
      handle.focus({preventScroll: true});
      announce(`${row.dataset.fieldName} moved to position ${position(row)}.`);
      save();
    });

    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      // Listen on the document: moving the row in the DOM would release pointer capture.
      row.classList.add('is-dragging');
      const onMove = moveEvent => {
        const over = rows().find(other => {
          const box = other.getBoundingClientRect();
          return moveEvent.clientY >= box.top && moveEvent.clientY < box.bottom;
        });
        if (!over || over === row) return;
        const box = over.getBoundingClientRect();
        const list = rows();
        const down = list.indexOf(over) > list.indexOf(row);
        // Swap once the pointer passes the middle of the neighbouring row.
        if (down ? moveEvent.clientY > box.top + box.height / 2 : moveEvent.clientY < box.top + box.height / 2) {
          move(row, over);
        }
      };
      const onEnd = () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onEnd);
        document.removeEventListener('pointercancel', onEnd);
        row.classList.remove('is-dragging');
        if (order().join() !== saved.join()) {
          announce(`${row.dataset.fieldName} moved to position ${position(row)}.`);
          save();
        }
      };
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onEnd);
      document.addEventListener('pointercancel', onEnd);
    });
  });
  table.setAttribute('data-field-order-ready', '');
})();
