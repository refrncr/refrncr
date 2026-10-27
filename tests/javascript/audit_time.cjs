const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
for (const zone of ['UTC', 'Europe/Amsterdam', 'America/New_York', 'Asia/Tokyo', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
  process.env.TZ = zone;
  const values = [
    '2026-03-29T00:59:59.123456+00:00', '2026-03-29T01:00:00.654321+00:00',
    '2026-10-25T00:30:00.123456+00:00', '2026-10-25T01:30:00.123456+00:00',
    '2026-01-01T00:00:00.000001+00:00', 'not ISO', '9999-99-99T99:99:99.123456+00:00',
    '2026-02-30T00:00:00.123456+00:00',
  ];
  const times = values.map(value => ({getAttribute: () => value, textContent: value + ' UTC'}));
  const document = {querySelectorAll: selector => selector.startsWith('time') ? times : []};
  const window = {};
  const context = vm.createContext({window, document, Date, Intl});
  vm.runInContext(source, context);
  values.forEach((value, i) => {
    if (i >= 5) assert.equal(times[i].textContent, value + ' UTC');
    else {
      const expected = new Intl.DateTimeFormat(undefined, {year: 'numeric', month: 'short',
        day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
        fractionalSecondDigits: 3, timeZoneName: 'shortOffset', hourCycle: 'h23'})
        .formatToParts(new Date(value)).map(p => p.type === 'fractionalSecond' ? value.slice(20, 26) : p.value).join('');
      assert.equal(times[i].textContent, expected);
    }
  });
  // Lists use the compact notation; the exact local instant moves to the title.
  const compactValue = '2026-03-29T01:00:00.654321+00:00';
  for (const mode of ['time', 'datetime', 'minute']) {
    const compact = {dataset: {localTime: mode}, getAttribute: () => compactValue, textContent: compactValue + ' UTC'};
    vm.runInContext(source, vm.createContext({window: {}, Date, Intl,
      document: {querySelectorAll: selector => selector.startsWith('time') ? [compact] : []}}));
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {year: 'numeric', month: 'numeric',
      day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'})
      .formatToParts(new Date(compactValue)).map(part => [part.type, part.value]));
    const time = `${p.hour}:${p.minute}:${p.second}`;
    const day = `${Number(p.day)} ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][p.month - 1]} ${p.year}`;
    assert.equal(compact.textContent, mode === 'minute' ? time.slice(0, 5) : mode === 'time' ? time : `${day}, ${time}`);
    assert(compact.title.includes('654321') && compact.title.includes('GMT'));
    assert.equal(compact.dataset.localDay, day);
  }
  const original = window.refrncrLocalTime;
  vm.runInContext(source, context);
  assert.equal(window.refrncrLocalTime, original);
  context.Intl = {DateTimeFormat: function () { throw Error('unsupported'); }};
  times[0].textContent = values[0] + ' UTC';
  window.refrncrLocalTime(document);
  assert.equal(times[0].textContent, values[0] + ' UTC');
}
for (const intl of [undefined, {}, {DateTimeFormat() { throw Error('unsupported'); }}]) {
  const input = {type: 'text', value: '2026-10-25T01:30:00.123456+00:00'};
  const form = {querySelectorAll: () => [input], addEventListener() { throw Error('Do not enhance unsupported form'); }};
  const document = {querySelectorAll: selector => selector.startsWith('time') ? [] : [form]};
  vm.runInNewContext(source, {window: {}, document, Date, Intl: intl});
  assert.equal(input.type, 'text');
  assert.equal(input.value, '2026-10-25T01:30:00.123456+00:00');
}
console.log('PASS: six timezones, compact list notation, DST spring/fall, date boundary, six-digit precision, invalid calendar/unsupported Intl form fallback, duplicate guard');

// Exercise the real submit handler, including preserved precision and DST folds/gaps.
function filterForm(zone, initial = {}) {
  process.env.TZ = zone;
  let submit, destination;
  const error = {hidden: true, focus() { this.focused = true; }};
  const inputs = ['start', 'end'].map(name => ({
    name, value: initial[name] || '', validity: {}, handlers: {},
    addEventListener(event, handler) { this.handlers[event] = handler; },
    removeAttribute(name) { delete this[name]; },
    setAttribute(name, value) { this[name] = value; },
  }));
  const form = {querySelectorAll: () => inputs, getAttribute: () => '/settings/audit-log',
    addEventListener(event, handler) { submit = handler; }};
  const document = {
    querySelectorAll: selector => selector === 'form[data-audit-filters]' ? [form] : [],
    getElementById: id => id === 'audit-date-error' ? error : {},
  };
  const window = {location: {href: 'https://example.test/settings/audit-log', assign(url) { destination = new URL(url); }}};
  class FormData { constructor() { return inputs.map(input => [input.name, input.value]).concat([['object_type', 'team']]); } }
  vm.runInNewContext(source, {window, document, Date, Intl, URL, URLSearchParams, FormData});
  return {inputs, error, submit() { submit({preventDefault() {}}); return destination; },
    edit(index, value) { inputs[index].value = value; inputs[index].handlers.input(); }};
}
for (const zone of ['UTC', 'Europe/Amsterdam', 'America/New_York', 'Asia/Tokyo', 'Pacific/Kiritimati']) {
  const precise = '2026-10-25T01:30:59.123456+00:00';
  const form = filterForm(zone, {start: precise});
  const [start, end] = form.inputs;
  assert.equal(start.type, 'datetime-local');
  assert.equal(start.step, '60');
  assert.match(start.value, /^2026-10-(24|25)T\d{2}:30$/);
  assert.equal(end.value, '');
  let url = form.submit();
  assert.equal(url.searchParams.get('start'), precise);
  assert.equal(url.searchParams.get('object_type'), 'team');
  assert(!url.searchParams.has('end'));
  // Editing and returning to the same displayed minute deliberately chooses a new instant.
  form.edit(0, start.value);
  url = form.submit();
  assert.equal(url.searchParams.get('start'), new Date(start.value).toISOString());
  assert(url.searchParams.get('start').endsWith(':00.000Z'));
  form.edit(0, '');
  assert(!form.submit().searchParams.has('start'));
}
for (const value of ['2026-03-29T02:30', '2026-02-30T12:00', '2026-01-01T12:30:45', '2026-01-01T12:30:00.123']) {
  const form = filterForm('Europe/Amsterdam');
  form.edit(0, value);
  assert.equal(form.submit(), undefined);
  assert.equal(form.inputs[0]['aria-invalid'], 'true');
  assert.equal(form.error.hidden, false);
  assert(form.error.focused);
}
const fold = filterForm('Europe/Amsterdam', {start: '2026-10-25T01:30:59.123456+00:00'});
assert.equal(fold.inputs[0].value, '2026-10-25T02:30');
assert.equal(fold.submit().searchParams.get('start'), '2026-10-25T01:30:59.123456+00:00');
fold.edit(0, '2026-10-25T02:30');
assert.equal(fold.submit().searchParams.get('start'), '2026-10-25T00:30:00.000Z');
const gap = filterForm('Europe/Amsterdam');
gap.edit(0, '2026-03-29T03:00');
assert.equal(gap.submit().searchParams.get('start'), '2026-03-29T01:00:00.000Z');
const bad = filterForm('UTC');
bad.inputs[0].validity.badInput = true;
assert.equal(bad.submit(), undefined);
assert(bad.error.focused);
console.log('PASS: minute controls, untouched exact links, edited minutes, empty/invalid input, DST fold preservation and first occurrence, spring gap rejection');
