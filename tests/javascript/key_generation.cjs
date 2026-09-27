const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Control {
  constructor(value = '') { this.value = value; this.events = {}; this.disabled = false; }
  addEventListener(event, fn) { (this.events[event] ||= []).push(fn); }
  fire(event) { for (const fn of this.events[event] || []) fn(); }
  closest() { return null; }
}
function form(name = '', key = '') {
  const f = new Control(); f.name = new Control(name); f.key = new Control(key);
  f.querySelector = selector => selector === '[name="name"]' ? f.name : f.key;
  f.querySelectorAll = selector => selector === '[name="name"]' ? [f.name] : [];
  return f;
}
const first = form();
const document = {querySelectorAll: () => [first]};
const window = {addEventListener() {}};
const context = {window, document};
const script = fs.readFileSync(process.argv[2], 'utf8');
vm.runInNewContext(script, context);
vm.runInNewContext(script, context);
window.refrncrKeyGeneration(document);
assert.equal(first.name.events.input.length, 1);
first.name.value = 'Café / sales'; first.name.fire('input');
assert.equal(first.key.value, 'cafe_sales', 'typing and pasted text normalize');
first.name.value = 'Autofill'; first.name.fire('change');
assert.equal(first.key.value, 'autofill');
first.name.value = 'Silent autofill'; first.fire('submit');
assert.equal(first.key.value, 'silent_autofill');
first.key.value = 'manual_key'; first.key.fire('input');
first.name.value = 'Changed'; first.name.fire('input');
assert.equal(first.key.value, 'manual_key');
first.key.value = ''; first.key.fire('change');
first.name.value = 'Still manual'; first.name.fire('change');
assert.equal(first.key.value, '', 'clearing a manually edited key does not resume generation');
const fragment = form('Crème brûlée');
window.refrncrKeyGeneration({querySelectorAll: () => [fragment]});
assert.equal(fragment.key.value, 'creme_brulee');
fragment.key.value = 'autofilled_key'; fragment.name.value = 'New name'; fragment.name.fire('input');
assert.equal(fragment.key.value, 'autofilled_key', 'silent key autofill is authoritative');
const redisplayed = form('Name', 'custom');
window.refrncrKeyGeneration({querySelectorAll: () => [redisplayed]});
redisplayed.name.value = 'Later'; redisplayed.name.fire('input');
assert.equal(redisplayed.key.value, 'custom');
console.log('Key generation: typing, paste, autofill, manual override, redisplay and fragment initialization passed.');
