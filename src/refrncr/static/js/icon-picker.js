(() => {
  if (window.refrncrIconPickers) return;
  const initialized = new WeakSet();
  window.refrncrIconPickers = (root = document) => {
    root.querySelectorAll("[data-icon-picker]").forEach((picker) => {
      if (initialized.has(picker)) return;
      initialized.add(picker);
      const search = picker.querySelector("[data-icon-search]");
      const list = picker.querySelector("[data-icon-options]");
      const options = [...list.querySelectorAll('[role="option"]')];
      const status = picker.querySelector("[data-icon-status]");
      let active = -1;
      const visible = () => options.filter((option) => !option.hidden);
      let committed = search.value;
      const close = () => {
        list.hidden = true;
        search.setAttribute("aria-expanded", "false");
        search.removeAttribute("aria-activedescendant");
        options.forEach((option) => option.classList.remove("is-active"));
        active = -1;
      };
      const move = (index) => {
        const items = visible();
        active = items.length ? (index + items.length) % items.length : -1;
        options.forEach((option) => option.classList.remove("is-active"));
        if (active < 0) { search.removeAttribute("aria-activedescendant"); return; }
        const option = items[active];
        option.classList.add("is-active");
        search.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({block: "nearest"});
      };
      const open = (filter = "") => {
        const query = filter.toLowerCase().trim();
        options.forEach((option) => {
          option.hidden = !`${option.dataset.iconName} ${option.dataset.iconValue}`.toLowerCase().includes(query);
        });
        list.hidden = false;
        search.setAttribute("aria-expanded", "true");
        active = -1;
        search.removeAttribute("aria-activedescendant");
        status.textContent = `${visible().length} suggestions available.`;
      };
      const choose = (option) => {
        search.value = committed = option.dataset.iconValue;
        options.forEach((item) => item.setAttribute("aria-selected", String(item === option)));
        status.textContent = `Selected: ${option.dataset.iconName}.`;
        close();
        search.dispatchEvent(new Event("change", {bubbles: true}));
      };
      search.setAttribute("role", "combobox");
      search.setAttribute("aria-autocomplete", "list");
      search.setAttribute("aria-expanded", "false");
      search.setAttribute("aria-controls", list.id);
      picker.querySelector("[data-icon-clear]").hidden = false;
      search.addEventListener("focus", () => { committed = search.value; open(); });
      search.addEventListener("click", () => { if (list.hidden) open(); });
      search.addEventListener("input", () => {
        options.forEach(option => option.setAttribute("aria-selected", String(option.dataset.iconValue === search.value)));
        open(search.value);
      });
      search.addEventListener("keydown", (event) => {
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          if (list.hidden && ["Home", "End"].includes(event.key)) return;
          event.preventDefault();
          if (list.hidden) open();
          move(event.key === "Home" ? 0 : event.key === "End" ? visible().length - 1 : active + (event.key === "ArrowDown" ? 1 : -1));
        } else if (event.key === "Enter" && !list.hidden) {
          event.preventDefault();
          if (active >= 0) choose(visible()[active]);
          else { committed = search.value; close(); }
        } else if (event.key === "Escape" && !list.hidden) {
          event.preventDefault();
          event.stopPropagation();
          search.value = committed;
          close();
        } else if (event.key === "Tab") {
          committed = search.value;
          close();
        }
      });
      list.addEventListener("pointerdown", (event) => event.preventDefault());
      list.addEventListener("click", (event) => {
        const option = event.target.closest('[role="option"]');
        if (option && list.contains(option) && !option.hidden) choose(option);
      });
      picker.querySelector("[data-icon-clear]").addEventListener("click", () => {
        choose(options[0]);
        search.focus();
        close();
      });
      picker.addEventListener("focusout", (event) => {
        if (!picker.contains(event.relatedTarget)) {
          committed = search.value;
          close();
        }
      });
    });
  };
  window.refrncrIconPickers();
})();
