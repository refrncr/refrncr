// Run with tests/browser_ref165_app.py; every mutation is confined to its in-memory data.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5165';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Disposable loopback fixture required');
const panels = ['dataset-details', 'fields', 'access-management', 'danger-zone'];
const visiblePanels = page => page.locator('[data-tab-panels] > section:visible').evaluateAll(es => es.map(e=>e.id));
const names = page => page.locator('#fields tbody .field-name').allTextContents();
async function login(page) {
  await page.goto(base + '/login');
  await page.getByLabel('Email address').fill('admin@example.com');
  await page.getByLabel('Password', {exact:true}).fill('correct horse battery staple');
  await Promise.all([page.waitForNavigation(), page.getByRole('button', {name:'Sign in',exact:true}).click()]);
  assert.equal(new URL(page.url()).pathname, '/');
}
async function layout(page) {
  assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), 'page must not overflow');
  const ids = await page.locator('[id]').evaluateAll(es=>es.map(e=>e.id));
  assert.equal(ids.length, new Set(ids).size, 'unique IDs');
  assert.deepEqual(await page.locator('[aria-controls]').evaluateAll(es=>es.flatMap(e=>
    e.getAttribute('aria-controls').split(/\s+/).filter(id=>!document.getElementById(id)))), []);
}
async function savedMove(page, action) {
  const [response] = await Promise.all([
    page.waitForResponse(r=>r.url().endsWith('/fields/order') && r.request().method()==='POST'), action(),
  ]);
  assert.equal(response.status(),204);
  await page.waitForFunction(()=>!document.querySelector('[data-field-order]').classList.contains('is-saving'));
}
(async()=>{
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  console.log('Chrome ' + browser.version());
  const errors = [], external = [];
  const observe = page => {
    page.on('pageerror', e=>errors.push(e.message));
    page.on('request', r=>{if (!r.url().startsWith(base)) external.push(r.url());});
  };
  try {
    for (const width of [320,768,1440]) {
      const context = await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
      const page = await context.newPage(); observe(page);
      await page.goto(base+'/login');
      assert(await page.locator('.auth-wordmark').isVisible());
      assert(!(await page.locator('.auth-loader').isVisible()));
      await page.emulateMedia({reducedMotion:'no-preference'});
      assert(await page.locator('.auth-loader').isVisible());
      await page.emulateMedia({reducedMotion:'reduce'});
      assert(!(await page.locator('.auth-loader').isVisible()));
      assert((await page.locator('link[rel=icon]').getAttribute('href')).endsWith('/favicon.svg'));
      await layout(page);
      await login(page);
      const key = `ref165-${width}`;
      const configure = `/datasets/${key}/configure`;
      const concept = `/datasets/${key}/concept`;
      await page.goto(base+configure+'?edit=1');
      const preview = page.locator('[data-icon-preview] use');
      assert((await preview.getAttribute('href')).endsWith('/lucide-legacy-0.468.0.svg#history'));
      await page.getByLabel('Name', {exact:true}).fill(`Renamed ${width}`);
      await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Save dataset',exact:true}).click()]);
      assert((await page.locator('.dataset-identity-card use').getAttribute('href')).endsWith('#history'));
      await page.goto(base+configure+'?edit=1');
      assert.equal(await page.getByRole('combobox',{name:'Dataset icon',exact:true}).inputValue(),'history');
      await page.getByRole('combobox',{name:'Dataset icon',exact:true}).fill('history');
      await page.waitForFunction(()=>document.querySelector('[data-icon-preview] use').getAttribute('href').endsWith('/lucide-legacy-0.468.0.svg#history'));
      await page.getByRole('button',{name:'Landmark',exact:true}).click();
      assert((await preview.getAttribute('href')).endsWith('/lucide-1.46.0.svg#landmark'));
      await layout(page);

      await page.goto(base+concept+'#fields');
      assert.deepEqual(await visiblePanels(page),['fields']);
      assert.equal(await page.getByRole('button',{name:/^Move Code (up|down)$/}).count(),0);
      await page.getByRole('button',{name:'Reorder Code',exact:true}).focus();
      await savedMove(page,()=>page.keyboard.press('ArrowDown'));
      assert.deepEqual(await names(page),['Label','Code']);
      await page.reload();
      await page.getByRole('button',{name:'Reorder Code',exact:true}).focus();
      assert(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle !== 'none'),'visible keyboard focus');
      await savedMove(page,()=>page.keyboard.press('ArrowUp'));
      assert.deepEqual(await names(page),['Code','Label']);
      await layout(page);

      // POST failure must navigate to a GET page for all source configuration variants.
      for (const path of [configure,concept,`/datasets/${key}`]) {
        await page.goto(base+path+'#access-management');
        await page.getByLabel('User or team',{exact:true}).selectOption({label:'Row user (row@example.com)'});
        let posts = 0;
        const fail = route => {
          if (route.request().method()==='POST') { posts++; return route.abort(); }
          return route.continue();
        };
        await page.route('**/export-access*',fail);
        const [response] = await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Grant access',exact:true}).click()]);
        assert.equal(response.status(),200); assert.equal(posts,1);
        assert.equal(page.url(),base+configure+'#access-management');
        assert.deepEqual(await visiblePanels(page),['access-management']);
        await page.unroute('**/export-access*',fail);
      }
      // Revoke uses the same safe GET target; a failed attempt is not replayed.
      await page.getByLabel('User or team',{exact:true}).selectOption({label:'Row user (row@example.com)'});
      await page.getByRole('button',{name:'Grant access',exact:true}).click();
      await page.getByRole('button',{name:'Remove export access for Row user',exact:true}).waitFor();
      await page.route('**/revoke*',route=>route.abort());
      const [revoked] = await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Remove export access for Row user',exact:true}).click()]);
      assert.equal(revoked.status(),200); assert.equal(page.url(),base+configure+'#access-management');
      assert(await page.getByRole('button',{name:'Remove export access for Row user',exact:true}).isVisible());
      await page.unroute('**/revoke*');

      const storageState = await context.storageState();
      for (const mode of ['nojs','blocked','initialization-error']) {
        const fallback = await browser.newContext({viewport:{width,height:1000},storageState,javaScriptEnabled:mode!=='nojs',reducedMotion:'reduce'});
        const plain = await fallback.newPage(); observe(plain);
        if (mode==='blocked') await plain.route('**/static/js/{section-tabs,icon-picker,field-order}.js',route=>route.abort());
        if (mode==='initialization-error') await plain.addInitScript(()=>{
          const original = Element.prototype.setAttribute;
          Element.prototype.setAttribute = function(name,value) {
            if (!window.injectedTabFailure && this.id==='tab-access-management' && name==='aria-selected') {
              window.injectedTabFailure=true; throw Error('Injected tab initialization failure');
            }
            return original.call(this,name,value);
          };
        });
        await plain.goto(base+configure+'?edit=1#access-management');
        assert.deepEqual(await visiblePanels(plain),panels,mode+': content remains available');
        if (mode==='initialization-error') assert(await plain.evaluate(()=>window.injectedTabFailure));
        else {
          assert(!(await plain.locator('[data-icon-suggestions]').isVisible()));
          assert(!(await plain.locator('[data-icon-preview]').isVisible()));
        }
        await layout(plain);
        await plain.goto(base+concept+'#fields');
        assert.deepEqual(await visiblePanels(plain),panels);
        if (mode!=='initialization-error') assert.equal(await plain.locator('.field-drag-handle:visible').count(),0);
        assert.equal(await plain.getByRole('button',{name:/^Move Code (up|down)$/}).count(),0);
        await fallback.close();
      }
      await context.close();
      console.log(`PASS ${width}px: legacy icons, button/keyboard order, grant/revoke failure, no-JS, blocked scripts, failed initialization, branding and layout`);
    }
    assert.deepEqual(errors,[]); assert.deepEqual(external,[]);
    console.log('PASS: no uncaught script errors or external asset requests');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
