(() => {
  if (window.refrncrKeyGeneration) return;
  // Keep equivalent to presentation_metadata.key_from_name; shared vectors test both.
  const canonical = (name) => name.normalize("NFKD").toLowerCase()
    .replace(/[\t\n\v\f\r /\\.\-\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\u2010-\u2015]+/g, "_")
    .replace(/[^a-z0-9_]/g, "").replace(/_+/g, "_").replace(/^_|_$/g, "");
  const states = new WeakMap();
  const initialize = (root = document) => {
    root.querySelectorAll("form[data-generate-key]").forEach((form) => {
      const pairs = [...form.querySelectorAll('[name="name"]')].map((name) => {
        const scope = name.closest("[data-mapping-field-panel]") || form;
        return {name, key: scope.querySelector('[name="key"]')};
      }).filter(({key}) => key);
      if (!pairs.length) return;
      if (states.has(form)) { states.get(form)(); return; }
      const pairStates = new WeakMap();
      pairs.forEach(({name, key}) => {
        // A redisplayed nonempty key is authoritative, including a manual override.
        const state = {manual: key.value !== "", generated: key.value};
        pairStates.set(key, state);
        const update = () => {
          // Also catch password-manager/autofill changes that do not emit input.
          if (key.value !== state.generated) state.manual = true;
          if (!state.manual) key.value = state.generated = canonical(name.value);
        };
        const override = () => { state.manual = true; };
        key.addEventListener("input", override);
        key.addEventListener("change", override);
        name.addEventListener("input", update);
        name.addEventListener("change", update);
      });
      const update = () => {
        pairs.filter(({name, key}) => !name.disabled && !key.disabled).forEach(({name, key}) => {
          const state = pairStates.get(key);
          if (key.value !== state.generated) state.manual = true;
          if (!state.manual) key.value = state.generated = canonical(name.value);
        });
      };
      form.addEventListener("focusin", update);
      form.addEventListener("submit", update);
      form.addEventListener("change", (event) => {
        if (event.target.name === "mapping_field_role") queueMicrotask(update);
      });
      states.set(form, update);
      update();
    });
  };
  window.refrncrKeyGeneration = initialize;
  window.refrncrKeyFromName = canonical;
  window.addEventListener("pageshow", () => initialize());
  initialize();
})();
