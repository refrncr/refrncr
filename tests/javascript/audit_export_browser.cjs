// Checks audit export controls, downloaded evidence and authorization boundaries.
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
    const groupText = {
      reference: [
        'Reporting table › Ready for BI tools and pivot tables Excel CSV',
        'File for editing › Edit the records and import them again Excel',
        'For systems › Same structure as the API JSON',
      ],
      mapping: [
        'Reporting table › Ready for BI tools and pivot tables Excel CSV',
        'File for editing › Edit the mappings and import them again Excel',
        'For systems › Same structure as the API JSON',
      ],
      hierarchy: [
        'Reporting table › Ready for BI tools and pivot tables Excel CSV',
        'File for editing › Edit the hierarchy and import it again Excel',
        'For systems › Same structure as the API JSON',
      ],
    };
    const exportOnlyGroupText = Object.fromEntries(
      Object.entries(groupText).map(([kind, groups]) => [kind, [groups[0], groups[2]]]),
    );
    async function assertMenu(path, kind, label) {
      await page.goto(base + path);
      const action = page.locator('.export-menu').filter({has: page.getByText(label, {exact: true})});
      await action.locator('summary').click();
      const groups = action.locator('.export-menu-group');
      assert.equal(await groups.count(), 3, `${kind} ${path} has three export groups`);
      assert.deepEqual(
        (await groups.allInnerTexts()).map(text => text.replace(/\s+/g, ' ').trim()),
        groupText[kind],
      );
      return action;
    }
    for (const [kind, key] of [
      ['reference', 'row-check'],
      ['mapping', 'mapping-check'],
      ['hierarchy', 'reporting-check'],
    ]) {
      await assertMenu(`/datasets/${key}/concept/records`, kind, 'Export draft');
      await assertMenu(`/datasets/${key}/records`, kind, 'Export');
    }
    await assertMenu('/datasets/row-check/concept', 'reference', 'Export draft');
    await assertMenu('/datasets/row-check', 'reference', 'Export');
    console.log('PASS: reference, mapping and hierarchy share grouped draft and published menus');
    for (const [label, extension] of [['CSV', 'csv'], ['Excel', 'xlsx'], ['JSON', 'json']]) {
      await page.goto(base + '/datasets/row-check/records');
      await page.locator('.export-menu summary').click();
      const pending = page.waitForEvent('download');
      const group = label === 'JSON' ? 2 : 0;
      await page.locator('.export-menu-group').nth(group).getByRole('link', {name: label, exact: true}).click();
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
    assert.match(await menu.innerText(), /For systems ›\s+Same structure as the API/);
    assert.equal(await menu.getByRole('link', {name: 'JSON', exact: true}).count(), 1);
    assert.equal((await menu.innerText()).includes('Copy API link'), false);
    const groups = menu.locator('.export-menu-group');
    for (const [groupIndex, label, filename] of [
      [0, 'Excel', 'reporting-check-concept-reporting.xlsx'],
      [0, 'CSV', 'reporting-check-concept-reporting.csv'],
      [1, 'Excel', 'reporting-check.xlsx'],
      [2, 'JSON', 'reporting-check.json'],
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
    console.log('PASS: hierarchy menu keeps reporting/editing variants and exposes its existing JSON export');
    await page.goto(base + '/datasets/row-check/records');
    await page.goto(base + '/');
    const card = page.locator('.dataset-card[data-dataset-key="row-check"]');
    const cardExport = card.locator('.dataset-card-foot .export-menu');
    await cardExport.locator('summary').click();
    assert.deepEqual(
      (await cardExport.locator('.export-menu-group').allInnerTexts())
        .map(text => text.replace(/\s+/g, ' ').trim()),
      groupText.reference,
    );
    assert.equal(await cardExport.locator('summary').innerText(), 'Export');
    console.log('PASS: dataset card uses the same export menu as the published dataset page');
    const exportOnly = await browser.newContext();
    const exportOnlyPage = await exportOnly.newPage();
    await login(exportOnlyPage, 'exporter-only@example.com');
    async function assertExportOnlyMenu(path, kind) {
      await exportOnlyPage.goto(base + path);
      const action = exportOnlyPage.locator('.export-menu')
        .filter({has: exportOnlyPage.getByText('Export', {exact: true})});
      await action.locator('summary').click();
      assert.deepEqual(
        (await action.locator('.export-menu-group').allInnerTexts())
          .map(text => text.replace(/\s+/g, ' ').trim()),
        exportOnlyGroupText[kind],
      );
      assert.equal(await action.getByText('File for editing', {exact: true}).count(), 0);
    }
    for (const [kind, key] of [
      ['reference', 'row-check'],
      ['mapping', 'mapping-check'],
      ['hierarchy', 'reporting-check'],
    ]) {
      await assertExportOnlyMenu(`/datasets/${key}/records`, kind);
      const draft = await exportOnlyPage.goto(`${base}/datasets/${key}/concept/records`);
      assert.equal(draft.status(), 404, 'export-only users cannot view a draft');
    }
    await assertExportOnlyMenu('/datasets/row-check', 'reference');
    await exportOnlyPage.goto(base + '/');
    const exportOnlyCard = exportOnlyPage.locator('.dataset-card[data-dataset-key="row-check"]');
    await exportOnlyCard.locator('.dataset-card-foot .export-menu summary').click();
    assert.deepEqual(
      (await exportOnlyCard.locator('.export-menu-group').allInnerTexts())
        .map(text => text.replace(/\s+/g, ' ').trim()),
      exportOnlyGroupText.reference,
    );
    console.log('PASS: export-only user sees reporting/system groups on pages and card, but no editing group or draft');
    await exportOnly.close();
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
