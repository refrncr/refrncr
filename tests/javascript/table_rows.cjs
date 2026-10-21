// Exercise the production delegated handlers; native layout/keyboard defaults need a browser.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const listeners = [];
let selected = false;
function tooltip(hovered = false, focused = false) {
  return {
    hovered, focused, attributes: new Set(), listeners: new Map(),
    matches() { return this.hovered || this.focused; },
    setAttribute(name) { this.attributes.add(name); },
    removeAttribute(name) { this.attributes.delete(name); },
    addEventListener(type, callback) { this.listeners.set(type, callback); },
  };
}
const hoveredTooltip = tooltip(true);
const focusedTooltip = tooltip(false, true);
const inactiveTooltip = tooltip();
const tooltips = [hoveredTooltip, focusedTooltip, inactiveTooltip];
const exportMenu = {
  open: true, contains: () => false,
  removeAttribute(name) { if (name === 'open') this.open = false; },
};
const document = {
  activeElement: focusedTooltip,
  querySelectorAll(selector) {
    if (selector === '.button-row-icon[data-tooltip]') return tooltips;
    if (selector === '.button-row-icon[data-tooltip]:is(:hover, :focus-visible)') return tooltips.filter(t => t.matches());
    if (selector === '.export-menu') return [exportMenu];
    return [];
  },
  querySelector: () => null,
  addEventListener(type, callback, capture = false) { listeners.push({type, callback, capture}); },
};
const source = fs.readFileSync(process.argv[2], 'utf8');
const context = {document, window: {getSelection: () => ({isCollapsed: !selected, toString: () => selected ? 'selected text' : ''})}};
vm.runInNewContext(source, context);
const count = listeners.length;
vm.runInNewContext(source, context);
assert.equal(listeners.length, count, 'initialization is idempotent');
function fixture() {
  const primary = {clicks: 0, disabled: false, matches() { return this.disabled; }, click() { this.clicks++; fire(this); }};
  const row = {contains: element => !element.outsideRow, hidden: false, inert: false, closest() { return this.inert ? {} : null; }, querySelector() { return primary; }};
  const target = (interactive = null) => ({
    closest(selector) {
      if (selector === 'tbody tr[data-row-action]') return row;
      if (selector === '[data-row-primary]') return interactive === primary ? primary : null;
      return interactive;
    },
  });
  primary.closest = target(primary).closest;
  return {primary, row, target};
}
function fire(target, extra = {}) {
  const event = {target, detail: 1, button: 0, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; }, ...extra};
  for (const capture of [true, false]) {
    for (const handler of listeners.filter(l => l.type === 'click' && l.capture === capture)) handler.callback(event);
  }
  return event;
}
const {primary, row, target} = fixture();
fire(target());
assert.equal(primary.clicks, 1, 'unused cell space activates exactly once, even when the synthetic click bubbles');
for (const modifier of ['ctrlKey', 'metaKey', 'shiftKey', 'altKey']) fire(target(), {[modifier]: true});
for (const button of [1, 2]) fire(target(), {button});
fire(target(), {defaultPrevented: true});
assert.equal(primary.clicks, 1, 'modifiers, non-primary buttons and handled events are ignored');
for (const control of ['a', 'button', 'input', 'select', 'textarea', 'label', 'summary', 'details', 'contenteditable', 'menu', 'tabindex']) {
  fire(target({matches: () => false}));
  assert.equal(primary.clicks, 1, `${control} retains its own action`);
}
const native = fire(primary, {metaKey: true});
assert.equal(native.defaultPrevented, false, 'native primary activation is not canceled or synthesized');
assert.equal(primary.clicks, 1);
selected = true;
fire(target());
assert.equal(primary.clicks, 1, 'selected row text never activates the primary');
assert.equal(fire(primary, {metaKey: true}).defaultPrevented, false, 'selection does not cancel modified native link activation');
assert.equal(fire(primary).defaultPrevented, true, 'drag selection on primary text also suppresses activation');
assert.equal(fire(primary, {detail: 0}).defaultPrevented, false, 'native keyboard activation is unaffected by an existing selection');
selected = false;
row.hidden = true; fire(target()); row.hidden = false;
row.inert = true; fire(target()); row.inert = false;
primary.disabled = true; fire(target()); primary.disabled = false;
assert.equal(primary.clicks, 1, 'hidden, inert and disabled rows never activate');
fire(target({matches: selector => selector.includes("td[role='cell']")}));
assert.equal(primary.clicks, 2, 'explicit semantic cells remain usable row space');
fire({closest: () => null});
assert.equal(primary.clicks, 2, 'headers and non-navigable rows are ignored');
const keydowns = listeners.filter(l => l.type === 'keydown');
assert.equal(keydowns.length, 3, 'global Escape, focus modality and row keyboard handlers are registered');
const keydown = keydowns.at(-1).callback;
exportMenu.open = true;
keydown({key: 'Enter'});
assert.equal(exportMenu.open, true, 'other keys leave export menus open');
assert.equal(tooltips.some(t => t.attributes.has('data-tooltip-dismissed')), false, 'other keys do not dismiss tooltips');
keydown({key: 'Escape'});
assert.equal(exportMenu.open, false, 'Escape still closes export menus');
assert.equal(hoveredTooltip.attributes.has('data-tooltip-dismissed'), true, 'Escape dismisses hovered icon labels');
assert.equal(focusedTooltip.attributes.has('data-tooltip-dismissed'), true, 'Escape dismisses focused icon labels');
assert.equal(inactiveTooltip.attributes.has('data-tooltip-dismissed'), false, 'inactive icon labels remain available');
assert.equal(document.activeElement, focusedTooltip, 'dismissing a tooltip preserves keyboard focus');
focusedTooltip.listeners.get('pointerleave')();
assert.equal(focusedTooltip.attributes.has('data-tooltip-dismissed'), true, 'dismissal persists while keyboard focus remains');
hoveredTooltip.hovered = false;
hoveredTooltip.listeners.get('pointerleave')();
focusedTooltip.focused = false;
focusedTooltip.listeners.get('blur')();
assert.equal(tooltips.some(t => t.attributes.has('data-tooltip-dismissed')), false, 'labels reset once hover and focus both leave');
fire(target({outsideRow:true,matches:()=>false}));
assert.equal(primary.clicks,3,'focusable main landmark outside the row must not block its click');
console.log('Shared table-row controller checks passed');
