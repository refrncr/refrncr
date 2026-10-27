// Optional REF-149 browser smoke test against tests/browser_table_rows_app.py.
// Uses the existing external Playwright convention; no project dependency is added.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) {
  throw Error('Use the disposable loopback fixture');
}
(async () => {
  const browser = await chromium.launch({
    headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE,
  });
  try {
    const context = await browser.newContext({acceptDownloads: true});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    async function login(target, email) {
      await target.goto(base + '/login');
      await target.getByLabel('Email address').fill(email);
      await target.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
      await target.getByRole('button', {name: 'Sign in', exact: true}).click();
      await target.waitForURL(base + '/');
    }
    await login(page, 'admin@example.com');
    for (const [label, extension] of [['CSV', 'csv'], ['Excel', 'xlsx'], ['JSON', 'json']]) {
      await page.goto(base + '/datasets/row-check/records');
      await page.locator('.export-menu summary').click();
      const pending = page.waitForEvent('download');
      await page.getByRole('link', {name: label, exact: true}).click();
      const download = await pending;
      assert.equal(await download.failure(), null);
      assert(download.suggestedFilename().endsWith('.' + extension));
      const chunks = [];
      for await (const chunk of await download.createReadStream()) chunks.push(chunk);
      const bytes = Buffer.concat(chunks);
      assert(bytes.length > 0);
      if (extension === 'json') assert.equal(JSON.parse(bytes.toString('utf8')).length, 52);
      if (extension === 'csv') {
        assert.equal(bytes.subarray(0, 3).toString('hex'), 'efbbbf');
        assert(bytes.toString('utf8').includes('ITEM-51'));
      }
      if (extension === 'xlsx') assert.equal(bytes.subarray(0, 2).toString(), 'PK');
      console.log('PASS: authorized ' + label + ' browser download completed with expected content');
    }
    await page.goto(base + '/datasets/reporting-check/concept/records');
    const hierarchyExport = page.locator('.export-menu').filter({has: page.getByText('Export draft', {exact: true})});
    await hierarchyExport.locator('summary').click();
    const menu = hierarchyExport.locator('.export-menu-options');
    assert.match(await menu.innerText(), /Reporting table ›\s+Ready for BI tools and pivot tables/);
    assert.match(await menu.innerText(), /File for editing ›\s+Edit the hierarchy and import it again/);
    assert.equal(await menu.getByRole('link', {name: 'JSON', exact: true}).count(), 0);
    assert.equal((await menu.innerText()).includes('Copy API link'), false);
    const groups = menu.locator('.export-menu-group');
    for (const [groupIndex, label, filename] of [
      [0, 'Excel', 'reporting-check-concept-reporting.xlsx'],
      [0, 'CSV', 'reporting-check-concept-reporting.csv'],
      [1, 'Excel', 'reporting-check.xlsx'],
    ]) {
      const pending = page.waitForEvent('download');
      await groups.nth(groupIndex).getByRole('link', {name: label, exact: true}).click();
      const download = await pending;
      assert.equal(await download.failure(), null);
      assert.equal(download.suggestedFilename(), filename);
      await hierarchyExport.locator('summary').click();
    }
    await page.keyboard.press('Escape');
    assert.equal(await hierarchyExport.getAttribute('open'), null);
    await page.setViewportSize({width: 320, height: 900});
    await page.reload();
    await hierarchyExport.locator('summary').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    console.log('PASS: hierarchy export menu exposes reporting CSV/Excel and editable Excel only');
    const denied = await browser.newContext();
    const deniedPage = await denied.newPage();
    await login(deniedPage, 'row@example.com');
    await deniedPage.goto(base + '/datasets/row-check/records');
    assert.equal(await deniedPage.locator('.export-menu').count(), 0);
    const response = await denied.request.get(base + '/datasets/row-check/records/export?format=json');
    assert.equal(response.status(), 404);
    assert.deepEqual(errors, []);
    console.log('PASS: ordinary reader cannot export; server denies direct URL; no script errors');
  } finally {
    await browser.close();
  }
})();
