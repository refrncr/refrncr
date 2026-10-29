// Checks responsive navigation, keyboard operation, focus management and page transitions.
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL;
const email = process.env.REFRNCR_BROWSER_EMAIL;
const password = process.env.REFRNCR_BROWSER_PASSWORD;
const output = process.env.REFRNCR_BROWSER_OUTPUT;
if (!base || !email || !password) throw Error('Set REFRNCR_BROWSER_URL, REFRNCR_BROWSER_EMAIL and REFRNCR_BROWSER_PASSWORD');
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Use a disposable loopback app');
if (output) require('node:fs').mkdirSync(output, {recursive:true});
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE, headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[]; page.on('pageerror', error=>errors.push(error.message));
  try {
    await page.addInitScript(() => {
      window.navigationFrames = [];
      const sample = () => {
        const main = document.querySelector('.app-main');
        if (main) window.navigationFrames.push(main.getBoundingClientRect().x);
        if (window.navigationFrames.length < 10) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await page.goto(`${base}/login`);
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password', {exact:true}).fill(password);
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    await page.waitForURL(`${base}/`);
    await page.evaluate(()=>document.fonts.ready);
    const open=page.locator('[data-nav-open]'), close=page.locator('[data-nav-close]'), nav=page.locator('[data-navigation]'), drawer=page.locator('[data-navigation-drawer]');
    const overflow = async () => assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), `overflow at ${page.viewportSize().width}`);
    const shot = async name => { if (output) await page.screenshot({path:`${output}/${name}.png`,fullPage:true}); };
    assert(await close.isVisible()); assert(!(await open.isVisible()));
    let logo=await page.locator('.brand').boundingBox(), control=await close.boundingBox();
    assert(logo.y < control.y+control.height && control.y < logo.y+logo.height, 'logo and collapse control share the header row');
    const expanded=(await page.locator('main').boundingBox()).width;
    await close.focus(); await page.keyboard.press('Enter');
    assert(!(await nav.isVisible())); assert(await open.isVisible());
    assert.equal((await page.locator('main').boundingBox()).width-expanded,240);
    assert.equal(await page.evaluate(()=>document.activeElement.hasAttribute('data-nav-open')),true);
    assert.equal(await open.getAttribute('aria-expanded'),'false');
    assert.equal(await open.evaluate(e=>getComputedStyle(e).outlineWidth),'3px');
    await shot('desktop-collapsed');
    const url=page.url(); await page.keyboard.press('Space');
    assert.equal(page.url(),url); assert(await nav.isVisible());
    assert.equal(await close.getAttribute('aria-expanded'),'true');
    await shot('desktop-expanded');
    await close.click();
    await page.goto(`${base}/datasets?kind=reference`); await page.waitForLoadState('load');
    assert(!(await nav.isVisible()),'collapsed state retained across link navigation');
    await page.waitForFunction(()=>window.navigationFrames.length === 10);
    assert((await page.evaluate(()=>window.navigationFrames)).every(x=>x===0), 'stored collapse has no expanded first frame or layout jump');
    await page.reload(); assert(!(await nav.isVisible()),'collapsed state retained across reload');
    await open.click();
    const settingsLink = page.getByRole('link',{name:'Settings',exact:true});
    const settingsPath = new URL(await settingsLink.getAttribute('href'), base).pathname;
    await Promise.all([page.waitForURL(url => url.pathname === settingsPath), settingsLink.click()]);
    await page.waitForLoadState('load');
    assert(await nav.isVisible(),'expanded state retained in Settings');
    // Settings has no desktop collapse control; its sidebar stays visible even when the app navigation is collapsed.
    assert(!(await close.isVisible()) && !(await open.isVisible()),'no desktop collapse control in Settings');
    await page.goto(`${base}/`); await close.click(); await page.goto(`${base}${settingsPath}`); await page.waitForLoadState('load');
    assert(await nav.isVisible(),'Settings sidebar visible while app navigation is collapsed');
    await Promise.all([
      page.waitForURL(`${base}/`),
      page.getByRole('link',{name:'Back to app',exact:true}).click(),
    ]);
    assert(!(await nav.isVisible()),'app navigation still collapsed after Settings'); await open.click();
    const resize = async size => { await page.setViewportSize(size); await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))); };
    for (const width of [320,767,768,1023,1024,1440]) {
      await resize({width,height:900});
      await page.goto(`${base}/datasets/reporting-check/concept/records?view=table`);
      await page.evaluate(()=>document.fonts.ready);
      await overflow();
      const controls = page.locator('.record-criteria-toolbar :is(input:not([type="hidden"]), button, summary)');
      for (const control of await controls.all()) {
        const box = await control.boundingBox();
        if (box) assert(box.x >= 0 && box.x + box.width <= width, `clipped hierarchy control at ${width}`);
      }
      const search = page.getByRole('searchbox',{name:'Search hierarchy'});
      await search.focus(); await page.keyboard.press('Tab');
      assert(await page.getByRole('button',{name:'Search',exact:true}).evaluate(e=>e===document.activeElement), `search tab order at ${width}`);
      const columns = page.locator('.hierarchy-columns');
      await columns.locator('summary').click();
      const menu = columns.locator('fieldset');
      const menuBox = await menu.boundingBox();
      assert(menuBox && menuBox.x >= 0 && menuBox.x + menuBox.width <= width, `clipped hierarchy column menu at ${width}`);
      await columns.locator('summary').click();
    }
    await page.goto(`${base}/`);
    for (const width of [320,767,768,1023,1024]) {
      await resize({width,height:900});
      await overflow();
      if(width<1024) {
        assert(!(await nav.isVisible())); assert.equal(await open.getAttribute('aria-label'),'Open navigation');
        await open.focus(); await page.keyboard.press('Enter');
        assert(await drawer.evaluate(e=>e.open)); assert.equal(await close.getAttribute('aria-label'),'Close navigation');
        assert.equal(await page.evaluate(()=>document.activeElement.hasAttribute('data-nav-close')),true);
        for (let i=0;i<12;i++) {await page.keyboard.press('Tab'); assert(await nav.evaluate(e=>e.contains(document.activeElement)));}
        for (let i=0;i<12;i++) {await page.keyboard.press('Shift+Tab'); assert(await nav.evaluate(e=>e.contains(document.activeElement)));}
        await overflow(); await shot(`drawer-${width}`);
        await page.keyboard.press('Escape');
        assert(!(await drawer.evaluate(e=>e.open))); assert(await open.evaluate(e=>e===document.activeElement));
        await open.click(); await page.mouse.click(width-5,450); assert(!(await drawer.evaluate(e=>e.open)));
        assert(await open.evaluate(e=>e===document.activeElement));
        await open.click(); await close.click(); assert(!(await drawer.evaluate(e=>e.open)));
        await shot(`closed-${width}`);
      } else assert(await nav.isVisible(),'restore expanded state on desktop');
    }
    await close.click();
    for(const width of [1023,768,767,320]) {
      await resize({width,height:600}); await open.click();
      await resize({width:1024,height:900});
      assert(!(await drawer.evaluate(e=>e.open))); assert(!(await nav.isVisible()));
      assert(await open.evaluate(e=>e===document.activeElement));
      await overflow();
    }
    await resize({width:320,height:600}); await open.click();
    const mobileSettings = page.getByRole('link',{name:'Settings',exact:true});
    await Promise.all([page.waitForURL(url => url.pathname === settingsPath), mobileSettings.click()]);
    await page.waitForLoadState('load');
    assert(!(await drawer.evaluate(e=>e.open))); await overflow();
    await open.click(); await overflow(); await shot('settings-drawer-320');
    await page.keyboard.press('Escape');
    await page.emulateMedia({reducedMotion:'reduce'}); await open.click(); await page.keyboard.press('Escape');
    assert(!(await drawer.evaluate(e=>e.open))); assert(await open.evaluate(e=>e===document.activeElement));
    // Touch activation uses the same real buttons, without synthetic click handlers.
    const touch = await browser.newContext({viewport:{width:320,height:600},hasTouch:true,storageState:await page.context().storageState()});
    const mobile = await touch.newPage(); await mobile.goto(`${base}/`);
    await mobile.getByRole('button',{name:'Open navigation',exact:true}).tap();
    assert(await mobile.locator('[data-navigation-drawer]').evaluate(e=>e.open));
    await mobile.getByRole('button',{name:'Close navigation',exact:true}).tap();
    assert(!(await mobile.locator('[data-navigation-drawer]').evaluate(e=>e.open)));
    await touch.close();
    assert.deepEqual(errors,[]);
    console.log('PASS: real Chromium layout, 320/767/768/1023/1024 boundaries, 240px recovery, icons aligned, keyboard, focus trapping/restoration, session navigation, Settings, backdrop, reduced motion, no page overflow or JS errors.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
