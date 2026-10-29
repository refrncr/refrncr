// Checks refreshed dataset tables, tabs, draft actions and component styling at key widths.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5162';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Loopback fixture required');
const output = process.env.REFRNCR_BROWSER_OUTPUT || '/private/tmp/ref162-browser';
fs.mkdirSync(output, {recursive: true});
async function login(page) {
  await page.goto(base + '/login');
  await page.getByLabel('Email address').fill('admin@example.com');
  await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await page.waitForURL(base + '/');
}
async function layout(page, label) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${label}: page overflow`);
  if (process.env.REFRNCR_AXE_SOURCE && !label.startsWith('no-JavaScript')) {
    if (!await page.evaluate(() => Boolean(window.axe))) await require('./browser_policy.cjs').injectAxe(page);
    const result = await page.evaluate(async () => {
      const result = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']}});
      return {violations: result.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.map(n => ({target:n.target, summary:n.failureSummary}))})), incomplete: result.incomplete.map(v => v.id)};
    });
    console.log(`axe ${label}: ${JSON.stringify(result)}`);
    assert.deepEqual(result.violations, [], `${label}: accessibility violations`);
  }
  const missingControls = await page.locator('[aria-controls]').evaluateAll(elements => elements.flatMap(e => e.getAttribute('aria-controls').split(/\s+/).filter(id => !document.getElementById(id))));
  assert.deepEqual(missingControls, [], `${label}: missing ARIA control targets`);
  const ids = await page.locator('[id]').evaluateAll(elements => elements.map(e => e.id));
  assert.equal(ids.length, new Set(ids).size, `${label}: duplicate IDs`);
}
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  const errors = [], external = [];
  try {
    console.log('Chrome ' + browser.version());
    for (const width of [320, 375, 767, 768, 1440]) {
      const context = await browser.newContext({viewport: {width, height: 1000}, reducedMotion: 'reduce'});
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => { if (!request.url().startsWith(base)) external.push(request.url()); });
      await page.goto(base + '/login');
      await page.locator('.brand .auth-wordmark').waitFor();
      await page.waitForFunction(() => document.querySelector('.brand .auth-wordmark').complete);
      assert((await page.locator('.brand .auth-wordmark').evaluate(e => e.currentSrc)).endsWith('refrncr-wordmark-primary.svg'));
      assert(!(await page.locator('.brand .auth-loader').isVisible()), 'loader hidden with reduced motion');
      await layout(page, `login ${width}`);
      await page.screenshot({path: `${output}/login-${width}.png`});
      await page.emulateMedia({reducedMotion: 'no-preference'});
      await page.locator('.brand .auth-loader').waitFor({state: 'visible'});
      assert((await page.locator('.brand .auth-loader').getAttribute('src')).endsWith('refrncr-loader.svg'));
      await page.emulateMedia({reducedMotion: 'reduce'});
      await page.locator('.brand .auth-loader').waitFor({state: 'hidden'});
      assert((await page.locator('link[rel=icon]').getAttribute('href')).endsWith('favicon.svg'));
      await login(page);
      await page.goto(base + '/datasets');
      const row = page.locator('.dataset-row').filter({has: page.locator(`[data-row-primary][href='/datasets/polish-${width}/concept/records']`)});
      const identity = await row.locator('.dataset-identity').evaluate(element => {
        const icon = element.querySelector('.dataset-icon').getBoundingClientRect();
        const text = element.querySelector('.dataset-identity-text').getBoundingClientRect();
        return {iconRight: icon.right, textLeft: text.left,
          iconCenter: icon.y + icon.height / 2, textCenter: text.y + text.height / 2};
      });
      assert(identity.iconRight < identity.textLeft, JSON.stringify(identity));
      assert(Math.abs(identity.iconCenter - identity.textCenter) <= 1, JSON.stringify(identity));
      const alignment = await row.locator('.badge-link').evaluate(badge => {
        const cell = badge.closest('td').getBoundingClientRect(), box = badge.getBoundingClientRect();
        return {cell: cell.y + cell.height / 2, badge: box.y + box.height / 2, text: badge.closest('td').textContent};
      });
      assert(Math.abs(alignment.cell - alignment.badge) <= 2, JSON.stringify({width, alignment}));
      assert(alignment.text.includes('Draft'));
      await layout(page, `datasets ${width}`);
      await Promise.all([
        page.waitForURL(`${base}/datasets/polish-${width}/concept/records`),
        row.locator('[data-row-primary]').click(),
      ]);
      await page.waitForURL(base + `/datasets/polish-${width}/concept/records`);
      await page.getByRole('link', {name: 'Fields', exact: true}).click();
      const generalTabs = page.getByRole('tablist', {name: 'Dataset sections'});
      assert(await generalTabs.getByRole('tab', {name: 'Fields', exact: true}).isEnabled());
      await generalTabs.getByRole('tab', {name: 'Settings', exact: true}).focus();
      await page.keyboard.press('ArrowRight');
      const readFields = page.getByRole('tabpanel', {name: 'Fields', exact: true});
      await readFields.waitFor();
      await page.goto(base + `/datasets/polish-${width}/concept?tab=fields#fields`);
      await page.getByRole('tabpanel', {name: 'Fields', exact: true}).waitFor();
      await layout(page, `general configuration ${width}`);
      await page.screenshot({path: `${output}/configuration-${width}.png`, fullPage: true});
      await page.goto(base + '/datasets');
      await row.locator('[data-row-primary]').click();
      await page.waitForURL(base + `/datasets/polish-${width}/concept/records`);
      const metadataButton = page.getByRole('button', {name: 'About E_NUMBER', exact: true});
      await metadataButton.click();
      assert(await page.getByText('A readable identifier', {exact: false}).isVisible());
      assert.equal(await metadataButton.getAttribute('aria-expanded'), 'true');
      await layout(page, `record metadata ${width}`);
      await page.goto(base + `/datasets/polish-${width}/concept?tab=dataset-details#dataset-details`);
      const tabs = page.getByRole('tablist', {name: 'Dataset sections'});
      await tabs.waitFor();
      assert.deepEqual(await tabs.getByRole('tab').allTextContents(), ['Fields', 'Access', 'Settings']);
      await page.evaluate(() => { window.refrncrSectionTabs(); window.refrncrSectionTabs(); });
      await tabs.getByRole('tab', {name: 'Settings', exact: true}).focus();
      await page.keyboard.press('ArrowRight');
      assert.equal(await tabs.getByRole('tab', {name: 'Fields', exact: true}).getAttribute('aria-selected'), 'true');
      assert.equal(await page.getByRole('tabpanel').count(), 1);
      await page.reload();
      await page.getByRole('tabpanel', {name: 'Fields', exact: true}).waitFor();
      await tabs.getByRole('tab', {name: 'Settings', exact: true}).focus();
      await page.keyboard.press('Home');
      await page.getByRole('tabpanel', {name: 'Fields', exact: true}).waitFor();
      await tabs.getByRole('tab', {name: 'Settings', exact: true}).click();
      const details = page.getByRole('tabpanel', {name: 'Settings', exact: true});
      const edit = details.locator('#dataset-general').getByRole('link', {name: 'Edit', exact: true});
      assert((await edit.getAttribute('class') || '').includes('button-secondary'));
      await details.getByRole('heading', {name: 'General', exact: true}).waitFor();
      assert.equal(await details.locator('#dataset-identification').getByText('Permanent', {exact:true}).count(), 0);
      assert.equal(await details.locator('#dataset-identification').getByText('Editable until first publication', {exact:true}).count(), 1);
      assert.equal(await details.locator('#dataset-general .dataset-icon use').count(), 1);
      await layout(page, `dataset details ${width}`);
      await edit.click();
      await page.waitForFunction(() => document.activeElement === document.getElementById("dataset-name"));
      let panel = page.locator('#dataset-edit-form');
      await panel.getByRole('combobox', {name: 'Dataset icon', exact: true}).waitFor();
      assert.equal(await panel.locator('input[name=icon]').isVisible(), true);
      assert.equal(await panel.locator('[data-icon-picker]').getByRole('combobox').count(), 1);
      assert.equal(await panel.locator('input[name=key]').count(), 0);
      assert.equal(await panel.locator('input[name=technical_key]').count(), 1);
      assert.equal(await page.locator('dialog[open]').count(), 0);
      assert.equal(await panel.getByLabel('Name', {exact: true}).evaluate(e => e === document.activeElement), true);
      await panel.getByLabel('Name', {exact: true}).fill('');
      await panel.getByLabel('Description', {exact: true}).fill('Draft retained after validation');
      await Promise.all([page.waitForNavigation(), panel.getByRole('button', {name: 'Save changes', exact: true}).click()]);
      assert.equal(await panel.getByLabel('Description', {exact: true}).inputValue(), 'Draft retained after validation');
      assert.equal(await page.locator('#dataset-edit-error').evaluate(e => e === document.activeElement), true);
      await panel.getByRole('link', {name: 'Cancel', exact: true}).click();
      assert.equal(await page.locator('#dataset-edit-form').count(), 0);
      await edit.click();
      await page.waitForFunction(() => document.activeElement === document.getElementById("dataset-name"));
      assert.equal(await panel.getByLabel('Description', {exact: true}).inputValue(), '');
      await panel.getByLabel('Description', {exact: true}).fill('Unsaved edit hides destructive actions');
      assert.equal(await page.getByRole('link', {name: 'Delete dataset', exact: true}).count(), 0);
      await panel.getByRole('link', {name: 'Cancel', exact: true}).click();
      const deletionTrigger = page.getByRole('link', {name: 'Delete dataset', exact: true});
      await deletionTrigger.click();
      const deletion = page.locator('dialog[open]');
      const confirmField = deletion.getByLabel(`Type Polish ${width} to confirm`, {exact: true});
      await confirmField.waitFor();
      assert.equal(await deletion.getByRole('button', {name: 'Delete dataset', exact: true}).isDisabled(), true);
      await confirmField.fill(`Polish ${width}`);
      assert.equal(await deletion.getByRole('button', {name: 'Delete dataset', exact: true}).isEnabled(), true);
      await deletion.getByRole('link', {name: 'Cancel', exact: true}).click();
      assert.equal(await page.locator('dialog[open]').count(), 0);
      assert.equal(await deletionTrigger.evaluate(e => e === document.activeElement), true);
      await edit.click();
      await page.waitForFunction(() => document.activeElement === document.getElementById("dataset-name"));
      panel = page.locator('#dataset-edit-form');
      await page.screenshot({path: `${output}/inline-edit-${width}.png`, fullPage: true});
      const picker = panel.getByRole('combobox', {name: 'Dataset icon', exact: true});
      await picker.waitFor();
      await page.evaluate(() => { window.refrncrIconPickers(); window.refrncrIconPickers(); });
      await picker.fill('no-matching-icon');
      assert.equal(await panel.locator('[role=option]:visible').count(), 0);
      await picker.press('Escape');
      assert.equal(await panel.count(), 1);
      assert.equal(await picker.getAttribute('aria-expanded'), 'false');
      await picker.fill('bank');
      await picker.press('ArrowDown');
      await picker.press('Enter');
      assert.equal(await panel.locator('input[name=icon]').inputValue(), 'banknote');
      // Suggestions are visible tiles; the preview follows the chosen or typed name.
      const suggestions = panel.getByRole('group', {name: 'Suggested icons', exact: true});
      assert(await suggestions.isVisible());
      assert.equal(await suggestions.getByRole('button', {name: 'Banknote', exact: true}).getAttribute('aria-pressed'), 'true');
      // The default tile plus one per suggestion in DATASET_ICONS.
      assert.equal(await suggestions.getByRole('button').count(), 36);
      await suggestions.getByRole('button', {name: 'Landmark', exact: true}).click();
      assert.equal(await panel.locator('input[name=icon]').inputValue(), 'landmark');
      assert((await panel.locator('[data-icon-preview] use').getAttribute('href')).endsWith('#landmark'));
      assert.equal(await panel.locator('[data-icon-preview-label]').textContent(), 'Landmark');
      await picker.fill('cloud');
      await page.waitForFunction(() => document.querySelector('[data-icon-preview] use').getAttribute('href').endsWith('#cloud'));
      assert.equal(await panel.locator('[data-icon-preview-label]').textContent(), 'Cloud');
      assert.equal(await suggestions.locator('[aria-pressed=true]').count(), 0);
      await picker.fill('not-a-real-icon');
      await page.waitForFunction(() => document.querySelector('[data-icon-preview-label]').textContent === 'Unknown name, default icon shown');
      assert((await panel.locator('[data-icon-preview] use').getAttribute('href')).endsWith('#table'));
      await picker.press('Escape');
      await panel.getByRole('button', {name: 'Clear icon', exact: true}).click();
      assert.equal(await panel.locator('input[name=icon]').inputValue(), '');
      assert.equal(await panel.locator('[data-icon-preview-label]').textContent(), 'Default dataset icon');
      await picker.fill('earth');
      await picker.press('ArrowDown');
      await picker.press('Enter');
      await layout(page, `icon picker ${width}`);
      await Promise.all([page.waitForNavigation(), panel.getByRole('button', {name: 'Save changes', exact: true}).click()]);
      await edit.click();
      await page.waitForFunction(() => document.activeElement === document.getElementById("dataset-name"));
      panel = page.locator('#dataset-edit-form');
      await panel.getByRole('combobox', {name: 'Dataset icon', exact: true}).fill('warehouse');
      await panel.getByRole('combobox', {name: 'Dataset icon', exact: true}).press('Tab');
      assert.equal(await panel.locator('input[name=icon]').inputValue(), 'warehouse');
      await Promise.all([page.waitForNavigation(), panel.getByRole('button', {name: 'Save changes', exact: true}).click()]);
      await page.goto(base + '/datasets');
      assert.equal(await row.locator('use').getAttribute('href'), '/static/images/lucide-1.46.0.svg#warehouse');
      await row.locator('[data-row-primary]').click();
      await page.getByRole('navigation', {name: 'Dataset sections'}).getByRole('link', {name: 'Settings', exact: true}).click();
      await edit.click();
      await page.waitForFunction(() => document.activeElement === document.getElementById("dataset-name"));
      panel = page.locator('#dataset-edit-form');
      assert.equal(await panel.locator('input[name=icon]').inputValue(), 'warehouse');
      await panel.getByRole('combobox', {name: 'Dataset icon', exact: true}).fill('not-a-real-icon');
      await Promise.all([page.waitForNavigation(), panel.getByRole('button', {name: 'Save changes', exact: true}).click()]);
      assert((await page.locator('.page-title use').getAttribute('href')).endsWith('#table'));
      for (const invalid of ['<svg onload=alert(1)>', 'https://invalid.example/icon.svg', '../icons']) {
        await edit.click();
      await page.waitForFunction(() => document.activeElement === document.getElementById("dataset-name"));
        await page.getByRole('combobox', {name: 'Dataset icon', exact: true}).fill(invalid);
        await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: 'Save changes', exact: true}).click()]);
        assert((await page.locator('.page-title use').getAttribute('href')).endsWith('#table'));
        assert.equal(await page.locator('#dataset-edit-error').count(), 0);
      }
      await page.goto(base + `/datasets/polish-${width}/concept#fields`);
      await tabs.getByRole('tab', {name: 'Fields', exact: true}).click();
      await page.getByRole('link', {name: 'Add field', exact: true}).click();
      panel = page.locator('dialog[open]');
      await panel.getByLabel('Name', {exact: true}).fill('Crème / total');
      assert.equal(await panel.getByLabel('Key', {exact: true}).inputValue(), 'creme_total');
      await panel.getByLabel('Key', {exact: true}).fill('custom');
      await panel.getByLabel('Name', {exact: true}).fill('Amount');
      assert.equal(await panel.getByLabel('Key', {exact: true}).inputValue(), 'custom');
      await panel.getByLabel('Definition', {exact: true}).fill('Plain <b>definition</b>');
      await panel.getByLabel('Unit', {exact: true}).fill('EUR');
      await Promise.all([page.waitForNavigation(), panel.getByRole('button', {name: 'Add field', exact: true}).click()]);
      // Fields reorder with the keyboard on the drag handle and save right away.
      const fieldNames = () => page.locator('#fields tbody .field-name').allTextContents();
      const fieldOrder = await fieldNames();
      const dragHandle = page.locator('#fields .field-drag-handle').first();
      assert(await dragHandle.isVisible());
      const dragAlignment = await dragHandle.evaluate(handle => {
        const cell = handle.closest('td').getBoundingClientRect();
        const row = handle.closest('tr').getBoundingClientRect();
        const box = handle.getBoundingClientRect();
        return {leftOffset: box.left - cell.left, centerOffset: box.top + box.height / 2 - row.top - row.height / 2};
      });
      assert(Math.abs(dragAlignment.leftOffset) <= 1, JSON.stringify({width, dragAlignment}));
      assert(Math.abs(dragAlignment.centerOffset) <= 1, JSON.stringify({width, dragAlignment}));
      assert.equal(await page.locator('#fields .button-stack').count(), 0);
      const reorder = async (name, key) => {
        await page.getByRole('button', {name: `Reorder ${name}`, exact: true}).focus();
        await Promise.all([
          page.waitForResponse(response => response.url().endsWith('/fields/order') && response.status() === 204),
          page.keyboard.press(key),
        ]);
      };
      await reorder(fieldOrder[0], 'ArrowDown');
      await page.reload();
      assert.deepEqual(await fieldNames(), [fieldOrder[1], fieldOrder[0], ...fieldOrder.slice(2)]);
      await reorder(fieldOrder[0], 'ArrowUp');
      await page.reload();
      assert.deepEqual(await fieldNames(), fieldOrder);
      await page.getByRole('link', {name: 'Edit field Amount', exact: true}).click();
      panel = page.locator('dialog[open]');
      await panel.getByLabel('Name', {exact: true}).waitFor();
      const savedDefinition = panel.locator('textarea[name=definition]');
      assert(await savedDefinition.isVisible());
      assert.equal(await savedDefinition.inputValue(), 'Plain <b>definition</b>');
      assert.equal(await panel.locator('input[name=key]').inputValue(), 'custom');
      await page.keyboard.press('Escape');
      assert.equal(await page.getByRole('link', {name: 'Edit field Amount', exact: true}).evaluate(e => e === document.activeElement), true);
      await page.goto(base + `/datasets/polish-${width}/concept?tab=access-management#access-management`);
      let access = page.locator('#access-management');
      await access.getByLabel('User or team', {exact: true}).locator('xpath=self::select | following-sibling::select[1]').selectOption({label: 'Reader (reader@example.com)'}, {force: true});
      await access.getByRole('button', {name: 'Grant export access', exact: true}).click();
      await access.getByRole('button', {name: 'Remove export access for Reader', exact: true}).waitFor();
      await access.locator('[data-panel-status]').getByText('Export access granted.', {exact: true}).waitFor();
      const subject = access.locator('#export-subject');
      await subject.evaluate(select => {
        select.add(new Option('Invalid subject', 'invalid-subject'));
        select.value = 'invalid-subject';
        select.dispatchEvent(new Event('change', {bubbles: true}));
      });
      await access.getByRole('button', {name: 'Grant export access', exact: true}).click();
      await access.getByRole('alert').waitFor();
      assert.equal(await access.locator('#export-subject-trigger').getAttribute('aria-invalid'), 'true');
      await access.getByRole('button', {name: 'Remove export access for Reader', exact: true}).click();
      await page.locator('dialog[open]').getByRole('button', {name: 'Remove', exact: true}).click();
      await access.getByText('Only the owning team has access.', {exact: false}).waitFor();
      await layout(page, `configure ${width}`);
      await page.screenshot({path: `${output}/configure-${width}.png`, fullPage: true});
      await Promise.all([
        page.waitForURL(base + `/datasets/polish-${width}/versions`),
        page.getByRole('link', {name: 'Versions', exact: true}).click(),
      ]);
      assert.equal(await page.locator('[data-concept-actions]').count(), 0);
      assert.equal(await page.getByRole('link', {name: 'Review & publish', exact: true}).count(), 1);
      assert.equal(await page.getByRole('link', {name: 'Review', exact: true}).count(), 0);
      await page.goto(base + '/users');
      await layout(page, `users ${width}`);
      await page.screenshot({path: `${output}/users-${width}.png`, fullPage: true});
      await page.getByRole('link', {name: 'Domain 0', exact: true}).first().click();
      await page.locator('dialog[open]').waitFor();
      await page.keyboard.press('Escape');
      await layout(page, `teams ${width}`);
      await page.getByRole('link', {name: 'Member 0', exact: true}).click();
      await page.locator('dialog[open]').waitFor();
      await page.keyboard.press('Escape');
      await context.close();
      console.log(`${width}px: logo motion, Draft route/alignment, icon picker, keys, metadata, inline access, focus and relationships passed`);
    }
    const nojs = await browser.newContext({javaScriptEnabled: false, viewport: {width: 320, height: 1000}});
    const page = await nojs.newPage();
    await login(page);
    await page.goto(base + '/datasets/new');
    assert(await page.locator('input[name=icon]').isVisible());
    assert(await page.getByLabel('Name', {exact: true}).isVisible());
    assert(await page.getByLabel('Key', {exact: true}).isVisible());
    await layout(page, 'no-JavaScript create');
    await page.goto(base + '/datasets/polish-320/edit');
    await page.getByLabel('Dataset icon', {exact: true}).fill('cloud');
    await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: 'Save dataset', exact: true}).click()]);
    assert((await page.locator('.page-title use').getAttribute('href')).endsWith('#cloud'));
    await page.screenshot({path: `${output}/manual-icon-nojs.png`});
    await nojs.close();
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('No JavaScript fallback passed; no page errors or external requests.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
