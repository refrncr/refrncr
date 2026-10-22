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
  const select = () => tab().getByLabel('User or team', {exact: true}).locator('xpath=self::select | following-sibling::select[1]');
  const grant = () => tab().getByRole('button', {name: 'Grant export access', exact: true});
  const trigger = () => tab().locator('#export-subject-trigger');
  const rows = () => tab().locator('.access-table tbody tr[data-access-entry]');
  const status = async (text, focusSelector = '[data-panel-status]') => {
    await page.waitForFunction(({text, focusSelector}) => {
      const e = document.querySelector('[data-export-access] [data-panel-status]');
      const focus = document.querySelector(`[data-export-access] ${focusSelector}`);
      return e && !e.hidden && e.getAttribute('role') === 'status' && e.textContent === text && focus === document.activeElement;
    }, {text, focusSelector}, {timeout: 10000});
  };
  const noOverflow = async () => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('Email address').fill('admin@example.com');
    await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    await page.waitForURL(`${base}/`);
    for (const colorScheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme});
      for (const width of [1440, 375]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(`${base}/datasets/row-check#access-management`);
      await tab().waitFor();
      const url = page.url();
      await tab().getByRole('heading', {name: 'Who has access', level: 3}).waitFor();
      await tab().getByRole('table', {name: 'Dataset access'}).waitFor();
      await tab().getByText('Only the owning team has access.', {exact: false}).waitFor();
      await tab().getByRole('link', {name: 'Change owning team', exact: true}).waitFor();
      assert.equal(await tab().locator('.access-table tbody tr').count(), 1);
      assert.match(await tab().locator('.access-owner-row').textContent(), /Owning team/);
      assert(await grant().isDisabled());
      assert(await trigger().evaluate(el => el.tabIndex === 0));
      assert(await grant().evaluate(el => el.tabIndex === 0));
      assert(await trigger().locator('.select-value').evaluate(el => el.scrollWidth <= el.clientWidth), 'selector prompt is not truncated');
      await noOverflow(); await shot(`access-empty-${width}`);

      // The popup opens independently of the short table and offers at least five visible choices.
      await trigger().focus(); await page.keyboard.press('Enter');
      const popup = tab().locator('#export-subject-popup');
      await popup.waitFor({state: 'visible'});
      const popupMetrics = await popup.locator('.select-listbox').evaluate(list => ({
        height: list.clientHeight,
        optionHeight: list.querySelector('.select-option')?.getBoundingClientRect().height ?? 0,
        available: list.querySelectorAll('.select-option:not([hidden])').length,
      }));
      assert(popupMetrics.available >= 5, 'fixture supplies at least five available options');
      assert(popupMetrics.height >= popupMetrics.optionHeight * 5, 'popup shows at least five options without table clipping');

      // Select by keyboard and Tab to Grant; this order is also checked manually in Safari.
      const search = popup.locator('.select-search-input');
      if (await search.isEnabled()) {
        await page.keyboard.type('Row user');
        await page.keyboard.press('Enter');
      } else {
        for (let attempt = 0; attempt < 10; attempt += 1) {
          const activeId = await trigger().getAttribute('aria-activedescendant');
          const activeText = activeId ? await page.locator(`#${activeId}`).innerText() : '';
          if (activeText.includes('Row user')) break;
          await page.keyboard.press('ArrowDown');
        }
        const activeId = await trigger().getAttribute('aria-activedescendant');
        assert((await page.locator(`#${activeId}`).innerText()).includes('Row user'));
        await page.keyboard.press('Enter');
      }
      assert.match(await trigger().innerText(), /Row user/);
      await page.keyboard.press('Tab');
      assert.equal(await grant().evaluate(el => el === document.activeElement), true, 'Tab reaches Grant export access');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelectorAll('[data-export-access] .access-table tbody tr[data-access-entry]').length === 1);
      await status('Export access granted.', '.select-trigger');
      assert(await tab().locator('.select-trigger').evaluate(el => el.matches(':focus-visible')));
      assert.equal(page.url(), url);
      assert.equal(await rows().count(), 1);
      assert((await rows().first().textContent()).includes('row@example.com'));
      assert.equal(await select().locator('option').filter({hasText: 'row@example.com'}).count(), 0);
      await noOverflow(); await shot(`access-granted-${width}`);

      await select().selectOption({label: 'Row team'}); await grant().click();
      await page.waitForFunction(() => document.querySelectorAll('[data-export-access] .access-table tbody tr[data-access-entry]').length === 2);
      await status('Export access granted.', '.select-trigger');
      assert.equal(await rows().count(), 2);
      assert.deepEqual(await rows().locator('td:nth-child(2) .badge').allTextContents(), ['User', 'Team']);
      await noOverflow(); await shot(`access-populated-${width}`);

      for (const name of ['Row user', 'Team Row team']) {
        const remove = tab().getByRole('button', {name: `Remove export access for ${name}`, exact: true});
        const dialog = page.locator('dialog[data-confirm-dialog]');
        assert.equal(await remove.getAttribute('tabindex'), '0', 'Remove is reachable with Safari default keyboard navigation');
        await remove.focus();
        await page.keyboard.press('Enter');
        await dialog.waitFor({state: 'visible'});
        await page.getByRole('heading', {name: 'Remove export access', exact: true}).waitFor();
        assert.equal(await dialog.locator('[data-confirm-message]').textContent(), `${name} loses export access to this dataset.`);
        assert.equal(await page.evaluate(() => document.activeElement?.matches('[data-confirm-cancel]')), true, 'dialog opens with Cancel focused');
        const box = await dialog.boundingBox();
        assert(box && box.x >= 0 && box.x + box.width <= width, 'dialog fits within the viewport');
        assert(await dialog.getByRole('button', {name: 'Remove', exact: true}).evaluate(el => el.classList.contains('button-danger')));
        assert(await dialog.getByRole('button', {name: 'Cancel', exact: true}).evaluate(el => el.classList.contains('button-secondary')));
        assert.equal(await dialog.getByRole('button', {name: 'Cancel', exact: true}).getAttribute('tabindex'), '0');
        assert.equal(await dialog.getByRole('button', {name: 'Remove', exact: true}).getAttribute('tabindex'), '0');
        if (name === 'Row user') {
          await page.keyboard.press('Escape');
          await dialog.waitFor({state: 'hidden'});
          assert.equal(await remove.evaluate(el => el === document.activeElement), true, 'Escape restores focus to the row action');
          await page.keyboard.press('Enter');
          await dialog.waitFor({state: 'visible'});
          await page.mouse.click(2, 2);
          await dialog.waitFor({state: 'hidden'});
          assert.equal(await remove.evaluate(el => el === document.activeElement), true, 'backdrop click restores focus to the row action');
          await page.keyboard.press('Enter');
          await dialog.waitFor({state: 'visible'});
        }
        await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement?.matches('[data-confirm-accept]')), true, 'Tab moves from Cancel to Remove');
        await page.keyboard.press('Enter');
        await dialog.waitFor({state: 'hidden'});
        await status('Export access removed.', '.select-trigger');
        assert(await trigger().evaluate(el => el.matches(':focus-visible')), 'success returns visible keyboard focus to the selector');
      }
      await tab().getByText('Only the owning team has access.', {exact: false}).waitFor();
      assert.equal(await tab().locator('.access-table tbody tr').count(), 1);
      console.log(`PASS: ${colorScheme} ${width}px empty, grant, duplicate, team, styled confirmation and revoke states`);
      }
    }

    // Without JavaScript the forms post normally and errors render on the configuration page.
    const plain = await browser.newContext({javaScriptEnabled: false, storageState: await context.storageState()});
    const fallback = await plain.newPage();
    await fallback.goto(`${base}/datasets/row-check`);
    const plainGrant = fallback.locator('#access-management').getByRole('button', {name: 'Grant export access', exact: true});
    assert(!(await plainGrant.isDisabled()));
    await plainGrant.click();
    await fallback.locator('#export-subject-error').waitFor();
    await fallback.locator('#access-management').getByLabel('User or team', {exact: true}).selectOption({label: 'Row team'});
    const plainSubmit = fallback.locator('#access-management').getByRole('button', {name: 'Grant export access', exact: true});
    await plainSubmit.focus();
    await plainSubmit.press('Enter');
    await fallback.waitForFunction(() => location.pathname.startsWith('/datasets/row-check/versions/') &&
      new URLSearchParams(location.search).get('tab') === 'access-management');
    const plainRemove = fallback.getByRole('button', {name: 'Remove export access for Team Row team', exact: true});
    await plainRemove.focus();
    await plainRemove.press('Enter');
    await fallback.waitForFunction(() => location.pathname.startsWith('/datasets/row-check/versions/') &&
      new URLSearchParams(location.search).get('tab') === 'access-management');
    await plain.close();
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('PASS: no-JavaScript grant, validation and revoke; no script errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
