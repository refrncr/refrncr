(() => {
  if (document.refrncrInitialized) return;
  document.refrncrInitialized = true;
  let keyboardInput = false;
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

  let openFieldMetadata = null;
  let fieldMetadataCloseTimer = null;
  const positionFieldMetadata = (trigger, panel) => {
    const rect = trigger.getBoundingClientRect();
    panel.style.width = "";
    const {width, height} = panel.getBoundingClientRect();
    const left = Math.max(8, Math.min(rect.left - 8, window.innerWidth - width - 8));
    const below = rect.bottom + 6;
    const top = below + height > window.innerHeight - 8 ? Math.max(8, rect.top - height - 6) : below;
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  };
  const closeFieldMetadata = () => {
    if (!openFieldMetadata) return;
    window.clearTimeout(fieldMetadataCloseTimer);
    const {trigger, panel} = openFieldMetadata;
    panel.hidePopover();
    trigger.setAttribute("aria-expanded", "false");
    openFieldMetadata = null;
  };
  const openTransientFieldMetadata = (trigger, panel) => {
    if (openFieldMetadata?.trigger === trigger) return;
    closeFieldMetadata();
    panel.showPopover();
    positionFieldMetadata(trigger, panel);
    trigger.setAttribute("aria-expanded", "true");
    openFieldMetadata = {trigger, panel, pinned: false};
  };
  const scheduleFieldMetadataClose = () => {
    window.clearTimeout(fieldMetadataCloseTimer);
    fieldMetadataCloseTimer = window.setTimeout(() => {
      if (openFieldMetadata && !openFieldMetadata.pinned) closeFieldMetadata();
    }, 120);
  };
  document.querySelectorAll("[data-field-metadata-trigger]").forEach((trigger) => {
    const panel = document.getElementById(trigger.getAttribute("aria-controls"));
    if (!panel || typeof panel.showPopover !== "function") return;
    trigger.addEventListener("click", () => {
      if (openFieldMetadata?.trigger === trigger) {
        if (openFieldMetadata.pinned) closeFieldMetadata();
        else openFieldMetadata.pinned = true;
        return;
      }
      openTransientFieldMetadata(trigger, panel);
      openFieldMetadata.pinned = true;
    });
    trigger.addEventListener("pointerenter", () => {
      window.clearTimeout(fieldMetadataCloseTimer);
      openTransientFieldMetadata(trigger, panel);
    });
    trigger.addEventListener("pointerleave", scheduleFieldMetadataClose);
    trigger.addEventListener("focus", () => openTransientFieldMetadata(trigger, panel));
    trigger.addEventListener("blur", scheduleFieldMetadataClose);
    panel.addEventListener("pointerenter", () => window.clearTimeout(fieldMetadataCloseTimer));
    panel.addEventListener("pointerleave", scheduleFieldMetadataClose);
    panel.addEventListener("focusin", () => window.clearTimeout(fieldMetadataCloseTimer));
    panel.addEventListener("focusout", scheduleFieldMetadataClose);
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { closeFieldMetadata(); trigger.focus(); }
    });
    window.addEventListener("resize", () => { if (openFieldMetadata?.panel === panel) positionFieldMetadata(trigger, panel); });
    window.addEventListener("scroll", () => { if (openFieldMetadata?.panel === panel) positionFieldMetadata(trigger, panel); }, true);
  });
  document.addEventListener("pointerdown", (event) => {
    keyboardInput = false;
    document.activeElement?.removeAttribute?.("data-pointer-restored-focus");
    if (openFieldMetadata && !openFieldMetadata.panel.contains(event.target) && event.target !== openFieldMetadata.trigger) closeFieldMetadata();
  });
  document.addEventListener("keydown", (event) => {
    keyboardInput = true;
    const active = document.activeElement;
    if (active?.matches?.(".interactive-row .row-action[data-pointer-restored-focus]")) {
      active.removeAttribute("data-pointer-restored-focus");
      active.setAttribute("data-keyboard-restored-focus", "");
    } else {
      active?.removeAttribute?.("data-pointer-restored-focus");
    }
    if (event.key === "Escape" && openFieldMetadata) {
      const {trigger} = openFieldMetadata;
      closeFieldMetadata();
      trigger.focus();
    }
  });

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

  const inlinePending = new WeakSet();
  const confirmedForms = new WeakSet();
  const confirmationDialog = document.querySelector("[data-confirm-dialog]");
  let pendingConfirmation = null;
  const confirmationClosures = [];
  if (confirmationDialog) {
    const cancel = confirmationDialog.querySelector("[data-confirm-cancel]");
    const accept = confirmationDialog.querySelector("[data-confirm-accept]");
    const closeConfirmation = (confirmed) => {
      if (pendingConfirmation) {
        pendingConfirmation.confirmed = confirmed;
        confirmationClosures.push(pendingConfirmation);
        pendingConfirmation = null;
      }
      confirmationDialog.close(confirmed ? "confirm" : "cancel");
    };
    confirmationDialog.addEventListener("click", (event) => {
      if (event.target === confirmationDialog) closeConfirmation(false);
    });
    cancel.addEventListener("click", () => closeConfirmation(false));
    accept.addEventListener("click", () => closeConfirmation(true));
    confirmationDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeConfirmation(false);
    });
    confirmationDialog.addEventListener("close", () => {
      const pending = confirmationClosures.shift();
      if (!pending) return;
      if (!pending.confirmed) {
        pending.submitter?.focus();
        return;
      }
      confirmedForms.add(pending.form);
      if (pending.submitter) pending.form.requestSubmit(pending.submitter);
      else pending.form.requestSubmit();
    });
  }
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form");
    if (!form) return;
    if (form.hasAttribute("data-confirm") && !confirmedForms.has(form)) {
      if (!confirmationDialog || typeof confirmationDialog.showModal !== "function") return;
      event.preventDefault();
      pendingConfirmation = {form, submitter: event.submitter};
      confirmationDialog.querySelector("[data-confirm-title]").textContent = form.dataset.confirmTitle || "Confirm action";
      confirmationDialog.querySelector("[data-confirm-message]").textContent = form.dataset.confirm;
      confirmationDialog.querySelector("[data-confirm-accept]").textContent = form.dataset.confirmAction || "Confirm";
      confirmationDialog.showModal();
      confirmationDialog.querySelector("[data-confirm-cancel]").focus();
      return;
    }
    if (confirmedForms.has(form)) confirmedForms.delete(form);
    if (!form.matches("form[data-inline-refresh]")) return;
    event.preventDefault();
    const host = form.closest("[data-export-access]");
    if (inlinePending.has(host)) return;
    inlinePending.add(host);
    form.setAttribute("aria-busy", "true");
    const submitted = new FormData(form);
    const submit = event.submitter;
    if (submit) submit.disabled = true;
    try {
      const response = await fetch(form.action, {method: "POST", body: submitted, credentials: "same-origin"});
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = page.querySelector("[data-export-access]");
      if (!replacement) {
        window.location.assign(response.redirected ? response.url : form.dataset.inlineRefresh);
        return;
      }
      const content = document.importNode(replacement, true);
      host.replaceWith(content);
      window.refrncrSelects?.(content);
      initializeSelectionForms(content);
      const error = content.querySelector("[data-error-focus]");
      if (error) error.focus();
      else if (form.hasAttribute("data-panel-no-success-feedback")) {
        const trigger = content.querySelector(".select-trigger");
        window.setTimeout(() => trigger?.focus(), 100);
      }
      else {
        const status = content.querySelector("[data-panel-status]");
        status.hidden = false;
        status.setAttribute("role", "status");
        status.textContent = form.dataset.panelSuccess;
        if (form.dataset.panelSuccessFocus === "selection") {
          const trigger = content.querySelector(".select-trigger");
          window.setTimeout(() => trigger?.focus(), 100);
        }
        else status.focus();
      }
    } catch {
      // Do not replay an uncertain POST. A safe GET shows the authoritative state.
      window.location.assign(form.dataset.inlineRefresh);
    } finally {
      inlinePending.delete(host);
      form.removeAttribute("aria-busy");
      if (submit) submit.disabled = false;
    }
  });

  const identifierRequirementSyncs = new WeakMap();
  const initializeIdentifierRequirements = (root) => {
    if (typeof root?.querySelectorAll !== "function") return;
    root.querySelectorAll("[data-identifier-toggle]").forEach((identifier) => {
      const form = identifier.form;
      const scope = identifier.closest("[data-mapping-field-panel]") || form;
      const required = scope?.querySelector("[data-identifier-required]");
      if (!form || !required || identifierRequirementSyncs.has(identifier)) return;
      const sync = (announce = false) => {
        const active = identifier.checked;
        const locked = required.hasAttribute("data-role-locked");
        const inactivePanel = Boolean(identifier.closest("[data-mapping-field-panel][hidden]"));
        if (active) required.checked = true;
        required.disabled = locked || inactivePanel;
        if (active && !locked) required.setAttribute("aria-disabled", "true");
        else required.removeAttribute("aria-disabled");
        const identifierHelp = scope.querySelector("[data-identifier-required-help]");
        const defaultHelp = scope.querySelector("[data-required-help]");
        const status = scope.querySelector("[data-identifier-status]");
        if (identifierHelp) identifierHelp.hidden = !active;
        if (defaultHelp) defaultHelp.hidden = active;
        const help = active ? identifierHelp : defaultHelp;
        if (help) required.setAttribute("aria-describedby", help.id);
        if (status && announce) {
          status.textContent = active
            ? "Required is checked and locked because this field is part of the Record identifier."
            : "Required can now be changed.";
        }
        let mirror = scope.querySelector("[data-required-mirror]");
        if (active && !locked && !mirror) {
          mirror = document.createElement("input");
          mirror.type = "hidden";
          mirror.name = required.name;
          mirror.value = "on";
          mirror.dataset.requiredMirror = "";
          required.closest("label")?.after(mirror);
        } else if ((!active || locked) && mirror) {
          mirror.remove();
        }
      };
      identifierRequirementSyncs.set(identifier, sync);
      identifier.addEventListener("change", () => sync(true));
      required.addEventListener("click", (event) => {
        if (required.getAttribute("aria-disabled") === "true") event.preventDefault();
      });
      required.addEventListener("change", () => {
        if (identifier.checked) required.checked = true;
      });
      sync();
    });
  };
  initializeIdentifierRequirements(document);
  const syncIdentifierRequirements = (root) => {
    if (typeof root?.querySelectorAll !== "function") return;
    initializeIdentifierRequirements(root);
    root.querySelectorAll("[data-identifier-toggle]").forEach((identifier) => {
      identifierRequirementSyncs.get(identifier)?.();
    });
  };

  const datatypeControls = new WeakSet();
  const initializeDatatypeControls = (root) => {
    root.querySelectorAll("[data-field-datatype]").forEach((select) => {
      if (datatypeControls.has(select)) return;
      const scope = select.closest("[data-mapping-field-panel]") || select.closest("form");
      const component = scope?.querySelector("[name='is_business_key_component']");
      if (!component) return;
      datatypeControls.add(select);
      const update = () => {
        const eligible = select.selectedOptions[0]?.dataset.identifierEligible === "true";
        component.disabled = component.hasAttribute("data-role-locked") || !eligible;
        if (!eligible) component.checked = false;
        syncIdentifierRequirements(scope);
      };
      select.addEventListener("change", update);
      update();
    });
  };
  initializeDatatypeControls(document);

  const mappingFieldForms = new WeakSet();
  const initializeMappingFieldForms = (root) => {
    root.querySelectorAll("[data-mapping-field-form]").forEach((form) => {
      if (mappingFieldForms.has(form)) return;
      mappingFieldForms.add(form);
      const sync = () => {
        const role = form.querySelector("input[name='mapping_field_role']:checked")?.value;
        form.querySelectorAll("[data-mapping-field-panel]").forEach((panel) => {
          const active = panel.dataset.mappingFieldPanel === role;
          panel.hidden = !active;
          panel.querySelectorAll("input, select, textarea").forEach((control) => {
            control.disabled = !active || control.hasAttribute("data-role-locked");
          });
        });
        syncIdentifierRequirements(form);
      };
      form.addEventListener("change", (event) => {
        if (event.target.name === "mapping_field_role") sync();
      });
      sync();
    });
  };
  initializeMappingFieldForms(document);

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

  // Icon labels remain available on hover/focus and can be dismissed without moving focus.
  document.querySelectorAll(".button-row-icon[data-tooltip]").forEach((trigger) => {
    const reset = () => {
      if (!trigger.matches(":hover, :focus-visible")) trigger.removeAttribute("data-tooltip-dismissed");
    };
    trigger.addEventListener("pointerleave", reset);
    trigger.addEventListener("blur", reset);
  });
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
    const selects = [...form.querySelectorAll("select[name^='mapping_']")];
    const options = selects.map((select) => [...select.querySelectorAll("option[data-option-group]")]);
    const required = JSON.parse(form.dataset.requiredKeys);
    const button = document.querySelector("[data-mapping-continue]");
    const panel = form.closest(".import-wizard") || form;
    const status = panel.querySelector("[data-mapping-status]");
    const reason = panel.querySelector("[data-mapping-reason]");
    const fieldLabels = new Map(options.flat().map((option) => [option.value, option.dataset.optionLabel]));
    const plural = (count, singular, many) => `${count} ${count === 1 ? singular : many}`;
    const statusText = ({unselected, missingLabels, ignored, mappedCount, notInFile}) => {
      const parts = [];
      if (unselected) parts.push(`${plural(unselected, "column", "columns")} still ${unselected === 1 ? "needs" : "need"} a choice`);
      if (missingLabels.length) {
        const labels = missingLabels.slice(0, 3).join(", ");
        const more = missingLabels.length > 3 ? ` and ${missingLabels.length - 3} more` : "";
        parts.push(`${plural(missingLabels.length, "field", "fields")} not mapped: ${labels}${more}`);
      }
      if (parts.length) return {ready: false, text: parts.join(" · "), unselected, missingLabels};
      const omitted = notInFile ? ` · ${plural(notInFile, "field", "fields")} not in file` : "";
      return {ready: true, text: `Ready to continue · ${plural(mappedCount, "field", "fields")} mapped, ${plural(ignored, "column", "columns")} not imported${omitted}`};
    };
    const renderStatus = (result) => {
      const line = status.querySelector("[data-mapping-status-text]");
      line.replaceChildren();
      if (result.unselected) {
        const jump = document.createElement("button");
        jump.type = "button";
        jump.className = "mapping-jump";
        jump.dataset.mappingJump = "";
        jump.textContent = `${result.unselected} ${result.unselected === 1 ? "column" : "columns"}`;
        line.append(jump, document.createTextNode(` still ${result.unselected === 1 ? "needs" : "need"} a choice`));
      }
      if (result.missingLabels?.length) {
        if (result.unselected) line.append(document.createTextNode(" · "));
        line.append(document.createTextNode(`${plural(result.missingLabels.length, "field", "fields")} not mapped: `));
        result.missingLabels.slice(0, 3).forEach((label, index) => {
          const code = document.createElement("code");
          code.className = "mapping-field-name";
          code.textContent = label;
          line.append(code);
          if (index < Math.min(result.missingLabels.length, 3) - 1) line.append(document.createTextNode(", "));
        });
        if (result.missingLabels.length > 3) line.append(document.createTextNode(` and ${result.missingLabels.length - 3} more`));
      }
      if (result.ready) line.textContent = result.text;
    };
    status.addEventListener("click", (event) => {
      if (!event.target.closest("[data-mapping-jump]")) return;
      const firstOpen = selects.find((select) => !select.value);
      if (!firstOpen) return;
      firstOpen.focus({preventScroll: false});
      firstOpen.scrollIntoView({block: "center"});
    });
    const update = () => {
      const values = selects.map((select) => select.value);
      const mapped = values.filter((value) => value && value !== "__ignored__");
      const valid = !values.includes("") && new Set(mapped).size === mapped.length && required.every((key) => mapped.includes(key));
      button.disabled = !valid;
      options.forEach((items, index) => {
        items.forEach((option) => {
          option.hidden = option.value !== selects[index].value && mapped.includes(option.value);
        });
      });
      selects.forEach((select) => {
        const badge = select.closest(".mapping-target").querySelector("[data-mapping-choice]");
        const duplicate = select.value && select.value !== "__ignored__" && mapped.filter((value) => value === select.value).length > 1;
        const error = duplicate || select.getAttribute("aria-invalid") === "true";
        badge.textContent = error ? "Mapping error" : !select.value ? "Needs a choice" : select.value === "__ignored__" ? "Do not import" : "Mapped";
        badge.className = `badge mapping-choice${error ? " badge-error" : !select.value ? " badge-warning" : select.value !== "__ignored__" ? " badge-success" : ""}`;
      });
      const missingLabels = required.filter((key) => !mapped.includes(key)).map((key) => fieldLabels.get(key) || key);
      const unselected = values.filter((value) => !value).length;
      const result = statusText({
        unselected,
        missingLabels,
        ignored: values.filter((value) => value === "__ignored__").length,
        mappedCount: new Set(mapped).size,
        notInFile: form.dataset.importMode === "new_version"
          ? [...fieldLabels.keys()].filter((key) => !mapped.includes(key)).length : 0,
      });
      status.dataset.state = result.ready ? "ready" : "pending";
      renderStatus(result);
      if (reason) reason.textContent = form.dataset.mappingReasonText;
      if (reason) reason.hidden = missingLabels.length === 0;
    };
    form.addEventListener("change", update);
    update();
  });
  document.querySelectorAll("form[data-auto-filter]").forEach((form) => {
    const search = form.querySelector("input[type='search']");
    let timer;
    search?.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => form.requestSubmit(), 250);
    });
    form.querySelectorAll("select, input[type='checkbox']").forEach((control) => {
      control.addEventListener("change", () => form.requestSubmit());
    });
  });
  document.querySelectorAll("form[data-import-confirm]").forEach((form) => {
    const acknowledgement = form.querySelector("[name='acknowledge_deletions']");
    const submit = form.querySelector("button[type='submit']");
    if (!acknowledgement || !submit) return;
    const update = () => { submit.disabled = !acknowledgement.checked; };
    acknowledgement.addEventListener("change", update);
    update();
  });
  const initializeSourceFormats = (root = document) => root.querySelectorAll("[data-source-formats-url]").forEach((form) => {
    if (form.dataset.sourceFormatsReady) { form.refrncrBindFormats?.(); return; }
    form.dataset.sourceFormatsReady = "true";
    const textChoices = new Map();
    const panel = form.closest(".import-wizard") || document;
    const advanced = panel.querySelector("[data-advanced-formats]");
    if (!advanced) return;
    let generation = 0;
    let pending = null;
    const details = () => [...form.querySelectorAll("[data-format-details]")];
    const needsAttention = (item) => ["undecided", "failing"].includes(item.closest("[data-source-formats]")?.dataset?.state);
    const focusControl = (item) => (item.querySelector('input:not([type="hidden"]):not([hidden]):not([disabled]), select:not([disabled])') || item.querySelector("[data-effective-format]"))?.focus();
    const sync = () => {
      const items = details();
      items.forEach((item) => {
        const button = item.closest("[data-source-formats]")?.querySelector("[data-format-toggle]");
        if (button) {
          button.hidden = false;
          button.setAttribute("aria-expanded", String(item.open));
          button.textContent = item.open ? "Done" : "Change";
        }
      });
      const expanded = items.length > 0 && items.every((item) => item.open);
      advanced.setAttribute("aria-expanded", String(expanded));
      advanced.setAttribute("aria-controls", items.map((item) => item.id).join(" "));
      advanced.hidden = items.length === 0;
      advanced.textContent = expanded ? "Hide all formats" : "Show all formats";
    };
    const focusError = () => form.querySelector("[data-format-error]")?.focus();
    const updateComponents = (row) => {
      const source = row?.querySelector("[data-source-type]");
      if (!source) return;
      const index = row.dataset.sourceFormats;
      const parts = row.querySelector("[data-text-formats]");
      if (!parts) return;
      const controls = [...parts.querySelectorAll("select")];
      if (source.value === "text") {
        const saved = textChoices.get(index);
        controls.forEach((control) => { if (control.disabled && saved?.[control.name]) control.value = saved[control.name]; });
        textChoices.set(index, Object.fromEntries(controls.map((control) => [control.name, control.value])));
      }
      parts.hidden = source.value !== "text";
      controls.forEach((control) => { control.disabled = parts.hidden; });
      const zone = row.querySelector("[data-source-timezone]");
      if (zone) {
        zone.hidden = !["text", "native"].includes(source.value);
        if (zone.hidden) zone.querySelectorAll("input, select").forEach((control) => { control.value = ""; });
      }
      const summary = row.querySelector("[data-effective-format]");
      if (!summary) return;
      summary.textContent = source.value === "text"
        ? controls.map((control) => control.selectedOptions[0]?.textContent || "").join(" + ")
        : source.selectedOptions[0]?.textContent || "Choose source type";
    };
    const bind = () => {
      details().forEach((item) => {
        updateComponents(item.closest("[data-source-formats]"));
        if (item.dataset.sourceDetailsReady) return;
        item.dataset.sourceDetailsReady = "true";
        item.addEventListener("toggle", sync);
        const row = item.closest("[data-source-formats]");
        const button = row.querySelector("[data-format-toggle]");
        const toggle = (event) => {
          event.preventDefault();
          item.open = !item.open;
          if (item.open) focusControl(item);
          sync();
        };
        if (button) {
          row.dataset.formatEnhanced = "true";
          button.addEventListener("click", toggle);
        }
        item.querySelector("summary").addEventListener("click", toggle);
        item.addEventListener("keydown", (event) => {
          if (event.key === "Escape" && item.open) {
            event.preventDefault();
            item.open = false;
            (button || item.querySelector("summary")).focus();
            sync();
          }
        });
      });
      sync();
    };
    form.refrncrBindFormats = bind;
    advanced.addEventListener("click", () => {
      const open = advanced.getAttribute("aria-expanded") !== "true";
      advanced.setAttribute("aria-expanded", String(open));
      details().forEach((item) => { item.open = open || needsAttention(item); });
      sync();
    });
    form.addEventListener("input", (event) => {
      if (!/^timezone_\d+$/.test(event.target.name || "")) return;
      // Text edits precede change/blur: an older response must not replace the input.
      ++generation;
      pending?.abort();
      form.removeAttribute("aria-busy");
    });
    form.addEventListener("change", async (event) => {
      const index = event.target.name?.match(/^(?:mapping|source|date|time|timezone|fold)_(\d+)$/)?.[1];
      if (index === undefined) return;
      if (event.target.name.startsWith("mapping_")) {
        textChoices.delete(index);
        ["source", "timezone", "fold"].forEach((prefix) => { form.elements.namedItem(`${prefix}_${index}`).value = ""; });
        form.querySelector(`[data-source-formats="${index}"] [data-text-formats]`)?.querySelectorAll("select").forEach((control) => { control.disabled = true; });
      } else {
        updateComponents(form.querySelector(`[data-source-formats="${index}"]`));
      }
      const current = ++generation;
      const open = new Set(details().filter((item) => item.open).map((item) => item.closest("[data-source-formats]").dataset.sourceFormats));
      // These forms contain only text. URL encoding avoids multipart framing
      // and preserves every control in wide imports.
      const data = new URLSearchParams(new FormData(form));
      pending?.abort();
      pending = new AbortController();
      const formatFeedback = form.closest(".import-wizard")?.querySelector("[data-format-error-status]");
      if (formatFeedback) formatFeedback.textContent = "";
      form.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(form.dataset.sourceFormatsUrl, {method: "POST", body: data, credentials: "same-origin", signal: pending.signal});
        if (!response.ok || response.redirected) throw new Error("Format review unavailable");
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        if (current !== generation) return;
        const focused = document.activeElement;
        const focusState = focused?.form === form && focused.name
          ? {id: focused.id, name: focused.name, value: focused.value}
          : null;
        page.querySelectorAll("[data-source-formats]").forEach((replacement) => {
          const original = form.querySelector(`[data-source-formats="${replacement.dataset.sourceFormats}"]`);
          const disclosure = replacement.querySelector("[data-format-details]");
          if (disclosure && open.has(replacement.dataset.sourceFormats)) disclosure.open = true;
          original.replaceWith(document.importNode(replacement, true));
        });
        bind();
        const affected = form.querySelector(`[data-source-formats="${index}"] [data-format-error]`);
        let previousFocus = focusState?.id ? document.getElementById(focusState.id) : null;
        if (previousFocus && !form.contains(previousFocus)) previousFocus = null;
        if (!previousFocus && focusState) {
          const named = form.elements.namedItem(focusState.name);
          previousFocus = typeof named?.focus === "function"
            ? named
            : [...(named || [])].find((control) => control.value === focusState.value)
              || [...(named || [])].find((control) => control.checked);
        }
        const row = form.querySelector(`[data-source-formats="${index}"]`);
        const announcement = form.querySelector("[data-format-status]");
        if (announcement) announcement.textContent = `${row.querySelector("legend")?.textContent || "Source format"}: ${row.querySelector("[data-effective-format]")?.textContent || "Updated"}. Examples from source and Stored as updated. ${row.querySelector(".error-text")?.textContent || ""}`;
        if (affected && focusState?.name.startsWith("mapping_")) affected.focus();
        else if (previousFocus?.focus && !previousFocus.disabled && !previousFocus.closest("[hidden]")) previousFocus.focus();
        else if (affected) affected.focus();
      } catch (error) {
        if (error.name !== "AbortError" && current === generation) {
          const formatFeedback = form.closest(".import-wizard")?.querySelector("[data-format-error-status]");
          if (formatFeedback) formatFeedback.textContent = "Format examples could not be refreshed. Continue to validate your current choices.";
        }
      } finally {
        if (current === generation) form.removeAttribute("aria-busy");
      }
    });
    form.addEventListener("submit", (event) => {
      ++generation;
      pending?.abort();
      // Earlier steps remain reachable even while a source interpretation is unresolved.
      if (event.submitter?.name === "go_step" && ["1", "2"].includes(event.submitter.value)) return;
      const undecided = [...form.querySelectorAll('[data-state="undecided"]')].find((row) =>
        !form.elements.namedItem(`source_${row.dataset.sourceFormats}`)?.value);
      if (undecided) {
        event.preventDefault();
        const disclosure = undecided.querySelector("[data-format-details]");
        if (disclosure) { disclosure.open = true; focusControl(disclosure); }
        sync();
      }
    });
    bind();
    focusError();
  });
  if (typeof window !== "undefined") window.refrncrSourceFormats = initializeSourceFormats;
  initializeSourceFormats();

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
  document.addEventListener("keydown", (event) => {
    keyboardInput = true;
    document.activeElement?.removeAttribute?.("data-pointer-restored-focus");
    if (event.target !== document.activeElement) {
      event.target?.removeAttribute?.("data-pointer-restored-focus");
    }
  }, true);
  document.addEventListener("pointerdown", () => {
    keyboardInput = false;
    document.activeElement?.removeAttribute?.("data-pointer-restored-focus");
    document.activeElement?.removeAttribute?.("data-keyboard-restored-focus");
  }, true);
  document.addEventListener("focusout", (event) => {
    event.target?.removeAttribute?.("data-pointer-restored-focus");
    event.target?.removeAttribute?.("data-keyboard-restored-focus");
  }, true);
  document.addEventListener("submit", (event) => {
    const focusReturn = event.target.querySelector?.("[name='return_focus']");
    if (focusReturn) focusReturn.value = keyboardInput ? "keyboard" : "pointer";
  }, true);
  const visibleControl = (element) =>
    !element.closest("[hidden]") && element.getClientRects().length > 0;
  const focusSidePanel = (dialog) => {
    const candidates = dialog.querySelectorAll(
      "[data-error-focus], [aria-invalid='true'], [data-initial-focus], input:not([type='hidden']):not([disabled]):not([readonly]), select:not([disabled]), textarea:not([disabled]):not([readonly])"
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
    const eyebrow = dialog.querySelector("[data-panel-eyebrow]");
    if (eyebrow) {
      eyebrow.textContent = content.dataset.panelEyebrow || "";
      eyebrow.hidden = !eyebrow.textContent;
    }
    dialog.querySelector("[data-panel-host]").replaceChildren(document.importNode(content, true));
    const host = dialog.querySelector("[data-panel-host]");
    window.refrncrLocalTime?.(host);
    initializeSelectionForms(host);
    initializeDatatypeControls(host);
    syncIdentifierRequirements(host);
    initializeMappingFieldForms(host);
    initializeSourceFormats(host);
    initializeConfirmations(host);
    window.refrncrKeyGeneration?.(host);
    window.refrncrIconPickers?.(host);
    window.refrncrMappingTableForms?.(host);
    host.querySelectorAll("[data-panel-section-heading]").forEach((heading) => {
      const marker = "data-panel-section-heading";
      const nested = document.createElement("h3");
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
    const eyebrow = dialog.querySelector("[data-panel-eyebrow]");
    if (eyebrow) eyebrow.hidden = true;
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
      const defaultMode = dialog.dataset.defaultMode || "view";
      dialog.querySelectorAll("[data-panel-view]").forEach((view) => {
        view.hidden = view.dataset.panelView !== defaultMode;
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
      // Menu actions must be visible again before Cancel/Escape restores focus.
      opener.closest("details")?.setAttribute("open", "");
      if (opener.matches(".interactive-row .row-action") && !keyboardInput) {
        opener.setAttribute("data-pointer-restored-focus", "");
      } else {
        opener.removeAttribute?.("data-pointer-restored-focus");
      }
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
      const page = event.target.closest("[data-panel-page]");
      if (page) {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
        event.preventDefault();
        if (!pendingForms.has(dialog)) loadPanel(dialog, page);
        return;
      }
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
  document.querySelectorAll('[data-concept-actions]').forEach((menu) => {
    menu.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && menu.open) menu.querySelector('summary').focus();
    });
  });
  document.querySelectorAll('.record-status-filter').forEach((menu) => {
    const summary = menu.querySelector('summary');
    menu.addEventListener('change', () => {
      const selected = [...menu.querySelectorAll('input:checked')].map(input => input.value);
      const caption = selected.length === 0 ? 'All records' : selected.length === 3 ? 'All changes' : selected.length === 1 ? selected[0] : `${selected.length} statuses`;
      summary.textContent = caption;
      summary.setAttribute('aria-label', `Change status: ${caption}`);
    });
    menu.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && menu.open) summary.focus();
    });
  });
  document.querySelectorAll(".export-menu-options a").forEach((option) => {
    option.addEventListener("click", () => option.closest(".export-menu")?.removeAttribute("open"));
  });
  document.addEventListener("click", (event) => {
    exportMenus.forEach((menu) => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", (event) => {
    keyboardInput = true;
    document.activeElement?.removeAttribute?.("data-pointer-restored-focus");
    if (event.key === "Escape") {
      exportMenus.forEach((menu) => menu.removeAttribute("open"));
      document.querySelectorAll(".button-row-icon[data-tooltip]:is(:hover, :focus-visible)").forEach((trigger) => {
        trigger.setAttribute("data-tooltip-dismissed", "");
      });
    }
  });

  if (typeof window !== 'undefined' && window.location?.search) {
    const focusReturn = new URLSearchParams(window.location.search);
    const focusRecord = focusReturn.get("focus_record");
    if (focusRecord) {
      const primary = document.getElementById(`record-row-${focusRecord}`)?.querySelector("[data-row-primary]");
      if (primary) {
        const keyboard = focusReturn.get("focus_mode") === "keyboard";
        primary.toggleAttribute("data-pointer-restored-focus", !keyboard);
        primary.toggleAttribute("data-keyboard-restored-focus", keyboard);
        primary.focus();
      }
      focusReturn.delete("focus_record");
      focusReturn.delete("focus_mode");
      const remaining = focusReturn.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${remaining ? `?${remaining}` : ""}`);
    }
  }

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
    if (event.defaultPrevented || modifiedRowClick(event) || event.detail > 1 ||
        window.getSelection?.()?.toString()) return;
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

  // Search fields: the clear control follows the field's text. A clear link that resets
  // applied criteria navigates; text that was typed but not applied is cleared in place.
  document.querySelectorAll("[data-search-field]").forEach((field) => {
    const input = field.querySelector("input[type=search]");
    const clear = field.querySelector("[data-search-clear]");
    if (!input || !clear) return;
    const applied = clear.hasAttribute("data-search-applied");
    const sync = () => { clear.hidden = !applied && input.value === ""; };
    input.addEventListener("input", sync);
    clear.addEventListener("click", (event) => {
      if (applied) return;
      event.preventDefault();
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
    sync();
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
