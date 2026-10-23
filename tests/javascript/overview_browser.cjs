// Run against tests/browser_overview_app.py with an existing Playwright installation.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE, headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = 'http://127.0.0.1:5195';
  const evidence = process.env.REFRNCR_BROWSER_EVIDENCE || '/private/tmp/ref238-evidence';
  fs.mkdirSync(evidence, {recursive: true});
  const login = async target => {
    await target.goto(`${base}/login`);
    await target.getByLabel('Email address').fill('admin@example.com');
    await target.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
    await target.getByRole('button', {name: 'Sign in', exact: true}).click();
    await target.waitForURL(`${base}/`);
  };
  try {
    await login(page);
    assert(await page.getByRole('heading', {name: "You're not in a team yet"}).isVisible());
    await page.getByRole('switch', {name: 'Show all owners'}).check();
    await page.waitForURL(`${base}/?all_owners=1`);
    assert(await page.getByRole('heading', {name: /^Needs action/}).isVisible());
    assert(await page.getByRole('heading', {name: /^Coming up/}).isVisible());
    assert(await page.getByRole('heading', {name: 'To finish'}).isVisible());
    const first = page.locator('.dataset-card-link').first();
    await first.focus();
    await page.keyboard.press('Tab');
    assert(await page.locator('.dataset-card-actions > summary').first().evaluate(el => el === document.activeElement));
    await page.keyboard.press('Enter');
    assert(await page.locator('.dataset-card-actions').first().evaluate(el => el.open));
    await page.keyboard.press('Enter');
    await page.getByRole('link', {name: 'View all', exact: true}).click();
    await page.waitForURL(`${base}/attention?all_owners=1`);
    await page.getByLabel('Reason', {exact: true}).selectOption('6');
    await page.waitForURL(/reason=6/);
    assert(await page.getByRole('switch').isChecked());
    await page.getByLabel('Reason', {exact: true}).selectOption('');
    await page.waitForURL(/reason=$/);
    assert(await page.getByRole('heading', {name: 'Needs your attention', exact: true}).isVisible());
    for (const theme of ['light', 'dark']) {
      for (const width of [320, 768, 1440]) {
        await page.setViewportSize({width, height: 1100});
        await page.goto(`${base}/?all_owners=1`);
        await page.waitForTimeout(200); // Let the app's cross-document view transition settle.
        await page.evaluate(value => document.documentElement.dataset.theme = value, theme);
        await page.evaluate(() => document.fonts.ready);
        const metrics = await page.evaluate(() => {
          const rgb = value => value.match(/[\d.]+/g).slice(0,3).map(Number);
          const lum = color => rgb(color).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i],0);
          const ratio = (a,b) => (Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
          const probe = document.createElement('div');
          probe.style.backgroundColor = 'var(--color-background)';
          document.body.append(probe);
          const background = getComputedStyle(probe).backgroundColor;
          probe.remove();
          const tile = getComputedStyle(document.querySelector('.dataset-icon-tile'));
          const track = getComputedStyle(document.querySelector('.switch input'));
          const count = getComputedStyle(document.querySelector('.count'));
          const calls = [...document.querySelectorAll('.callout')].map(el => {const s=getComputedStyle(el); return ratio(s.color,s.backgroundColor);});
          const grid=getComputedStyle(document.querySelector('.card-grid'));
          return {overflow:document.documentElement.scrollWidth > innerWidth, columns:grid.gridTemplateColumns.split(' ').length,
            icon:ratio(tile.color,tile.backgroundColor), switch:ratio(track.backgroundColor,background),
            count:ratio(count.color,count.backgroundColor), calls,
            switchHeight:document.querySelector('.switch').getBoundingClientRect().height};
        });
        assert(!metrics.overflow, `${theme} ${width}: horizontal overflow`);
        if (width < 768) assert.equal(metrics.columns, 1);
        assert(metrics.icon >= 3 && metrics.switch >= 3, JSON.stringify(metrics));
        assert(metrics.count >= 4.5 && metrics.calls.every(value => value >= 4.5), JSON.stringify(metrics));
        assert(metrics.switchHeight >= 44);
        await page.locator('.dataset-card-link').first().focus();
        assert.equal(await page.locator('.dataset-card').first().evaluate(el => getComputedStyle(el).outlineWidth), '3px');
        await page.screenshot({path:`${evidence}/${theme}-${width}.png`, fullPage:true});
        console.log(theme, width, metrics);
      }
    }
    // Native form fallback remains operable without scripts.
    const nojs = await browser.newContext({javaScriptEnabled:false});
    const native = await nojs.newPage();
    await login(native);
    await native.getByRole('switch').check();
    await native.getByRole('button', {name:'Apply', exact:true}).click();
    await native.waitForURL(`${base}/?all_owners=1`);
    assert(await native.getByRole('heading', {name:/^Needs action/}).isVisible());
    await nojs.close();
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
