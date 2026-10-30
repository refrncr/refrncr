// Checks overview sections, filtering, responsive cards, accessibility and color contrast.
const {chromium, settleViewTransition} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE, headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = 'http://127.0.0.1:5195';
  const evidence = process.env.REFRNCR_BROWSER_EVIDENCE
    || process.env.REFRNCR_BROWSER_OUTPUT
    || path.join(os.tmpdir(), 'ref238-review-evidence');
  fs.mkdirSync(evidence, {recursive: true});
  const login = async target => {
    await target.goto(`${base}/login`);
    await target.getByLabel('Email address').fill('admin@example.com');
    await target.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
    await Promise.all([
      target.waitForURL(`${base}/`),
      target.getByRole('button', {name: 'Sign in', exact: true}).click(),
    ]);
  };
  try {
    await login(page);
    assert(await page.getByRole('heading', {name: "You're not in a team yet"}).isVisible());
    await Promise.all([
      page.waitForURL(`${base}/?all_owners=1`),
      page.getByRole('switch', {name: 'Show all owners'}).check(),
    ]);
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
    const visitedCard = page.locator('.dataset-card').filter({has: page.locator('.dataset-card-foot')}).first();
    await visitedCard.locator('.dataset-card-link').focus();
    await page.keyboard.press('Tab');
    assert(await visitedCard.locator('.dataset-card-actions > summary').evaluate(el => el === document.activeElement));
    await page.keyboard.press('Tab'); // Full description is independently accessible.
    assert(await visitedCard.locator('.dataset-card-desc').evaluate(el => el === document.activeElement));
    await page.keyboard.press('Tab');
    assert(await visitedCard.locator('.dataset-card-foot summary').evaluate(el => el === document.activeElement));
    await page.keyboard.press('Enter');
    assert(await visitedCard.getByRole('link', {name:'CSV', exact:true}).isVisible());
    await page.keyboard.press('Enter');
    await Promise.all([
      page.waitForURL(`${base}/attention?all_owners=1`),
      page.getByRole('link', {name: 'View all', exact: true}).click(),
    ]);
    await Promise.all([
      page.waitForURL(/reason=6/),
      page.locator('#attention-reason').selectOption('6'),
    ]);
    assert(await page.getByRole('switch').isChecked());
    await Promise.all([
      page.waitForURL(/reason=$/),
      page.locator('#attention-reason').selectOption(''),
    ]);
    assert(await page.getByRole('heading', {name: /^Needs your attention/}).isVisible());
    for (const theme of ['light', 'dark']) {
      for (const width of [375, 768, 1440]) {
        await page.setViewportSize({width, height: width === 1440 ? 2000 : 1100});
        await page.goto(`${base}/?all_owners=1`);
        await settleViewTransition(page);
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
          const grid=getComputedStyle(document.querySelector('.card-grid-attention'));
          const cardStyle = getComputedStyle(document.querySelector('.dataset-card'));
          const surface = cardStyle.backgroundColor;
          const text = [...document.querySelectorAll('.dataset-card-title, .dataset-card-kind, .dataset-card-desc, .dataset-card-meta, .dataset-card-more, .dataset-card-note, .dataset-card-actions > summary')].map(el => ratio(getComputedStyle(el).color, surface));
          // Only control boundaries need 3:1 (WCAG 1.4.11): cards are identified by their name link,
          // the tally by its links and callouts by text and icon, so their edges may stay subtle.
          const borders = [...document.querySelectorAll('.dataset-card-foot .button')].map(el => {
            const s=getComputedStyle(el);
            return Math.min(ratio(s.borderTopColor, s.backgroundColor), ratio(s.borderTopColor, surface));
          });
          const titles = [...document.querySelectorAll('.dataset-card-title')].map(el => ({name:el.textContent, clipped:el.scrollHeight > el.clientHeight + 1, lines:el.clientHeight / parseFloat(getComputedStyle(el).lineHeight)}));
          const firstCard = document.querySelector('.dataset-card');
          const head = firstCard.querySelector('.dataset-card-head').getBoundingClientRect();
          const title = firstCard.querySelector('.dataset-card-title').getBoundingClientRect();
          const description = firstCard.querySelector('.dataset-card-desc').getBoundingClientRect();
          const meta = firstCard.querySelector('.dataset-card-meta').getBoundingClientRect();
          const callout = firstCard.querySelector('.callout').getBoundingClientRect();
          const section = document.querySelector('.section-heading').getBoundingClientRect();
          const actions = document.querySelector('.section-heading-end').getBoundingClientRect();
          const menu = getComputedStyle(firstCard.querySelector('.dataset-card-actions > summary'));
          return {overflow:document.documentElement.scrollWidth > innerWidth, columns:grid.gridTemplateColumns.split(' ').length,
            icon:ratio(tile.color,tile.backgroundColor), switch:ratio(track.backgroundColor,background),
            count:ratio(count.color,count.backgroundColor), calls, text, borders, titles,
            correctAnatomy: head.bottom <= title.top && title.bottom <= description.top && description.bottom <= meta.top && meta.bottom <= callout.top,
            fullWidthTitle: Math.abs(head.width - title.width) < 1,
            actionsRight: Math.abs(section.right - actions.right) < 1,
            actionsSameRow: actions.top < section.top + section.height / 2,
            menuBorder: menu.borderTopWidth,
            switchHeight:document.querySelector('.switch').getBoundingClientRect().height};
        });
        assert(!metrics.overflow, `${theme} ${width}: horizontal overflow`);
        assert.equal(metrics.columns, width < 768 ? 1 : width < 1200 ? 2 : 3);
        assert(metrics.correctAnatomy && metrics.fullWidthTitle, JSON.stringify(metrics));
        assert(metrics.titles.every(title => !title.clipped && title.lines <= 2.1), JSON.stringify(metrics.titles));
        if (width === 1440) assert(metrics.actionsRight && metrics.actionsSameRow);
        assert.equal(metrics.menuBorder, '0px');
        assert(metrics.text.every(value => value >= 4.5) && metrics.borders.every(value => value >= 3), JSON.stringify(metrics));
        assert(metrics.icon >= 3 && metrics.switch >= 3, JSON.stringify(metrics));
        assert(metrics.count >= 4.5 && metrics.calls.every(value => value >= 4.5), JSON.stringify(metrics));
        assert(metrics.switchHeight >= 44);
        await page.locator('.dataset-card-link').first().focus();
        assert.equal(await page.locator('.dataset-card').first().evaluate(el => getComputedStyle(el).outlineWidth), '3px');
        const menu = page.locator('.dataset-card-actions > summary').first();
        await menu.focus();
        assert.equal(await menu.evaluate(el => getComputedStyle(el).outlineWidth), '3px');
        await page.locator('h1').click();
        await page.screenshot({path:`${evidence}/${theme}-${width}.png`, fullPage:true});
        console.log(theme, width, metrics);
      }
    }
    // Native form fallback remains operable without scripts.
    const nojs = await browser.newContext({javaScriptEnabled:false});
    const native = await nojs.newPage();
    await login(native);
    await native.getByRole('switch').check();
    await Promise.all([
      native.waitForURL(`${base}/?all_owners=1`),
      native.getByRole('button', {name:'Apply', exact:true}).click(),
    ]);
    assert(await native.getByRole('heading', {name:/^Needs action/}).isVisible());
    await nojs.close();
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
