// Optional browser acceptance for the Access tab (REF-165).
// Start tests/browser_table_rows_app.py with fresh fixture data first.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
const output = process.env.REFRNCR_BROWSER_OUTPUT;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Use the disposable loopback fixture');
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  if (output) fs.mkdirSync(output, {recursive: true});
  const shot = async name => { if (output) await page.screenshot({path: `${output}/${name}.png`, fullPage: true}); };
  const tab = () => page.getByRole('tabpanel', {name: 'Access', exact: true});
  const select = () => tab().getByLabel('User or team', {exact: true});
  const grant = () => tab().getByRole('button', {name: 'Grant access', exact: true});
  const rows = () => tab().locator('.access-table tbody tr');
  const status = async text => {
    await page.waitForFunction(text => {
      const e = document.querySelector('[data-export-access] [data-panel-status]');
      return e && !e.hidden && e.textContent === text && e === document.activeElement;
    }, text);
  };
  const noOverflow = async () => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('Email address').fill('admin@example.com');
    await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    await page.waitForURL(`${base}/`);
    for (const width of [1440, 320]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(`${base}/datasets/row-check#access-management`);
      await tab().waitFor();
      const url = page.url();
      for (const heading of ['Who has access', 'Give export access', 'Additional export access']) {
        await tab().getByRole('heading', {name: heading, level: 3}).waitFor();
      }
      await tab().getByRole('heading', {name: 'No additional access yet', level: 4}).waitFor();
      await tab().getByRole('link', {name: 'Change owning team', exact: true}).waitFor();
      assert(await grant().isDisabled());
      await noOverflow(); await shot(`access-empty-${width}`);

      // Keyboard grant refreshes the tab in place and announces the result.
      await select().selectOption({label: 'Row user (row@example.com)'});
      await grant().focus(); await page.keyboard.press('Enter');
      await status('Export access granted.');
      assert.equal(page.url(), url);
      assert.equal(await rows().count(), 1);
      assert((await rows().first().textContent()).includes('row@example.com'));
      assert.equal(await rows().first().locator('time').count(), 1);

      // Duplicates keep the selection and move focus to the error summary.
      await select().selectOption({label: 'Row user (row@example.com)'}); await grant().click();
      await tab().locator('[data-error-focus]').waitFor();
      assert(await tab().locator('[data-error-focus]').evaluate(e => e === document.activeElement));
      assert.equal(await select().getAttribute('aria-invalid'), 'true');
      assert.match(await select().getAttribute('aria-describedby'), /export-subject-error/);
      assert.equal(await select().inputValue() !== '', true);
      await noOverflow(); await shot(`access-duplicate-${width}`);

      await select().selectOption({label: 'Row team'}); await grant().click();
      await status('Export access granted.');
      assert.equal(await rows().count(), 2);
      assert.deepEqual(await rows().locator('.badge').allTextContents(), ['User', 'Team']);
      await noOverflow(); await shot(`access-populated-${width}`);

      for (const name of ['Row user', 'Team Row team']) {
        await tab().getByRole('button', {name: `Remove export access for ${name}`, exact: true}).click();
        await status('Export access removed.');
      }
      await tab().getByRole('heading', {name: 'No additional access yet', level: 4}).waitFor();
      console.log(`PASS: ${width}px empty, grant, duplicate, team and revoke states`);
    }

    // Without JavaScript the forms post normally and errors render on the configuration page.
    const plain = await browser.newContext({javaScriptEnabled: false, storageState: await context.storageState()});
    const fallback = await plain.newPage();
    await fallback.goto(`${base}/datasets/row-check`);
    const plainGrant = fallback.locator('#access-management').getByRole('button', {name: 'Grant access', exact: true});
    assert(!(await plainGrant.isDisabled()));
    await plainGrant.click();
    await fallback.locator('#export-subject-error').waitFor();
    await fallback.locator('#access-management').getByLabel('User or team', {exact: true}).selectOption({label: 'Row team'});
    await fallback.locator('#access-management').getByRole('button', {name: 'Grant access', exact: true}).click();
    await fallback.waitForURL(`${base}/datasets/row-check#access-management`);
    await fallback.getByRole('button', {name: 'Remove export access for Team Row team', exact: true}).click();
    await fallback.waitForURL(`${base}/datasets/row-check#access-management`);
    await plain.close();
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('PASS: no-JavaScript grant, validation and revoke; no script errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
