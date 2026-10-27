// Required CI regression: production lifecycle handlers, no test-side promise catches.
const assert = require('node:assert/strict');
const {chromium} = require('./browser_policy.cjs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname)) throw Error('Loopback only');
const navigate = async (page, action) => {
  const [response] = await Promise.all([page.waitForNavigation({waitUntil: 'load'}), action()]);
  if (response) assert.equal(response.status(), 200);
  await page.waitForFunction(() => typeof window.revealedWithTransition === 'boolean');
  // Let the page paint before the next action; browsers may skip overlapping transitions.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
};
const scrollPosition = page => page.locator('#content').evaluate(node => node.scrollTop);
(async () => {
  const browser = await chromium.launch();
  console.log('View transitions browser ' + browser.version());
  try {
    for (const reducedMotion of ['no-preference', 'reduce']) {
      const context = await browser.newContext({viewport: {width: 1440, height: 700}, reducedMotion});
      // Observation only: do not handle promises, cancel events or suppress errors.
      await context.addInitScript(() => {
        window.addEventListener('pagereveal', event => {
          window.revealedWithTransition = !!event.viewTransition;
          if (event.viewTransition) sessionStorage.setItem('test.transitions',
            String(Number(sessionStorage.getItem('test.transitions') || 0) + 1));
        });
      });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/login');
      await page.getByLabel('Email address').fill('admin@example.com');
      await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
      await navigate(page, () => page.getByRole('button', {name: 'Sign in', exact: true}).click());
      await page.goto(base + '/datasets/row-check/records');
      for (let round = 0; round < 3; round++) {
        // Native criteria submission retains keyboard focus and scroll position.
        const search = page.locator('#record-search');
        await search.fill('ITEM');
        await page.locator('#content').evaluate(node => { node.scrollTop = 120; });
        const before = await scrollPosition(page);
        assert(before > 0);
        await navigate(page, () => search.press('Enter'));
        assert.equal(new URL(page.url()).searchParams.get('q'), 'ITEM');
        await page.waitForFunction(() => document.activeElement.id === 'record-search');
        assert.equal(await scrollPosition(page), before);
        assert.equal(await page.locator('.record-table tbody tr').count(), 50);
        await navigate(page, () => page.getByRole('link', {name: 'Next', exact: true}).click());
        const secondPage = page.url();
        assert.equal(await page.locator('.record-table tbody tr').count(), 2);
        await page.goBack({waitUntil: 'load'});
        assert.equal(await page.locator('.record-table tbody tr').count(), 50);
        await page.goForward({waitUntil: 'load'});
        assert.equal(page.url(), secondPage);
        assert.equal(await page.locator('.record-table tbody tr').count(), 2);
        await page.goBack({waitUntil: 'load'});
        // Opening a panel is not navigation; closing restores its keyboard opener.
        const primary = page.getByRole('button', {name: /^View record /}).first();
        await primary.focus();
        const panelScroll = await scrollPosition(page);
        const panelURL = page.url();
        await page.keyboard.press('Enter');
        const panel = page.locator('dialog[data-modal-side-panel][open]');
        await panel.waitFor();
        assert.equal(page.url(), panelURL);
        assert(await panel.evaluate(node => node.contains(document.activeElement)));
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('dialog[data-modal-side-panel][open]'));
        await page.waitForFunction(node => node === document.activeElement, await primary.elementHandle());
        assert.equal(await scrollPosition(page), panelScroll);
        await navigate(page, () => page.getByRole('link', {name: 'Clear search', exact: true}).click());
      }
      // Cross-document navigation works both into and out of the sensitive opt-out.
      await page.goto(base + '/users');
      await navigate(page, () => page.getByRole('link', {name: 'API keys', exact: true}).click());
      await page.getByRole('heading', {name: 'API keys', exact: true}).waitFor();
      assert.equal(await page.evaluate(() => window.revealedWithTransition), false,
        'API-key pages remain excluded from incoming snapshots');
      await navigate(page, () => page.getByRole('link', {name: 'Users', exact: true}).click());
      assert.equal(await page.evaluate(() => window.revealedWithTransition), false,
        'API-key pages remain excluded from outgoing snapshots');
      // History has no annotated click: the incoming handler must also handle opt-out.
      await navigate(page, () => page.goBack());
      assert(new URL(page.url()).pathname.endsWith('/settings/api-keys'));
      assert.equal(await page.evaluate(() => window.revealedWithTransition), false);
      await navigate(page, () => page.goForward());
      assert.equal(new URL(page.url()).pathname, '/users');
      await navigate(page, () => page.getByRole('link', {name: 'Teams', exact: true}).click());
      if (reducedMotion === 'no-preference') {
        assert(Number(await page.evaluate(() => sessionStorage.getItem('test.transitions'))) > 0,
          'ordinary navigation retains cross-document transitions (individual swaps may be skipped by the browser)');
      }
      await page.waitForLoadState('networkidle');
      assert.deepEqual(errors, [], 'no page errors or unhandled promise rejections');
      await context.close();
      console.log(`PASS ${reducedMotion}: navigation, criteria, history, panel focus/scroll, API-key opt-out`);
    }
  } finally { await browser.close(); } // Shared guard asserts production CSP throughout.
})().catch(error => { console.error(error); process.exitCode = 1; });
