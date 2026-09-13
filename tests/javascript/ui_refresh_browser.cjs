/* Real Chrome regressions: use tests/browser_ui_refresh_app.py, disposable loopback only. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5186';
if (!['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname)) throw Error('Disposable loopback only');
const output = process.env.REFRNCR_BROWSER_OUTPUT;
async function settled(page) {
  await page.evaluate(async()=>{await document.fonts.ready;await Promise.all(document.getAnimations().map(a=>a.finished));});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Document overflow');
}
async function shot(page,name) {
  await settled(page);
  if(output)await page.screenshot({path:path.join(output,name+'.png'),fullPage:false});
}
async function closing(dialog) {
  await dialog.evaluate(d=>{d.dataset.testClosed='pending';d.addEventListener('close',()=>{d.dataset.testClosed='done';},{once:true});});
}
async function restored(page,opener) {
  await page.locator('dialog[open]').waitFor({state:'hidden'});
  await page.waitForFunction(()=>!!document.querySelector('dialog[data-test-closed="done"]'));
  await page.locator('dialog[data-test-closed="done"]').evaluateAll(nodes=>nodes.forEach(d=>delete d.dataset.testClosed));
  assert(await opener.evaluate(el=>el===document.activeElement),'Focus restored to opener');
}
(async()=>{
  if(output)await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  const results=[],errors=[];
  try {
    for(const width of [320,768,1440]) {
      const context=await browser.newContext({viewport:{width,height:900},colorScheme:'light'});
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      page.on('dialog',async d=>{errors.push(`Unexpected ${d.type()}`);await d.dismiss();});
      await page.goto(base+'/login');
      assert(!/administrator to reset|Forgot your password/i.test(await page.locator('main').innerText()));
      await page.getByLabel('Email address').fill('admin@example.com');
      await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
      await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForURL(base+'/');
      for(const count of [100,1]) {
        await page.goto(`${base}/datasets/report-${width}-${count}/records/import`);
        await page.locator('#import-file').setInputFiles({name:'duplicates.csv',mimeType:'text/csv',buffer:Buffer.from('code,description\n'+Array(count).fill('DUPLICATE,Duplicate record').join('\n')+'\n')});
        await page.getByRole('button',{name:'Continue',exact:true}).click();
        await page.locator('[data-import-mapping]').waitFor();
        await page.getByRole('button',{name:'Continue to preview',exact:true}).click();
        await page.waitForURL(/\/step\/4$/);
        const opener=page.getByRole('button',{name:'View error report',exact:true});
        await opener.focus();await opener.press('Enter');
        const dialog=page.getByRole('dialog',{name:'Import error report',exact:true});await dialog.waitFor();
        await settled(page);
        assert.equal(await dialog.locator('section').count(),count);
        const body=dialog.locator('.dialog-large-body'),header=dialog.locator('header');
        const before=await header.boundingBox();
        const measures=await dialog.evaluate(d=>{
          const b=d.querySelector('.dialog-large-body'),r=d.getBoundingClientRect();
          return {top:r.top,bottom:r.bottom,height:r.height,viewport:innerHeight,dialogClient:d.clientHeight,dialogScroll:d.scrollHeight,bodyClient:b.clientHeight,bodyScroll:b.scrollHeight};
        });
        assert(measures.top>=0 && measures.bottom<=900,'Dialog within viewport');
        assert(measures.dialogScroll<=measures.dialogClient+1,'Only body scrolls');
        if(count===100) {
          assert(measures.bodyScroll>measures.bodyClient,'Long report body scrolls');
          await body.focus();
          await body.evaluate(b=>{b.dataset.testScrollEnd='pending';b.addEventListener('scrollend',()=>{b.dataset.testScrollEnd='done';},{once:true});});
          await page.keyboard.press('PageDown');
          await page.waitForFunction(()=>document.querySelector('.dialog-large-body').dataset.testScrollEnd==='done');
          assert(await body.evaluate(b=>b.scrollTop>0),'Keyboard scrolls the body');
          await body.hover();await page.mouse.wheel(0,measures.bodyScroll);
          await page.waitForFunction(()=>{const b=document.querySelector('.dialog-large-body');return b.scrollTop+b.clientHeight>=b.scrollHeight-1;});
          assert.deepEqual(await header.boundingBox(),before,'Header remains fixed after scrolling');
          assert(await dialog.locator('section').last().isVisible());
        } else {
          assert(measures.bodyScroll<=measures.bodyClient+1,'Short report has no scrolling');
          assert(measures.height<450,'Short report uses natural height');
        }
        for(let i=0;i<6;i++){await page.keyboard.press(i<3?'Tab':'Shift+Tab');assert(await page.evaluate(()=>!!document.activeElement.closest('dialog[open]')),'Focus contained');}
        await shot(page,`report-${count}-${width}`);
        const close=dialog.getByRole('button',{name:'Close report',exact:true});
        const closeBounds=await close.boundingBox();assert(closeBounds.y>=0&&closeBounds.y+closeBounds.height<=900);
        await closing(dialog);await close.click();await restored(page,opener);
        await opener.press('Enter');await dialog.waitFor();await closing(dialog);await page.keyboard.press('Escape');await restored(page,opener);
        results.push({width,reportRows:count,...measures});
      }
      await page.goto(base+'/users');await settled(page);
      const row=page.locator('tbody tr').filter({hasText:'long@example.com'});
      const geometry=await row.evaluate(r=>{
        const name=r.querySelector('.row-action-label'),chevron=r.querySelector('.row-chevron').getBoundingClientRect(),range=document.createRange();range.selectNodeContents(name);
        const bounds=r.getBoundingClientRect(),scroll=r.closest('.table-scroll');
        return {rowWidth:bounds.width,containerClient:scroll.clientWidth,containerScroll:scroll.scrollWidth,nameRects:[...range.getClientRects()].map(b=>({left:b.left,right:b.right,top:b.top,bottom:b.bottom})),chevron:{left:chevron.left,right:chevron.right,top:chevron.top,bottom:chevron.bottom}};
      });
      await shot(page,`users-${width}`);
      console.log(JSON.stringify({width,users:geometry}));
      if(width<768)assert(geometry.containerScroll<=geometry.containerClient,'Mobile Users table overflow');
      assert(geometry.nameRects.every(r=>r.left>=0 && r.right<=geometry.chevron.right),'Name stays inside the row');
      for(const r of geometry.nameRects){const c=geometry.chevron;assert(r.right<=c.left||r.left>=c.right||r.bottom<=c.top||r.top>=c.bottom,'Name overlaps chevron');}
      const normal=page.locator('tbody tr').filter({hasText:'row@example.com'});assert((await normal.boundingBox()).height<100,'Normal names stay compact');
      const primary=row.locator('[data-row-primary]');await primary.focus();await primary.press('Enter');
      await page.locator('dialog[open]').waitFor();assert.match(await page.locator('dialog[open]').innerText(),/LongLong/);
      await closing(page.locator('dialog[open]'));await page.keyboard.press('Escape');await restored(page,primary);
      await row.locator('td[headers="user-column-email"]').click();await page.locator('dialog[open]').waitFor();
      await closing(page.locator('dialog[open]'));await page.keyboard.press('Escape');await restored(page,primary);
      results.push({width,users:geometry});await context.close();
    }
    assert.deepEqual(errors,[]);
    const report={browser:await browser.version(),results,pageErrors:errors};
    if(output)await fs.writeFile(path.join(output,'results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
