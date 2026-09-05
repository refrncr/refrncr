(() => {
  const toggle = document.querySelector("[data-nav-toggle]");
  const navigation = document.querySelector("[data-navigation]");
  if (toggle && navigation) {
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      navigation.classList.toggle("is-open", !expanded);
    });
  }

  const closeSidePanel = (dialog) => {
    dialog.close();
    if (dialog.hasAttribute("data-record-panel")) {
      dialog.querySelectorAll("[data-record-view]").forEach((view) => {
        view.hidden = view.dataset.recordView !== "view";
      });
      const subtitle = dialog.querySelector("[data-record-subtitle]");
      if (subtitle) subtitle.textContent = dialog.dataset.defaultSubtitle || "View record";
    }
    document.querySelectorAll("[data-record-row].is-selected").forEach((row) => {
      row.classList.remove("is-selected");
      row.removeAttribute("aria-selected");
    });
    if (!document.querySelector("dialog[data-side-panel][open]")) {
      dialog.closest(".app-main")?.classList.remove("has-open-side-panel");
    }
  };

  document.querySelectorAll("[data-dialog-open]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = document.getElementById(trigger.dataset.dialogOpen);
      if (!dialog) return;
      if (dialog.hasAttribute("data-side-panel")) {
        document.querySelectorAll("dialog[data-side-panel][open]").forEach(closeSidePanel);
        dialog.show();
        dialog.closest(".app-main")?.classList.add("has-open-side-panel");
        if (trigger.hasAttribute("data-record-row")) {
          trigger.classList.add("is-selected");
          trigger.setAttribute("aria-selected", "true");
        }
      } else {
        dialog.showModal();
      }
    });
  });
  document.querySelectorAll("[data-dialog-close]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = trigger.closest("dialog");
      if (dialog?.hasAttribute("data-side-panel")) closeSidePanel(dialog);
      else dialog?.close();
    });
  });

  document.querySelectorAll("[data-record-row]").forEach((row) => {
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        row.click();
      }
    });
  });

  document.querySelectorAll("[data-record-mode]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = trigger.closest("dialog[data-record-panel]");
      if (!dialog) return;
      const mode = trigger.dataset.recordMode;
      dialog.querySelectorAll("[data-record-view]").forEach((view) => {
        view.hidden = view.dataset.recordView !== mode;
      });
      const subtitle = dialog.querySelector("[data-record-subtitle]");
      const entity = dialog.dataset.panelEntity || "record";
      if (subtitle) {
        subtitle.textContent =
          trigger.dataset.modeLabel || `${mode[0].toUpperCase()}${mode.slice(1)} ${entity}`;
      }
      if (mode === "edit") dialog.querySelector("[data-record-view='edit'] input, [data-record-view='edit'] select")?.focus();
    });
  });

  document.querySelectorAll("dialog[data-side-panel]").forEach((dialog) => {
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeSidePanel(dialog);
      }
    });
  });

  const exportMenus = [...document.querySelectorAll(".export-menu")];
  document.querySelectorAll(".export-menu-options a").forEach((option) => {
    option.addEventListener("click", () => option.closest(".export-menu")?.removeAttribute("open"));
  });
  document.addEventListener("click", (event) => {
    exportMenus.forEach((menu) => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      exportMenus.forEach((menu) => menu.removeAttribute("open"));
    }
  });

  document.querySelectorAll("[data-row-href]").forEach((row) => {
    const openRow = () => window.location.assign(row.dataset.rowHref);
    row.addEventListener("click", (event) => {
      if (!event.target.closest("a, button, input")) openRow();
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openRow();
      }
    });
  });

  const listSearch = document.querySelector("[data-list-search]");
  if (listSearch) {
    listSearch.addEventListener("input", () => {
      const query = listSearch.value.trim().toLocaleLowerCase();
      document.querySelectorAll("[data-list-row]").forEach((row) => {
        row.hidden = !row.dataset.searchText.toLocaleLowerCase().includes(query);
      });
    });
  }

  const autoOpenPanel = document.querySelector("dialog[data-auto-open]");
  if (autoOpenPanel) {
    autoOpenPanel.show();
    autoOpenPanel.closest(".app-main")?.classList.add("has-open-side-panel");
    autoOpenPanel.querySelectorAll("[data-record-view]").forEach((view) => {
      view.hidden = view.dataset.recordView !== autoOpenPanel.dataset.autoOpen;
    });
    const subtitle = autoOpenPanel.querySelector("[data-record-subtitle]");
    if (subtitle && autoOpenPanel.dataset.autoOpen === "members") {
      subtitle.textContent = "Manage members";
    }
    document.querySelectorAll("[data-record-row]").forEach((row) => {
      if (row.dataset.dialogOpen === autoOpenPanel.id) {
        row.classList.add("is-selected");
        row.setAttribute("aria-selected", "true");
      }
    });
  }
})();
