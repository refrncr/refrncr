// Checks draft review summaries, field and record diffs, reordering, publication and access.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5184';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Disposable loopback required');
const output = process.env.REFRNCR_BROWSER_OUTPUT;
async function login(page, email) {
  await page.goto(`${base}/login`);
  await page.locator('#login-email').fill(email);
  await page.locator('#login-password').fill('correct horse battery staple');
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await page.waitForURL(`${base}/`);
}
async function noOverflow(page) {
  await page.locator('dialog[open]').evaluateAll(async dialogs => {
    await Promise.all(dialogs.flatMap(d => d.getAnimations({subtree:true}).map(a => a.finished)));
    for (const dialog of dialogs) {
      const r = dialog.getBoundingClientRect();
      assertBounds(r.left >= 0 && r.right <= window.innerWidth);
    }
    function assertBounds(value) { if (!value) throw Error('Dialog outside viewport'); }
  });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Horizontal page overflow');
}
async function renameReviewField(page, width, name) {
  const fields = `${base}/datasets/diff-${width}/concept?tab=fields`;
  await page.goto(fields);
  await page.waitForLoadState('load');
  await page.locator(`a[href='/datasets/diff-${width}/fields/review/edit']`).click();
  const panel = page.locator('dialog[open]');
  await panel.getByLabel('Name',{exact:true}).fill(name);
  await Promise.all([
    page.waitForNavigation({waitUntil:'load'}),
    panel.getByRole('button',{name:'Save field',exact:true}).click(),
  ]);
  assert.equal(page.url(),fields);
}
async function capture(page, name) {
  await page.waitForLoadState("load");
  await page.evaluate(()=>document.fonts.ready);
  await noOverflow(page);
  if (output) await page.screenshot({path:path.join(output, `${name}.png`), fullPage:true});
}
(async () => {
  if (output) await fs.mkdir(output, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  const results = [], errors = [];
  try {
    for (const width of [320,768,1440]) {
      const context = await browser.newContext({viewport:{width,height:950},colorScheme:'light'});
      context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
      const page = await context.newPage();
      await login(page,'owner@example.com');
      const versions = `${base}/datasets/diff-${width}/versions`;
      await page.goto(versions);
      const reviewEntry=page.getByRole('link',{name:'Review & publish',exact:true});
      await reviewEntry.focus(); await reviewEntry.press('Enter');
      await page.getByRole('heading',{name:'Summary',exact:true}).waitFor();
      const summary = page.url();
      assert.match(await page.locator('main').innerText(),/You’re working on a draft/);
      assert.equal(await page.getByRole('table',{name:'Change counts'}).count(),1);
      assert.equal(await page.getByRole('rowheader',{name:'Fields',exact:true}).count(),1);
      await capture(page,`summary-${width}`);
      await page.getByRole('link',{name:'Fields',exact:true}).click();
      assert.match(await page.locator('main').innerText(),/Renamed review field/);
      assert.equal(await page.getByRole('columnheader',{name:'Field',exact:true}).count(),1);
      assert.equal(await page.getByRole('columnheader',{name:'Status',exact:true}).count(),0);
      assert.equal(await page.getByRole('columnheader',{name:'Property',exact:true}).count(),1);
      const headingStyle = await page.locator('.review-field-heading').first().evaluate(el => ({
        family: getComputedStyle(el).fontFamily,
        bodyFamily: getComputedStyle(document.body).fontFamily,
        transform: getComputedStyle(el).textTransform,
      }));
      assert.equal(headingStyle.family, headingStyle.bodyFamily);
      assert.equal(headingStyle.transform, 'none');
      assert.equal(await page.locator('.review-field-heading .badge').count(),1);
      const fieldRowgroup = page.locator('.review-fields-table tbody th[scope="rowgroup"]').first();
      assert(await fieldRowgroup.evaluate(el => getComputedStyle(el).display === 'table-cell'));
      assert(await fieldRowgroup.evaluate(el => el.rowSpan > 1));
      const firstProperty = page.locator('.review-fields-table tbody').first().locator('tr').first().locator('th[scope="row"]');
      const secondProperty = page.locator('.review-fields-table tbody').first().locator('tr').nth(1).locator('th[scope="row"]');
      const [firstPropertyBox, secondPropertyBox] = await Promise.all([firstProperty.boundingBox(), secondProperty.boundingBox()]);
      assert(Math.abs(firstPropertyBox.x - secondPropertyBox.x) < 1, 'rowspan keeps property headers in the same column');
      assert.equal(await page.locator('.record-toolbar + .review-fields-table').count(),1);
      if (width === 320) {
        assert(await page.locator('.review-fields-table').evaluate(el=>el.scrollWidth>el.clientWidth),'field table scrolls inside its frame on phone width');
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'field table does not overflow the page');
      }
      for (const theme of ['light','dark']) {
        await page.locator('html').evaluate((el,value)=>el.dataset.theme=value,theme);
        await capture(page,`fields-${theme}-${width}`);
      }
      await page.locator('html').evaluate(el=>delete el.dataset.theme);
      await page.waitForLoadState('load');
      await page.getByRole('link',{name:'Renamed review field',exact:true}).click();
      await page.locator('#diff-detail-heading').waitFor();
      await page.waitForFunction(()=>document.activeElement.id === 'diff-detail-heading');
      assert.equal(await page.locator('dialog[open]').getByRole('columnheader',{name:'Before',exact:true}).count(),1);
      await page.keyboard.press('Escape'); await page.locator('dialog[open]').waitFor({state:'hidden'});
      await page.getByRole('link',{name:'Records',exact:true}).click();
      assert.equal(await page.locator('.review-record-toolbar .search-field').count(),1);
      assert.equal(await page.locator('.review-record-toolbar .status-toggle').count(),3);
      assert.equal(await page.locator('.review-record-toolbar .status-toggle[aria-pressed="false"]').count(),3);
      for (const theme of ['light','dark']) {
        await page.locator('html').evaluate((el,value)=>el.dataset.theme=value,theme);
        await capture(page,`records-${theme}-${width}`);
      }
      await page.locator('html').evaluate(el=>delete el.dataset.theme);
      const reviewSearch = page.getByRole('searchbox',{name:'Search old or new identifier'});
      await reviewSearch.fill('ADDED00');
      await reviewSearch.press('Enter');
      assert.equal(new URL(page.url()).searchParams.get('q'),'ADDED00');
      assert.equal(await page.locator('table tbody tr').count(),1);
      const chipCounts = async () => Object.fromEntries(await Promise.all(['Added','Changed','Deleted'].map(async status => {
        const label = await page.getByRole('button',{name:new RegExp(`^${status}`)}).innerText();
        return [status, Number(label.match(/\d+/)?.[0] ?? 0)];
      })));
      const countsBeforeStatusFilter = await chipCounts();
      await page.getByRole('button',{name:/^Added/}).click();
      assert.deepEqual(new URL(page.url()).searchParams.getAll('status'),['Added']);
      assert.equal(await page.locator('table tbody tr').count(),1);
      assert.deepEqual(await chipCounts(),countsBeforeStatusFilter);
      await page.getByRole('button',{name:/^Changed/}).click();
      assert.deepEqual(new URL(page.url()).searchParams.getAll('status'),['Changed']);
      assert.equal(await page.locator('table tbody tr').count(),0);
      await page.getByRole('button',{name:/^Added/}).click();
      assert.deepEqual(new URL(page.url()).searchParams.getAll('status'),['Added']);
      await page.getByRole('link',{name:'Clear search'}).click();
      assert.equal(new URL(page.url()).searchParams.get('q') ?? '', '');
      assert.deepEqual(new URL(page.url()).searchParams.getAll('status'),['Added']);
      await page.getByRole('button',{name:/^Added/}).click();
      assert.deepEqual(new URL(page.url()).searchParams.getAll('status'),[]);
      assert.equal(await page.locator('table tbody tr').count(),50);
      await page.getByRole('link',{name:'Next page',exact:true}).click();
      assert.equal(await page.locator('table tbody tr').count(),3);
      await page.getByRole('link',{name:'Previous page',exact:true}).click();
      assert.equal(await page.locator('table tbody tr').count(),50);
      await page.waitForLoadState('load');
      const opener = page.getByRole('link',{name:'Review record ADDED00',exact:true});
      await opener.focus(); await opener.press('Enter');
      await page.locator('#diff-detail-heading').waitFor();
      await page.waitForFunction(()=>document.activeElement.id === 'diff-detail-heading');
      assert.equal(await page.locator('dialog[open] table tbody tr').count(),50);
      assert.match(await page.locator('dialog[open]').innerText(),/No value/);
      const requests=[];
      const observe = req => requests.push(req.method());
      page.on('request',observe);
      await page.getByRole('link',{name:'Next values',exact:true}).click();
      await page.locator('dialog[open]').getByText(/Page 2/).waitFor();
      await page.waitForFunction(()=>document.activeElement.id === 'review-panel-title');
      assert.equal(await page.locator('dialog[open] table tbody tr').count(),4);
      await page.getByRole('link',{name:'Previous values',exact:true}).click();
      await page.locator('dialog[open]').getByText(/Page 1/).waitFor();
      for (let n=0;n<12;n++) {
        await page.keyboard.press(n%2?'Shift+Tab':'Tab');
        assert(await page.evaluate(()=>!!document.activeElement.closest('dialog[open]')),'Focus escaped');
      }
      await capture(page,`added-panel-${width}`);
      await page.keyboard.press('Escape');
      await page.locator('dialog[open]').waitFor({state:'hidden'});
      assert(await opener.evaluate(el=>el===document.activeElement),'Escape focus restoration');
      await opener.click(); await page.locator('#diff-detail-heading').waitFor();
      await page.locator('dialog[open]').getByRole('link',{name:'Close',exact:true}).click();
      await page.locator('dialog[open]').waitFor({state:'hidden'});
      assert(await opener.evaluate(el=>el===document.activeElement),'Close focus restoration');
      page.off('request',observe); assert(!requests.includes('POST'),'Read-only panel mutated');
      await page.getByRole('button',{name:/^Changed/}).click();
      assert.equal(await page.locator('table tbody tr').count(),1);
      for (const term of ['OLD','NEW']) {
        await page.getByLabel('Search old or new identifier',{exact:true}).fill(term);
        await Promise.all([
          page.waitForURL(url=>new URL(url).searchParams.get('q')===term),
          page.getByRole('button',{name:'Search',exact:true}).click(),
        ]);
        assert.equal(await page.locator('table tbody tr').count(),1);
      }
      const changedURL=page.url();
      await page.waitForLoadState('load');
      const changed=page.getByRole('link',{name:'Review record NEW',exact:true});
      await changed.focus(); await changed.press('Enter');
      await page.locator('#diff-detail-heading').waitFor();
      await page.waitForFunction(()=>document.activeElement.id === 'diff-detail-heading');
      assert.equal(await page.locator('dialog[open] table tbody tr').count(),2);
      assert.match(await page.locator('dialog[open]').innerText(),/Before value/);
      assert.equal(await page.evaluate(()=>window.unsafe),undefined);
      await capture(page,`changed-panel-${width}`);
      await page.getByRole('button',{name:'Close panel',exact:true}).click();
      await page.locator('dialog[open]').waitFor({state:'hidden'});
      assert.equal(page.url(),changedURL);
      assert(await changed.evaluate(el=>el===document.activeElement));
      await page.getByRole('link',{name:'Clear search',exact:true}).click();
      await page.getByRole('button',{name:/^Changed/}).click();
      await page.getByRole('button',{name:/^Deleted/}).click();
      await page.waitForLoadState('load');
      await page.getByRole('link',{name:'Review record REMOVED',exact:true}).click();
      await page.locator('#diff-detail-heading').waitFor();
      await page.waitForFunction(()=>document.activeElement.id === 'diff-detail-heading');
      assert.match(await page.locator('dialog[open]').innerText(),/Removed value/);
      await page.keyboard.press('Escape'); await page.locator('dialog[open]').waitFor({state:'hidden'});
      await page.goto(summary);
      const publish = page.getByRole('link',{name:'Publish version',exact:true});
      await publish.click(); await page.locator('#publication-date').waitFor();
      await page.locator('dialog[open]').getByRole('link',{name:'Review & publish',exact:true}).click();
      await page.getByRole('heading',{name:'Summary',exact:true}).waitFor();
      // A second tab changes versioned field metadata through the existing UI.
      const edit=await context.newPage();
      await renameReviewField(edit,width,`Changed in another tab ${width}`);
      await page.getByRole('link',{name:'Records',exact:true}).click();
      await page.getByRole('heading',{name:'Review unavailable',exact:true}).waitFor();
      assert(!/Before value|Renamed review field/.test(await page.locator('main').innerText()));
      // Programmatic history navigation can overlap Chromium's synthetic
      // cross-document transition; reduced motion keeps this a Back-flow test.
      await page.emulateMedia({reducedMotion:'reduce'});
      await page.goBack();
      await page.getByRole('heading',{name:'Review unavailable',exact:true}).waitFor();
      await page.emulateMedia({reducedMotion:'no-preference'});
      await page.reload();
      await page.getByRole('link',{name:'Reload review',exact:true}).click();
      await page.getByRole('heading',{name:'Summary',exact:true}).waitFor();
      await capture(page,`reloaded-${width}`);
      await page.getByRole('link',{name:'Publish version',exact:true}).click();
      await page.locator('#publication-date').waitFor();
      const reviewedRevision=await page.locator('#publication-form input[name="expected_revision"]').inputValue();
      await renameReviewField(edit,width,`Changed after publication panel ${width}`);
      assert.equal(await page.locator('#publication-form input[name="expected_revision"]').inputValue(),reviewedRevision);
      await page.locator('#publication-confirmed').check();
      await page.locator('dialog[open]').getByRole('button',{name:'Publish version',exact:true}).click();
      await page.getByRole('heading',{name:'Review unavailable',exact:true}).waitFor();
      assert.equal(await page.locator('#publication-form').count(),0);
      await page.getByRole('link',{name:'Reload review',exact:true}).click();
      await page.getByRole('heading',{name:'Summary',exact:true}).waitFor();
      await page.getByRole('link',{name:'Publish version',exact:true}).click();
      await page.locator('#publication-date').waitFor();
      const currentDate=await page.locator('#publication-date').inputValue();
      const tomorrow=new Date(Date.parse(`${currentDate}T00:00:00Z`)+86400000).toISOString().slice(0,10);
      await page.locator('#publication-date').fill(tomorrow);
      await page.locator('#publication-confirmed').check();
      await page.locator('dialog[open]').getByRole('button',{name:'Publish version',exact:true}).click();
      await page.waitForURL(`${versions}?notice=published`);
      const publishedVersion = page.getByRole('rowheader').getByRole('link',{name:'Version 2',exact:true});
      assert.equal(await publishedVersion.count(),1);
      assert.equal(await publishedVersion.isVisible(),true);
      assert.match(await publishedVersion.locator('xpath=ancestor::tr').innerText(),/Scheduled/);
      results.push({width, scenarios:['Versions and Publish entrypoints','Summary/Fields/Records','filters','old/new search','pagination','broad detail pagination','null and long values','keyboard/focus/Escape/Close','read-only GET','second-tab stale','Back/Refresh/Reload','stale publication POST rejected','existing publication','no overflow']});
      await context.close();
    }
    const deniedContext=await browser.newContext();const denied=await deniedContext.newPage();
    await login(denied,'reader@example.com');
    await denied.goto(`${base}/datasets/diff-1440/versions`);
    assert.equal(await denied.getByRole('link',{name:'Review & publish',exact:true}).count(),0);
    const response=await denied.goto(`${base}/datasets/diff-1440/versions/review`);
    assert.equal(response.status(),404);
    await deniedContext.close();
    assert.deepEqual(errors,[]);
    const report={browser:await browser.version(),results,denied:'404; no entrypoint',pageErrors:errors};
    if(output) await fs.writeFile(path.join(output,'results.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
