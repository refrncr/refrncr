// REF-145 real-browser regression; start browser_field_datatypes_app.py first.
// Uses the repository's optional external Playwright convention.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5146';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) {
  throw Error('Use the disposable loopback fixture');
}
const types = [
  ['string', 'String'], ['integer', 'Integer'], ['number', 'Decimal'],
  ['float', 'Float'], ['boolean', 'Boolean'], ['date', 'Date'],
  ['time', 'Time'], ['datetime', 'DateTime'], ['timestamp', 'Timestamp'],
];
async function login(page) {
  await page.goto(base + '/login');
  await page.getByLabel('Email address').fill('admin@example.com');
  await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await page.waitForURL(base + '/');
}
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    console.log('Chrome ' + browser.version());
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await login(page);
    await page.goto(base + '/datasets/types/concept');
    for (const [id, label] of types) {
      await page.getByRole('link', {name: 'Add field', exact: true}).click();
      const panel = page.locator('dialog[open]');
      const select = panel.locator('select[name=data_type]');
      await select.waitFor();
      assert.deepEqual(await select.locator('option').evaluateAll(es => es.map(e => [e.value, e.textContent])), types);
      await panel.getByLabel('Key', {exact: true}).fill(id);
      await panel.getByLabel('Name', {exact: true}).fill(label);
      await select.selectOption(id);
      assert.equal(await panel.getByLabel('Part of record identifier', {exact: true}).isDisabled(), id === 'float');
      if (id === 'string') {
        const identifier = panel.getByLabel('Part of record identifier', {exact: true});
        const required = panel.getByLabel('Required', {exact: true});
        const unique = panel.getByLabel('Unique', {exact: true});
        await identifier.check();
        assert(await required.isChecked());
        assert.equal(await required.getAttribute('aria-disabled'), 'true');
        assert.equal(await required.evaluate(el => getComputedStyle(el).cursor), 'not-allowed');
        assert(Number(await required.evaluate(el => getComputedStyle(el).opacity)) < 1);
        assert.match(await panel.locator('[data-identifier-status]').textContent(), /checked and locked/);
        await required.click();
        assert(await required.isChecked(), 'the required constraint stays checked while locked');
        assert.equal(await panel.locator('[data-required-mirror]').count(), 1);
        assert.equal(await unique.isChecked(), false, 'identifier membership does not imply uniqueness');
        await identifier.uncheck();
        assert(await required.isChecked(), 'removing identifier membership preserves Required');
        assert.equal(await required.isDisabled(), false);
        assert.equal(await required.getAttribute('aria-disabled'), null);
        await identifier.check();
      }
      if (id === 'float') {
        await select.selectOption('integer');
        await panel.getByLabel('Part of record identifier', {exact: true}).check();
        await select.selectOption('float');
        assert.equal(await panel.getByLabel('Part of record identifier', {exact: true}).isChecked(), false);
      }
      assert.equal(await panel.getByLabel('Output format', {exact: true}).count(), 0);
      await panel.getByRole('button', {name: 'Add field', exact: true}).click();
      await page.waitForURL(base + '/datasets/types/concept');
      await page.locator(`a[href='/datasets/types/fields/${id}/edit']`).waitFor();
      await page.locator(`a[href='/datasets/types/fields/${id}/edit']`).click();
      await page.locator('dialog[open] select[name=data_type]').waitFor();
      assert.equal(await page.locator('dialog[open] select[name=data_type]').inputValue(), id);
      await page.locator('dialog[open]').getByLabel('Name', {exact: true}).fill(label + ' reviewed');
      await page.getByRole('button', {name: 'Save field', exact: true}).click();
      await page.waitForURL(base + '/datasets/types/concept');
      await page.getByText(label + ' reviewed', {exact: true}).waitFor();
      console.log('PASS create/edit ' + label);
    }
    // Invalid definition remains in the real sidepanel, escaped and focused.
    const opener = page.locator("a[href='/datasets/types/fields/integer/edit']");
    await opener.click();
    await page.locator('dialog[open] select[name=data_type]').waitFor();
    await page.locator('dialog[open]').getByLabel('Name', {exact: true}).fill('');
    await page.getByRole('button', {name: 'Save field', exact: true}).click();
    await page.locator('dialog[open] [data-error-focus]').waitFor();
    assert(await page.locator('dialog[open] [data-error-focus]').evaluate(e => e === document.activeElement));
    assert.equal(await page.locator('dialog[open] select[name=data_type]').inputValue(), 'integer');
    await page.keyboard.press('Escape');
    await page.locator('dialog[open]').waitFor({state: 'hidden'});
    await page.waitForFunction(() => document.activeElement?.getAttribute('href') === '/datasets/types/fields/integer/edit');
    // Forge Float membership through an actual browser form; service must refuse.
    await page.locator("a[href='/datasets/types/fields/float/edit']").click();
    await page.locator('dialog[open] select[name=data_type]').waitFor();
    await page.locator('dialog[open]').getByLabel('Required', {exact: true}).check();
    await page.locator('dialog[open] input[name=is_business_key_component]').evaluate(e => { e.disabled = false; e.checked = true; });
    await page.getByRole('button', {name: 'Save field', exact: true}).click();
    await page.locator('dialog[open] [data-error-focus]').waitFor();
    assert.match(await page.locator('dialog[open] [data-error-focus]').textContent(), /Float cannot/);
    assert(await page.locator('dialog[open] input[name=is_business_key_component]').isDisabled());
    for (const width of [1440, 320]) {
      await page.setViewportSize({width, height: 1000});
      assert(await page.locator('dialog[open]').evaluate(d => d.scrollWidth <= d.clientWidth));
      await page.screenshot({path: path.join(process.env.REFRNCR_SCREENSHOT_DIR || os.tmpdir(), `ref145-field-${width}.png`)});
    }
    await page.goto(base + '/datasets/types/concept/records');
    await page.getByRole('button', {name: 'Add record', exact: true}).click();
    await page.locator('dialog[open] input[name=id]').waitFor();
    const inputs = {id: 'browser-record', string: 'Cafe\u0301', integer: '0009007199254740993', number: '0.000000000000000001', float: '1.25e2', boolean: 'false', date: '2024-02-29', time: '12:00:00.120000', datetime: '2024-02-29T12:00:00.123400', timestamp: '2024-02-29T13:00:00.123400+01:00'};
    const canonical = {...inputs, string: 'Café', integer: '9007199254740993', float: '125.0', time: '12:00:00.12', datetime: '2024-02-29T12:00:00.1234', timestamp: '2024-02-29T12:00:00.1234Z'};
    for (const [id, value] of Object.entries(inputs)) {
      const input = page.locator(`dialog[open] [name='${id}']`);
      if (id === 'boolean') await input.selectOption(value);
      else await input.fill(value);
    }
    await page.getByRole('button', {name: 'Create record', exact: true}).click();
    await page.getByRole('button', {name: /^Edit record browser-record/}).waitFor();
    await page.getByRole('button', {name: /^Edit record browser-record/}).click();
    for (const [id, value] of Object.entries(canonical)) {
      assert.equal(await page.locator(`dialog[open] [data-panel-view=edit] [name='${id}']`).inputValue(), value, id);
    }
    assert.equal(await page.locator('dialog[open] [data-panel-view=edit] [name=expected_revision]').inputValue(), '1');
    await page.getByRole('button', {name: 'Save record', exact: true}).click();
    await page.getByRole('button', {name: /^Edit record browser-record/}).waitFor();
    await page.getByRole('button', {name: /^Edit record browser-record/}).click();
    assert.equal(await page.locator('dialog[open] [data-panel-view=edit] [name=expected_revision]').inputValue(), '1', 'canonical sidepanel save is a no-op');
    assert.deepEqual(errors, []);
    await context.close();
    const plain = await browser.newContext({javaScriptEnabled: false});
    const nojs = await plain.newPage();
    await login(nojs);
    for (const [id, label] of types) {
      await nojs.goto(base + `/datasets/types/fields/${id}/edit`);
      assert.equal(await nojs.locator('select[name=data_type]').inputValue(), id);
      await nojs.getByLabel('Name', {exact: true}).fill(label + ' nojs');
      await nojs.getByRole('button', {name: 'Save field', exact: true}).click();
      await nojs.waitForURL(base + '/datasets/types/concept');
      await nojs.getByText(label + ' nojs', {exact: true}).waitFor();
    }
    await plain.close();
    console.log('PASS nine types, dynamic panels, Float UI and forged POST, validation focus, Escape restoration, 320px, Record canonical edit/no-op, no-JavaScript edits; zero page errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
