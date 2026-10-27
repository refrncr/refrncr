/* Explicit lifecycle in real Chrome, against disposable loopback data only. */
// Covers current and draft Concept records, actions, responsive layout and accessibility.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5185';
if (!['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname)) throw Error('Disposable loopback only');
const output = process.env.REFRNCR_BROWSER_OUTPUT;
async function shot(page,name) {
  await page.waitForLoadState('load');
  await page.evaluate(()=>document.fonts.ready);
  await page.locator('dialog[open]').evaluateAll(async dialogs=>{
    await Promise.all(dialogs.flatMap(d=>d.getAnimations({subtree:true}).map(a=>a.finished)));
    for(const dialog of dialogs){const bounds=dialog.getBoundingClientRect();if(bounds.left<0||bounds.right>innerWidth)throw Error('Dialog outside viewport');}
  });
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'Page overflow');
  if (output) await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
}
async function login(page,email) {
  await page.goto(base+'/login'); await page.locator('#login-email').fill(email);
  await page.locator('#login-password').fill('correct horse battery staple');
  await page.getByRole('button',{name:'Sign in',exact:true}).click(); await page.waitForURL(base+'/');
}
(async()=>{
  if(output) await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  const results=[],errors=[];
  try {
    for(const width of [320,768,1440]) {
      const context=await browser.newContext({viewport:{width,height:950},colorScheme:'light'});
      const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
      await login(page,'owner@example.com');
      await page.goto(base+'/datasets'); await shot(page,`list-before-${width}`);
      const key=`lifecycle-${width}`, current=`${base}/datasets/${key}/records`, versions=`${base}/datasets/${key}/versions`;
      await page.getByRole('link',{name:`Payments and detailed reference classifications ${width}`,exact:true}).click();
      assert.equal(page.url(),current);
      assert.equal(await page.getByRole('button',{name:'Add record',exact:true}).count(),0);
      assert.equal(await page.getByRole('link',{name:'Configure',exact:true}).count(),0);
      assert.equal(await page.getByRole('link',{name:'Import',exact:true}).count(),0);
      assert.equal(await page.getByRole('link',{name:'Versions',exact:true}).count(),1);
      assert.equal(await page.locator('.page-header .page-actions').getByRole('link',{name:'Versions',exact:true}).count(),1);
      assert.deepEqual(await page.locator("tbody td.record-system-status .badge-published").allTextContents(),[]);
      await shot(page,`current-${width}`);
      await page.getByRole('link',{name:'Versions',exact:true}).click();
      assert.equal(await page.getByRole('link',{name:'Open draft',exact:true}).count(),0);
      assert.equal(await page.getByRole('link',{name:'Review & publish',exact:true}).count(),0);
      assert.equal(await page.getByRole('link',{name:'Publish version',exact:true}).count(),0);
      await shot(page,`versions-before-${width}`);
      const start=page.getByRole('button',{name:'Create draft',exact:true});await start.focus();await start.press('Enter');
      await page.waitForURL(`${base}/datasets/${key}/concept/records`);
      await page.getByRole('heading',{name:/You’re working on a draft/,exact:true}).waitFor();
      assert.match(await page.locator('.concept-banner').first().innerText(),/No changes yet/);
      await page.getByRole('link',{name:'Versions',exact:true}).click();
      await page.getByRole('link',{name:'Open draft',exact:true}).click();
      const opener=page.getByRole('button',{name:/Edit record Betaling/});await opener.focus();await opener.press('Enter');
      await page.locator('dialog[open]').waitFor();
      await page.keyboard.press('Escape');await page.locator('dialog[open]').waitFor({state:'hidden'});
      assert(await opener.evaluate(el=>el===document.activeElement));
      await opener.press('Enter');
      await page.locator('dialog[open] [data-panel-view="edit"] input[name="label"]').fill('Edited concept value');
      await page.locator('dialog[open]').getByRole('button',{name:'Save record',exact:true}).click();
      await page.waitForURL(`${base}/datasets/${key}/concept/records`);
      const row=page.locator('table.record-table tbody tr').filter({hasText:'Betaling'});
      await row.getByText('Edited concept value',{exact:true}).waitFor();
      assert.match(await row.innerText(),/Complete/);assert.match(await row.innerText(),/Changed/);
      assert.equal(await page.getByRole('columnheader',{name:'Status',exact:true}).count(),1);
      assert.equal(await page.getByRole('columnheader',{name:'Status (application metadata)',exact:true}).count(),1);
      assert.deepEqual(await page.locator('table .record-system-status .badge-published').allTextContents(),[]);
      const remove=page.getByRole('button',{name:/Edit record Verwijderen/});
      await remove.click();await page.locator('dialog[open]').getByRole('button',{name:'Delete',exact:true}).click();
      await page.locator('dialog[open] [data-panel-view="delete"]').getByRole('button',{name:'Delete record',exact:true}).click();
      await page.waitForURL(`${base}/datasets/${key}/concept/records`);
      await page.getByRole('button',{name:'Add record',exact:true}).click();
      await page.locator('dialog[open] input[name="code"]').fill('Toegevoegd');
      await page.locator('dialog[open]').getByRole('button',{name:'Create record',exact:true}).click();
      await page.waitForURL(`${base}/datasets/${key}/concept/records`);
      assert.match(await page.locator('.page-context .metadata').innerText(),/3 records.*1 marked for deletion/s);
      assert.deepEqual(await page.locator('tbody td.record-system-status').allTextContents(),['Changed','','Added','Deleted']);
      await shot(page,`concept-${width}`);
      await page.getByRole('columnheader',{name:'Status (application metadata)',exact:true}).scrollIntoViewIfNeeded();
      await shot(page,`system-status-${width}`);
      await page.getByLabel('Search identifier',{exact:true}).fill('verwijderen');
      await page.getByRole('button',{name:'Search',exact:true}).click();
      await page.waitForURL(/q=verwijderen/);
      assert.equal(await page.locator('table.record-table tbody tr').count(),1);
      const tombstone=page.getByRole('button',{name:/View deleted record Verwijderen/});
      await tombstone.focus();await tombstone.press('Enter');
      await page.locator('dialog[open]').waitFor();
      assert.match(await page.locator('dialog[open]').innerText(),/will be removed when you publish/);
      assert.match(await page.locator('dialog[open]').innerText(),/Content change/);
      assert.equal(await page.locator('dialog[open]').getByRole('button',{name:'Edit',exact:true}).count(),0);
      assert.equal(await page.locator('dialog[open] form').count(),0);
      await shot(page,`deleted-panel-${width}`);
      await page.keyboard.press('Escape');await page.locator('dialog[open]').waitFor({state:'hidden'});
      assert(await tombstone.evaluate(el=>el===document.activeElement));
      await page.getByLabel('Search identifier',{exact:true}).fill('');
      await page.getByLabel('Search identifier',{exact:true}).press('Enter');
      await page.waitForURL(`${base}/datasets/${key}/concept/records?q=`);
      assert.equal(await page.locator('table.record-table tbody tr').count(),4);
      await page.getByRole('link',{name:'View current version',exact:true}).click();
      assert.equal(page.url(),current);assert.match(await page.locator('table').innerText(),/Published value/);
      assert(!/Edited concept value/.test(await page.locator('table').innerText()));
      await page.getByRole('link',{name:'Versions',exact:true}).click();
      await shot(page,`versions-active-${width}`);
      assert.equal(await page.getByRole('button',{name:'Create draft',exact:true}).count(),0);
      await page.goto(base+'/datasets');await shot(page,`list-active-${width}`);
      assert.match(await page.locator('table').innerText(),/Draft in progress/);
      await page.goto(versions);await page.getByRole('link',{name:'Open draft',exact:true}).click();await page.getByRole('link',{name:'Review & publish',exact:true}).click();
      await page.getByRole('heading',{name:'Summary',exact:true}).waitFor();await shot(page,`review-${width}`);
      const publish=page.getByRole('link',{name:'Publish version',exact:true});await publish.focus();await publish.press('Enter');
      await page.locator('#publication-date').waitFor();
      for(let i=0;i<15;i++){await page.keyboard.press('Tab');assert(await page.evaluate(()=>!!document.activeElement.closest('dialog[open]')));}
      await page.keyboard.press('Escape');await page.locator('dialog[open]').waitFor({state:'hidden'});
      assert(await publish.evaluate(el=>el===document.activeElement));
      await publish.click();await page.locator('#publication-confirmed').check();
      await page.locator('dialog[open]').getByRole('button',{name:'Publish version',exact:true}).click();
      await page.waitForURL(versions+'?notice=published');
      assert.equal(await page.getByRole('link',{name:'Open draft',exact:true}).count(),0);
      assert.equal(await page.getByRole('button',{name:'Create draft',exact:true}).count(),1);
      await page.getByRole('link',{name:'Version 2',exact:true}).click();
      assert.match(await page.locator('table').innerText(),/Edited concept value/);
      assert.equal(await page.getByRole('columnheader',{name:'Status (application metadata)',exact:true}).count(),1);
      assert.deepEqual(await page.locator('tbody td.record-system-status .badge-published').allTextContents(),[]);
      assert.equal(await page.getByRole('button',{name:/Verwijderen/}).count(),0);
      await shot(page,`published-status-${width}`);
      await page.getByRole('link',{name:'Versions',exact:true}).click();
      await page.getByRole('link',{name:'Version 1',exact:true}).click();
      assert.deepEqual(await page.locator('tbody td.record-system-status .badge-published').allTextContents(),[]);
      assert.equal(await page.getByRole('columnheader',{name:'Status',exact:true}).count(),1);
      assert.equal(await page.getByRole('columnheader',{name:'Status (application metadata)',exact:true}).count(),1);
      assert.match(await page.locator('tbody').innerText(),/Published value/);
      assert(!/Edited concept value/.test(await page.locator('tbody').innerText()));
      await shot(page,`historical-status-${width}`);
      results.push({width,passed:true});await context.close();
    }
    const context=await browser.newContext();const page=await context.newPage();await login(page,'reader@example.com');
    await page.goto(base+'/datasets');assert.equal(await page.getByText('Draft in progress',{exact:true}).count(),0);
    const response=await page.goto(base+'/datasets/lifecycle-1440/concept/records');assert.equal(response.status(),404);
    await context.close();assert.deepEqual(errors,[]);
    const report={browser:await browser.version(),results,denied:404,pageErrors:errors};
    if(output)await fs.writeFile(path.join(output,'results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
