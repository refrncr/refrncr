(() => {
  if (document.refrncrInitialized) return;
  document.refrncrInitialized = true;
  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const input = document.getElementById(button.getAttribute("aria-controls"));
    if (!input || input.type !== "password") return;
    button.hidden = false;
    button.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "Hide password" : "Show password";
    });
  });
  document.querySelector("[data-error-focus]")?.focus();

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
  const panelLoads = new WeakMap();
  const pendingForms = new WeakSet();
  const visibleControl = (element) =>
    !element.closest("[hidden]") && element.getClientRects().length > 0;
  const focusSidePanel = (dialog) => {
    const candidates = dialog.querySelectorAll(
      "[data-error-focus], [aria-invalid='true'], [data-initial-focus], input:not([type='hidden']):not([disabled]), select:not([disabled]), textarea:not([disabled])"
    );
    // Error summaries take precedence over invalid fields and ordinary inputs.
    const initialFocus =
      [...candidates].find((element) => element.hasAttribute("data-error-focus") && visibleControl(element)) ||
      [...candidates].find((element) => element.getAttribute("aria-invalid") === "true" && visibleControl(element)) ||
      [...candidates].find(visibleControl) ||
      dialog.querySelector("[data-dialog-close]");
    initialFocus?.focus();
  };

  const renderPanelResponse = async (dialog, response, load) => {
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    if (panelLoads.get(dialog) !== load) return true;
    const content = page.querySelector("[data-side-panel-content]");
    if (!content) return false;
    const title = document.getElementById(dialog.getAttribute("aria-labelledby"));
    title.textContent = content.dataset.panelTitle;
    dialog.querySelector("[data-panel-host]").replaceChildren(document.importNode(content, true));
    if (dialog.open) focusSidePanel(dialog);
    return true;
  };

  const loadPanel = async (dialog, trigger) => {
    const load = {};
    panelLoads.set(dialog, load);
    document.getElementById(dialog.getAttribute("aria-labelledby")).textContent = "Loading…";
    dialog.querySelector("[data-panel-host]").replaceChildren();
    try {
      const response = await fetch(trigger.href, {credentials: "same-origin"});
      if (panelLoads.get(dialog) !== load) return;
      if (!response.ok || response.redirected || !await renderPanelResponse(dialog, response, load)) {
        window.location.assign(response.redirected ? response.url : trigger.href);
      }
    } catch {
      if (panelLoads.get(dialog) === load) window.location.assign(trigger.href);
    }
  };

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
      panelLoads.delete(dialog);
      resetSidePanel(dialog);
      restoreSidePanelFocus(dialog);
    });
  });

  document.querySelectorAll("[data-panel-host]").forEach((host) => {
    const dialog = host.closest("dialog");
    host.addEventListener("click", (event) => {
      if (!event.target.closest("[data-panel-cancel]")) return;
      event.preventDefault();
      dialog.close();
    });
    host.addEventListener("submit", async (event) => {
      const form = event.target;
      event.preventDefault();
      if (pendingForms.has(dialog)) return;
      pendingForms.add(dialog);
      const load = panelLoads.get(dialog);
      const buttons = [...form.elements].filter((control) => control.matches("button[type='submit']"));
      const data = new FormData(form, event.submitter);
      buttons.forEach((button) => { button.disabled = true; });
      form.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(form.action, {
          method: "POST", body: data, credentials: "same-origin",
        });
        if (response.redirected) {
          window.location.assign(response.url);
        } else if (panelLoads.get(dialog) !== load) {
          return;
        } else if (![400, 409].includes(response.status) || !await renderPanelResponse(dialog, response, load)) {
          const error = document.createElement("p");
          error.setAttribute("role", "alert");
          error.tabIndex = -1;
          error.textContent = "The form could not be saved. Reload the page to check your access and try again.";
          form.prepend(error);
          if (dialog.open) error.focus();
        }
      } catch {
        if (panelLoads.get(dialog) !== load) return;
        const error = document.createElement("p");
        error.setAttribute("role", "alert");
        error.tabIndex = -1;
        error.textContent = "The result could not be confirmed. Check the dataset before trying again.";
        form.prepend(error);
        if (dialog.open) error.focus();
      } finally {
        pendingForms.delete(dialog);
        buttons.forEach((button) => { button.disabled = false; });
        form.removeAttribute("aria-busy");
      }
    });
  });

  document.querySelectorAll("[data-dialog-open]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      if (trigger.hasAttribute("data-record-row") && event.target.closest("a, button, input")) return;
      const dialog = document.getElementById(trigger.dataset.dialogOpen);
      if (!dialog || typeof dialog.showModal !== "function") return;
      if (trigger.hasAttribute("data-panel-url")) {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
        event.preventDefault();
      }
      if (dialog.hasAttribute("data-modal-side-panel")) {
        if (dialog.open) {
          dialog.focus();
          return;
        }
        if (document.querySelector("dialog[data-modal-side-panel][open]")) return;
        sidePanelOpeners.set(dialog, trigger);
        dialog.showModal();
        if (trigger.hasAttribute("data-panel-url")) loadPanel(dialog, trigger);
        focusSidePanel(dialog);
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
    focusSidePanel(autoOpenPanel);
  }
})();
