// Checks audit-history filters, entries, responsive layout and light/dark presentation.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const screenshotPath = name => path.join(process.env.REFRNCR_SCREENSHOT_DIR || os.tmpdir(), name);
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5145';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) {
  throw Error('Use the disposable loopback fixture');
}
(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    console.log('Chrome ' + browser.version());
    for (const timezoneId of ['Europe/Amsterdam', 'UTC', 'America/New_York', 'Asia/Tokyo', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
      const context = await browser.newContext({timezoneId, locale: 'en-GB', colorScheme: 'light'});
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => { errors.push(e.message); console.error(e.message); });
      await page.goto(base + '/login');
      await page.getByLabel('Email address').fill('admin@example.com');
      await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
      await Promise.all([
        page.waitForURL(base + '/'),
        page.getByRole('button', {name: 'Sign in', exact: true}).click(),
      ]);
      await page.goto(base + '/settings/audit-log?object_type=dataset_record');
      assert.equal(await page.locator('tbody tr:has(td)').count(), 50);
      assert.equal(await page.locator('tbody tr td:nth-child(5)').first().textContent(), 'No name to display');
      const utc = await page.locator('tbody time').first().getAttribute('datetime');
      const local = await page.locator('tbody time').first().textContent();
      // Rows show the local time to the minute; the exact instant stays in the title.
      const exact = await page.locator('tbody time').first().getAttribute('title');
      assert(exact.includes('123456') && exact.includes('GMT') && local !== utc + ' UTC');
      assert.match(local, /^\d{2}:\d{2}$/);
      assert(await page.locator('tbody tr.table-day-row th[scope="rowgroup"]').count() >= 1);
      assert.equal(await page.locator('thead [data-local-timezone]').textContent(), `(${timezoneId})`);
      const date = new Date(utc);
      const expected = new Intl.DateTimeFormat('en-GB', {timeZone: timezoneId,
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(date);
      assert(local.includes(expected), `${timezoneId}: ${local} vs ${expected}`);
      const links = page.locator('tbody a[data-panel-url]');
      const names = await links.allTextContents();
      assert.equal(new Set(names).size, 50, 'Accessible names remain unique for timestamp/action ties');
      assert.equal(await page.locator('table caption').textContent(), 'Installation audit history, newest events first');
      for (const index of [0, 1, 49]) {
        console.log(`Checking ${timezoneId} row ${index}`);
        const target = links.nth(index);
        const href = await target.getAttribute('href');
        await target.focus();
        await page.keyboard.press('Enter');
        const dialog = page.getByRole('dialog', {name: 'Audit event details'});
        try { await dialog.waitFor({state: 'visible'}); }
        catch (error) {
          console.error('URL', page.url(), 'dialogs', await page.locator('dialog').evaluateAll(ds => ds.map(d => ({open: d.open, text: d.textContent}))));
          throw error;
        }
        await dialog.locator('dl').first().waitFor();
        assert.equal(await dialog.locator('[data-initial-focus]').evaluate(e => document.activeElement === e), true);
        assert.equal(await dialog.getByRole('region', {name: 'Additional details'}).count(), 1);
        const text = await dialog.textContent();
        const expectedObject = (BigInt('0x' + href.split('/').pop().replaceAll('-', '')) + 1000n)
          .toString(16).padStart(32, '0').replace(/(.{8})(.{4})(.{4})(.{4})(.{12})/, '$1-$2-$3-$4-$5');
        assert(text.includes(expectedObject), 'Each row loads its own object detail');
        assert.equal(await dialog.locator('dt').filter({hasText: /^Object$/}).evaluate(e => e.nextElementSibling.textContent), 'No name to display');
        assert(text.includes('updated') && text.includes('dataset_record'));
        assert(text.includes('No additional details') && !text.includes('secret-business-value'));
        assert(await dialog.evaluate(d => d.contains(document.activeElement)));
        for (let n = 0; n < 8; n++) {
          await page.keyboard.press('Tab');
          assert(await dialog.evaluate(d => d.contains(document.activeElement)));
        }
        await page.keyboard.press('Shift+Tab');
        assert(await dialog.evaluate(d => d.contains(document.activeElement)));
        // Native focus restoration precedes the queued close event. Wait for the
        // controller's close handler before focusing and opening the next row.
        await dialog.evaluate(d => {
          window.auditTestPanelClosed = new Promise(resolve => d.addEventListener('close', () => resolve(), {once: true}));
        });
        if (index === 1) await dialog.getByRole('link', {name: 'Close', exact: true}).click();
        else if (index === 49) await dialog.getByRole('button', {name: 'Close panel', exact: true}).click();
        else await page.keyboard.press('Escape');
        await dialog.waitFor({state: 'hidden'});
        await page.evaluate(() => window.auditTestPanelClosed);
        await page.waitForFunction(href => document.activeElement.getAttribute('href') === href, href);
        assert.equal(await page.evaluate(() => document.activeElement.getAttribute('href')), href);
      }
      await links.nth(0).click();
      await page.getByRole('dialog', {name: 'Audit event details'}).locator('dl').first().waitFor();
      await page.evaluate(() => {
        document.querySelector('dialog[data-modal-side-panel]').close();
        document.querySelectorAll('tbody a[data-panel-url]')[1].click();
      });
      await page.getByRole('dialog', {name: 'Audit event details'}).locator('dl').first().waitFor();
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.querySelector('dialog[data-modal-side-panel]').open);
      const firstIds = await links.evaluateAll(es => es.map(e => e.getAttribute('href')));
      await Promise.all([
        page.waitForNavigation({waitUntil: 'load'}),
        page.getByRole('link', {name: 'Older events', exact: true}).click(),
      ]);
      assert(new URL(page.url()).searchParams.get('cursor'));
      assert.equal(new URL(page.url()).searchParams.get('object_type'), 'dataset_record');
      const secondIds = await page.locator('tbody a[data-panel-url]').evaluateAll(es => es.map(e => e.getAttribute('href')));
      assert(secondIds.every(id => !firstIds.includes(id)));
      const secondURL = page.url();
      await page.reload();
      assert.equal(page.url(), secondURL);
      await page.goBack();
      assert.equal(await page.locator('tbody a[data-panel-url]').first().getAttribute('href'), firstIds[0]);
      await page.goForward();
      assert.equal(page.url(), secondURL);
      await Promise.all([
        page.waitForNavigation({waitUntil: 'load'}),
        page.getByRole('link', {name: 'Clear filters', exact: true}).click(),
      ]);
      assert.equal(new URL(page.url()).search, '');
      if (timezoneId === 'Europe/Amsterdam') {
        await page.getByLabel('Start date and time').fill('2026-03-29T02:30');
        await page.getByRole('button', {name: 'Apply filters'}).click();
        assert(await page.locator('#audit-date-error').isVisible());
        assert.equal(new URL(page.url()).search, '');
        await page.getByLabel('Start date and time').fill('2026-03-29T03:00');
        await Promise.all([
          page.waitForURL(url => url.searchParams.has('start')),
          page.getByRole('button', {name: 'Apply filters'}).click(),
        ]);
        assert.equal(new Date(new URL(page.url()).searchParams.get('start')).toISOString(), '2026-03-29T01:00:00.000Z');
        assert.equal(await page.getByLabel('Start date and time').inputValue(), '2026-03-29T03:00');
        await page.screenshot({path: screenshotPath('ref39-audit-list.png'), animations: 'disabled'});
        await page.locator('tbody a[data-panel-url]').first().click();
        await page.getByRole('dialog', {name: 'Audit event details'}).locator('dl').first().waitFor();
        await page.screenshot({path: screenshotPath('ref39-audit-detail.png'), animations: 'disabled'});
        await page.keyboard.press('Escape');
        await page.setViewportSize({width: 390, height: 844});
        await page.screenshot({path: screenshotPath('ref39-audit-mobile.png'), animations: 'disabled'});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.setViewportSize({width: 1280, height: 960});
        await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
        await page.screenshot({path: screenshotPath('ref39-audit-zoom.png'), animations: 'disabled'});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.evaluate(() => { document.documentElement.style.zoom = ''; });
        const light = await page.locator('body').evaluate(e => getComputedStyle(e).backgroundColor);
        await page.emulateMedia({colorScheme: 'dark'});
        assert.notEqual(await page.locator('body').evaluate(e => getComputedStyle(e).backgroundColor), 'rgba(0, 0, 0, 0)');
        await page.screenshot({path: screenshotPath('ref39-audit-dark-preference.png'), animations: 'disabled'});
      }
      // Inject only a test DOM node; fallback must survive malformed timestamps.
      assert.equal(await page.evaluate(() => {
        const t = document.createElement('time');
        t.dataset.localTime = ''; t.dateTime = 'invalid'; t.textContent = 'UTC fallback';
        document.body.append(t); window.refrncrLocalTime(document); const text = t.textContent; t.remove(); return text;
      }), 'UTC fallback');
      const original = await page.evaluate(() => {
        window.auditTestOriginal = window.refrncrLocalTime;
        return document.refrncrInitialized;
      });
      await page.addScriptTag({url: base + '/static/js/audit-time.js'});
      await page.addScriptTag({url: base + '/static/js/refrncr.js'});
      assert(original);
      assert(await page.evaluate(() => window.auditTestOriginal === window.refrncrLocalTime));
      if (timezoneId === 'Europe/Amsterdam') {
        await page.goto(base + '/settings/audit-log?start=2026-10-25T01%3A30%3A00.123456Z');
        assert.equal(await page.getByLabel('Start date and time').inputValue(), '2026-10-25T02:30');
        await Promise.all([
          page.waitForNavigation({waitUntil: 'load'}),
          page.getByRole('button', {name: 'Apply filters'}).click(),
        ]);
        assert.equal(new URL(page.url()).searchParams.get('start'), '2026-10-25T01:30:00.123456+00:00');
      }
      assert.deepEqual(errors, []);
      console.log(`PASS ${timezoneId}: time, 50 rows, modal keyboard/focus, privacy, pagination/history, filters, script guards`);
      const noJS = await browser.newContext({storageState: await context.storageState(), javaScriptEnabled: false});
      const fallback = await noJS.newPage();
      await fallback.goto(base + '/settings/audit-log?object_type=dataset_record');
      assert((await fallback.locator('tbody time').first().textContent()).match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$/));
      await Promise.all([
        fallback.waitForNavigation({waitUntil: 'load'}),
        fallback.locator('tbody a[data-panel-url]').first().click(),
      ]);
      assert((await fallback.locator('main').textContent()).includes('Exact UTC timestamp'));
      await noJS.close();
      if (timezoneId === 'Europe/Amsterdam') {
        await page.setViewportSize({width: 1280, height: 960});
        await page.goto(base + '/settings/audit-log?action=dataset_record%3Adeleted');
        assert.equal(await page.locator('tbody tr:has(td)').count(), 1);
        assert.equal(await page.locator('tbody tr td:nth-child(5)').textContent(), 'Deleted object');
        await page.locator('tbody a[data-panel-url]').click();
        const deletedDialog = page.getByRole('dialog', {name: 'Audit event details'});
        await deletedDialog.locator('dl').first().waitFor();
        assert.equal(await deletedDialog.locator('dt').filter({hasText: /^Object$/}).evaluate(e => e.nextElementSibling.textContent), 'Deleted object');
        assert((await deletedDialog.textContent()).includes('00000000-0000-0000-0000-000000001770'));
        await page.screenshot({path: screenshotPath('ref39-audit-deleted-record.png'), animations: 'disabled'});
        await page.keyboard.press('Escape');
        for (const status of [403, 503]) {
          await page.goto(base + '/settings/audit-log?object_type=dataset_record');
          const target = page.locator('tbody a[data-panel-url]').first();
          const detailURL = base + await target.getAttribute('href');
          await page.route(detailURL, route => route.fulfill({status, contentType: 'text/html',
            body: '<main><h1>Audit unavailable</h1></main>'}));
          await Promise.all([
            page.waitForURL(detailURL),
            target.click(),
          ]);
          assert.equal(await page.getByRole('heading', {name: 'Audit unavailable'}).count(), 1);
          assert.equal(await page.locator('dialog[open]').count(), 0);
          await page.unroute(detailURL);
        }
        const unsupported = await browser.newContext({storageState: await context.storageState()});
        await unsupported.addInitScript(() => { Intl.DateTimeFormat = undefined; });
        const plain = await unsupported.newPage();
        await plain.goto(base + '/settings/audit-log');
        assert.equal(await plain.getByLabel('Start date and time').getAttribute('type'), 'text');
        await plain.getByLabel('Start date and time').fill('2026-03-29T01:00:00Z');
        await Promise.all([
          plain.waitForURL(url => url.searchParams.has('start')),
          plain.getByRole('button', {name: 'Apply filters'}).click(),
        ]);
        assert.equal(await plain.locator('tbody tr').count(), 50);
        await unsupported.close();
        await page.goto(base + '/settings/audit-log');
        await context.clearCookies();
        await Promise.all([
          page.waitForURL(url => url.pathname === '/login'),
          page.locator('tbody a[data-panel-url]').first().click(),
        ]);
        assert.equal(await page.getByRole('button', {name: 'Sign in', exact: true}).count(), 1);
        console.log('PASS: 403/503 panel fallback, lost session, unsupported Intl native ISO form, light under dark preference, small viewport and 200% CSS zoom');
      }
      await context.close();
    }
    console.log('PASS no-JavaScript full-page details and exact UTC fallback');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
