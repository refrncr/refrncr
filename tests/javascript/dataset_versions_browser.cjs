/* Real Chrome REF-83 checks; external Playwright, disposable loopback fixture. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5183';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Use the disposable loopback fixture');
const output = process.env.REFRNCR_BROWSER_OUTPUT;
const password = 'correct horse battery staple';
const results = [];

async function login(page, email) {
  await page.goto(`${base}/login`);
  await page.locator('#login-email').fill(email);
  await page.locator('#login-password').fill(password);
  await Promise.all([page.waitForURL(`${base}/`), page.getByRole('button', {name: 'Sign in', exact: true}).click()]);
}
async function noOverflow(page) {
  await page.locator('dialog[open]').evaluateAll(async dialogs => {
    await Promise.all(dialogs.flatMap(dialog => dialog.getAnimations({subtree: true}).map(animation => animation.finished)));
    for (const dialog of dialogs) {
      const bounds = dialog.getBoundingClientRect();
      if (bounds.left < 0 || bounds.right > window.innerWidth) throw new Error('dialog outside viewport');
    }
  });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'page overflow');
}
async function screenshot(page, name) {
  if (output) await page.screenshot({path: path.join(output, `${name}.png`), fullPage: true});
}
async function openPanel(page, opener, input) {
  await page.waitForLoadState('load');
  await opener.focus();
  await opener.press('Enter');
  await page.locator(input).waitFor({state: 'visible'});
  assert.equal(await page.evaluate(() => document.activeElement.id), input.slice(1));
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('Tab');
    assert(await page.evaluate(() => !!document.activeElement.closest('dialog[open]')), 'focus escaped dialog');
  }
}
async function closedWithFocus(page, opener) {
  await page.locator('dialog[open]').waitFor({state: 'hidden'});
  assert(await opener.evaluate(node => node === document.activeElement), 'focus not restored');
}

(async () => {
  if (output) await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  const scriptErrors = [];
  const observedContext = async options => {
    const context = await browser.newContext(options);
    context.on('page', page => page.on('pageerror', error => scriptErrors.push(error.message)));
    return context;
  };
  try {
    for (const width of [320, 768, 1440]) {
      const context = await observedContext({viewport: {width, height: 950}, colorScheme: 'light', timezoneId: 'Europe/Amsterdam'});
      const page = await context.newPage();
      await login(page, 'owner@example.com');
      const versionsURL = `${base}/datasets/publish-${width}/versions`;
      await page.goto(versionsURL);
      await page.getByRole('link', {name: 'Open draft', exact: true}).click();
      await page.getByRole('link', {name: 'Review & publish', exact: true}).click();
      const opener = page.getByRole('link', {name: 'Publish version', exact: true});
      await openPanel(page, opener, '#publication-date');
      await noOverflow(page);
      await screenshot(page, `publish-${width}`);
      await page.keyboard.press('Escape');
      await closedWithFocus(page, opener);
      await opener.click();
      await page.locator('#publication-date').waitFor({state: 'visible'});
      await page.locator('dialog[open]').getByRole('link', {name: 'Cancel', exact: true}).click();
      await closedWithFocus(page, opener);
      await page.getByRole('link', {name: 'Versions', exact: true}).click();
      assert.equal(await page.locator('table tbody tr').count(), 1, 'close mutated versions');
      assert.equal(await page.getByRole('link', {name: 'Open draft', exact: true}).count(), 1);
      assert.equal(await page.getByRole('link', {name: 'Version 1', exact: true}).count(), 0);
      await page.getByRole('link', {name: 'Open draft', exact: true}).click();
      await page.waitForLoadState('load');
      await page.getByRole('link', {name: 'Review & publish', exact: true}).click();
      await opener.click();
      await page.locator('#publication-date').waitFor({state: 'visible'});
      await page.locator('dialog[open]').getByRole('button', {name: 'Publish version', exact: true}).click();
      await page.locator('#publication-error').waitFor({state: 'visible'});
      assert.equal(await page.evaluate(() => document.activeElement.id), 'publication-error');
      assert.match(await page.locator('#publication-error').innerText(), /Confirm/);
      const date = await page.locator('#publication-date').inputValue();
      assert.equal(await page.locator('#publication-date').getAttribute('min'), date);
      await page.locator('#publication-date').fill('2000-01-01');
      await page.locator('#publication-confirmed').check();
      await page.locator('dialog[open]').getByRole('button', {name: 'Publish version', exact: true}).click();
      await page.locator('#publication-error').filter({hasText: 'Effective date must'}).waitFor();
      const tomorrow = new Date(Date.parse(`${date}T00:00:00Z`) + 86400000).toISOString().slice(0, 10);
      await page.locator('#publication-date').fill(tomorrow);
      const notePrefix = '<script>window.ref83Unsafe=true</script> Release note';
      assert.equal(await page.locator('#publication-note').getAttribute('maxlength'), '2000');
      await page.locator('#publication-note').fill(width === 1440 ? notePrefix + 'X'.repeat(2000 - notePrefix.length) : notePrefix);
      await page.locator('#publication-confirmed').check();
      let release;
      const held = new Promise(resolve => { release = resolve; });
      let posts = 0;
      await page.route(`**/datasets/publish-${width}/versions/publish`, async route => {
        if (route.request().method() === 'POST') { posts++; await held; }
        await route.continue();
      });
      const submit = page.locator('dialog[open]').getByRole('button', {name: 'Publish version', exact: true});
      await submit.click();
      await page.locator('#publication-form[aria-busy="true"]').waitFor();
      assert(await submit.isDisabled());
      assert.equal(await page.locator('#publication-form [data-submit-status]').innerText(), 'Publishing version…');
      await page.locator('#publication-form').evaluate(form => form.requestSubmit());
      await screenshot(page, `busy-${width}`);
      release();
      await page.waitForURL(`${versionsURL}?notice=published`);
      assert.equal(posts, 1, 'duplicate POST');
      await page.unroute(`**/datasets/publish-${width}/versions/publish`);
      assert.equal(await page.locator('table tbody tr').count(), 1);
      assert.match(await page.locator('table tbody').innerText(), /Scheduled/);
      assert.equal(await page.evaluate(() => window.ref83Unsafe), undefined);
      await noOverflow(page);
      await screenshot(page, `scheduled-${width}`);
      await page.getByRole('link', {name: 'Version 1', exact: true}).click();
      assert.match(await page.locator('body').innerText(), /Published value/);
      assert.equal(await page.getByRole('button', {name: 'Add record', exact: true}).count(), 0);
      assert.equal(await page.getByText('Export', {exact: true}).count(), 0, 'Scheduled export exposed');
      await page.getByRole('link', {name: 'Versions', exact: true}).click();
      const cancel = page.getByRole('link', {name: 'Cancel Version 1', exact: true});
      await openPanel(page, cancel, '#cancellation-confirmed');
      assert.match(await page.locator('dialog[open]').innerText(), /number will never be reused/);
      await noOverflow(page);
      await screenshot(page, `cancel-${width}`);
      await page.keyboard.press('Escape');
      await closedWithFocus(page, cancel);
      await cancel.click();
      await page.locator('#cancellation-confirmed').check();
      await page.locator('dialog[open]').getByRole('button', {name: 'Cancel version', exact: true}).click();
      await page.waitForURL(`${versionsURL}?notice=cancelled`);
      assert.match(await page.locator('table tbody').innerText(), /Cancelled/);
      assert.equal(await page.getByRole('link', {name: 'Version 1', exact: true}).count(), 0);
      await page.getByRole('button', {name: 'Create draft', exact: true}).click();
      await page.waitForLoadState('load');
      await page.getByRole('link', {name: 'Review & publish', exact: true}).click();
      await page.getByRole('link', {name: 'Publish version', exact: true}).click();
      await page.locator('#publication-date').waitFor({state: 'visible'});
      assert.match(await page.locator('dialog[open]').innerText(), /Version 2/);
      await page.keyboard.press('Escape');
      await closedWithFocus(page, page.getByRole('link', {name: 'Publish version', exact: true}));
      results.push({viewport: width, scenarios: ['publish panel', 'focus containment/restoration', 'Escape/Cancel', 'validation focus', 'past date denied', 'busy/double submit', 'escaping', 'Scheduled read-only', 'cancel', 'number retained', 'no overflow']});
      await context.close();
    }
    const readerContext = await observedContext({viewport: {width: 768, height: 950}, colorScheme: 'light', timezoneId: 'America/Los_Angeles'});
    const reader = await readerContext.newPage();
    await login(reader, 'reader@example.com');
    await reader.goto(`${base}/datasets`);
    assert(!/Private future|Publication 320|Unpublished history name/.test(await reader.locator('body').innerText()));
    await reader.getByRole('link', {name: 'History demo', exact: true}).click();
    assert.equal(reader.url(), `${base}/datasets/history/records`);
    assert.match(await reader.locator('body').innerText(), /Second published value/);
    assert(!/DRAFT ONLY|Add record|Export/.test(await reader.locator('main').innerText()));
    await reader.getByRole('link', {name: 'Versions', exact: true}).click();
    assert.match(await reader.locator('table tbody').innerText(), /Expired/);
    await reader.getByRole('link', {name: 'Version 1', exact: true}).click();
    assert.match(await reader.locator('body').innerText(), /Published value/);
    assert(!/Second published value|DRAFT ONLY/.test(await reader.locator('body').innerText()));
    await reader.getByRole('button', {name: /^View record FIRST /}).click();
    await reader.locator('dialog[open]').waitFor();
    assert.equal(await reader.locator('dialog[open] form').count(), 0);
    await reader.keyboard.press('Escape');
    await noOverflow(reader);
    await screenshot(reader, 'expired-reader-768');
    results.push({viewport: 768, scenarios: ['ordinary Current only', 'no concept leak', 'Expired snapshot', 'read-only record panel', 'no export grant']});
    await readerContext.close();

    const exportContext = await observedContext({viewport: {width: 1440, height: 950}, colorScheme: 'light', acceptDownloads: true});
    const exporter = await exportContext.newPage();
    await login(exporter, 'exporter@example.com');
    for (const version of [2, 1]) {
      await exporter.goto(`${base}/datasets/history/versions`);
      await exporter.getByRole('link', {name: `Version ${version}`, exact: true}).click();
      for (const [label, extension] of [['CSV', 'csv'], ['Excel', 'xlsx'], ['JSON', 'json']]) {
        await exporter.locator('.export-menu summary').click();
        const promise = exporter.waitForEvent('download');
        await exporter.getByRole('link', {name: label, exact: true}).click();
        const download = await promise;
        assert.equal(download.suggestedFilename(), `history-v${version}.${extension}`);
        assert.equal(await download.failure(), null);
        const bytes = await fs.readFile(await download.path());
        if (extension !== 'xlsx') {
          assert(bytes.toString('utf8').includes(version === 2 ? 'Second published value' : 'Published value'));
          assert(!bytes.toString('utf8').includes('DRAFT ONLY'));
        } else assert.equal(bytes.subarray(0, 2).toString(), 'PK');
      }
    }
    results.push({viewport: 1440, scenarios: ['Current CSV/Excel/JSON downloads', 'Expired CSV/Excel/JSON downloads', 'version filenames']});
    await exportContext.close();

    const importContext = await observedContext({viewport: {width: 320, height: 950}, colorScheme: 'light'});
    const importer = await importContext.newPage();
    await login(importer, 'owner@example.com');
    await importer.goto(`${base}/datasets/future-only/versions`);
    await importer.getByRole('button', {name: 'Create draft', exact: true}).click();
    await importer.goto(`${base}/datasets/future-only/records/import`);
    await importer.locator('#import-file').setInputFiles({name: 'records.csv', mimeType: 'text/csv', buffer: Buffer.from('code,label\nIMPORTED,Concept import only\n')});
    await importer.getByRole('button', {name: 'Continue', exact: true}).click();
    await importer.locator('[data-import-mapping]').waitFor();
    await importer.getByRole('button', {name: 'Continue to preview', exact: true}).click();
    const deletedRow = importer.getByRole('row').filter({hasText: 'Deleted'});
    await deletedRow.locator('td').first().click();
    const deletedPanel = importer.locator('dialog[open]');
    await deletedPanel.waitFor();
    assert.match(await deletedPanel.innerText(), /This record will be marked as deleted\./);
    assert.equal(await deletedPanel.getByText('Current', {exact: true}).count(), 0);
    assert.equal(await deletedPanel.getByText('New', {exact: true}).count(), 0);
    assert(await deletedPanel.locator('.record-details > div').count() >= 2);
    await importer.keyboard.press('Escape');
    await importer.getByRole('checkbox', {name: 'I understand that 1 Records will be marked as deleted.'}).check();
    await importer.getByRole('button', {name: 'Import records', exact: true}).click();
    await importer.waitForURL(/import_results=1/);
    assert.match(await importer.locator('main').innerText(), /Added: 1.*Deleted: 1/s);
    assert.match(await importer.locator('main').innerText(), /Concept import only/);
    await importer.getByRole('link', {name: 'Versions', exact: true}).click();
    await importer.getByRole('link', {name: 'Version 1', exact: true}).click();
    assert.match(await importer.locator('main').innerText(), /Published value/);
    assert(!/Concept import only|IMPORTED/.test(await importer.locator('main').innerText()));
    await noOverflow(importer);
    results.push({viewport: 320, scenarios: ['CSV import mapping/preview/confirmation', 'concept-only import', 'Scheduled snapshot unchanged']});
    await importContext.close();

    const nojsContext = await observedContext({viewport: {width: 320, height: 950}, javaScriptEnabled: false, colorScheme: 'light'});
    const nojs = await nojsContext.newPage();
    await login(nojs, 'owner@example.com');
    await nojs.goto(`${base}/datasets/publish-nojs/versions`);
    await nojs.getByRole('link', {name: 'Open draft', exact: true}).click();
    await nojs.getByRole('link', {name: 'Review & publish', exact: true}).click();
    await nojs.getByRole('link', {name: 'Publish version', exact: true}).click();
    await nojs.getByRole('button', {name: 'Publish version', exact: true}).click();
    assert.match(await nojs.locator('#publication-error').innerText(), /Confirm/);
    await nojs.locator('#publication-confirmed').check();
    await Promise.all([nojs.waitForURL('**/versions?notice=published'), nojs.getByRole('button', {name: 'Publish version', exact: true}).click()]);
    assert.match(await nojs.locator('table tbody').innerText(), /Current/);
    await noOverflow(nojs);
    results.push({viewport: 320, scenarios: ['no-JavaScript full-page publication', 'server validation', 'Current status']});
    await nojsContext.close();
    assert.deepEqual(scriptErrors, [], 'Unexpected page JavaScript errors');
    const report = {browser: await browser.version(), base, results};
    if (output) await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
