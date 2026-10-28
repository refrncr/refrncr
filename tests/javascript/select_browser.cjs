// Checks enhanced select keyboard navigation, validation, focus transitions and CSP behavior.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5246';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Loopback fixture required');

async function axe(page, label) {
  if (!process.env.REFRNCR_AXE_SOURCE) return;
  if (!await page.evaluate(() => Boolean(window.axe))) await require('./browser_policy.cjs').injectAxe(page);
  const violations = await page.evaluate(async () => (await axe.run(document, {preload: false, runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']}})).violations.map(v => ({id: v.id, nodes: v.nodes.map(n => n.target)})));
  assert.deepEqual(violations, [], `${label}: accessibility violations`);
}
const expanded = (trigger) => trigger.getAttribute('aria-expanded');
const nativeValue = (page, id) => page.locator(`#${id}`).evaluate(select => select.value);
const activeId = (page) => page.evaluate(() => document.activeElement.id);
const activeOption = async (page, owner) => {
  const id = await page.locator(owner).getAttribute('aria-activedescendant');
  return id ? page.locator(`#${id}`).innerText() : null;
};

async function login(page) {
  await page.goto(base + '/login');
  await page.getByLabel('Email address').fill('admin@example.com');
  await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await page.waitForURL(base + '/');
}

(async () => {
  const browser = await chromium.launch(process.env.REFRNCR_CHROMIUM ? {executablePath: process.env.REFRNCR_CHROMIUM} : {});
  for (const width of [1280, 768]) {
    const page = await (await browser.newContext({viewport: {width, height: 800}})).newPage();
    await login(page);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/select-fixture');

    // Enhanced: the label names the trigger; the native select is out of sight and out of the tab order.
    const country = page.getByRole('combobox', {name: 'Country'});
    const status = page.getByRole('combobox', {name: 'Status'});
    assert.equal(await country.count(), 1);
    assert.equal(await page.locator('#country').getAttribute('tabindex'), '-1');
    assert.equal(await page.locator('#country').evaluate(select => select.getBoundingClientRect().width <= 1), true);
    assert.equal(await country.innerText(), 'Choose a country');
    assert.match(await status.innerText(), /Published/i);
    await axe(page, `${width} closed`);

    // Keyboard on a short list: no search field, arrows move, Enter chooses, focus returns.
    await status.focus();
    await page.keyboard.press('ArrowDown');
    assert.equal(await expanded(status), 'true');
    assert.equal(await page.locator('#status-popup .select-search').isVisible(), false);
    assert.match(await activeOption(page, '#status-trigger'), /Published/i);
    await page.keyboard.press('ArrowDown');
    assert.match(await activeOption(page, '#status-trigger'), /Published/i, 'disabled option is skipped');
    await page.keyboard.press('ArrowUp');
    assert.match(await activeOption(page, '#status-trigger'), /Draft/i);
    await page.keyboard.press('Enter');
    assert.equal(await expanded(status), 'false');
    assert.equal(await activeId(page), 'status-trigger');
    assert.equal(await nativeValue(page, 'status'), 'draft');
    assert.equal(await page.locator('#changes').innerText(), '1', 'change event fires once');

    // Escape and outside clicks close without changing the value.
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Escape');
    assert.equal(await expanded(status), 'false');
    assert.equal(await nativeValue(page, 'status'), 'draft');
    assert.equal(await activeId(page), 'status-trigger');
    await status.click();
    assert.equal(await expanded(status), 'true');
    await page.locator('h1').click();
    assert.equal(await expanded(status), 'false');
    assert.equal(await nativeValue(page, 'status'), 'draft');

    // Typeahead jumps to the first matching option.
    await status.focus();
    await page.keyboard.press('p');
    assert.equal(await expanded(status), 'true');
    assert.match(await activeOption(page, '#status-trigger'), /Published/i);
    await page.keyboard.press('Escape');

    // More than 10 options: a search field that filters on name and code, with a count and an empty state.
    await country.click();
    assert.equal(await activeId(page), 'country-search');
    assert.equal(await page.locator('#country-popup .select-footer').innerText(), '14 of 14 countries'.toUpperCase());
    await page.keyboard.type('new');
    assert.deepEqual(await page.locator('#country-listbox .select-option:visible .select-option-label').allInnerTexts(), ['New Zealand', 'Papua New Guinea']);
    assert.equal(await page.locator('#country-listbox mark').first().innerText(), 'New');
    await page.locator('#country-search').fill('be');
    assert.deepEqual(await page.locator('#country-listbox .select-option:visible .select-option-label').allInnerTexts(), ['Belgium']);
    await page.locator('#country-search').fill('holland');
    assert.match(await page.locator('#country-popup .select-empty').innerText(), /No countries match “holland”/);
    await page.waitForTimeout(350);
    assert.match(await page.locator('#country-popup [role="status"]').innerText(), /0 of 14 countries/);
    await page.locator('#country-search').fill('neth');
    await page.keyboard.press('Enter');
    assert.equal(await expanded(country), 'false');
    assert.equal(await nativeValue(page, 'country'), 'NL');
    assert.match(await country.innerText(), /Netherlands/);
    assert.equal(await activeId(page), 'country-trigger');

    // Tab from the open list closes it and moves on to the next field.
    await country.click();
    await page.keyboard.press('Tab');
    assert.equal(await expanded(country), 'false');
    assert.equal(await activeId(page), 'status-trigger');

    // Compact variant in dense tables.
    const compact = await page.locator('#target-trigger').boundingBox();
    assert.equal(Math.round(compact.height), 36);
    const regular = await page.locator('#country-trigger').boundingBox();
    assert.equal(Math.round(regular.height), 44);

    // Near the bottom of a scrolling panel the popup opens upward and stays in view.
    await page.locator('#fixture-panel').evaluate(panel => { panel.scrollTop = 0; });
    await page.locator('#fixture-panel').evaluate(panel => panel.scrollIntoView({block: 'end'}));
    const team = page.getByRole('combobox', {name: 'Owner team'});
    await page.evaluate(() => {
      const trigger = document.getElementById('team-trigger');
      const panel = document.getElementById('fixture-panel');
      panel.scrollTop = trigger.offsetTop;
    });
    await team.click();
    const placement = await page.locator('#team-popup').getAttribute('data-placement');
    const popup = await page.locator('#team-popup').boundingBox();
    const triggerBox = await team.boundingBox();
    const panelBox = await page.locator('#fixture-panel').boundingBox();
    if (placement === 'top') assert.ok(popup.y + popup.height <= triggerBox.y + 1, 'popup sits above the trigger');
    else assert.ok(popup.y >= triggerBox.y + triggerBox.height - 1, 'popup sits below the trigger');
    assert.ok(popup.y >= 0 && popup.y + popup.height <= 800, 'popup stays in the viewport');
    assert.ok(popup.y + 1 >= panelBox.y && popup.y + popup.height <= panelBox.y + panelBox.height + 1, 'popup stays inside the scrolling panel');
    await axe(page, `${width} open`);
    await page.keyboard.press('Escape');

    // Options added later by other scripts appear in the list.
    await page.locator('#add-option').click();
    await team.click();
    assert.equal(await page.locator('#team-listbox').getByText('Late team').count(), 1);
    await page.keyboard.press('Escape');

    // Values and focus set by other scripts reach the trigger.
    await page.locator('#set-value').click();
    assert.match(await page.getByRole('combobox', {name: 'Country'}).innerText(), /Germany/);
    await page.locator('#focus-native').click();
    assert.equal(await activeId(page), 'status-trigger');

    // A select inside its label: the label names the trigger and clicking the text opens the list.
    const datatype = page.getByRole('combobox', {name: /^Data type/});
    assert.equal(await datatype.count(), 1);
    await page.locator('label', {has: page.locator('select[name=data_type]')}).click({position: {x: 4, y: 6}});
    assert.equal(await datatype.getAttribute('aria-expanded'), 'true');
    await page.getByRole('option', {name: 'Whole number'}).click();
    assert.equal(await page.locator('select[name=data_type]').inputValue(), 'integer');
    assert.equal(await datatype.getAttribute('aria-expanded'), 'false');

    // Required without a value: no submission, the field is marked and focused; a choice clears it.
    await page.goto(base + '/select-fixture');
    await page.getByRole('button', {name: 'Send'}).click();
    assert.match(page.url(), /\/select-fixture$/);
    assert.equal(await page.locator('#country-trigger').getAttribute('aria-invalid'), 'true');
    assert.equal(await activeId(page), 'country-trigger');
    assert.equal(await page.locator('#country-validation').count(), 1);
    await page.getByRole('combobox', {name: 'Country'}).click();
    await page.keyboard.type('swe');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#country-trigger').getAttribute('aria-invalid'), null);
    assert.equal(await page.locator('#country-validation').count(), 0);
    await page.getByRole('combobox', {name: 'Target field'}).click();
    await page.getByRole('option', {name: 'valid_from'}).click();
    await page.getByRole('button', {name: 'Send'}).click();
    await page.waitForURL(/echo/);
    assert.deepEqual(JSON.parse(await page.locator('body').innerText()), {country: 'SE', status: 'published', target: 'valid_from'});

    // Dark (Forest): the same checks for contrast.
    await page.goto(base + '/select-fixture');
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    await page.getByRole('combobox', {name: 'Country'}).click();
    await axe(page, `${width} dark open`);
    assert.deepEqual(errors, [], 'no script errors');
    await page.close();
  }

  // Without JavaScript the native select is used and the form still submits.
  const context = await browser.newContext({javaScriptEnabled: false});
  const plain = await context.newPage();
  await login(plain);
  await plain.goto(base + '/select-fixture');
  assert.equal(await plain.getByRole('combobox', {name: 'Country'}).evaluate(node => node.tagName), 'SELECT');
  await plain.locator('#country').selectOption('BE');
  await plain.getByRole('button', {name: 'Send'}).click();
  await plain.waitForURL(/echo/);
  assert.equal(JSON.parse(await plain.locator('body').innerText()).country, 'BE');

  // The audit log uses the component in the real layout.
  const page = await (await browser.newContext({viewport: {width: 1280, height: 900}})).newPage();
  await login(page);
  await page.goto(base + '/settings/audit-log');
  for (const name of ['Actor', 'Action', 'Object type']) assert.equal(await page.getByRole('combobox', {name, exact: true}).count(), 1, name);
  const action = page.getByRole('combobox', {name: 'Action', exact: true});
  await action.click();
  assert.equal(await action.getAttribute('aria-expanded'), 'true');
  await axe(page, 'audit log open');
  await browser.close();
  console.log('Select component: keyboard, search, flip, validation, no-JavaScript and audit log passed');
})().catch(error => { console.error(error); process.exit(1); });
