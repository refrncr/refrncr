(() => {
  if (window.refrncrSectionTabs) return;
  const initialized = new WeakSet();
  window.refrncrSectionTabs = (root = document) => {
    root.querySelectorAll('[data-section-tabs]').forEach(nav => {
      if (initialized.has(nav)) return;
      const tabs = [...nav.querySelectorAll('[data-tab-target]')];
      const panels = tabs.map(tab => document.getElementById(tab.dataset.tabTarget));
      if (!tabs.length || panels.some(panel => !panel)) return;
      const enabled = tabs.map((tab, i) => tab.getAttribute('aria-disabled') === 'true' ? -1 : i).filter(i => i >= 0);
      if (!enabled.length) return;
      initialized.add(nav);
      nav.setAttribute('role', 'tablist');
      tabs.forEach((tab, index) => {
        tab.setAttribute('role', 'tab');
        tab.setAttribute('aria-controls', panels[index].id);
        panels[index].setAttribute('role', 'tabpanel');
        panels[index].setAttribute('aria-labelledby', tab.id);
        panels[index].tabIndex = 0;
      });
      const activate = (index, updateUrl = false) => {
        if (!enabled.includes(index)) index = enabled[0];
        tabs.forEach((tab, i) => {
          tab.setAttribute('aria-selected', String(i === index));
          tab.tabIndex = i === index ? 0 : -1;
          panels[i].hidden = i !== index;
        });
        if (updateUrl) {
          const url = new URL(location.href);
          url.searchParams.delete('tab');
          url.hash = panels[index].id;
          history.replaceState(history.state, '', url);
        }
      };
      const fromLocation = () => {
        const requested = location.hash.slice(1) || new URL(location.href).searchParams.get('tab');
        const target = document.getElementById(requested || '');
        const index = panels.findIndex(panel => panel === target || panel.contains(target));
        activate(index < 0 ? 0 : index);
      };
      tabs.forEach((tab, index) => {
        tab.addEventListener('click', event => {
          if (!enabled.includes(index)) { event.preventDefault(); return; }
          if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          activate(index, true);
        });
        tab.addEventListener('keydown', event => {
          if (!enabled.includes(index)) return;
          const position = enabled.indexOf(index);
          let next;
          if (event.key === 'ArrowRight') next = enabled[(position + 1) % enabled.length];
          else if (event.key === 'ArrowLeft') next = enabled[(position + enabled.length - 1) % enabled.length];
          else if (event.key === 'Home') next = enabled[0];
          else if (event.key === 'End') next = enabled[enabled.length - 1];
          else return;
          event.preventDefault();
          activate(next, true);
          tabs[next].focus({preventScroll: true});
          tabs[next].scrollIntoView({block: 'nearest', inline: 'nearest'});
        });
      });
      window.addEventListener('hashchange', fromLocation);
      window.addEventListener('pageshow', fromLocation);
      fromLocation();
    });
  };
  window.refrncrSectionTabs();
})();
