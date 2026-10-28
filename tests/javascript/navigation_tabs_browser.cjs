// Checks dataset tab navigation, responsive behavior and keyboard focus across sections.
const assert = require('node:assert/strict');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL;
const output = process.env.REFRNCR_BROWSER_OUTPUT;
if (!base || !['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) {
  throw Error('Use a disposable loopback app');
}

(async () => {
  const browser = await chromium.launch();
  try {
    // Authenticate through the real CSRF-protected form without cross-document animation.
    // Each scenario starts a fresh page; legacy transition failures are tested separately.
    const auth = await browser.newContext();
    const login = await auth.request.get(base + '/login');
    const csrf = (await login.text()).match(/name=['"]csrf_token['"]\s+value=['"]([^'"]+)/)[1];
    const signedIn = await auth.request.post(base + '/login', {form: {
      email: process.env.REFRNCR_BROWSER_EMAIL,
      password: process.env.REFRNCR_BROWSER_PASSWORD,
      csrf_token: csrf,
    }});
    assert.equal(signedIn.status(), 200);
    assert.equal(signedIn.url(), base + '/');
    const storageState = await auth.storageState();
    await auth.close();

    for (const theme of ['light', 'dark']) {
      for (const [name, path] of [
        ['app', '/'],
        ['fields', '/datasets/mapping-browser/concept?tab=fields'],
        ['settings', '/settings/appearance'],
      ]) {
        const context = await browser.newContext({
          storageState, viewport: {width: 1440, height: 900}, colorScheme: theme, hasTouch: true,
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + path);
        await page.evaluate(() => document.fonts.ready);
        const open = page.locator('[data-nav-open]');
        const close = page.locator('[data-nav-close]');
        const nav = page.locator('[data-navigation]');
        const drawer = page.locator('[data-navigation-drawer]');
        const resize = async width => {
          await page.setViewportSize({width, height: 900});
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        };
        const shot = async label => {
          if (output) await page.screenshot({path: `${output}/${theme}-${name}-${label}.png`});
        };
        const edgeTab = async (button, opening) => {
          const box = await button.boundingBox();
          assert(box.width >= 44 && box.height >= 44, '44px touch target');
          assert.equal(await button.locator('svg').count(), 0, 'no legacy panel icon');
          const tab = button.locator('.navigation-tab');
          const visual = await tab.boundingBox();
          assert.equal(visual.width, 14); assert.equal(visual.height, 33);
          const arrow = await tab.evaluate(e => {
            const style = getComputedStyle(e, '::before');
            return [style.borderLeftWidth, style.borderRightWidth];
          });
          assert.deepEqual(arrow, opening ? ['5px', '0px'] : ['0px', '5px']);
          if (opening) assert.equal(visual.x, 0, 'open tab on viewport edge');
          else {
            const sidebar = await nav.boundingBox();
            assert(Math.abs(visual.x + visual.width - sidebar.x - sidebar.width) <= 1,
              'close tab on sidebar edge');
          }
        };
        assert.equal(await page.locator('.mobile-bar, .mobile-bar-brand, .mobile-bar-title').count(), 0);
        if (name !== 'settings') {
          const expanded = (await page.locator('main').boundingBox()).width;
          await close.focus(); await page.keyboard.press('Enter');
          await edgeTab(open, true);
          assert.equal((await page.locator('main').boundingBox()).width - expanded, 240);
          assert(await open.evaluate(e => e === document.activeElement));
          await page.reload();
          assert(!(await nav.isVisible()), 'desktop collapse survives reload');
          await open.focus(); await page.keyboard.press('Space');
          assert(await nav.isVisible());
        }
        for (const width of [320, 767, 768, 1023, 1024, 1440]) {
          await resize(width);
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page overflow');
          if (width >= 1024) {
            assert(await nav.isVisible(), 'desktop sidebar restored');
            assert(!(await open.isVisible()));
            if (name === 'settings') assert(!(await close.isVisible()), 'Settings stays fixed');
            else await edgeTab(close, false);
            continue;
          }
          assert(!(await nav.isVisible()));
          assert.equal(await open.getAttribute('aria-label'), 'Open navigation');
          assert.equal(await open.getAttribute('aria-expanded'), 'false');
          await edgeTab(open, true);
          if (name !== 'settings') assert.equal((await page.locator('main').boundingBox()).y, 0);
          await shot(`closed-${width}`);
          // Tap outside the 14px tab to prove the larger transparent target works.
          const hit = await open.boundingBox();
          if (name === 'settings') {
            assert((await page.locator('.settings-title').boundingBox()).x >= hit.x + hit.width,
              'opener does not cover Settings title');
          } else {
            const inset = await page.locator('main').evaluate(e => parseFloat(getComputedStyle(e).paddingTop));
            assert(inset >= hit.y + hit.height, 'opener does not cover first page content');
          }
          await page.touchscreen.tap(hit.x + hit.width - 2, hit.y + hit.height / 2);
          assert(await drawer.evaluate(e => e.open));
          assert.equal(await close.getAttribute('aria-label'), 'Close navigation');
          assert.equal(await close.getAttribute('aria-expanded'), 'true');
          await edgeTab(close, false);
          assert(await close.evaluate(e => e === document.activeElement));
          if (name !== 'settings') assert(await nav.locator('.brand').isVisible());
          for (const key of ['Tab', 'Shift+Tab']) {
            for (let i = 0; i < 12; i++) {
              await page.keyboard.press(key);
              assert(await nav.evaluate(e => e.contains(document.activeElement)), 'focus stays in drawer');
            }
          }
          await shot(`drawer-${width}`);
          await page.keyboard.press('Escape');
          assert(!(await drawer.evaluate(e => e.open)));
          assert(await open.evaluate(e => e === document.activeElement));
          assert.equal(await open.evaluate(e => getComputedStyle(e).outlineWidth), '3px');
          await open.click(); await page.mouse.click(width - 2, 450);
          assert(!(await drawer.evaluate(e => e.open)), 'backdrop closes');
          assert(await open.evaluate(e => e === document.activeElement));
          await open.click(); await close.tap();
          assert(!(await drawer.evaluate(e => e.open)), 'close tab works');
          await open.click(); await resize(1440);
          assert(!(await drawer.evaluate(e => e.open)), 'resizing closes drawer');
        }
        await resize(320);
        await page.emulateMedia({reducedMotion: 'reduce'});
        await open.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Escape');
        assert(!(await drawer.evaluate(e => e.open)));
        assert(await open.evaluate(e => e === document.activeElement));
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    console.log('PASS: navigation tabs on app, mapping Fields and Settings; light/dark; 320/767/768/1023/1024/1440px; touch, keyboard, focus, resize, persistence and reduced motion.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
