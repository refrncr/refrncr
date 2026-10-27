/* REF-160: real Chrome with disposable loopback data. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5187';
if (!['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname)) throw Error('Loopback only');
const output = process.env.REFRNCR_BROWSER_OUTPUT;
const measurements = [];
async function evidence(page,name) {
  const layout = await page.evaluate(()=>({viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,focus:document.activeElement.outerHTML.slice(0,200)}));
  assert(layout.scrollWidth <= layout.viewport, JSON.stringify({name,...layout}));
  measurements.push({name,...layout});
  if(output) await page.screenshot({path:path.join(output,name+'.png'),fullPage:false});
}
async function enter(page,control) {await control.focus();await Promise.all([page.waitForNavigation(),page.keyboard.press('Enter')]);}
(async()=>{
 const browser = await chromium.launch({headless:true,executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
 try {
 {
  const context=await browser.newContext({viewport:{width:1440,height:950}});
  const page=await context.newPage();
  await page.goto(base+'/login');
  await page.locator('#login-email').fill('admin@example.com');
  await page.locator('#login-password').fill('correct horse battery staple');
  await enter(page,page.getByRole('button',{name:'Sign in',exact:true}));
  await page.goto(base+'/datasets/new-version-empty/concept/records');
  await enter(page,page.getByRole('link',{name:'Import',exact:true}));
  assert.match(await page.locator('body').textContent(),/file defines the complete desired set/);
  await page.locator('#import-file').setInputFiles({name:'initial.csv',mimeType:'text/csv',buffer:Buffer.from('code,label\nfirst,First\nsecond,Second\n')});
  await enter(page,page.getByRole('button',{name:'Continue',exact:true}));
  await enter(page,page.getByRole('button',{name:'Continue to preview',exact:true}));
  assert.equal(await page.getByRole('table',{name:'Changes in this import',exact:true}).locator('tbody tr').count(),2);
  await evidence(page,'empty-preview');
  await enter(page,page.getByRole('link',{name:'Continue',exact:true}));
  assert.equal(await page.locator('input[name="acknowledge_deletions"]').count(),0);
  await enter(page,page.getByRole('button',{name:'Import records',exact:true}));
  assert.match(await page.locator('body').textContent(),/Added: 2.*Changed: 0.*Unchanged: 0.*Deleted: 0/s);
  await evidence(page,'empty-results');
  await context.close();
 }
 for(const width of [320,768,1440,'nojs']) {
  const context = await browser.newContext({viewport:{width:width==='nojs'?320:width,height:950},javaScriptEnabled:width!=='nojs'});
  context.setDefaultTimeout(10000);
  context.setDefaultNavigationTimeout(10000);
  const page = await context.newPage();
  await page.goto(base+'/login');
  await page.locator('#login-email').fill('admin@example.com');
  await page.locator('#login-password').fill('correct horse battery staple');
  await enter(page,page.getByRole('button',{name:'Sign in',exact:true}));
  await page.waitForURL(base+'/');
  await page.goto(base+`/datasets/new-version-${width}/concept/records`);
  const editToolbar=page.getByRole('group',{name:'Edit Concept Records',exact:true});
  assert.equal(await page.locator('.page-header').getByRole('link',{name:'Configure',exact:true}).count(),0);
  assert.equal(await page.locator('.page-header').getByRole('button',{name:'Add record',exact:true}).count(),0);
  const bannerBox=await page.locator('.concept-banner:visible').first().boundingBox();
  const toolbarBox=await editToolbar.boundingBox();
  assert(toolbarBox.y>=bannerBox.y && toolbarBox.y+toolbarBox.height<=bannerBox.y+bannerBox.height,'Editing actions belong inside the Concept banner');
  assert(await page.locator('.page-header').getByRole('link',{name:'View current version',exact:true}).isVisible());
  assert.equal(await page.locator('.concept-banner:visible').first().getByRole('link',{name:'View current version',exact:true}).count(),0);
  assert.equal(await page.getByRole('link',{name:'Add records from file',exact:true}).count(),0);
  await enter(page,editToolbar.getByRole('link',{name:'Import',exact:true}));
  await enter(page,page.getByRole('button',{name:'Continue',exact:true}));
  await page.locator('#import-error').waitFor();
  if(width!=='nojs') {
    assert(await page.locator('#import-error').evaluate(el=>el===document.activeElement));
    await page.keyboard.press('Tab');
    assert.notEqual(await page.evaluate(()=>document.activeElement.tagName),'BODY');
  }
  await evidence(page,width+'-error');
  await page.locator('#import-file').setInputFiles({name:'complete.csv',mimeType:'text/csv',buffer:Buffer.from('code,label\nchanged,New value\nunchanged,Old value\n'+Array.from({length:55},(_,i)=>`added-${i},New ${i}\n`).join(''))});
  await enter(page,page.getByRole('button',{name:'Continue',exact:true}));
  await page.getByRole('button',{name:'Continue to preview',exact:true}).waitFor();
  await evidence(page,width+'-mapping');
  await enter(page,page.getByRole('button',{name:'Continue to preview',exact:true}));
  await page.getByRole('table',{name:'Changes in this import',exact:true}).waitFor();
  assert.equal(await page.locator('table tbody tr').count(),50);
  await evidence(page,width+'-preview');
  const firstPreviewRow=page.getByRole('row').nth(1);
  await firstPreviewRow.locator('td').first().click();
  const addedPanel=page.locator('dialog[open]');
  await addedPanel.waitFor();
  assert.match(await addedPanel.innerText(),/This record will be added\./);
  assert.equal(await addedPanel.getByText('Current',{exact:true}).count(),0);
  assert.equal(await addedPanel.getByText('New',{exact:true}).count(),0);
  assert( await addedPanel.locator('.record-details > div').count() >= 2);
  await page.keyboard.press('Escape');
  await enter(page,page.getByRole('link',{name:'Next',exact:true}));
  assert.equal(await page.locator('table tbody tr').count(),7);
  await evidence(page,width+'-page2');
  await page.getByLabel('Search identifier').fill('changed');
  await page.waitForTimeout(350);
  assert.match(await page.getByRole('table',{name:'Changes in this import'}).textContent(),/Changed fields/);
  const changedRow=page.getByRole('row').filter({hasText:'changed'});
  await changedRow.locator('td').first().click();
  const previewPanel=page.locator('dialog[open]');
  await previewPanel.waitFor();
  assert.equal(await previewPanel.locator('.import-change').count(),1);
  assert.equal(await previewPanel.getByText('Current',{exact:true}).count(),1);
  assert.equal(await previewPanel.getByText('New',{exact:true}).count(),1);
  assert.equal(await previewPanel.locator('.import-change-new').innerText().then(text=>text.includes('New value')),true);
  assert.doesNotMatch(await previewPanel.innerText(),/[−+]|Before:|After:/);
  assert.equal(await previewPanel.locator('[role="region"][tabindex="0"]').count(),0);
  assert.equal(await previewPanel.locator('.import-change dt').first().evaluate(el=>el.closest('dl').classList.contains('record-details')),true);
  assert.equal(await previewPanel.locator('.import-unchanged-fields dt').first().evaluate(el=>el.closest('dl').classList.contains('record-details')),true);
  assert.equal(await page.evaluate(()=>window.getSelection().toString()),'');
  await page.keyboard.press('Escape');
  await previewPanel.waitFor({state:'hidden'});
  const ack=page.getByRole('checkbox',{name:'I understand that 1 Records will be marked as deleted.'});
  assert.equal(await ack.isChecked(),false);
  await evidence(page,width+'-confirm');
  await ack.focus(); await page.keyboard.press('Space');
  await enter(page,page.getByRole('button',{name:'Import records',exact:true}));
  await page.waitForURL(/import_results=1/);
  assert.match(await page.locator('body').textContent(),/Added: 55.*Changed: 1.*Unchanged: 1.*Deleted: 1/s);
  const conceptActions=page.locator('.concept-banner:visible').first().locator('[data-concept-actions]');
  const review=conceptActions.getByRole('link',{name:'Review changes',exact:true});
  assert(!await review.isVisible());
  await conceptActions.locator('summary').focus();
  await page.keyboard.press('Enter');
  assert(await review.isVisible());
  if(width==='nojs') {
    await page.keyboard.press('Enter');
  } else {
    await review.focus();
    await page.keyboard.press('Escape');
    assert(await conceptActions.locator('summary').evaluate(el=>el===document.activeElement));
  }
  const importMenu=page.locator('.import-action-help');
  const importTrigger=importMenu.locator('summary');
  const disabled=page.getByRole('button',{name:'Import',exact:true});
  await importTrigger.focus();
  if(width==='nojs') await page.keyboard.press('Enter');
  assert.equal(await disabled.getAttribute('aria-disabled'),'true');
  if(width===1440) {
    const actionBox=await importTrigger.boundingBox();
    const configureBox=await page.getByRole('link',{name:'Configure',exact:true}).boundingBox();
    assert(Math.abs(actionBox.y-configureBox.y)<2,'Import action must align with Configure');
    assert(actionBox.x-configureBox.x-configureBox.width<=12,'Configure and Import must stay together');
    const searchBox=await page.locator('#record-search').boundingBox();
    const statusBox=await page.getByLabel('Change status: All changes',{exact:true}).boundingBox();
    const submitBox=await page.getByRole('button',{name:'Search',exact:true}).boundingBox();
    assert(Math.abs(searchBox.y-statusBox.y)<2,'Status filter must align with search');
    assert(Math.abs(searchBox.y-submitBox.y)<2,'Search button must stay in the filter row');
    assert(statusBox.width<200,'Status filter must remain compact');
  }
  await disabled.focus();
  assert(await disabled.evaluate(el=>getComputedStyle(el).outlineStyle!=='none'));
  await disabled.hover();
  assert.match(await page.locator('#'+await disabled.getAttribute('aria-describedby')).textContent(),/already been imported/);
  const explanation=page.locator('#'+await disabled.getAttribute('aria-describedby'));
  assert(await explanation.isVisible());
  await evidence(page,width+'-import-menu');
  if(width!=='nojs') {
    await page.keyboard.press('Escape');
    assert(!await explanation.isVisible());
    assert(await importTrigger.evaluate(el=>el===document.activeElement));
  } else {
    await importTrigger.focus();
    await page.keyboard.press('Enter');
  }
  await evidence(page,width+'-results');
  const filter=page.locator('.record-status-filter');
  await filter.locator('summary').focus();
  await page.keyboard.press('Enter');
  for (const status of ['Added','Changed','Deleted']) assert(await filter.getByRole('checkbox',{name:status,exact:true}).isChecked());
  await evidence(page,width+'-status-menu');
  await filter.getByRole('checkbox',{name:'Added',exact:true}).uncheck();
  await filter.getByRole('checkbox',{name:'Changed',exact:true}).uncheck();
  await enter(page,page.getByRole('button',{name:'Search',exact:true}));
  assert.equal(await page.locator('.record-table tbody tr').count(),1);
  if(width!=='nojs') {
    await page.getByRole('button',{name:/View deleted record deleted/}).focus();
    await page.keyboard.press('Enter');
    await page.getByRole('dialog').waitFor();
    await evidence(page,width+'-deleted');
    await enter(page,page.getByRole('button',{name:'Undo delete',exact:true}));
    await page.waitForURL(/concept/);
    assert(!await page.getByRole('button',{name:/View deleted record deleted/}).count());
    await page.goto(base+`/datasets/new-version-${width}/concept/records?import_results=1`);
    await page.locator('.record-status-filter summary').click();
    await enter(page,page.getByRole('link',{name:'All records',exact:true}));
    assert(!new URL(page.url()).searchParams.has('import_results'));
    await page.getByLabel('Change status: All records',{exact:true}).click();
    assert.equal(await page.locator('.record-status-filter input:checked').count(),0);
    await page.getByRole('checkbox',{name:'Added',exact:true}).check();
    await page.getByRole('checkbox',{name:'Changed',exact:true}).check();
    await enter(page,page.getByRole('button',{name:'Search',exact:true}));
    assert.deepEqual(new URL(page.url()).searchParams.getAll('status'),['Added','Changed']);
    assert.match(await page.locator('.record-toolbar').textContent(),/of 56/);
  }
  await context.close();
 }
 console.log(JSON.stringify({browser:browser.version(),measurements},null,2));
 if(output) await fs.writeFile(path.join(output,'measurements.json'),JSON.stringify({browser:browser.version(),measurements},null,2));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
