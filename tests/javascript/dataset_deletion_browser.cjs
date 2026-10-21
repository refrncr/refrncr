// REF-141: use a fresh tests/browser_table_rows_app.py disposable fixture.
// REF-165: Delete dataset lives in Settings; editing is inline, not a panel.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) {
  throw Error('Use the disposable loopback fixture');
}
const output = process.env.REFRNCR_BROWSER_OUTPUT;
async function signIn(page, email='admin@example.com') {
  await page.goto(base + '/login');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', {exact:true}).fill('correct horse battery staple');
  await page.getByRole('button', {name:'Sign in', exact:true}).click();
  await page.waitForURL(base + '/');
}
async function focused(page, locator) {
  assert(await locator.evaluate(e => e === document.activeElement));
}
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  console.log('Chrome ' + browser.version());
  const context = await browser.newContext({colorScheme:'light'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const dialog = page.getByRole('dialog');
  // The confirmation label carries the Dataset's own name, in its own casing.
  const confirmField = name => dialog.getByLabel(`Type ${name} to confirm`, {exact:true});
  try {
    await signIn(page);
    for (const width of [320, 768, 1440]) {
      await page.setViewportSize({width,height:900});
      // Editing is inline; an unsaved draft has to survive the deletion detour.
      await page.goto(base + '/datasets/row-check/configure?edit=1#dataset-name');
      await page.locator('#dataset-description').fill('Unsaved draft must survive cancel');
      await page.locator('[data-section-tabs]').getByRole('tab', {name:'Dataset details', exact:true}).click();
      assert.equal(await page.locator('#dataset-description').inputValue(), 'Unsaved draft must survive cancel');

      // The tab states what a deletion removes before anything is confirmed.
      assert.equal(await page.locator('#danger-zone .danger-counts').count(),0);
      assert(await page.locator('#danger-zone').getByText('This cannot be undone.',{exact:true}).isVisible());

      const trigger = page.getByRole('link', {name:'Delete dataset', exact:true});
      await trigger.focus();
      await page.keyboard.press('Enter');
      const name = confirmField('Row check');
      await name.waitFor();
      await dialog.evaluate(async e => {await Promise.all(e.getAnimations().map(a => a.finished));});
      await focused(page,name);
      assert.equal(await page.locator('dialog[open]').count(),1);
      assert.equal(await dialog.getAttribute('aria-labelledby'),'configuration-panel-title');
      assert.equal(await dialog.getByRole('heading', {name:'Delete dataset',exact:true}).count(),1);
      // The panel repeats the counts and keeps the audit promise.
      assert.deepEqual(
        await dialog.locator('.deletion-counts > div').evaluateAll(els => els.map(
          e => `${e.querySelector('dt').textContent} ${e.querySelector('dd').textContent}`)),
        ['Versions 1','Records 52','Fields 2','Export access 0']);
      assert.match(await dialog.innerText(),/Deletion is permanent and cannot be undone/);
      assert.match(await dialog.innerText(),/Audit history is retained/);

      const submit = dialog.getByRole('button',{name:'Delete dataset',exact:true});
      assert(await submit.isDisabled());
      for (const wrong of ['row check','Row check ',' Row check']) {
        await name.fill(wrong);
        assert(await submit.isDisabled());
      }
      await name.fill('Row check');
      assert(await submit.isEnabled());
      assert(await name.getAttribute('aria-describedby'));
      await submit.focus();
      await page.keyboard.press('Tab');
      await focused(page,dialog.getByRole('button',{name:'Close panel'}));
      await page.keyboard.press('Shift+Tab');
      await focused(page,submit);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert(await dialog.evaluate(e => {
        const bounds = e.getBoundingClientRect();
        return bounds.left >= 0 && bounds.right <= innerWidth && bounds.width <= innerWidth;
      }));
      if (output) {
        fs.mkdirSync(output,{recursive:true});
        await page.screenshot({path:path.join(output,`delete-${width}.png`)});
      }
      // Escape, Cancel and Close all return to the trigger and keep the draft.
      await page.keyboard.press('Escape');
      await focused(page,trigger);
      assert.equal(await page.locator('#dataset-description').inputValue(),'Unsaved draft must survive cancel');
      await trigger.click();
      await confirmField('Row check').waitFor();
      await dialog.getByRole('link',{name:'Cancel',exact:true}).click();
      await focused(page,trigger);
      await trigger.click();
      await confirmField('Row check').waitFor();
      await dialog.getByRole('button',{name:'Close panel'}).click();
      await focused(page,trigger);
      console.log(`${width}px: counts, name, keyboard, containment, Cancel/Escape/close and draft/focus restoration passed`);
    }

    // A server metadata change while the confirmation is open rejects the old state.
    await page.goto(base + '/datasets/row-check/configure#dataset-details');
    await page.getByRole('link',{name:'Delete dataset',exact:true}).click();
    let name = confirmField('Row check');
    await name.waitFor();
    const other = await context.newPage();
    await other.goto(base + '/datasets/row-check/configure?edit=1#dataset-name');
    await other.getByLabel('Name',{exact:true}).fill('Renamed dataset');
    await other.getByRole('button',{name:'Save dataset',exact:true}).click();
    await other.waitForURL(url => url.pathname === '/datasets/row-check/configure');
    await name.fill('Row check');
    await dialog.getByRole('button',{name:'Delete dataset',exact:true}).click();
    await dialog.getByRole('alert').filter({hasText:'Dataset changed'}).waitFor();
    await focused(page,dialog.locator('[data-error-focus]'));
    // The re-rendered panel asks for the new name, so address the field by id.
    name = dialog.locator('#dataset-delete-name');
    assert((await dialog.locator('#dataset-delete-form label').innerText()).includes('Renamed dataset'));
    assert.equal(await name.getAttribute('aria-invalid'),'true');
    assert(await dialog.getByRole('button',{name:'Delete dataset',exact:true}).isDisabled());
    // Force client validation off: the server still rejects a mismatching name.
    await name.fill('wrong');
    await name.evaluate(e => {e.setCustomValidity(''); e.form.removeAttribute('data-exact-confirmation');});
    await dialog.getByRole('button',{name:'Delete dataset',exact:true}).evaluate(e => {e.disabled=false;});
    await dialog.getByRole('button',{name:'Delete dataset',exact:true}).click();
    await dialog.getByRole('alert').filter({hasText:'Enter the current dataset name exactly'}).waitFor();
    await focused(page,dialog.locator('[data-error-focus]'));
    await name.fill('Renamed dataset');
    let deletes = 0;
    page.on('request', request => {if (request.method()==='POST' && request.url().endsWith('/delete')) deletes++;});
    await dialog.getByRole('button',{name:'Delete dataset',exact:true}).dblclick();
    await page.waitForURL(base + '/datasets?deleted=1');
    assert.equal(deletes,1);
    await page.getByRole('status').filter({hasText:'Dataset deleted permanently.'}).waitFor();
    assert.equal(await page.getByRole('link',{name:'Renamed dataset',exact:true}).count(),0);
    await page.reload();
    await page.goBack();
    await page.reload();
    assert.equal(deletes,1,'Back/Refresh never repeats a POST');
    await other.close();

    // Long hostile names stay escaped in confirmation and post-deletion audit UI.
    await page.goto(base + '/datasets/long-check/configure#dataset-details');
    await page.getByRole('link',{name:'Delete dataset',exact:true}).click();
    await dialog.locator('form[data-exact-confirmation]').waitFor();
    const longName = await dialog.locator('form[data-exact-confirmation]').getAttribute('data-exact-confirmation');
    assert(longName.includes('<img'));
    assert.equal(await dialog.locator('img[src="x"]').count(),0);
    // The escaped name is shown in the label, not executed.
    assert((await dialog.locator('#dataset-delete-form label').innerText()).includes('<img'));
    name = confirmField(longName);
    await name.fill(longName);
    await dialog.getByRole('button',{name:'Delete dataset',exact:true}).click();
    await page.waitForURL(base + '/datasets?deleted=1');
    await page.goto(base + '/settings/audit-log');
    const auditRow = page.getByRole('row').filter({hasText:longName + ' (deleted)'});
    assert.equal(await auditRow.count(),1);
    assert.equal(await auditRow.locator('a[href*="/datasets/"]').count(),0);
    await auditRow.getByRole('link',{name:/View details/}).click();
    await page.getByRole('dialog').getByText('Dataset name at deletion',{exact:true}).waitFor();
    assert.equal(await page.getByRole('dialog').locator('img[src="x"]').count(),0);
    assert((await page.getByRole('dialog').innerText()).includes(longName));

    // A regular reader gets neither the edit entry point nor the Danger zone tab.
    const readerContext = await browser.newContext();
    const reader = await readerContext.newPage();
    await signIn(reader,'row@example.com');
    await reader.goto(base + '/datasets/empty-check');
    assert.equal(await reader.locator('#dataset-general').getByRole('link',{name:'Edit',exact:true}).count(),0);
    assert.equal(await reader.locator('#danger-zone').count(),0);
    await readerContext.close();
    assert.deepEqual(errors,[]);
    console.log('Stale validation, server-side exact match, one POST, success redirect, Back/Refresh, escaped snapshots and reader visibility passed');
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
