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

  const sidePanelOpeners = new WeakMap();
  const focusableSelector =
    "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

  const resetSidePanel = (dialog) => {
    if (dialog.hasAttribute("data-panel-modes")) {
      dialog.querySelectorAll("[data-panel-view]").forEach((view) => {
        view.hidden = view.dataset.panelView !== "view";
      });
      const subtitle = dialog.querySelector("[data-panel-subtitle]");
      if (subtitle) {
        subtitle.textContent =
          subtitle.dataset.defaultText || dialog.dataset.defaultSubtitle || "View record";
      }
    }
    document.querySelectorAll("[data-record-row].is-selected").forEach((row) => {
      row.classList.remove("is-selected");
      row.removeAttribute("aria-selected");
    });
  };

  const restoreSidePanelFocus = (dialog) => {
    const opener = sidePanelOpeners.get(dialog);
    sidePanelOpeners.delete(dialog);
    if (opener?.isConnected && !opener.matches(":disabled, [hidden], [tabindex='-1']")) {
      opener.focus();
    } else {
      document.getElementById("content")?.focus();
    }
  };

  document.querySelectorAll("dialog[data-modal-side-panel]").forEach((dialog) => {
    dialog.addEventListener("close", () => {
      resetSidePanel(dialog);
      restoreSidePanelFocus(dialog);
    });
  });

  document.querySelectorAll("[data-dialog-open]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      if (trigger.hasAttribute("data-record-row") && event.target.closest("a, button, input")) return;
      const dialog = document.getElementById(trigger.dataset.dialogOpen);
      if (!dialog) return;
      if (dialog.hasAttribute("data-modal-side-panel")) {
        if (dialog.open) {
          dialog.focus();
          return;
        }
        if (document.querySelector("dialog[data-modal-side-panel][open]")) return;
        sidePanelOpeners.set(dialog, trigger);
        dialog.showModal();
        const initialFocus =
          dialog.querySelector("[data-initial-focus]") ||
          dialog.querySelector("[data-dialog-close]") ||
          dialog.querySelector(focusableSelector);
        initialFocus?.focus();
        const selectedRow = trigger.closest("[data-record-row]");
        if (selectedRow) {
          selectedRow.classList.add("is-selected");
          selectedRow.setAttribute("aria-selected", "true");
        }
      } else {
        dialog.showModal();
      }
    });
  });
  document.querySelectorAll("[data-dialog-close]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = trigger.closest("dialog");
      dialog?.close();
    });
  });

  document.querySelectorAll("[data-panel-mode]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = trigger.closest("dialog[data-panel-modes]");
      if (!dialog) return;
      const mode = trigger.dataset.panelMode;
      dialog.querySelectorAll("[data-panel-view]").forEach((view) => {
        view.hidden = view.dataset.panelView !== mode;
      });
      const subtitle = dialog.querySelector("[data-panel-subtitle]");
      const entity = dialog.dataset.panelEntity || "record";
      if (subtitle) {
        subtitle.textContent =
          trigger.dataset.modeLabel || `${mode[0].toUpperCase()}${mode.slice(1)} ${entity}`;
      }
      if (mode === "edit") dialog.querySelector("[data-panel-view='edit'] input, [data-panel-view='edit'] select")?.focus();
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
    sidePanelOpeners.set(autoOpenPanel, null);
    autoOpenPanel.showModal();
    autoOpenPanel.querySelectorAll("[data-panel-view]").forEach((view) => {
      view.hidden = view.dataset.panelView !== autoOpenPanel.dataset.autoOpen;
    });
    autoOpenPanel.querySelector("[data-dialog-close]")?.focus();
  }
})();
