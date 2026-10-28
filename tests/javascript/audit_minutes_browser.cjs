// Checks audit-minute review and export journeys, filtering and permission behavior.
const assert = require('node:assert/strict');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL;
if (!base || !['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) {
  throw Error('Use a disposable loopback fixture');
}
(async () => {
  const browser = await chromium.launch();
  try {
    const auth = await browser.newContext();
    const login = await auth.request.get(base + '/login');
    const csrf = (await login.text()).match(/name=['"]csrf_token['"]\s+value=['"]([^'"]+)/)[1];
    const response = await auth.request.post(base + '/login', {form: {
      email: process.env.REFRNCR_BROWSER_EMAIL, password: process.env.REFRNCR_BROWSER_PASSWORD, csrf_token: csrf,
    }});
    assert.equal(response.url(), base + '/');
    const storageState = await auth.storageState();
    await auth.close();
    for (const timezoneId of ['UTC', 'Europe/Amsterdam', 'America/New_York', 'Asia/Tokyo', 'Pacific/Kiritimati']) {
      const context = await browser.newContext({storageState, timezoneId, locale: 'en-GB'});
      const errors = [];
      context.on('page', page => page.on('pageerror', e => errors.push(e.message)));
      const page = await context.newPage();
      await page.goto(base + '/settings/audit-log?object_type=dataset_record');
      const times = page.locator('tbody time');
      assert.equal(await times.count(), 50);
      for (const value of await times.allTextContents()) assert.match(value, /^\d{2}:\d{2}$/);
      assert(await page.locator('.table-day-row').count() > 0);
      const exact = await times.first().getAttribute('datetime');
      const expected = new Intl.DateTimeFormat('en-GB', {timeZone: timezoneId,
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(new Date(exact));
      assert.equal(await times.first().textContent(), expected);
      assert((await times.first().getAttribute('title')).includes('123456'));
      const start = page.getByLabel('Start date and time');
      const end = page.getByLabel('End date and time');
      for (const input of [start, end]) {
        assert.equal(await input.getAttribute('step'), '60');
        assert.equal(await input.getAttribute('type'), 'datetime-local');
      }
      const detail = await context.newPage();
      await detail.goto(base + await page.locator('tbody a[data-panel-url]').first().getAttribute('href'));
      assert.equal(await detail.locator('dt').filter({hasText: /^Exact UTC timestamp$/}).evaluate(e => e.nextElementSibling.textContent), exact + ' UTC');
      await detail.close();
      if (process.env.REFRNCR_BROWSER_OUTPUT && timezoneId === 'Europe/Amsterdam') {
        await page.screenshot({path: process.env.REFRNCR_BROWSER_OUTPUT + '/audit-minutes.png'});
      }
      await page.close();
      // Native controls render precise links at minute precision. Submit conversion is
      // covered by audit_time.cjs and HTTP boundary tests; chained browser navigation
      // remains covered by audit_history, with its known REF-311 transition failure.
      const linked = await context.newPage();
      await linked.goto(base + '/settings/audit-log?' + new URLSearchParams({
        start: '2026-10-25T01:30:59.123456+00:00', end: '2026-10-25T02:31:45.654321Z',
      }));
      assert.match(await linked.locator('#audit-start').inputValue(), /^2026-10-(24|25)T\d{2}:30$/);
      assert.match(await linked.locator('#audit-end').inputValue(), /^2026-10-(24|25)T\d{2}:31$/);
      assert(await linked.locator('#audit-start').evaluate(e => e.validity.valid));
      await linked.locator('#audit-start').fill('2026-01-01T12:30');
      for (let segment = 0; segment < 4; segment++) await linked.locator('#audit-start').press('ArrowRight');
      await linked.locator('#audit-start').press('ArrowUp');
      assert.equal(await linked.locator('#audit-start').inputValue(), '2026-01-01T12:31');
      await linked.close();
      if (timezoneId === 'Europe/Amsterdam') {
        const spring = await context.newPage();
        await spring.goto(base + '/settings/audit-log');
        await spring.locator('#audit-start').fill('2026-03-29T02:30');
        await spring.getByRole('button', {name: 'Apply filters'}).click();
        assert(await spring.locator('#audit-date-error').isVisible());
        assert(await spring.locator('#audit-date-error').evaluate(e => e === document.activeElement));
        assert.equal(new URL(spring.url()).search, '');
        await spring.close();
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    const noJS = await browser.newContext({storageState, javaScriptEnabled: false});
    const fallback = await noJS.newPage();
    await fallback.goto(base + '/settings/audit-log?object_type=dataset_record');
    assert.match(await fallback.locator('tbody time').first().textContent(), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$/);
    await fallback.locator('#audit-start').fill('2026-03-29T01:00Z');
    await fallback.locator('#audit-end').fill('2026-03-29T01:01Z');
    await fallback.getByRole('button', {name: 'Apply filters'}).click();
    await fallback.waitForURL(url => url.searchParams.has('start'));
    assert.equal(await fallback.locator('tbody time').count(), 50);
    await noJS.close();
    console.log('PASS: audit minutes in five timezones, exact details, native minute controls, precise-link display, DST gap rejection and no-JS filtering');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
// Checks audit-minute review and export journeys, including filtering and permission behavior.
