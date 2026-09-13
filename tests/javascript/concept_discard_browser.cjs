/* REF-155: real Chrome, disposable loopback data, no timing sleeps. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5186';
if (!['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname)) throw Error('Disposable loopback only');
const output = process.env.REFRNCR_BROWSER_OUTPUT;
const measurements = [], errors = [], consoleErrors = [], expectedResponses = [];
async function login(page, email='owner@example.com') {
  await page.goto(base+'/login');
  await page.locator('#login-email').fill(email);
  await page.locator('#login-password').fill('correct horse battery staple');
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.waitForURL(base+'/');
}
async function evidence(page, name) {
  await page.evaluate(()=>document.fonts.ready);
  await page.locator('dialog[open]').evaluateAll(async ds=>{
    await Promise.all(ds.flatMap(d=>d.getAnimations({subtree:true}).map(a=>a.finished)));
  });
  const layout = await page.evaluate(()=>{
    const rect = el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
    const dialog=document.querySelector('dialog[open]');
    return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,
      dialog:dialog?rect(dialog):null,
      actions:dialog?[...dialog.querySelectorAll('.side-panel-footer a,.side-panel-footer button')].map(rect):[],
      labels:dialog?[...dialog.querySelectorAll('dt')].map(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth})):[],
      focus:document.activeElement.outerHTML.slice(0,250),
      outline:getComputedStyle(document.activeElement).outline};
  });
  assert(layout.scrollWidth<=layout.viewport,JSON.stringify(layout));
  if(layout.dialog){
    assert(layout.dialog.left>=0 && layout.dialog.right<=layout.viewport);
    assert(layout.labels.every(l=>l.scrollWidth<=l.width),'Count label overflow');
    for(const r of layout.actions) assert(r.left>=0 && r.right<=layout.viewport && r.top>=0 && r.bottom<=950);
  }
  measurements.push({name,...layout});
  if(output) await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
}
async function open(page) {
  const menu=page.getByText('Concept actions',{exact:true});
  if(!await menu.evaluate(el=>el.parentElement.open)) {await menu.focus();await page.keyboard.press('Enter');}
  const link=page.getByRole('link',{name:'Discard concept',exact:true});
  await link.focus(); await page.keyboard.press('Enter');
  const dialog=page.getByRole('dialog',{name:'Discard concept',exact:true});
  await dialog.locator('#discard-concept-form').waitFor({state:'attached'});
  await page.waitForFunction(()=>document.activeElement.hasAttribute('data-initial-focus'));
  assert.match(await dialog.getAttribute('aria-describedby'),/description/);
  assert.match(await dialog.locator('#concept-discard-panel-description').textContent(),/Current, Scheduled and history remain unchanged/);
  return dialog;
}
async function confirmByKeyboard(page, dialog) {
  // Reach the destructive control through the native tab order only.
  for(let n=0;n<10;n++) {
    if(await dialog.getByRole('button',{name:'Discard concept',exact:true}).evaluate(el=>el===document.activeElement)) break;
    await page.keyboard.press('Tab');
  }
  assert(await dialog.getByRole('button',{name:'Discard concept',exact:true}).evaluate(el=>el===document.activeElement));
  assert(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle!=='none'));
  await evidence(page,`confirm-${page.viewportSize().width}-${measurements.length}`);
  await page.keyboard.press('Enter');
}
(async()=>{
  if(output) await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    for(const width of [320,768,1440]) {
      const context=await browser.newContext({viewport:{width,height:950}});
      const page=await context.newPage();
      page.on('pageerror',e=>errors.push(e.message));
      page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
      page.on('response',r=>{if(r.status()>=400)expectedResponses.push({status:r.status(),url:r.url()});});
      await login(page);
      for(const kind of ['unchanged','mixed']) {
        const key=`discard-${kind}-${width}`, concept=`${base}/datasets/${key}/concept/records`;
        await page.goto(concept);
        assert.match(await page.locator('.concept-banner:visible').innerText(),kind==='unchanged'?/No changes yet/:/Changes in progress/);
        let dialog=await open(page);
        const counts=await dialog.locator('dd').allTextContents();
        assert.deepEqual(counts,kind==='unchanged'?['0','0','0','0','0','0','0']:['0','1','0','0','1','1','1']);
        assert(!/Published value|Unpublished value/.test(await dialog.innerText()));
        await evidence(page,`${kind}-${width}`);
        await dialog.locator('dd').last().scrollIntoViewIfNeeded();
        await evidence(page,`counts-bottom-${kind}-${width}`);
        for(let i=0;i<12;i++) {
          await page.keyboard.press(i<6?'Tab':'Shift+Tab');
          assert(await page.evaluate(()=>!!document.activeElement.closest('dialog[open]')));
        }
        await page.keyboard.press('Escape');
        await dialog.waitFor({state:'hidden'});
        await page.waitForFunction(()=>document.activeElement.matches('a[data-dialog-open="concept-discard-panel"]'));
        assert(await page.getByText('Concept actions',{exact:true}).evaluate(el=>el.parentElement.open));
        dialog=await open(page);
        await dialog.getByRole('link',{name:'Cancel',exact:true}).click();
        await dialog.waitFor({state:'hidden'});
        await page.waitForFunction(()=>document.activeElement.matches('a[data-dialog-open="concept-discard-panel"]'));
        dialog=await open(page);
        if(kind==='mixed') {
          const other=await context.newPage();
          await other.goto(`${base}/datasets/${key}/edit`);
          await other.locator('#dataset-name').fill(`Changed while reviewing ${width}`);
          await other.getByRole('button',{name:'Save dataset',exact:true}).click();
          await other.waitForURL(`${base}/datasets/${key}/concept`);
          await other.close();
          await confirmByKeyboard(page,dialog);
          await dialog.getByText(/confirm discard again/).waitFor();
          await page.waitForFunction(()=>document.activeElement.matches('[data-error-focus]'));
          assert.equal(await dialog.locator('dd').first().innerText(),'1');
          await evidence(page,`stale-${width}`);
          // One controlled 503 exercises the shared safe server-error path.
          await page.route(`**/datasets/${key}/concept/discard`, async route=>{
            if(route.request().method()==='POST') await route.fulfill({status:503,contentType:'text/plain',body:'Service unavailable'});
            else await route.continue();
          });
          await confirmByKeyboard(page,dialog);
          await dialog.getByRole('alert').filter({hasText:'The form could not be saved. Reload the page to check your access and try again.'}).waitFor();
          await page.waitForFunction(()=>document.activeElement.getAttribute('role')==='alert');
          await evidence(page,`server-error-${width}`);
          await page.unroute(`**/datasets/${key}/concept/discard`);
        }
        await confirmByKeyboard(page,dialog);
        await page.waitForURL(`${base}/datasets/${key}?notice=discarded`);
        await page.getByText('Concept discarded. Current was not changed.',{exact:true}).waitFor();
        await evidence(page,`success-${kind}-${width}`);
        await page.reload();
        assert.equal(await page.getByRole('button',{name:'Create concept',exact:true}).count(),1);
        await page.goBack();
        await page.reload();
        assert.equal(await page.locator('#discard-concept-form').count(),0);
        await page.goto(`${base}/datasets/${key}/records`);
        assert.match(await page.locator('tbody').innerText(),/Published value/);
        assert.match(await page.locator('tbody').innerText(),/Retained value/);
        assert(!/Unpublished value|Added/.test(await page.locator('tbody').innerText()));
        await page.goto(`${base}/datasets/${key}?notice=discarded`);
        await page.getByRole('button',{name:'Create concept',exact:true}).focus();
        await page.keyboard.press('Enter');
        await page.waitForURL(concept);
        assert.match(await page.locator('.concept-banner:visible').innerText(),/No changes yet/);
      }
      await page.goto(`${base}/datasets/discard-first-${width}/concept/records`);
      await page.getByText('Concept actions',{exact:true}).click();
      const disabled=page.getByRole('button',{name:'Discard concept',exact:true});
      assert.equal(await disabled.getAttribute('aria-disabled'),'true');
      await disabled.focus();
      const explanation='This Concept contains the Dataset’s first unpublished version. There is no Current version to return to. Publish it first or delete the Dataset.';
      assert.equal(await page.locator('#concept-discard-panel-unavailable').innerText(),explanation);
      await evidence(page,`disabled-${width}`);
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('dialog[open]').count(),0);
      await context.close();
      const nojs=await browser.newContext({viewport:{width,height:950},javaScriptEnabled:false});
      const fallback=await nojs.newPage();await login(fallback);
      const key=`discard-fallback-${width}`;
      await fallback.goto(`${base}/datasets/${key}/concept/records`);
      await fallback.getByText('Concept actions',{exact:true}).click();
      await fallback.getByRole('link',{name:'Discard concept',exact:true}).click();
      await fallback.waitForURL(`${base}/datasets/${key}/concept/discard`);
      await evidence(fallback,`nojs-${width}`);
      await fallback.getByRole('button',{name:'Discard concept',exact:true}).click();
      await fallback.waitForURL(`${base}/datasets/${key}?notice=discarded`);
      await nojs.close();
    }
    const denied=await browser.newPage();await login(denied,'reader@example.com');
    const denial=await denied.goto(`${base}/datasets/discard-mixed-1440/concept/discard`);
    assert.equal(denial.status(),404);await denied.close();
    assert.deepEqual(errors,[]);
    // Chrome reports HTTP 409/503 document/fetch responses as console errors;
    // retain them with exact expected HTTP evidence, reject all other console errors.
    const unexpected=consoleErrors.filter(e=>!/^Failed to load resource: the server responded with a status of (409|503) /.test(e));
    assert.deepEqual(unexpected,[]);
    const result={browser:await browser.version(),widths:[320,768,1440],measurements,pageErrors:errors,consoleErrors,expectedResponses,denied:404};
    if(output)await fs.writeFile(path.join(output,'results.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify({browser:result.browser,widths:result.widths,measurements:measurements.length,pageErrors:errors,unexpectedConsoleErrors:unexpected,expectedHttpErrors:expectedResponses.length}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
