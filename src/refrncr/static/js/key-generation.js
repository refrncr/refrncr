(() => {
  if (window.refrncrKeyGeneration) return;
  // Keep equivalent to presentation_metadata.key_from_name; shared vectors test both.
  const canonical = (name) => name.normalize("NFKD").toLowerCase()
    .replace(/[\t\n\v\f\r /\\.\-\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\u2010-\u2015]+/g, "_")
    .replace(/[^a-z0-9_]/g, "").replace(/_+/g, "_").replace(/^_|_$/g, "");
  const states = new WeakMap();
  const initialize = (root = document) => {
    root.querySelectorAll("form[data-generate-key]").forEach((form) => {
      const name = form.querySelector('[name="name"]');
      const key = form.querySelector('[name="key"]');
      if (!name || !key) return;
      if (states.has(form)) { states.get(form)(); return; }
      // A redisplayed nonempty key is authoritative, including a manual override.
      let manual = key.value !== "";
      let generated = key.value;
      const update = () => {
        // Also catch password-manager/autofill changes that do not emit input.
        if (key.value !== generated) manual = true;
        if (!manual) key.value = generated = canonical(name.value);
      };
      const override = () => { manual = true; };
      key.addEventListener("input", override);
      key.addEventListener("change", override);
      name.addEventListener("input", update);
      name.addEventListener("change", update);
      form.addEventListener("focusin", update);
      form.addEventListener("submit", update);
      states.set(form, update);
      update();
    });
  };
  window.refrncrKeyGeneration = initialize;
  window.refrncrKeyFromName = canonical;
  window.addEventListener("pageshow", () => initialize());
  initialize();
})();
