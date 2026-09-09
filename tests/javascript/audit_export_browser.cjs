// Optional REF-149 browser smoke test against tests/browser_table_rows_app.py.
// Uses the existing external Playwright convention; no project dependency is added.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
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
