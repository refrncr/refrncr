/* REF-136 real Chrome acceptance; run against browser_record_search_app.py. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5136';
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname)) throw Error('Loopback only');
const output = process.env.REFRNCR_BROWSER_OUTPUT;
const measurements = [];
async function navigate(page, action) {
  await Promise.all([page.waitForNavigation(), action()]);
}
async function evidence(page, name) {
  const result = await page.evaluate(() => ({
    viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth,
    controls: [...document.querySelectorAll('[data-record-criteria] input:not([type=hidden]), [data-record-criteria] select, [data-record-criteria] button')]
      .filter(el => el.getClientRects().length).map(el => {const box = el.getBoundingClientRect(); return {id: el.id, left: box.left, right: box.right};}),
  }));
  assert(result.scrollWidth <= result.viewport, JSON.stringify({name, ...result}));
  for (const box of result.controls) assert(box.left >= 0 && box.right <= result.viewport, JSON.stringify(box));
  measurements.push({name, ...result});
  if (output) await page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
}
(async () => {
  if (output) await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    for (const width of [320, 768, 1440, 'nojs']) {
      const context = await browser.newContext({viewport: {width: width === 'nojs' ? 320 : width, height: 950}, javaScriptEnabled: width !== 'nojs'});
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/login');
      await page.getByLabel('Email address').fill('admin@example.com');
      await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
      await navigate(page, () => page.getByRole('button', {name: 'Sign in', exact: true}).click());
      const records = base + `/datasets/search-${width}/concept/records`;
      await page.goto(records);
      const statusGroup = page.getByRole('group', {name: 'Record status', exact: true});
      const toggle = name => statusGroup.getByRole('button', {name: new RegExp(`^${name} \\d+$`)});
      assert.equal(await toggle('Active').getAttribute('aria-pressed'), 'true');
      assert.equal(await toggle('Active').getAttribute('aria-disabled'), 'true');
      assert.equal(await toggle('Deleted').getAttribute('aria-pressed'), 'false');
      assert.match(await page.locator('#record-result-count').textContent(), /of 65/);
      await page.getByRole('searchbox', {name: 'Search records', exact: true}).fill('AMS');
      await navigate(page, () => page.locator('#record-search').press('Enter'));
      assert.match(await page.locator('#record-result-count').textContent(), /of 59/);
      // Toggle counts follow the applied search term.
      assert.equal((await toggle('Active').textContent()).replace(/\s+/g, ' ').trim(), 'Active 59');
      assert.equal(await page.locator('.record-table tbody tr').count(), 50);
      await navigate(page, () => page.getByRole('link', {name: 'Next', exact: true}).click());
      const second = page.url();
      assert.equal(await page.locator('.record-table tbody tr').count(), 9);
      await page.reload();
      assert.equal(page.url(), second);
      await page.goBack();
      assert.equal(await page.locator('.record-table tbody tr').count(), 50);
      await page.goForward();
      assert.equal(page.url(), second);
      await page.locator('#record-search').fill('Rotterdam');
      // Switching Deleted on while Active stays on shows all Records.
      if (width === 'nojs') {
        await navigate(page, () => toggle('Deleted').click());
      } else {
        await toggle('Deleted').focus();
        await navigate(page, () => page.keyboard.press('Enter'));
      }
      assert.equal(new URL(page.url()).searchParams.get('record_status'), 'all');
      assert.equal(await toggle('Active').getAttribute('aria-pressed'), 'true');
      assert.equal(await toggle('Deleted').getAttribute('aria-pressed'), 'true');
      assert.equal(await toggle('Active').getAttribute('aria-disabled'), null);
      if (width !== 'nojs') {
        assert.equal(await page.evaluate(() => !!document.activeElement.closest('[data-record-status-form]')), true);
      }
      assert.equal(new URL(page.url()).searchParams.get('q'), 'AMS');
      assert(!new URL(page.url()).searchParams.has('page'));
      assert.match(await page.locator('#record-result-count').textContent(), /of 60/);
      const shared = await context.newPage();
      await shared.goto(page.url());
      assert.match(await shared.locator('#record-result-count').textContent(), /of 60/);
      await shared.close();
      assert.equal(await page.getByText('Add filter', {exact: true}).count(), 0);
      await evidence(page, width + '-search');
      // The clear control is always visible for an applied term; it resets search and status.
      const clearSearch = page.getByRole('link', {name: 'Clear search', exact: true});
      assert(await clearSearch.isVisible());
      assert.equal(await page.getByText('Clear all', {exact: true}).count(), 0);
      await navigate(page, () => clearSearch.click());
      assert.equal(new URL(page.url()).search, '');
      assert.equal(await page.locator('#record-search').inputValue(), '');
      assert(!(await clearSearch.isVisible()));
      assert.match(await page.locator('#record-result-count').textContent(), /of 65/);
      if (width !== 'nojs') {
        // Typed but not applied text is cleared in place, without a page load.
        await page.locator('#record-search').fill('draft');
        assert(await clearSearch.isVisible());
        const before = page.url();
        await clearSearch.click();
        assert.equal(page.url(), before);
        assert.equal(await page.locator('#record-search').inputValue(), '');
        assert(!(await clearSearch.isVisible()));
        assert.equal(await page.evaluate(() => document.activeElement.id), 'record-search');
      }
      if (width !== 'nojs') {
        await page.getByRole('button', {name: /View record id-000/}).focus();
        await page.keyboard.press('Enter');
        assert(await page.getByRole('dialog').isVisible());
        await page.keyboard.press('Escape');
        assert.match(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), /View record id-000/);
      }
      await page.goto(base + `/datasets/search-${width}/records?q=AMS`);
      assert.match(await page.locator('#record-result-count').textContent(), /of 60/);
      assert.equal(await page.locator('[data-record-status-form]').count(), 0);
      await evidence(page, width + '-current');
      const next = await page.getByRole('link', {name: 'Next', exact: true}).getAttribute('href');
      assert.match(next, /\/versions\/[^/]+\/records\?/);
      assert.equal(new URL(next, base).searchParams.get('q'), 'AMS');
      await page.goto(base + `/datasets/search-${width}/records?q=AMS&page=999`);
      assert(await page.getByRole('heading', {name: 'No records on this page'}).isVisible());
      assert.match(await page.locator('#record-result-count').textContent(), /Showing 0–0 of 60/);
      await navigate(page, () => page.getByRole('link', {name: 'First page', exact: true}).click());
      assert.equal(await page.locator('.record-table tbody tr').count(), 50);
      assert.equal(new URL(page.url()).searchParams.get('q'), 'AMS');
      assert.deepEqual(errors, []);
      await context.close();
    }
    const result = {browser: browser.version(), measurements};
    console.log(JSON.stringify(result, null, 2));
    if (output) await fs.writeFile(path.join(output, 'measurements.json'), JSON.stringify(result, null, 2));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
