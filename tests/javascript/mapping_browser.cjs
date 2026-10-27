/* Disposable loopback browser checks for the shared mapping table variant. */
const assert = require('node:assert/strict');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5193';
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname)) throw Error('Loopback only');
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    for (const width of [320, 768, 1440]) {
      const context = await browser.newContext({viewport: {width, height: 1000}, reducedMotion: 'reduce'});
      const page = await context.newPage();
      await page.goto(base + '/login');
      await page.locator('#login-email').fill('admin@example.com');
      await page.locator('#login-password').fill('correct horse battery staple');
      await page.getByRole('button', {name: 'Sign in', exact: true}).click();
      await page.waitForURL(base + '/');
      await page.goto(base + '/datasets/mapping-browser/concept/records');
      const table = page.getByRole('table', {name: 'Records', exact: true});
      assert.equal(await table.count(), 1);
      assert.equal(await table.locator('tbody tr').count(), 2);
      assert.match(await table.textContent(), /Source · Currencies V1/);
      assert.equal(await page.locator('[data-panel-mode="delete"]').count(), 0);
      assert.equal(await page.getByRole('link', {name: 'Import', exact: true}).count(), 1);
      const layout = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth, row: getComputedStyle(document.querySelector('.mapping-record-table tbody tr')).display}));
      assert(layout.scroll <= width, JSON.stringify(layout));
      for (const button of await page.locator('.record-criteria-toolbar button').all()) {
        const box = await button.boundingBox();
        if (box) assert(box.x + box.width <= width, `Clipped record control at ${width}`);
      }
      if (width === 320) {
        assert.equal(layout.row, 'block');
        const arrow = await table.locator('tbody .mapping-direction-column').first().boundingBox();
        const row = await table.locator('tbody tr').first().boundingBox();
        assert(Math.abs(arrow.width - row.width) <= 2, 'Arrow strip must span the mobile card');
      }
      if (width > 640) {
        const groups = await table.evaluate(el => {
          const style = selector => getComputedStyle(el.querySelector(selector));
          return {
            source: style('tbody .mapping-source-cell').backgroundColor,
            target: style('tbody .mapping-target-cell').backgroundColor,
            sourceHeading: style('.mapping-group-headings .mapping-source-cell').backgroundColor,
            targetHeading: style('.mapping-group-headings .mapping-target-cell').backgroundColor,
            targetColumnHeading: style('thead tr:last-child .mapping-target-cell').backgroundColor,
            borders: [...el.querySelectorAll('tbody tr:first-child td')].map(cell => getComputedStyle(cell).borderBottom),
            frames: [...el.querySelectorAll('.mapping-group-headings [scope="colgroup"]')].map(cell => {
              const cs = getComputedStyle(cell);
              return [cs.borderLeftWidth, cs.borderRightWidth, cs.borderTopWidth];
            }),
            status: style('.mapping-group-headings .record-system-status').backgroundColor,
          };
        });
        assert.notEqual(groups.source, groups.target);
        assert.notEqual(groups.sourceHeading, groups.targetHeading);
        assert.notEqual(groups.sourceHeading, groups.source);
        assert.notEqual(groups.targetHeading, groups.target);
        assert.equal(groups.targetHeading, groups.targetColumnHeading);
        assert.equal(groups.targetHeading, groups.status);
        assert.notEqual(groups.status, 'rgba(0, 0, 0, 0)');
        assert.equal(new Set(groups.borders).size, 1, JSON.stringify(groups));
        assert.deepEqual(groups.frames, [['1px', '1px', '1px'], ['1px', '1px', '1px']]);
      }
      if (process.env.REFRNCR_BROWSER_OUTPUT) await page.screenshot({path: `${process.env.REFRNCR_BROWSER_OUTPUT}/mapping-${width}.png`, fullPage: true, animations: 'disabled'});
      await table.locator('[data-row-primary]').first().click();
      const recordDialog = page.getByRole('dialog');
      await recordDialog.waitFor();
      assert.equal(await page.evaluate(() => document.activeElement.name), await recordDialog.locator('input:not([readonly]):not([type="hidden"])').first().getAttribute('name'));
      const readonly = recordDialog.locator('input[readonly]');
      assert.equal(await readonly.count(), 1);
      assert.notEqual(await readonly.evaluate(el => getComputedStyle(el).backgroundColor), await recordDialog.locator('input[name="target"]').evaluate(el => getComputedStyle(el).backgroundColor));
      if (process.env.REFRNCR_BROWSER_OUTPUT) await page.screenshot({path: `${process.env.REFRNCR_BROWSER_OUTPUT}/edit-${width}.png`, fullPage: true, animations: 'disabled'});
      await page.keyboard.press('Escape');
      await recordDialog.waitFor({state: 'hidden'});
      await page.goto(base + '/datasets/mapping-browser/concept?tab=fields');
      assert.equal(await page.getByRole('link', {name: 'Add source field', exact: true}).count(), 1);
      const fieldBlocks = await page.locator('.mapping-fields-block').evaluateAll(blocks => blocks.map(block => {
        const cs = getComputedStyle(block);
        return {border: cs.border, padding: cs.padding, table: getComputedStyle(block.querySelector('.configuration-table')).backgroundColor};
      }));
      assert.equal(fieldBlocks[0].border, fieldBlocks[1].border);
      assert.match(fieldBlocks[1].border, /^1px solid/);
      assert.equal(fieldBlocks[0].padding, fieldBlocks[1].padding);
      assert.notEqual(fieldBlocks[0].table, fieldBlocks[1].table);
      await page.getByRole('link', {name: 'Add target field', exact: true}).click();
      await page.getByRole('dialog').getByRole('heading', {name: 'Add target field', exact: true}).waitFor();
      assert.equal(await page.getByRole('dialog').getByText('Field role', {exact: true}).count(), 0);
      await page.keyboard.press('Escape');
      await page.getByRole('dialog').waitFor({state: 'hidden'});
      await page.waitForFunction(() => document.activeElement?.textContent.trim() === 'Add target field');
      await page.locator('[data-field-order-ready]').waitFor();
      const handles = page.locator('.mapping-fields-block').last().locator('.field-drag-handle');
      await handles.last().focus();
      await Promise.all([
        page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/fields/order')),
        handles.last().press('ArrowUp')
      ]);
      assert.match(await page.locator('#field-order-status').textContent(), /moved/);
      if (process.env.REFRNCR_BROWSER_OUTPUT) await page.screenshot({path: `${process.env.REFRNCR_BROWSER_OUTPUT}/fields-${width}.png`, fullPage: true, animations: 'disabled'});
      await page.goto(base + '/datasets/mapping-browser/versions/review');
      await page.getByRole('link', {name: 'Records', exact: true}).click();
      await page.waitForLoadState('networkidle');
      await page.evaluate(() => document.fonts.ready);
      assert.equal(await page.locator('.mobile-bar').count(), 1);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.equal(await page.getByRole('table', {name: 'Mapping record changes', exact: true}).count(), 1);
      if (process.env.REFRNCR_BROWSER_OUTPUT) await page.screenshot({path: `${process.env.REFRNCR_BROWSER_OUTPUT}/review-${width}.png`, fullPage: true, animations: 'disabled'});
      await context.close();
    }
    console.log('Mapping desktop, tablet, mobile, panels and keyboard reordering passed.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
