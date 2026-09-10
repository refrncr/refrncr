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
console.log('PASS: six timezones, DST spring/fall, date boundary, six-digit precision, invalid calendar/unsupported Intl form fallback, duplicate guard');
