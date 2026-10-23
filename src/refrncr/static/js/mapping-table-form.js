"use strict";

const mappingTableForms = new WeakSet();
window.refrncrMappingTableForms = (root = document) => root.querySelectorAll("[data-mapping-table-form]").forEach((form) => {
  if (mappingTableForms.has(form)) return;
  mappingTableForms.add(form);
  const setup = form.querySelector("[data-mapping-setup]");
  const linked = form.querySelector("[data-linked-mapping]");
  const sourceTable = form.querySelector("#mapping-source-table");
  const sourceField = form.querySelector("#mapping-source-field");
  const sourceSelectGroup = form.querySelector("[data-source-identifier-select]");

  const controls = (container) => [...container.querySelectorAll("input, select")];
  const setActive = (container, active) => {
    container.hidden = !active;
    controls(container).forEach((control) => { control.disabled = !active; });
  };
  const filterFields = (table, field, prompt) => {
    const tableId = table.value;
    [...field.options].forEach((option, index) => {
      if (index === 0) {
        option.textContent = tableId ? prompt : `Choose a ${table.id.includes("source") ? "source" : "target"} table first`;
        return;
      }
      const visible = option.dataset.tableId === tableId;
      option.hidden = !visible;
      option.disabled = !visible;
    });
    if (field.selectedOptions[0]?.dataset.tableId !== tableId) field.value = "";
    field.disabled = !tableId;
  };

  const sync = () => {
    const mapping = form.querySelector("input[name='kind']:checked")?.value === "mapping";
    setup.hidden = !mapping;
    controls(setup).forEach((control) => { control.disabled = !mapping; });
    if (!mapping) return;

    setActive(linked, true);

    const sourceIsLinked = Boolean(sourceTable.value);
    sourceSelectGroup.hidden = !sourceIsLinked;
    sourceField.disabled = !sourceIsLinked;
    if (sourceIsLinked) filterFields(sourceTable, sourceField, "Choose a source identifier");
  };

  form.addEventListener("change", (event) => {
    if (event.target.matches("input[name='kind'], input[name='mapping_mode'], #mapping-source-table")) sync();
  });
  sync();
});

window.refrncrMappingTableForms();
