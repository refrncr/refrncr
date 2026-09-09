// Optional REF-73 acceptance check; start tests/browser_table_rows_app.py first.
// Uses the same external Playwright environment as table_rows_browser.cjs.
const {chromium}=require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
 const output=process.env.REFRNCR_BROWSER_OUTPUT;
 if (!['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname)) throw Error('Use the disposable loopback fixture');
 if(output) fs.mkdirSync(output,{recursive:true});
 const shot=async name=>{if(output) await page.screenshot({path:output+'/'+name+'.png'});};
 try{
 await page.goto(base+'/login');await page.getByLabel('Email address').fill('admin@example.com');await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForURL(base+'/');
 for(const width of [1440,768,320]){
   await page.setViewportSize({width,height:900});
   await page.goto(base+'/datasets');
   await page.getByRole('link',{name:'+ New dataset',exact:true}).click();
   await page.locator('dialog[open] #owner-team').waitFor();
   assert.equal(await page.locator('dialog[open] #owner-team').inputValue(),'');
   await shot(`create-${width}`);
   await page.keyboard.press('Escape');
   assert(await page.getByRole('link',{name:'+ New dataset',exact:true}).evaluate(e=>e===document.activeElement));
   await page.goto(base+'/datasets/row-check');
   assert(await page.getByRole('link',{name:'Access management',exact:true}).evaluate(e=>e.classList.contains('button-secondary') && !!e.closest('.page-header .page-actions')));
   assert(await page.getByRole('link',{name:'Access management',exact:true}).evaluate(e=>Math.abs(e.getBoundingClientRect().right-e.closest('.page-header').getBoundingClientRect().right)<2));
   await shot(`configuration-${width}`);
   const owner=page.getByRole('link',{name:'Edit dataset',exact:true});await owner.click();
   await page.locator('dialog[open] #owner-team').waitFor();
   assert(await page.locator('dialog[open] input[name=expected_owner_team_id]').inputValue());
   await shot(`owner-${width}`);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.keyboard.press('Escape');assert(await owner.evaluate(e=>e===document.activeElement));
   await page.getByRole('link',{name:'Access management',exact:true}).click();await page.locator('dialog[open] #export-subject').waitFor();
   assert(await page.locator('dialog[open] button[type=submit]').isDisabled());
   await page.locator('dialog[open] #export-subject').selectOption({label:'Row user (row@example.com)'});
   await page.locator('dialog[open]').getByRole('button',{name:'Grant access'}).click();
   await page.locator('dialog[open] .access-list-row').waitFor();
   await shot(`export-${width}`);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.getByRole('button',{name:'Remove export access for Row user'}).click();await page.getByRole('heading',{name:'No additional access',exact:true}).waitFor();
 }
 await page.goto(base+'/datasets/row-check');await page.getByRole('link',{name:'Edit dataset',exact:true}).click();await page.locator('dialog[open] #owner-team').waitFor();
 const target=await page.locator('dialog[open] #owner-team').evaluate(e=>[...e.options].find(o=>o.value && o.value!==e.value).value);
 const second=await page.context().newPage();await second.goto(base+'/datasets/row-check/owner');await second.getByLabel('Owning team',{exact:true}).selectOption(target);await second.getByRole('button',{name:'Save owning team'}).click();await second.waitForURL(base+'/datasets/row-check');
 await page.locator('dialog[open] #owner-team').selectOption(target);await page.getByRole('button',{name:'Save dataset',exact:true}).click();
 await page.locator('dialog[open] [data-error-focus]').waitFor();assert(await page.locator('dialog[open] [data-error-focus]').evaluate(e=>e===document.activeElement));
 await shot('stale-320');
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS: desktop/320px create, owner and export panels; persistence/revoke; no overflow; Escape focus restoration; stale error focus; no JS errors');
 }finally{await browser.close();}
})();
