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

  const selectionForms = new WeakSet();
  const initializeSelectionForms = (root) => {
    root.querySelectorAll("form[data-requires-selection]").forEach((form) => {
      if (selectionForms.has(form)) return;
      const select = form.querySelector("select[required]");
      const submit = form.querySelector("button[type='submit']");
      if (!select || !submit) return;
      selectionForms.add(form);
      const update = () => { submit.disabled = !select.value || form.getAttribute("aria-busy") === "true"; };
      select.addEventListener("change", update);
      update();
    });
  };
  initializeSelectionForms(document);

  const datatypeControls = new WeakSet();
  const initializeDatatypeControls = (root) => {
    root.querySelectorAll("[data-field-datatype]").forEach((select) => {
      if (datatypeControls.has(select)) return;
      const component = select.closest("form")?.querySelector("[name='is_business_key_component']");
      if (!component) return;
      datatypeControls.add(select);
      const update = () => {
        const eligible = select.selectedOptions[0]?.dataset.identifierEligible === "true";
        component.disabled = !eligible;
        if (!eligible) component.checked = false;
      };
      select.addEventListener("change", update);
      update();
    });
  };
  initializeDatatypeControls(document);

  const confirmationForms = new WeakSet();
  const initializeConfirmations = (root) => {
    root.querySelectorAll("form[data-exact-confirmation]").forEach((form) => {
      if (confirmationForms.has(form)) return;
      confirmationForms.add(form);
      const input = form.querySelector("[name='confirmation_name']");
      const update = () => {
        const matches = input.value === form.dataset.exactConfirmation;
        input.setCustomValidity(matches ? "" : "Enter the dataset name exactly.");
        [...form.elements].filter((control) => control.type === "submit").forEach((button) => {
          button.disabled = !matches || form.getAttribute("aria-busy") === "true";
        });
      };
      input.addEventListener("input", update);
      update();
    });
  };
  initializeConfirmations(document);

  document.querySelectorAll("[data-mapping-tooltip]").forEach((details) => {
    const trigger = details.querySelector("summary");
    const tooltip = details.querySelector('[role="tooltip"]');
    let pinned = false;
    details.dataset.tooltipReady = "true";
    const position = () => {
      if (!details.open) return;
      const anchor = trigger.getBoundingClientRect();
      const box = tooltip.getBoundingClientRect();
      tooltip.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - box.width - 8))}px`;
      tooltip.style.top = `${Math.max(8, Math.min(anchor.bottom, window.innerHeight - box.height - 8))}px`;
    };
    const show = () => { details.open = true; position(); };
    const hide = () => { details.open = false; pinned = false; };
    details.addEventListener("mouseenter", show);
    details.addEventListener("mouseleave", () => { if (!pinned && !details.contains(document.activeElement)) hide(); });
    trigger.addEventListener("focus", show);
    details.addEventListener("focusout", (event) => { if (!details.contains(event.relatedTarget)) hide(); });
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      pinned = !pinned;
      if (pinned) show(); else hide();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (document.activeElement === tooltip) trigger.focus();
        hide();
      }
    });
    document.addEventListener("pointerdown", (event) => { if (!details.contains(event.target)) hide(); });
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
  });

  document.querySelectorAll("[data-import-mapping]").forEach((form) => {
    const selects = [...form.querySelectorAll("select")];
    const optionLists = selects.map((select) => ({
      select,
      options: [...select.querySelectorAll("option[data-option-group]")].sort((a, b) => Number(a.dataset.optionOrder) - Number(b.dataset.optionOrder)),
      groups: Object.fromEntries([...select.querySelectorAll("optgroup[data-option-group]")].map((group) => [group.dataset.optionGroup, group])),
    }));
    const required = JSON.parse(form.dataset.requiredKeys);
    const button = document.querySelector("[data-mapping-continue]");
    const panel = form.closest(".import-wizard") || form;
    const status = panel.querySelector("[data-mapping-status]");
    const update = () => {
      const values = selects.map((select) => select.value);
      const mapped = values.filter((value) => value && value !== "__ignored__");
      const valid = !values.includes("") && new Set(mapped).size === mapped.length && required.every((key) => mapped.includes(key));
      button.disabled = !valid;
      optionLists.forEach(({select, options, groups}) => {
        const selected = select.value;
        options.forEach((option) => {
          const used = option.value !== selected && mapped.includes(option.value);
          const group = groups[used ? "used" : option.dataset.optionGroup];
          option.textContent = option.dataset.optionLabel;
          group.append(option);
        });
        Object.values(groups).forEach((group) => { group.hidden = !group.children.length; });
        // Moving native options must not change the user's selection or submitted value.
        select.value = selected;
      });
      const summary = panel.querySelector("[data-mapping-summary]");
      if (summary) {
        summary.textContent = `${required.filter((key) => mapped.includes(key)).length} of ${required.length} required fields mapped · ${values.filter((value) => !value).length} choices remaining · ${values.filter((value) => value === "__ignored__").length} columns ignored`;
        const missing = panel.querySelector("[data-mapping-missing]");
        missing.hidden = required.every((key) => mapped.includes(key));
        missing.querySelectorAll("[data-missing-key]").forEach((label) => { label.hidden = mapped.includes(label.dataset.missingKey); });
        selects.forEach((select) => {
          const badge = select.closest(".mapping-target").querySelector("[data-mapping-choice]");
          const duplicate = select.value && select.value !== "__ignored__" && mapped.filter((value) => value === select.value).length > 1;
          const error = duplicate || select.getAttribute("aria-invalid") === "true";
          badge.textContent = error ? "Mapping error" : !select.value ? "Needs a choice" : select.value === "__ignored__" ? "Do not import" : "Mapped";
          badge.className = `badge mapping-choice${error ? " badge-error" : !select.value ? " badge-warning" : select.value !== "__ignored__" ? " badge-success" : ""}`;
        });
      }
      status.textContent = valid ? "All required and identifier fields are mapped. Ready to continue." : "Map every required and identifier field once, and choose a field or Do not import for each column.";
    };
    form.addEventListener("change", update);
    update();
  });
  const setSubmitBusy = (form, busy) => {
    if (busy) form.setAttribute("aria-busy", "true");
    else form.removeAttribute("aria-busy");
    if (!form.hasAttribute("data-busy-message")) return;
    const status = form.querySelector("[data-submit-status]");
    if (status) {
      status.textContent = busy ? form.dataset.busyMessage : "";
      status.hidden = !busy;
    }
  };
  document.querySelectorAll("[data-import-confirm], [data-single-submit]").forEach((form) => {
    let pending = false;
    form.addEventListener("submit", (event) => {
      if (pending) { event.preventDefault(); return; }
      pending = true;
      setSubmitBusy(form, true);
      [...form.elements].filter((element) => element.type === "submit").forEach((button) => { button.disabled = true; });
    });
  });

  const sidePanelOpeners = new WeakMap();
  const panelLoads = new WeakMap();
  const panelParents = new WeakMap();
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
    const host = dialog.querySelector("[data-panel-host]");
    window.refrncrLocalTime?.(host);
    initializeSelectionForms(host);
    initializeDatatypeControls(host);
    initializeConfirmations(host);
    host.querySelectorAll("[data-panel-section-heading], [data-panel-subheading]").forEach((heading) => {
      const marker = heading.hasAttribute("data-panel-section-heading") ? "data-panel-section-heading" : "data-panel-subheading";
      const nested = document.createElement(marker === "data-panel-section-heading" ? "h3" : "h4");
      nested.id = heading.id;
      nested.className = heading.className;
      nested.textContent = heading.textContent;
      nested.setAttribute(marker, "");
      if (heading.hasAttribute("data-initial-focus")) {
        nested.setAttribute("data-initial-focus", "");
        nested.setAttribute("tabindex", "-1");
      }
      heading.replaceWith(nested);
    });
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

  const restorePanelParent = (dialog) => {
    const parent = panelParents.get(dialog);
    if (!parent) return false;
    panelParents.delete(dialog);
    // Invalidate any late confirmation GET/validation response.
    panelLoads.set(dialog, {});
    dialog.querySelector("[data-panel-host]").replaceChildren(...parent.children);
    document.getElementById(dialog.getAttribute("aria-labelledby")).textContent = parent.title;
    parent.trigger.focus();
    return true;
  };

  document.querySelectorAll("dialog[data-modal-side-panel]").forEach((dialog) => {
    dialog.addEventListener("cancel", (event) => {
      if (restorePanelParent(dialog)) event.preventDefault();
    });
    dialog.addEventListener("close", () => {
      // Native close events are queued. A newer opening already owns this host.
      if (dialog.open) return;
      panelLoads.delete(dialog);
      panelParents.delete(dialog);
      resetSidePanel(dialog);
      restoreSidePanelFocus(dialog);
    });
  });

  document.querySelectorAll("[data-panel-host]").forEach((host) => {
    const dialog = host.closest("dialog");
    host.addEventListener("click", (event) => {
      const next = event.target.closest("[data-panel-next]");
      if (next) {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
        event.preventDefault();
        if (pendingForms.has(dialog) || panelParents.has(dialog)) return;
        panelParents.set(dialog, {
          trigger: next, children: [...host.childNodes],
          title: document.getElementById(dialog.getAttribute("aria-labelledby")).textContent,
        });
        loadPanel(dialog, next);
        return;
      }
      if (!event.target.closest("[data-panel-cancel]")) return;
      event.preventDefault();
      if (!restorePanelParent(dialog)) dialog.close();
    });
    host.addEventListener("submit", async (event) => {
      const form = event.target;
      event.preventDefault();
      if (form.hasAttribute("data-exact-confirmation") && !form.reportValidity()) return;
      if (pendingForms.has(dialog)) return;
      pendingForms.add(dialog);
      const load = panelLoads.get(dialog);
      const buttons = [...form.elements].filter((control) => control.matches("button[type='submit']"));
      const data = new FormData(form, event.submitter);
      buttons.forEach((button) => { button.disabled = true; });
      setSubmitBusy(form, true);
      try {
        const response = await fetch(form.action, {
          method: "POST", body: data, credentials: "same-origin",
        });
        if (response.redirected) {
          const refresh = form.getAttribute("data-panel-refresh");
          if (refresh && panelLoads.get(dialog) !== load) return;
          if (refresh && response.ok && response.url === new URL(refresh, form.action).href &&
              await renderPanelResponse(dialog, response, load)) {
            if (panelLoads.get(dialog) !== load) return;
            const status = dialog.querySelector("[data-panel-host]").querySelector("[data-panel-status]");
            if (status) {
              status.hidden = false;
              status.setAttribute("role", "status");
              status.textContent = form.dataset.panelSuccess;
              if (dialog.open) status.focus();
            }
          } else {
            window.location.assign(response.url);
          }
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
        setSubmitBusy(form, false);
        if (form.hasAttribute("data-exact-confirmation")) {
          const input = form.querySelector("[name='confirmation_name']");
          buttons.forEach((button) => { button.disabled = input.value !== form.dataset.exactConfirmation; });
        }
        if (form.hasAttribute("data-requires-selection")) {
          const select = form.querySelector("select[required]");
          buttons.forEach((button) => { button.disabled = !select?.value; });
        }
      }
    });
  });

  document.querySelectorAll("dialog[data-modal-report]").forEach((dialog) => {
    dialog.addEventListener("close", () => restoreSidePanelFocus(dialog));
  });
  document.querySelectorAll("dialog[data-modal-report], dialog[data-modal-side-panel]").forEach((dialog) => {
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const controls = [...dialog.querySelectorAll("button:not([disabled]), a[href], input:not([type='hidden']):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']")].filter((element) => element.getAttribute("tabindex") !== "-1" && visibleControl(element));
      if (!controls.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || !controls.includes(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    });
  });

  const hasRowSelection = () => window.getSelection?.()?.isCollapsed === false;
  const modifiedRowClick = (event) =>
    event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0;

  document.querySelectorAll("[data-dialog-open]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      if (trigger.hasAttribute("data-row-primary") &&
          (event.defaultPrevented || modifiedRowClick(event) ||
           (event.detail !== 0 && hasRowSelection()))) return;
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
        if (dialog.hasAttribute("data-modal-report")) sidePanelOpeners.set(dialog, trigger);
        dialog.showModal();
      }
    });
  });
  document.querySelectorAll("[data-dialog-close]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = trigger.closest("dialog");
      if (dialog && !restorePanelParent(dialog)) dialog.close();
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

  // Keep selectable text unobstructed: only enhance unused parts of a row.
  // The primary anchor/button retains its own native keyboard and pointer behavior.
  const rowInteractiveSelector =
    "a, button, input, select, textarea, label, summary, details, [contenteditable]:not([contenteditable='false']), " +
    "[tabindex], [role], [data-dialog-open]";
  document.addEventListener("click", (event) => {
    if (!modifiedRowClick(event) && event.detail !== 0 && hasRowSelection() &&
        event.target.closest("[data-row-primary]")) event.preventDefault();
  }, true);
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || modifiedRowClick(event) || hasRowSelection()) return;
    const row = event.target.closest("tbody tr[data-row-action]");
    if (!row || row.hidden || row.closest("[inert]")) return;
    // Cell/row semantics (e.g. role='cell') are not nested controls.
    const interactive = event.target.closest(rowInteractiveSelector);
    if (interactive && row.contains(interactive) && interactive !== row &&
        !interactive.matches("td[role='cell'], th[role='rowheader']")) return;
    const primary = row.querySelector("[data-row-primary]");
    if (!primary || primary.matches(":disabled, [aria-disabled='true'], [hidden]")) return;
    primary.click();
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
