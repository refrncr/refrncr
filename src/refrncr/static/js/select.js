// REF-246: turns every select[data-select] into the styled refrncr Select. The native select stays
// in the form as the source of truth: options, disabled and selected are read from it and every
// choice is written back with input and change events, so forms and other scripts keep working.
(() => {
  if (window.refrncrSelects) return;
  const SEARCH_FROM = 10;
  const PAGE = 8;
  const GAP = 4;
  const EDGE = 8;
  const NS = "http://www.w3.org/2000/svg";
  const controllers = new WeakMap();
  const popoverSupported = Object.prototype.hasOwnProperty.call(HTMLElement.prototype, "popover");
  let sequence = 0;

  const icon = (className, shapes) => {
    const svg = document.createElementNS(NS, "svg");
    for (const [name, value] of Object.entries({viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false", class: className})) svg.setAttribute(name, value);
    for (const [tag, attributes] of shapes) {
      const shape = document.createElementNS(NS, tag);
      for (const [name, value] of Object.entries(attributes)) shape.setAttribute(name, value);
      svg.append(shape);
    }
    return svg;
  };
  const chevronIcon = () => icon("select-chevron", [["path", {d: "m6 9 6 6 6-6"}]]);
  const checkIcon = () => icon("select-check", [["path", {d: "M20 6 9 17l-5-5"}]]);
  const searchIcon = () => icon("select-search-icon", [["circle", {cx: "11", cy: "11", r: "8"}], ["path", {d: "m21 21-4.3-4.3"}]]);
  const clearIcon = () => icon("select-clear-icon", [["path", {d: "M18 6 6 18"}], ["path", {d: "m6 6 12 12"}]]);
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const remPixels = (value) => {
    const amount = parseFloat(value);
    if (!amount) return 320;
    return value.trim().endsWith("rem") ? amount * parseFloat(getComputedStyle(document.documentElement).fontSize) : amount;
  };
  const isPrintable = (event) => event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
  // The nearest ancestor that clips or scrolls bounds where the popup may open (side panel, dialog).
  const boundary = (node) => {
    let rect = {top: 0, bottom: window.innerHeight};
    for (let parent = node.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(parent).overflowY)) {
        const box = parent.getBoundingClientRect();
        rect = {top: Math.max(0, box.top), bottom: Math.min(window.innerHeight, box.bottom)};
        break;
      }
    }
    return rect;
  };

  const enhance = (select) => {
    if (controllers.has(select) || select.multiple || select.size > 1) return;
    if (!select.id) select.id = `refrncr-select-${++sequence}`;
    const compact = select.dataset.select === "compact";
    const noun = select.dataset.noun || "options";

    const trigger = element("button", `select-trigger${compact ? " is-compact" : ""}`);
    trigger.type = "button";
    trigger.setAttribute("tabindex", "0");
    trigger.id = `${select.id}-trigger`;
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    const valueNode = element("span", "select-value");
    trigger.append(valueNode, chevronIcon());

    const popup = element("div", `select-popup${compact ? " is-compact" : ""}`);
    popup.id = `${select.id}-popup`;
    if (popoverSupported) popup.popover = "manual";
    const listbox = element("ul", "select-listbox");
    listbox.id = `${select.id}-listbox`;
    listbox.setAttribute("role", "listbox");
    listbox.tabIndex = -1;
    trigger.setAttribute("aria-controls", listbox.id);

    const searchRow = element("div", "select-search");
    const searchInput = element("input", "select-search-input");
    searchInput.type = "text";
    searchInput.disabled = true;
    searchInput.id = `${select.id}-search`;
    searchInput.autocomplete = "off";
    searchInput.spellcheck = false;
    searchInput.setAttribute("role", "combobox");
    searchInput.setAttribute("aria-autocomplete", "list");
    searchInput.setAttribute("aria-expanded", "true");
    searchInput.setAttribute("aria-controls", listbox.id);
    const searchLabel = element("label", "visually-hidden");
    searchLabel.htmlFor = searchInput.id;
    const clear = element("button", "select-clear");
    clear.type = "button";
    clear.tabIndex = -1;
    clear.setAttribute("aria-label", "Clear search");
    clear.append(clearIcon());
    searchRow.append(searchIcon(), searchLabel, searchInput, clear);
    const empty = element("div", "select-empty");
    empty.hidden = true;
    const footer = element("div", "select-footer");
    const count = element("span");
    count.setAttribute("aria-hidden", "true");
    footer.append(count);
    const status = element("span", "visually-hidden");
    status.setAttribute("role", "status");
    popup.append(searchRow, listbox, empty, footer, status);

    const labels = [...select.labels];
    labels.forEach((label, index) => {
      if (!label.id) label.id = `${select.id}-label${index ? `-${index}` : ""}`;
      if (label.htmlFor === select.id) label.htmlFor = trigger.id;
    });
    if (labels.length) {
      trigger.setAttribute("aria-labelledby", labels.map((label) => label.id).join(" "));
      searchLabel.textContent = `Search ${labels[0].textContent.trim()}`;
    } else if (select.getAttribute("aria-label")) {
      trigger.setAttribute("aria-label", select.getAttribute("aria-label"));
      searchLabel.textContent = `Search ${select.getAttribute("aria-label")}`;
    }

    select.classList.add("select-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.before(trigger);
    // The popup follows the select, so scripts that look for the first control find the select;
    // inside a wrapping <label> it would re-trigger the label, so it goes after the label.
    (trigger.closest("label") || select).after(popup);

    let rows = [];
    let dirty = true;
    let active = -1;
    let isOpen = false;
    let typed = "";
    let typedTimer = 0;
    let statusTimer = 0;
    let clientError = null;

    const isPlaceholder = (option) => option.value === "" && select.required;
    const optionLabel = (option) => option.label || option.textContent.trim();
    const searchable = () => {
      if (select.dataset.searchable === "true") return true;
      if (select.dataset.searchable === "false") return false;
      return [...select.options].filter((option) => !isPlaceholder(option)).length > SEARCH_FROM;
    };
    const labelContent = (option, query = "") => {
      if (option.dataset.badge) {
        const badge = element("span", `badge badge-${option.dataset.badge}`, optionLabel(option));
        return badge;
      }
      const text = optionLabel(option);
      const at = query ? text.toLowerCase().indexOf(query) : -1;
      if (at < 0) return document.createTextNode(text);
      const fragment = document.createDocumentFragment();
      fragment.append(text.slice(0, at), element("mark", "", text.slice(at, at + query.length)), text.slice(at + query.length));
      return fragment;
    };

    const syncTrigger = () => {
      const option = select.options[select.selectedIndex];
      valueNode.replaceChildren();
      valueNode.classList.remove("select-placeholder");
      if (!option) {
        valueNode.textContent = " ";
      } else if (option.value === "" && select.required) {
        valueNode.textContent = optionLabel(option);
        valueNode.classList.add("select-placeholder");
      } else {
        valueNode.append(labelContent(option));
      }
      trigger.disabled = select.disabled;
      if (select.required) trigger.setAttribute("aria-required", "true");
      else trigger.removeAttribute("aria-required");
      const describers = [select.getAttribute("aria-describedby"), clientError?.id].filter(Boolean).join(" ");
      if (describers) trigger.setAttribute("aria-describedby", describers);
      else trigger.removeAttribute("aria-describedby");
      if (select.getAttribute("aria-invalid") === "true" || clientError) trigger.setAttribute("aria-invalid", "true");
      else trigger.removeAttribute("aria-invalid");
    };

    const render = () => {
      listbox.replaceChildren();
      rows = [];
      let group = null;
      [...select.options].forEach((option, index) => {
        if (isPlaceholder(option)) return;
        const parent = option.parentElement;
        if (parent.tagName === "OPTGROUP" && parent !== group) {
          group = parent;
          const heading = element("li", "select-group", parent.label);
          heading.setAttribute("role", "presentation");
          heading.id = `${select.id}-group-${index}`;
          listbox.append(heading);
        }
        const row = element("li", "select-option");
        row.id = `${select.id}-option-${index}`;
        row.setAttribute("role", "option");
        row.dataset.index = String(index);
        const disabled = option.disabled || (parent.tagName === "OPTGROUP" && parent.disabled);
        if (disabled) row.setAttribute("aria-disabled", "true");
        listbox.append(row);
        rows.push({row, option, index, disabled, text: `${optionLabel(option)} ${option.dataset.code || ""}`.toLowerCase()});
      });
      dirty = false;
    };

    const paintRows = (query) => {
      rows.forEach(({row, option}) => {
        const selected = option.index === select.selectedIndex;
        row.setAttribute("aria-selected", String(selected));
        row.replaceChildren();
        if (option.dataset.code) row.append(element("span", "select-code", option.dataset.code));
        const label = element("span", "select-option-label");
        label.append(labelContent(option, query));
        row.append(label);
        if (selected) row.append(checkIcon());
      });
    };

    const visibleRows = () => rows.filter(({row}) => !row.hidden);
    const enabledRows = () => visibleRows().filter(({disabled}) => !disabled);

    const setActive = (index, scroll = true) => {
      const items = enabledRows();
      rows.forEach(({row}) => row.classList.remove("is-active"));
      const owner = searchable() ? searchInput : trigger;
      if (!items.length) {
        active = -1;
        owner.removeAttribute("aria-activedescendant");
        return;
      }
      active = Math.max(0, Math.min(index, items.length - 1));
      const {row} = items[active];
      row.classList.add("is-active");
      owner.setAttribute("aria-activedescendant", row.id);
      if (scroll) row.scrollIntoView({block: "nearest"});
    };
    const activeIndexOfSelected = () => {
      const position = enabledRows().findIndex(({option}) => option.index === select.selectedIndex);
      return position < 0 ? 0 : position;
    };

    const announce = () => {
      clearTimeout(statusTimer);
      statusTimer = setTimeout(() => { status.textContent = count.textContent; }, 300);
    };
    const filter = () => {
      const query = searchInput.value.trim().toLowerCase();
      let shown = 0;
      rows.forEach((item) => {
        item.row.hidden = item.option.hidden || (Boolean(query) && !item.text.includes(query));
        if (!item.row.hidden) shown += 1;
      });
      listbox.querySelectorAll(".select-group").forEach((heading) => {
        let next = heading.nextElementSibling;
        let any = false;
        while (next && !next.classList.contains("select-group")) {
          if (!next.hidden) any = true;
          next = next.nextElementSibling;
        }
        heading.hidden = !any;
      });
      paintRows(query);
      empty.hidden = shown > 0;
      listbox.hidden = shown === 0;
      if (!shown) {
        const hasCodes = rows.some(({option}) => option.dataset.code);
        empty.replaceChildren(element("strong", "", `No ${noun} match “${searchInput.value.trim()}”`), element("span", "", hasCodes ? "Check the spelling, or search by code." : "Check the spelling, or try fewer letters."));
      }
      count.textContent = `${shown} of ${rows.length} ${noun}`;
      announce();
      setActive(query ? 0 : activeIndexOfSelected());
    };

    const position = () => {
      const rect = trigger.getBoundingClientRect();
      const bounds = boundary(trigger);
      const limit = remPixels(getComputedStyle(document.documentElement).getPropertyValue("--select-popup-max-height"));
      popup.style.maxHeight = "";
      popup.style.minWidth = `${Math.round(rect.width)}px`;
      popup.style.maxWidth = `${window.innerWidth - EDGE * 2}px`;
      const natural = Math.min(popup.scrollHeight, limit);
      const below = Math.min(bounds.bottom, window.innerHeight - EDGE) - rect.bottom - GAP;
      const above = rect.top - Math.max(bounds.top, EDGE) - GAP;
      const downward = below >= natural || below >= above;
      const height = Math.max(0, Math.min(natural, downward ? below : above));
      popup.style.maxHeight = `${height}px`;
      popup.style.top = `${Math.round(downward ? rect.bottom + GAP : rect.top - GAP - height)}px`;
      const width = popup.getBoundingClientRect().width;
      popup.style.left = `${Math.round(Math.max(EDGE, Math.min(rect.left, window.innerWidth - width - EDGE)))}px`;
      popup.dataset.placement = downward ? "bottom" : "top";
    };

    const onOutsidePointer = (event) => {
      if (!popup.contains(event.target) && !trigger.contains(event.target)) close(false);
    };
    const onScroll = (event) => {
      if (event.target instanceof Node && popup.contains(event.target)) return;
      close(false);
    };
    const onResize = () => close(false);

    const open = ({at = "selected", text = ""} = {}) => {
      if (isOpen || trigger.disabled) return;
      if (dirty) render();
      const search = searchable();
      searchRow.hidden = !search;
      footer.hidden = !search;
      searchInput.value = text;
      searchInput.disabled = !search;
      popup.classList.add("is-open");
      if (popoverSupported) popup.showPopover();
      isOpen = true;
      trigger.setAttribute("aria-expanded", "true");
      filter();
      position();
      if (at === "first") setActive(0);
      else if (at === "last") setActive(enabledRows().length - 1);
      else if (!text) setActive(activeIndexOfSelected());
      if (search) searchInput.focus();
      else trigger.focus();
      document.addEventListener("pointerdown", onOutsidePointer, true);
      document.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onResize);
    };

    function close(restoreFocus = true) {
      if (!isOpen) return;
      isOpen = false;
      popup.classList.remove("is-open");
      if (popoverSupported && popup.matches(":popover-open")) popup.hidePopover();
      trigger.setAttribute("aria-expanded", "false");
      trigger.removeAttribute("aria-activedescendant");
      searchInput.removeAttribute("aria-activedescendant");
      searchInput.disabled = true;
      document.removeEventListener("pointerdown", onOutsidePointer, true);
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
      clearTimeout(statusTimer);
      status.textContent = "";
      if (restoreFocus) trigger.focus();
    }

    const choose = (item) => {
      if (!item || item.disabled) return;
      if (select.selectedIndex !== item.index) {
        select.selectedIndex = item.index;
        select.dispatchEvent(new Event("input", {bubbles: true}));
        select.dispatchEvent(new Event("change", {bubbles: true}));
      }
      if (clientError && select.checkValidity()) {
        clientError.remove();
        clientError = null;
      }
      syncTrigger();
      close(true);
    };

    const typeahead = (character) => {
      clearTimeout(typedTimer);
      typed += character.toLowerCase();
      typedTimer = setTimeout(() => { typed = ""; }, 500);
      const items = enabledRows();
      const start = typed.length === 1 ? active + 1 : active;
      const order = [...items.slice(start), ...items.slice(0, start)];
      const match = order.find(({option}) => optionLabel(option).toLowerCase().startsWith(typed));
      if (match) setActive(items.indexOf(match));
    };

    trigger.addEventListener("click", () => {
      if (isOpen) close(true);
      else open();
    });
    trigger.addEventListener("keydown", (event) => {
      if (!isOpen) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault();
          open({at: event.key === "Home" ? "first" : "last"});
        } else if (isPrintable(event)) {
          event.preventDefault();
          if (searchable()) open({text: event.key});
          else {
            open();
            typeahead(event.key);
          }
        }
        return;
      }
      navigate(event, true);
    });
    const navigate = (event, onTrigger) => {
      const items = enabledRows();
      const moves = {ArrowDown: active + 1, ArrowUp: active - 1, PageDown: active + PAGE, PageUp: active - PAGE};
      if (event.key in moves) {
        event.preventDefault();
        if (event.altKey && event.key === "ArrowUp") {
          choose(items[active]);
          return;
        }
        setActive(moves[event.key]);
      } else if (onTrigger && (event.key === "Home" || event.key === "End")) {
        event.preventDefault();
        setActive(event.key === "Home" ? 0 : items.length - 1);
      } else if (event.key === "Enter" || (onTrigger && event.key === " ")) {
        event.preventDefault();
        choose(items[active]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close(true);
      } else if (event.key === "Tab") {
        // Move focus to the trigger first, so the browser's Tab continues from the field itself.
        close(false);
        trigger.focus();
      } else if (onTrigger && isPrintable(event)) {
        event.preventDefault();
        typeahead(event.key);
      }
    };
    searchInput.addEventListener("keydown", (event) => navigate(event, false));
    searchInput.addEventListener("input", filter);
    clear.addEventListener("click", () => {
      searchInput.value = "";
      filter();
      searchInput.focus();
    });
    listbox.addEventListener("pointerdown", (event) => event.preventDefault());
    listbox.addEventListener("click", (event) => {
      const row = event.target.closest(".select-option");
      if (!row) return;
      choose(rows.find((item) => item.row === row));
    });
    popup.addEventListener("focusout", (event) => {
      if (isOpen && event.relatedTarget && !popup.contains(event.relatedTarget) && event.relatedTarget !== trigger) close(false);
    });
    trigger.addEventListener("blur", (event) => {
      if (isOpen && event.relatedTarget && !popup.contains(event.relatedTarget)) close(false);
    });

    // Other scripts set value or selectedIndex directly, without an event; follow those writes.
    for (const property of ["value", "selectedIndex"]) {
      const native = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, property);
      Object.defineProperty(select, property, {
        configurable: true,
        get() { return native.get.call(this); },
        set(next) {
          native.set.call(this, next);
          syncTrigger();
          if (isOpen) paintRows(searchInput.value.trim().toLowerCase());
        },
      });
    }
    // Scripts that focus the select (errors, side panels, autofocus) reach the visible trigger.
    select.addEventListener("focus", () => trigger.focus());
    select.addEventListener("change", syncTrigger);
    select.addEventListener("invalid", (event) => {
      event.preventDefault();
      if (!clientError) {
        clientError = element("p", "error-text", select.validationMessage);
        clientError.id = `${select.id}-validation`;
        popup.after(clientError);
      }
      syncTrigger();
      if (select.form && select.form.querySelector(":invalid") === select) trigger.focus();
    });
    select.form?.addEventListener("reset", () => setTimeout(syncTrigger));
    new MutationObserver(() => {
      dirty = true;
      syncTrigger();
      if (isOpen) {
        render();
        filter();
      }
    }).observe(select, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["disabled", "hidden", "selected", "label", "required", "aria-invalid", "aria-describedby"]});

    syncTrigger();
    if (document.activeElement === select) trigger.focus();
    controllers.set(select, {open, close, trigger, popup});
  };

  window.refrncrSelects = (root = document) => {
    if (root.matches?.("select[data-select]")) enhance(root);
    root.querySelectorAll?.("select[data-select]").forEach(enhance);
  };
  window.refrncrSelects();
  // Side panels and inline refreshes insert new markup; enhance any select they bring in.
  new MutationObserver((records) => {
    records.forEach((record) => record.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) window.refrncrSelects(node);
    }));
  }).observe(document.documentElement, {childList: true, subtree: true});
})();
